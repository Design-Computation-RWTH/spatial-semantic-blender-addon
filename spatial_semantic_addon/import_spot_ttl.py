from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import bpy
from mathutils import Matrix, Vector

from .build_spot_scene import instantiate_helpers, save_blend_file
from .spot_am_placement import (
    BoundaryPointMapping,
    SpotAmSpace,
    derive_spot_am_placements,
)
from .spot_blender_common import (
    BOT_CLASS_BUILDING,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SITE,
    BOT_CLASS_SPACE,
    BOT_CLASS_STOREY,
    HELPER_COLLECTION_NAME,
    LENGTH_UNIT_METER,
    LENGTH_UNIT_PIXEL,
    PROP_HELPER,
    PROP_REPRESENTS_LINKS,
    PROP_URI_SUFFIX,
    ROLE_BUILDING,
    ROLE_ELEMENT,
    ROLE_MODEL,
    ROLE_SITE,
    ROLE_SPACE,
    ROLE_STOREY,
    SPOT_CLASS_ASSET,
    SPOT_CLASS_ENTITY,
    Aabb,
    SpaceNodeSpec,
    append_name_tags,
    bbox_vertices,
    blender_script_argv,
    reset_helper_collection,
    slugify,
)


SPOT_ASSET_APPEARANCE = "spot:AssetAppearance"
SPOT_ENTITY_APPEARANCE = "spot:EntityAppearance"
SPOT_DOCUMENT_APPEARANCE = "spot:DocumentAppearance"

SPOT_CLASS_DOCUMENT_SPACE = "DocumentSpace"
# Plans and sections never stand in the BOT hierarchy themselves: when one of
# them represents a BOT referent that another space also represents, the other
# space owns it and the plan only links to it.
LINKING_SPOT_CLASSES = {"HorizontalAreaSpace", "VerticalAreaSpace"}

KNOWN_PREFIXES = {
    "beo": "https://pi.pauwel.be/voc/buildingelement#",
    "bot": "https://w3id.org/bot#",
    "gom": "https://w3id.org/gom#",
    "qudt": "http://qudt.org/schema/qudt/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "reloc": "https://w3id.org/reloc#",
    "spot": "https://w3id.org/spot#",
    "spot-am": "https://w3id.org/spot/am#",
    "spot-sg": "https://w3id.org/spot/sg#",
    "unit": "http://qudt.org/vocab/unit/",
}
PREFIX_PATTERN = re.compile(r"^@prefix\s+([^:]*):\s*<([^>]*)>\s*\.$")
BASE_PATTERN = re.compile(r"^@base\s+<([^>]*)>\s*\.$")


def appearance_priority(appearance_type: str | None) -> int:
    """Rank competing appearances of one space.

    A space that is both placed in an asset space and laid out on a drawing
    sheet carries several appearances. The spatial hierarchy follows the
    highest-ranked one; document appearances describe sheet layout rather than
    a position in the building, so they never win.
    """
    if appearance_type == SPOT_ASSET_APPEARANCE:
        return 3
    if appearance_type == SPOT_ENTITY_APPEARANCE:
        return 2
    if appearance_type == SPOT_DOCUMENT_APPEARANCE:
        return 0
    return 1


@dataclass
class ImportedSpace:
    iri: str
    local_name: str
    spot_class: str
    label: str
    local_bbox: list[float] | None
    order: int
    length_unit: str = LENGTH_UNIT_METER
    bot_iri: str | None = None
    bot_local_name: str | None = None
    represented_iris: list[str] = field(default_factory=list)
    # Local names of the spaces owning the further BOT referents this space
    # represents (see PROP_REPRESENTS_LINKS).
    link_target_names: list[str] = field(default_factory=list)
    bot_class: str | None = None
    beo_class: str | None = None
    local_matrix: Matrix = field(default_factory=lambda: Matrix.Identity(4))
    parent_iri: str | None = None
    children: list[str] = field(default_factory=list)
    approximate_region: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a SPOT scene-graph file or dataset folder as a Blender helper scene.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input SPOT scene-graph Turtle file or RDF dataset folder.",
    )
    parser.add_argument(
        "--helper-collection",
        default=HELPER_COLLECTION_NAME,
        help="Name of the helper collection to create or replace.",
    )
    parser.add_argument(
        "--save-blend",
        help="Optional output .blend path to save after importing the helper scene.",
    )
    return parser.parse_args(blender_script_argv())


def split_turtle_statements(text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current.append(line)
        if line.endswith("."):
            statements.append(" ".join(current))
            current = []

    if current:
        raise RuntimeError("Encountered an incomplete Turtle statement while importing")

    return statements


def compact_iri(iri: str) -> str:
    for prefix, namespace in KNOWN_PREFIXES.items():
        if iri.startswith(namespace):
            return f"{prefix}:{iri[len(namespace):]}"
    return f"<{iri}>"


def normalize_resource_token(
    token: str,
    prefixes: dict[str, str],
    base_iri: str | None,
) -> str:
    value = token.strip()
    if value == "a":
        return "a"
    if value.startswith("<") and value.endswith(">"):
        iri = value[1:-1]
        return compact_iri(urljoin(base_iri, iri) if base_iri else iri)
    if ":" in value and not value.startswith("_:"):
        prefix, local_name = value.split(":", 1)
        namespace = prefixes.get(prefix)
        if namespace is not None:
            return compact_iri(f"{namespace}{local_name}")
    return value


def normalize_object_token(
    token: str,
    prefixes: dict[str, str],
    base_iri: str | None,
) -> str:
    value = token.strip()
    datatype_marker = value.rfind("^^")
    if value.startswith('"') and datatype_marker >= 0:
        literal = value[:datatype_marker]
        datatype = normalize_resource_token(
            value[datatype_marker + 2 :],
            prefixes,
            base_iri,
        )
        return f"{literal}^^{datatype}"
    if value.startswith('"'):
        return value
    return normalize_resource_token(value, prefixes, base_iri)


def parse_rdf_source(text: str) -> list[tuple[str, str, str]]:
    prefixes = dict(KNOWN_PREFIXES)
    base_iri: str | None = None
    content_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        prefix_match = PREFIX_PATTERN.match(line)
        if prefix_match:
            prefixes[prefix_match.group(1)] = prefix_match.group(2)
            continue
        base_match = BASE_PATTERN.match(line)
        if base_match:
            base_iri = base_match.group(1)
            continue
        if line.startswith("VERSION ") or line == "}" or line.endswith(" {"):
            continue
        content_lines.append(raw_line)

    triples: list[tuple[str, str, str]] = []
    for statement in split_turtle_statements("\n".join(content_lines)):
        subject, predicate_text = statement.split(" ", 1)
        normalized_subject = normalize_resource_token(subject, prefixes, base_iri)
        for chunk in parse_predicate_chunks(predicate_text):
            if chunk.startswith("a "):
                predicate = "a"
                object_text = chunk[2:].strip()
            else:
                predicate, object_text = chunk.split(" ", 1)
                predicate = normalize_resource_token(predicate, prefixes, base_iri)
                if predicate == "rdf:type":
                    predicate = "a"
            for object_value in split_turtle_object_list(object_text):
                triples.append(
                    (
                        normalized_subject,
                        predicate,
                        normalize_object_token(object_value, prefixes, base_iri),
                    )
                )
    return triples


def serialize_grouped_triples(triples: list[tuple[str, str, str]]) -> str:
    grouped: dict[str, dict[str, list[str]]] = {}
    for subject, predicate, object_value in triples:
        values = grouped.setdefault(subject, {}).setdefault(predicate, [])
        if object_value not in values:
            values.append(object_value)

    statements: list[str] = []
    for subject, predicates in grouped.items():
        chunks = [
            f"{predicate} {', '.join(values)}"
            for predicate, values in predicates.items()
        ]
        statements.append(f"{subject} {' ; '.join(chunks)} .")
    return "\n".join(statements)


ENTRY_PATTERN = re.compile(r"^#!lbcf-entry\s+(delta|retraction)\b.*$")


def split_mutation_entries(source: str) -> list[tuple[str, str]]:
    """Splits the committed mutation log into its entries, in commit order.

    The gateway writes additions and the retractions an update produces into one
    ordered log, because they cannot be replayed as two independent sets: a
    field edited back to an earlier value asserts that value twice and retracts
    it once, so subtracting the retractions afterwards deletes the restored
    current value as well.

    Content before the first marker was written by an older gateway that had no
    entry markers. It is treated as one addition, which is how the file behaved
    then.
    """
    entries: list[tuple[str, str]] = []
    kind = "delta"
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer)
        if body.strip():
            entries.append((kind, body))
        buffer.clear()

    for line in source.split("\n"):
        match = ENTRY_PATTERN.match(line)
        if match:
            flush()
            kind = match.group(1)
            continue
        buffer.append(line)
    flush()
    return entries


def load_dataset_folder(input_dir: str | Path) -> tuple[str, list[str]]:
    dataset_dir = Path(input_dir).expanduser().resolve()
    rdf_files = sorted(
        path
        for path in dataset_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".ttl", ".trig"}
    )
    if not rdf_files:
        raise RuntimeError(
            f"The dataset folder contains no Turtle or TriG files: {dataset_dir}"
        )

    # The mutation log is replayed separately and in order, so it is skipped
    # here. Loading it as ordinary data would assert every value a later entry
    # retracted.
    mutations_path = dataset_dir / "rdf" / "mutations.trig"

    # The dump already holds the authored RDF with the log replayed over it. When
    # it exists it is the only file read: adding the sources back would re-assert
    # the superseded values the replay just removed. This mirrors what the graph
    # gateway does, so both tools agree on the current state.
    dump_path = dataset_dir / "dump" / "dataset.trig"
    triples: list[tuple[str, str, str]] = []
    if dump_path.is_file():
        triples.extend(parse_rdf_source(dump_path.read_text(encoding="utf8")))
        relative_files = [str(path.relative_to(dataset_dir)) for path in rdf_files]
        return serialize_grouped_triples(triples), relative_files

    for rdf_file in rdf_files:
        if rdf_file == mutations_path:
            continue
        triples.extend(parse_rdf_source(rdf_file.read_text(encoding="utf8")))

    if mutations_path.is_file():
        for kind, body in split_mutation_entries(
            mutations_path.read_text(encoding="utf8")
        ):
            if kind == "delta":
                triples.extend(parse_rdf_source(body))
                continue
            retracted = set(parse_rdf_source(body))
            triples = [triple for triple in triples if triple not in retracted]

    relative_files = [str(path.relative_to(dataset_dir)) for path in rdf_files]
    return serialize_grouped_triples(triples), relative_files


def parse_prefixed_local_name(value: str) -> str:
    if value.startswith("<") and value.endswith(">"):
        iri = value[1:-1]
        if "#" in iri:
            return iri.rsplit("#", 1)[1]
        return iri.rsplit("/", 1)[-1]

    if ":" not in value:
        raise RuntimeError(f"Expected a prefixed name or IRI, received '{value}'")
    return value.split(":", 1)[1]


def unescape_turtle_string(literal: str) -> str:
    if len(literal) < 2 or not literal.startswith('"') or not literal.endswith('"'):
        raise RuntimeError(f"Expected a quoted Turtle string, received '{literal}'")

    inner = literal[1:-1]
    result: list[str] = []
    escaped = False
    for char in inner:
        if escaped:
            result.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            result.append(char)

    if escaped:
        raise RuntimeError(f"Dangling escape sequence in Turtle string '{literal}'")

    return "".join(result)


def parse_typed_string_literal(object_value: str, datatype: str) -> str:
    suffix = f"^^{datatype}"
    if not object_value.endswith(suffix):
        raise RuntimeError(
            f"Expected a {datatype} typed literal, received '{object_value}'"
        )
    return unescape_turtle_string(object_value[: -len(suffix)])


def split_turtle_object_list(object_value: str) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    in_string = False
    in_iri = False
    escaped = False

    for char in object_value:
        if in_string:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if in_iri:
            current.append(char)
            if char == ">":
                in_iri = False
            continue

        if char == '"':
            in_string = True
            current.append(char)
        elif char == "<":
            in_iri = True
            current.append(char)
        elif char == ",":
            value = "".join(current).strip()
            if value:
                values.append(value)
            current = []
        else:
            current.append(char)

    value = "".join(current).strip()
    if value:
        values.append(value)
    return values


def parse_number_array(text: str, expected_length: int) -> list[float]:
    trimmed = text.strip()
    inner = (
        trimmed[1:-1] if trimmed.startswith("[") and trimmed.endswith("]") else trimmed
    )
    values = [float(part.strip()) for part in inner.split(",") if part.strip()]
    if len(values) != expected_length:
        raise RuntimeError(
            f"Expected {expected_length} numeric values, received {len(values)} in '{text}'"
        )
    return values


def enclosing_bbox(local_bboxes: list[list[float]]) -> list[float]:
    return [
        *(min(local_bbox[index] for local_bbox in local_bboxes) for index in range(3)),
        *(max(local_bbox[index] for local_bbox in local_bboxes) for index in range(3, 6)),
    ]


def parse_column_major_matrix(text: str) -> Matrix:
    values = parse_number_array(text, 16)
    rows = [[values[column * 4 + row] for column in range(4)] for row in range(4)]
    return Matrix(rows)


def parse_length_unit(object_value: str) -> str:
    local_name = parse_prefixed_local_name(object_value).upper()
    if local_name == "PIXEL":
        return LENGTH_UNIT_PIXEL
    return LENGTH_UNIT_METER


def parse_predicate_chunks(predicate_text: str) -> list[str]:
    body = predicate_text.strip()
    if not body.endswith("."):
        raise RuntimeError(
            f"Expected Turtle statement terminator in '{predicate_text}'"
        )
    body = body[:-1].strip()
    return [chunk.strip() for chunk in body.split(" ; ") if chunk.strip()]


def local_bbox_to_world_bbox(local_bbox: list[float], world_matrix: Matrix) -> Aabb:
    corners = [world_matrix @ Vector(corner) for corner in bbox_vertices(local_bbox)]
    return Aabb(
        min_corner=Vector(
            tuple(min(corner[index] for corner in corners) for index in range(3)),
        ),
        max_corner=Vector(
            tuple(max(corner[index] for corner in corners) for index in range(3)),
        ),
    )


def infer_role(
    space: ImportedSpace,
    parent_role: str | None,
) -> str:
    if space.bot_class == BOT_CLASS_SITE:
        return ROLE_SITE
    if space.bot_class == BOT_CLASS_BUILDING:
        return ROLE_BUILDING
    if space.bot_class == BOT_CLASS_STOREY:
        return ROLE_STOREY
    if space.bot_class == BOT_CLASS_SPACE:
        return ROLE_SPACE
    if space.bot_class == BOT_CLASS_ELEMENT:
        return ROLE_ELEMENT

    if parent_role is None or space.spot_class == "AssetSpace":
        return ROLE_SITE
    if parent_role == ROLE_SITE:
        return ROLE_BUILDING if space.children else ROLE_ELEMENT
    if parent_role == ROLE_BUILDING:
        if space.bot_class is None and space.children:
            return ROLE_MODEL
        return ROLE_STOREY if space.children else ROLE_ELEMENT
    if parent_role == ROLE_MODEL:
        return ROLE_STOREY if space.children else ROLE_ELEMENT
    return ROLE_ELEMENT


def helper_name_for_import(
    role: str,
    local_name: str,
    bot_class: str | None,
    spot_class: str,
    beo_class: str | None,
) -> str:
    if role == ROLE_SITE:
        base_name = f"SPOT::Imported::Site::{local_name}"
    elif role == ROLE_BUILDING:
        base_name = f"SPOT::Imported::Building::{local_name}"
    elif role == ROLE_MODEL:
        base_name = f"SPOT::Imported::Model::{local_name}"
    elif role == ROLE_STOREY:
        base_name = f"SPOT::Imported::Storey::{local_name}"
    elif role == ROLE_SPACE:
        base_name = f"SPOT::Imported::Space::{local_name}"
    else:
        base_name = f"SPOT::Imported::Element::{local_name}"

    return append_name_tags(
        base_name,
        bot_class=bot_class,
        spot_class=spot_class,
        beo_class=beo_class,
    )


def apply_spot_am_placements(
    spaces: dict[str, ImportedSpace],
    appearance_sources: dict[str, str],
    appearance_matrices: dict[str, Matrix],
    appearance_boundary_mappings: dict[str, list[str]],
    boundary_point_mappings: dict[str, BoundaryPointMapping],
    boundary_points: set[str],
    axis_directions: dict[str, str],
    space_axes: dict[str, dict[str, str]],
    warning_messages: list[str],
) -> None:
    """Place spaces that SPOT-AM locates only approximately.

    Such a space carries no transformation matrix, so the importer would
    otherwise leave it at its parent's origin at a default marker size. The
    derived region replaces both its position and its bounding box.
    """
    mappings_by_space: dict[str, list[BoundaryPointMapping]] = {}
    for appearance_id, mapping_ids in appearance_boundary_mappings.items():
        source_iri = appearance_sources.get(appearance_id)
        if source_iri is None or source_iri not in spaces:
            continue
        # An explicit matrix states the position outright; the approximate
        # region is only needed when nothing else places the space.
        matrix = appearance_matrices.get(appearance_id)
        if matrix is not None and matrix != Matrix.Identity(4):
            continue

        resolved = [
            boundary_point_mappings[mapping_id]
            for mapping_id in mapping_ids
            if mapping_id in boundary_point_mappings
            and boundary_point_mappings[mapping_id].source_point_iri
            in boundary_points
        ]
        if resolved:
            mappings_by_space[source_iri] = resolved

    if not mappings_by_space:
        return

    directions_by_space: dict[str, dict[str, str]] = {}
    for space_iri, axes in space_axes.items():
        directions = {
            axis_name: axis_directions[axis_iri]
            for axis_name, axis_iri in axes.items()
            if axis_iri in axis_directions
        }
        if directions:
            directions_by_space[space_iri] = directions

    am_spaces = {
        space_iri: SpotAmSpace(
            iri=space_iri,
            local_bbox=space.local_bbox,
            parent_iri=space.parent_iri,
            axis_directions=directions_by_space.get(space_iri, {}),
            local_matrix=space.local_matrix,
        )
        for space_iri, space in spaces.items()
    }

    placements = derive_spot_am_placements(am_spaces, mappings_by_space)
    for space_iri, placement in placements.items():
        space = spaces[space_iri]
        space.local_matrix = Matrix.Translation(Vector(placement.center))
        space.local_bbox = placement.centered_local_bbox()
        space.approximate_region = True

    unresolved = sorted(set(mappings_by_space) - set(placements))
    if unresolved:
        warning_messages.append(
            f"Could not derive an approximate position for {len(unresolved)} "
            "SPOT-AM spaces"
        )
    if placements:
        warning_messages.append(
            f"Derived approximate regions for {len(placements)} SPOT-AM spaces"
        )


def parse_scene_graph_turtle(
    text: str,
    warnings: list[str] | None = None,
) -> tuple[dict[str, ImportedSpace], list[str]]:
    warning_messages = warnings if warnings is not None else []
    spaces: dict[str, ImportedSpace] = {}
    appearance_sources: dict[str, str] = {}
    appearance_targets: dict[str, str] = {}
    appearance_matrices: dict[str, Matrix] = {}
    appearance_types: dict[str, str] = {}
    appearance_boundary_mappings: dict[str, list[str]] = {}
    boundary_point_mappings: dict[str, BoundaryPointMapping] = {}
    boundary_points: set[str] = set()
    axis_directions: dict[str, str] = {}
    space_axes: dict[str, dict[str, str]] = {}
    bot_types: dict[str, str] = {}
    beo_types: dict[str, str] = {}
    order = 0
    skipped_statements = 0

    for statement in split_turtle_statements(text):
        if statement.startswith("@prefix "):
            continue

        subject, predicate_text = statement.split(" ", 1)
        predicate_chunks = parse_predicate_chunks(predicate_text)

        rdf_types: list[str] = []
        predicates: dict[str, str] = {}
        for chunk in predicate_chunks:
            if chunk.startswith("a "):
                rdf_types.extend(split_turtle_object_list(chunk[2:].strip()))
                continue
            predicate, object_value = chunk.split(" ", 1)
            predicate = predicate.strip()
            # A repeated predicate adds objects rather than replacing them,
            # as `p a ; p b` means the same as `p a, b`.
            if predicate in predicates:
                predicates[predicate] += f", {object_value.strip()}"
            else:
                predicates[predicate] = object_value.strip()

        linked_appearances = split_turtle_object_list(
            predicates.get("spot:hasAppearance", "")
        )
        for appearance_id in linked_appearances:
            appearance_sources[appearance_id] = subject

        if "spot:appearsIn" in predicates:
            appearance_type = next(
                (
                    rdf_type
                    for rdf_type in rdf_types
                    if rdf_type.endswith("Appearance")
                ),
                None,
            )
            if appearance_type is not None:
                appearance_types[subject] = appearance_type
            boundary_point_mapping_ids = split_turtle_object_list(
                predicates.get("spot-am:hasBoundaryPointMapping", "")
            )
            if boundary_point_mapping_ids:
                appearance_boundary_mappings[subject] = boundary_point_mapping_ids
            target_values = split_turtle_object_list(predicates["spot:appearsIn"])
            if len(target_values) > 1:
                warning_messages.append(
                    f"Appearance '{subject}' has {len(target_values)} spot:appearsIn "
                    f"values; using the first one"
                )
            appearance_targets[subject] = target_values[0]
            source_values = split_turtle_object_list(
                predicates.get("gom:fromCoordinateSystem", "")
            )
            if source_values:
                appearance_sources.setdefault(subject, source_values[0])
            matrix_values = split_turtle_object_list(
                predicates.get("gom:hasTransformationMatrix", "")
            )
            if len(matrix_values) > 1:
                warning_messages.append(
                    f"Appearance '{subject}' has {len(matrix_values)} transformation "
                    f"matrices; using the first one"
                )
            appearance_matrices[subject] = (
                parse_column_major_matrix(
                    parse_typed_string_literal(
                        matrix_values[0],
                        "gom:columnMajorArray",
                    )
                )
                if matrix_values
                else Matrix.Identity(4)
            )
            continue

        if "spot-am:BoundaryPointMapping" in rdf_types:
            # The reloc: predicate names the relation ("containedInRear") and
            # its object names the space the source point is related to.
            relation = next(
                (
                    predicate
                    for predicate in predicates
                    if predicate.startswith("reloc:")
                ),
                None,
            )
            source_points = split_turtle_object_list(
                predicates.get("spot-am:hasSourcePoint", "")
            )
            if relation is not None and source_points:
                targets = split_turtle_object_list(predicates[relation])
                if targets:
                    boundary_point_mappings[subject] = BoundaryPointMapping(
                        relation_name=parse_prefixed_local_name(relation),
                        source_point_iri=source_points[0],
                        target_iri=targets[0],
                    )
            continue

        if "spot-am:MinBoundaryPoint" in rdf_types or (
            "spot-am:MaxBoundaryPoint" in rdf_types
        ):
            boundary_points.add(subject)
            continue

        if "spot:Axis" in rdf_types:
            directions = split_turtle_object_list(
                predicates.get("spot:hasDirection", "")
            )
            if directions:
                axis_directions[subject] = parse_prefixed_local_name(directions[0])
            continue

        spot_space_types = [
            rdf_type
            for rdf_type in rdf_types
            if rdf_type.startswith("spot:")
            and parse_prefixed_local_name(rdf_type).endswith("Space")
        ]
        if spot_space_types:
            local_name = parse_prefixed_local_name(subject)
            spot_type = next(
                (
                    rdf_type
                    for rdf_type in spot_space_types
                    if parse_prefixed_local_name(rdf_type) != SPOT_CLASS_ENTITY
                ),
                spot_space_types[0],
            )
            label_objects = split_turtle_object_list(
                predicates.get("rdfs:label", "")
            )
            labels = [unescape_turtle_string(value) for value in label_objects]
            if len(labels) > 1:
                warning_messages.append(
                    f"Space '{subject}' has {len(labels)} rdfs:label values; "
                    f"using the first one ('{labels[0]}')"
                )
            bbox_objects = split_turtle_object_list(
                predicates.get("spot-sg:hasBoundingBox", "")
            )
            local_bboxes = [
                parse_number_array(
                    parse_typed_string_literal(
                        bbox_object,
                        "spot-sg:boundingBoxArray",
                    ),
                    6,
                )
                for bbox_object in bbox_objects
            ]
            if len(local_bboxes) > 1:
                warning_messages.append(
                    f"Space '{subject}' has {len(local_bboxes)} bounding boxes; "
                    "using their enclosing axis-aligned bounding box"
                )
            spaces[subject] = ImportedSpace(
                iri=subject,
                local_name=local_name,
                spot_class=parse_prefixed_local_name(spot_type),
                label=labels[0] if labels else local_name,
                local_bbox=enclosing_bbox(local_bboxes) if local_bboxes else None,
                order=order,
                length_unit=(
                    parse_length_unit(predicates["qudt:hasUnit"])
                    if "qudt:hasUnit" in predicates
                    else LENGTH_UNIT_METER
                ),
                represented_iris=split_turtle_object_list(
                    predicates.get("spot:represents", "")
                ),
            )
            for axis_name, axis_predicate in (
                ("X", "spot:hasXAxis"),
                ("Y", "spot:hasYAxis"),
                ("Z", "spot:hasZAxis"),
            ):
                axis_values = split_turtle_object_list(
                    predicates.get(axis_predicate, "")
                )
                if axis_values:
                    space_axes.setdefault(subject, {})[axis_name] = axis_values[0]
            order += 1
            continue

        bot_type = next(
            (rdf_type for rdf_type in rdf_types if rdf_type.startswith("bot:")),
            None,
        )
        beo_type = next(
            (rdf_type for rdf_type in rdf_types if rdf_type.startswith("beo:")),
            None,
        )
        if bot_type is not None or beo_type is not None:
            if bot_type is not None:
                bot_types[subject] = parse_prefixed_local_name(bot_type)
            if beo_type is not None:
                beo_types[subject] = parse_prefixed_local_name(beo_type)
            continue

        if any(predicate.startswith("bot:") for predicate in predicates):
            continue

        if linked_appearances:
            continue

        skipped_statements += 1

    if skipped_statements:
        warning_messages.append(
            f"Ignored {skipped_statements} non-scene-graph statements during import"
        )

    if not spaces:
        raise RuntimeError("No SPOT spaces were found in the Turtle input.")

    chosen_priorities: dict[str, int] = {}

    for appearance_id, source_iri in appearance_sources.items():
        source_space = spaces.get(source_iri)
        if source_space is None:
            raise RuntimeError(
                f"Appearance '{appearance_id}' references unknown source '{source_iri}'"
            )
        target_iri = appearance_targets.get(appearance_id)
        if target_iri is None:
            raise RuntimeError(
                f"Appearance '{appearance_id}' is missing spot:appearsIn"
            )
        if target_iri not in spaces:
            raise RuntimeError(
                f"Appearance '{appearance_id}' references unknown target '{target_iri}'"
            )

        # A document appearance places the space on a drawing sheet rather than
        # in the building, so it only wins when nothing else placed the space.
        priority = appearance_priority(appearance_types.get(appearance_id))
        if priority <= chosen_priorities.get(source_iri, -1):
            continue

        chosen_priorities[source_iri] = priority
        source_space.parent_iri = target_iri
        source_space.local_matrix = appearance_matrices.get(
            appearance_id, Matrix.Identity(4)
        )

    document_only_spaces = {
        space_id
        for space_id, priority in chosen_priorities.items()
        if priority <= 0
    }
    if document_only_spaces:
        warning_messages.append(
            f"Dropped {len(document_only_spaces)} spaces that are only placed on a "
            "document sheet"
        )

    document_spaces = {
        space_id
        for space_id, space in spaces.items()
        if space.spot_class == SPOT_CLASS_DOCUMENT_SPACE
    }
    if document_spaces:
        warning_messages.append(
            f"Dropped {len(document_spaces)} document spaces; their contents were "
            "kept where another placement exists"
        )

    # A space whose only placement was on a sheet takes its descendants with it:
    # they were positioned relative to it and have nowhere else to go.
    dropped_spaces = document_only_spaces | document_spaces
    pending = list(dropped_spaces)
    while pending:
        dropped_id = pending.pop()
        for space_id, space in spaces.items():
            if space.parent_iri == dropped_id and space_id not in dropped_spaces:
                dropped_spaces.add(space_id)
                pending.append(space_id)

    orphaned_count = len(dropped_spaces) - len(document_only_spaces) - len(
        document_spaces
    )
    if orphaned_count:
        warning_messages.append(
            f"Dropped {orphaned_count} spaces that were only placed below "
            "document-sheet content"
        )

    for space_id in dropped_spaces:
        spaces.pop(space_id, None)

    if not spaces:
        raise RuntimeError(
            "No SPOT spaces remained after dropping document-only content."
        )

    root_ids = [
        space_id for space_id, space in spaces.items() if space.parent_iri is None
    ]
    if not root_ids:
        raise RuntimeError(
            "Expected at least one root space in the imported scene graph"
        )

    apply_spot_am_placements(
        spaces,
        appearance_sources,
        appearance_matrices,
        appearance_boundary_mappings,
        boundary_point_mappings,
        boundary_points,
        axis_directions,
        space_axes,
        warning_messages,
    )

    for space_id, space in spaces.items():
        if space.parent_iri is None:
            continue
        spaces[space.parent_iri].children.append(space_id)

    for space in spaces.values():
        space.children.sort(key=lambda child_id: spaces[child_id].order)
    assign_represented_referents(spaces, bot_types, beo_types)

    return spaces, sorted(root_ids, key=lambda space_id: spaces[space_id].order)


def assign_represented_referents(
    spaces: dict[str, ImportedSpace],
    bot_types: dict[str, str],
    beo_types: dict[str, str],
) -> None:
    """
    Split each space's `spot:represents` values into the one BOT referent it
    owns and the further ones it only links to, such as the storey a floor
    plan stands for. A referent represented by a plan and by any other space
    belongs to the other space; otherwise every representing space owns it,
    as shared document and physical proxies do.
    """
    ordered = sorted(spaces.values(), key=lambda value: value.order)
    representers: dict[str, list[ImportedSpace]] = {}
    for space in ordered:
        for iri in space.represented_iris:
            if iri in bot_types or iri in beo_types:
                representers.setdefault(iri, []).append(space)

    owners_by_iri: dict[str, list[ImportedSpace]] = {}
    for iri, candidates in representers.items():
        standing = [
            space
            for space in candidates
            if space.spot_class not in LINKING_SPOT_CLASSES
        ]
        owners_by_iri[iri] = standing or candidates

    for space in ordered:
        for iri in space.represented_iris:
            owners = owners_by_iri.get(iri)
            if owners is None:
                continue
            if space in owners and space.bot_iri is None:
                space.bot_iri = iri
                space.bot_class = bot_types.get(iri)
                space.beo_class = beo_types.get(iri)
            elif space.bot_iri != iri:
                target = next((owner for owner in owners if owner is not space), None)
                if target is not None:
                    space.link_target_names.append(target.local_name)
        # A space representing no BOT referent keeps its first referent, a
        # `dot:Damage` say, as the stem of its BOT URI suffix, as before.
        if space.bot_iri is None and space.represented_iris:
            space.bot_iri = space.represented_iris[0]
        if space.bot_iri is not None:
            space.bot_local_name = parse_prefixed_local_name(space.bot_iri)


def apply_represents_links(
    spaces: dict[str, ImportedSpace],
    helper_collection: bpy.types.Collection,
    warnings: list[str],
) -> None:
    linking = [space for space in spaces.values() if space.link_target_names]
    if not linking:
        return
    if not hasattr(bpy.types.Object, PROP_REPRESENTS_LINKS):
        warnings.append(
            f"{len(linking)} space(s) represent further BOT referents, but the "
            "add-on is not registered, so these links were not imported"
        )
        return
    helpers_by_suffix = {
        str(obj.get(PROP_URI_SUFFIX)): obj
        for obj in helper_collection.objects
        if obj.get(PROP_HELPER)
    }
    for space in sorted(linking, key=lambda value: value.order):
        helper = helpers_by_suffix.get(space.local_name)
        if helper is None:
            continue
        links = getattr(helper, PROP_REPRESENTS_LINKS)
        for target_name in space.link_target_names:
            target = helpers_by_suffix.get(target_name)
            if target is None:
                warnings.append(
                    f"Space '{space.local_name}' represents the referent of "
                    f"'{target_name}', which was not imported"
                )
                continue
            if any(entry.object == target for entry in links):
                continue
            links.add().object = target


def build_imported_scene_spec(
    spaces: dict[str, ImportedSpace],
    root_id: str,
) -> SpaceNodeSpec:
    def build_node(
        space_id: str,
        parent_world_matrix: Matrix | None,
        parent_role: str | None,
        depth: int,
    ) -> SpaceNodeSpec:
        space = spaces[space_id]
        role = infer_role(space, parent_role)
        local_matrix = (
            space.local_matrix
            if parent_world_matrix is not None
            else Matrix.Identity(4)
        )
        world_matrix = (
            parent_world_matrix @ local_matrix
            if parent_world_matrix is not None
            else Matrix.Identity(4)
        )
        if space.local_bbox is not None:
            world_bbox = local_bbox_to_world_bbox(space.local_bbox, world_matrix)
        else:
            origin = world_matrix.translation.copy()
            world_bbox = Aabb(
                min_corner=origin.copy(),
                max_corner=origin.copy(),
            )
        bot_class = space.bot_class
        if bot_class is None:
            bot_class = {
                ROLE_SITE: BOT_CLASS_SITE,
                ROLE_BUILDING: BOT_CLASS_BUILDING,
                ROLE_STOREY: BOT_CLASS_STOREY,
                # No `bot:Space` by position: a space is a room only when the
                # graph says so.
                ROLE_ELEMENT: BOT_CLASS_ELEMENT,
            }.get(role)
        node = SpaceNodeSpec(
            role=role,
            spot_class=space.spot_class,
            bot_class=bot_class,
            helper_name=helper_name_for_import(
                role,
                space.local_name,
                bot_class,
                space.spot_class,
                space.beo_class,
            ),
            uri_suffix=space.local_name,
            bot_uri_suffix=space.bot_local_name or f"ref_{space.local_name}",
            label=space.label,
            world_bbox=world_bbox,
            world_matrix=world_matrix,
            length_unit=space.length_unit,
            beo_class=space.beo_class,
            local_bbox=(
                list(space.local_bbox) if space.local_bbox is not None else None
            ),
            sort_key=f"{depth:03d}:{space.order:05d}:{slugify(space.label)}",
            source_name=space.local_name,
            approximate_region=space.approximate_region,
        )
        for child_id in space.children:
            node.children.append(build_node(child_id, world_matrix, role, depth + 1))
        return node

    return build_node(root_id, None, None, 0)


def import_scene_graph_turtle(
    input_path: str | Path,
    helper_collection_name: str = HELPER_COLLECTION_NAME,
    save_blend_path: str | None = None,
) -> dict[str, object]:
    resolved_input = Path(input_path).expanduser().resolve()
    if resolved_input.is_dir():
        text, input_files = load_dataset_folder(resolved_input)
    else:
        text = resolved_input.read_text(encoding="utf8")
        input_files = [resolved_input.name]
    warnings: list[str] = []
    spaces, root_ids = parse_scene_graph_turtle(text, warnings=warnings)
    root_specs = [build_imported_scene_spec(spaces, root_id) for root_id in root_ids]

    scene = bpy.context.scene
    helper_collection = reset_helper_collection(scene, helper_collection_name)
    for root_spec in root_specs:
        instantiate_helpers(helper_collection, root_spec)
    apply_represents_links(spaces, helper_collection, warnings)
    bpy.context.view_layer.update()

    helper_count = len(
        [obj for obj in helper_collection.objects if obj.get(PROP_HELPER)],
    )
    result: dict[str, object] = {
        "input_path": str(resolved_input),
        "input_files": input_files,
        "helper_collection_name": helper_collection.name,
        "helper_count": helper_count,
        "root_helper_name": root_specs[0].helper_name,
        "root_label": root_specs[0].label,
        "root_helper_names": [root_spec.helper_name for root_spec in root_specs],
        "warning_count": len(warnings),
        "warnings": warnings,
        "saved_blend_path": None,
    }

    if save_blend_path:
        save_blend_file(save_blend_path)
        result["saved_blend_path"] = str(Path(save_blend_path).expanduser().resolve())

    return result


def print_import_summary(result: dict[str, object]) -> None:
    print(
        f"Imported {result['helper_count']} SPOT helpers from {result['input_path']} "
        f"into '{result['helper_collection_name']}'"
    )
    print(f"Root helper: {result['root_helper_name']} ({result['root_label']})")
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    if result["saved_blend_path"]:
        print(f"Saved helper scene to {result['saved_blend_path']}")


def main() -> None:
    args = parse_args()
    result = import_scene_graph_turtle(
        input_path=args.input,
        helper_collection_name=args.helper_collection,
        save_blend_path=args.save_blend,
    )
    print_import_summary(result)


if __name__ == "__main__":
    main()
