"""Record spaces moving from a SPOT-AM placement to a SPOT-SG one.

An imported dataset may locate a space only qualitatively, with SPOT-AM
relations. Someone then places it precisely in Blender. That refinement is a
statement of its own, made by someone at some time on some basis, so it is
recorded as provenance rather than written over the original.

The record follows the reification pattern with the current state asserted
(see the provenance chapter): the link from the space to its SPOT-AM
appearance is retracted, the link to the new SPOT-SG appearance is asserted,
and RDF 1.2 reifiers name both triples, the old one invalidated by the
activity and the new one a revision of it. The SPOT-AM appearance's own
statements stay in the graph, as what SPOT-AM said. Placing a space again
revises its matrix and box the same way.

The result is written as a dataset folder: the imported input as the
authored RDF and the transitions as entries in ``rdf/mutations.trig``, the
log the graph gateway keeps. Importing that folder replays the log, so the
importer, the gateway and the demonstrator all read the same current state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from .build_spot_scene import frame_without_scale
from .spot_blender_common import (
    PROP_HELPER,
    PROP_LABEL,
    PROP_LOCAL_BBOX,
    format_number_list,
    helper_asset_axis_direction,
    object_local_bbox,
    quote_turtle_string,
)

# Full IRI of the space a helper stands for, as the imported input names it.
PROP_IMPORT_IRI = "spot_import_iri"
# Set while the helper's placement is still the imported SPOT-AM one.
PROP_AM_APPEARANCE_IRI = "spot_am_appearance_iri"
PROP_AM_APPEARANCE_TYPE = "spot_am_appearance_type"
# The spot-sg:hasBoundingBox object the input gave the space, if any.
PROP_IMPORT_BBOX = "spot_import_bbox"
# JSON: the axis directions the input states for the imported root.
PROP_IMPORT_AXIS_DIRECTIONS = "spot_import_axis_directions"
# JSON: the SPOT-SG placement last committed for the helper (see SgState).
PROP_SG_STATE = "spot_sg_state"

# Scene properties: the imported input, and the committed activities.
SCENE_BASELINE = "spot_transition_baseline"
SCENE_LOG = "spot_transition_log"

PREFIXES = {
    "gom": "https://w3id.org/gom#",
    "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "spot": "https://w3id.org/spot#",
    "spot-sg": "https://w3id.org/spot/sg#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}
PREFIX_LINE = re.compile(r"^@prefix\s+([^:\s]*):\s*<([^>]*)>\s*\.\s*$")
BASE_LINE = re.compile(r"^@base\s+<([^>]*)>\s*\.\s*$")
ENTRY_LINE = re.compile(r"^#!lbcf-entry\s+(delta|retraction)\b", re.MULTILINE)
PRECISION = 6


# --- IRIs ------------------------------------------------------------------


def turtle_prefixes(text: str) -> tuple[dict[str, str], str | None]:
    """The prefixes and base a Turtle or TriG text declares."""
    prefixes: dict[str, str] = {}
    base: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        prefix_match = PREFIX_LINE.match(stripped)
        if prefix_match:
            prefixes[prefix_match.group(1)] = prefix_match.group(2)
            continue
        base_match = BASE_LINE.match(stripped)
        if base_match:
            base = base_match.group(1)
    return prefixes, base


def expand_iri(token: str, prefixes: dict[str, str], base: str | None) -> str:
    """The full IRI of a prefixed name or bracketed IRI."""
    from urllib.parse import urljoin

    from .import_spot_ttl import KNOWN_PREFIXES

    value = token.strip()
    if value.startswith("<") and value.endswith(">"):
        iri = value[1:-1]
        return urljoin(base, iri) if base else iri
    prefix, _, local = value.partition(":")
    namespace = prefixes.get(prefix, KNOWN_PREFIXES.get(prefix))
    if namespace is None:
        raise RuntimeError(f"Cannot expand '{token}': prefix '{prefix}' is not declared")
    return f"{namespace}{local}"


def expand_literal(token: str, prefixes: dict[str, str], base: str | None) -> str:
    """A typed literal with its datatype written as a full IRI."""
    lexical, marker, datatype = token.rpartition("^^")
    if not marker:
        return token
    return f"{lexical}^^<{expand_iri(datatype, prefixes, base)}>"


def namespace_of(iri: str) -> str:
    cut = max(iri.rfind("#"), iri.rfind("/"))
    return iri[: cut + 1]


def local_name_of(iri: str) -> str:
    return iri[len(namespace_of(iri)) :]


def term(iri: str) -> str:
    return f"<{iri}>"


# --- Import bookkeeping ----------------------------------------------------


def record_import_metadata(
    spaces: dict[str, object],
    root_ids: list[str],
    helper_collection: bpy.types.Collection,
    anchor_helper: bpy.types.Object | None,
    text: str,
    input_path: Path,
) -> None:
    """Remember what the imported input said, for a later transition.

    Each helper learns the IRI of its space. A helper SPOT-AM placed also
    learns the appearance that did so; the root learns its stated box and
    axis directions. The scene learns the input, and a fresh import starts a
    fresh transition log.
    """
    prefixes, base = turtle_prefixes(text)
    # A document proxy only lays its space out on a sheet; the space itself
    # is the physical helper.
    imported_suffixes = {
        space.local_name: space
        for space in spaces.values()
        if not space.anchored and getattr(space, "proxy_of", None) is None
    }
    helpers: list[tuple[bpy.types.Object, object]] = [
        (obj, imported_suffixes[str(obj.get("spot_uri_suffix"))])
        for obj in helper_collection.objects
        if obj.get(PROP_HELPER)
        and obj is not anchor_helper
        and str(obj.get("spot_uri_suffix")) in imported_suffixes
    ]
    for space_id in root_ids:
        space = spaces[space_id]
        if space.anchored and anchor_helper is not None:
            helpers.append((anchor_helper, space))

    for helper, space in helpers:
        for prop in (
            PROP_AM_APPEARANCE_IRI,
            PROP_AM_APPEARANCE_TYPE,
            PROP_IMPORT_BBOX,
            PROP_IMPORT_AXIS_DIRECTIONS,
            PROP_SG_STATE,
        ):
            if prop in helper:
                del helper[prop]
        helper[PROP_IMPORT_IRI] = expand_iri(space.iri, prefixes, base)
        if space.bbox_literal is not None:
            helper[PROP_IMPORT_BBOX] = expand_literal(space.bbox_literal, prefixes, base)
        if space.chosen_has_am and space.chosen_appearance is not None:
            helper[PROP_AM_APPEARANCE_IRI] = expand_iri(
                space.chosen_appearance, prefixes, base
            )
            helper[PROP_AM_APPEARANCE_TYPE] = expand_iri(
                space.chosen_appearance_type or "spot:EntityAppearance",
                prefixes,
                base,
            )
        if space.parent_iri is None:
            helper[PROP_IMPORT_AXIS_DIRECTIONS] = json.dumps(space.axis_directions)

    scene = bpy.context.scene
    scene[SCENE_BASELINE] = str(input_path)
    scene[SCENE_LOG] = "[]"


# --- Committing placements -------------------------------------------------


@dataclass
class SgState:
    """A SPOT-SG placement as last committed, and the reifiers naming it."""

    appearance: str
    matrix: str
    bbox: str | None
    matrix_assertion: str
    bbox_assertion: str | None

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @staticmethod
    def from_json(text: str) -> SgState:
        return SgState(**json.loads(text))


def helper_box_in_metres(helper: bpy.types.Object) -> list[float]:
    """The helper's box as it now stands, with any scale applied by hand."""
    local = object_local_bbox(helper)
    if local is None:
        local = [float(value) for value in helper.get(PROP_LOCAL_BBOX, [0.0] * 6)]
    scale = helper.matrix_world.to_scale()
    pairs = [
        [local[index] * scale[index], local[index + 3] * scale[index]]
        for index in range(3)
    ]
    return [min(pair) for pair in pairs] + [max(pair) for pair in pairs]


def matrix_literal(matrix: Matrix) -> str:
    values = [matrix[row][column] for column in range(4) for row in range(4)]
    return f'{quote_turtle_string(format_number_list(values, PRECISION))}^^<{PREFIXES["gom"]}columnMajorArray>'


def bbox_literal(bbox: list[float]) -> str | None:
    if all(abs(bbox[index + 3] - bbox[index]) <= 1e-9 for index in range(3)):
        # A point has no box to state.
        return None
    return f'{quote_turtle_string(format_number_list(bbox, PRECISION))}^^<{PREFIXES["spot-sg"]}boundingBoxArray>'


def is_placed_by_spot_am(helper: bpy.types.Object) -> bool:
    return PROP_AM_APPEARANCE_IRI in helper and PROP_SG_STATE not in helper


def committable_reason(helper: bpy.types.Object) -> str | None:
    """Why a helper cannot be committed, or None when it can."""
    if not helper.get(PROP_HELPER) or PROP_IMPORT_IRI not in helper:
        return f"'{helper.name}' was not imported from a dataset"
    if helper.parent is None:
        return f"'{helper.name}' is a root; only spaces inside one can be placed"
    if PROP_IMPORT_IRI not in helper.parent:
        return f"the parent of '{helper.name}' was not imported from a dataset"
    if PROP_AM_APPEARANCE_IRI not in helper and PROP_SG_STATE not in helper:
        return (
            f"'{helper.name}' was placed precisely in the input already; only "
            "SPOT-AM placements and earlier commits are tracked"
        )
    if is_placed_by_spot_am(helper.parent):
        return (
            f"the parent of '{helper.name}' is still placed by SPOT-AM; commit "
            "the parent first, so the matrix has a stated frame to refer to"
        )
    return None


def root_direction_mismatch(root: bpy.types.Object) -> str | None:
    stated = json.loads(str(root.get(PROP_IMPORT_AXIS_DIRECTIONS, "{}")))
    used = {axis: helper_asset_axis_direction(root, axis) for axis in ("X", "Y", "Z")}
    differing = {axis: (direction, used[axis]) for axis, direction in stated.items() if used[axis] != direction}
    if not differing:
        return None
    return (
        f"the input states axis directions {stated} for "
        f"'{root.get(PROP_LABEL, root.name)}', but the helper uses {used}; a matrix "
        "written in the helper's frame would contradict the input"
    )


def read_log(scene: bpy.types.Scene) -> list[dict]:
    return json.loads(str(scene.get(SCENE_LOG, "[]")))


def commit_sg_placements(
    helpers: list[bpy.types.Object],
    agent: str,
    label: str,
    used: list[str] | None = None,
    ended_at: datetime | None = None,
) -> dict:
    """Snapshot the helpers' current placement as one SPOT-SG activity."""
    if not helpers:
        raise RuntimeError("Select at least one imported space to commit")
    problems = [reason for reason in map(committable_reason, helpers) if reason]
    if problems:
        raise RuntimeError("; ".join(problems))

    root = helpers[0]
    while root.parent is not None:
        root = root.parent
    mismatch = root_direction_mismatch(root)
    if mismatch:
        raise RuntimeError(mismatch)

    if PROP_LOCAL_BBOX not in root:
        raise RuntimeError(
            f"'{root.name}' has no extent, so its frame cannot be stated; import "
            "the dataset into an existing AssetSpace"
        )
    namespace = namespace_of(str(root[PROP_IMPORT_IRI]))
    activity_id = str(uuid.uuid4())
    time = (ended_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    changes: list[dict] = []

    # The matrices refer to the root's frame, which the input never stated
    # when it located everything qualitatively. It is stated once, by an
    # activity of its own just before the first placement: it is no part of
    # that placement, and a history read back to before the placement still
    # needs the frame to derive the SPOT-AM placements.
    frame_changes: list[dict] = []
    if PROP_SG_STATE not in root and PROP_IMPORT_BBOX not in root:
        box = bbox_literal(helper_box_in_metres(root))
        if box is not None:
            frame_changes.append(
                {
                    "kind": "root-box",
                    "space": root[PROP_IMPORT_IRI],
                    "bbox": box,
                    "bbox_assertion": f"{namespace}assertion-{uuid.uuid4()}",
                }
            )

    for helper in helpers:
        parent = helper.parent
        relative = frame_without_scale(parent.matrix_world).inverted_safe() @ frame_without_scale(helper.matrix_world)
        # The origin goes on the centre of the box, as on every other exported
        # helper. A box edited in place around its parent's origin would
        # otherwise be stated with an identity matrix and an off-centre box,
        # which readers take for a placement not made yet.
        box_in_metres = helper_box_in_metres(helper)
        centre = Vector(
            [(box_in_metres[index] + box_in_metres[index + 3]) / 2.0 for index in range(3)]
        )
        relative = relative @ Matrix.Translation(centre)
        box_in_metres = [
            value - centre[index % 3] for index, value in enumerate(box_in_metres)
        ]
        matrix = matrix_literal(relative)
        box = bbox_literal(box_in_metres)
        space = str(helper[PROP_IMPORT_IRI])

        if is_placed_by_spot_am(helper):
            appearance = (
                f"{namespace}SGAP_{local_name_of(space)}_in_"
                f"{local_name_of(str(parent[PROP_IMPORT_IRI]))}"
            )
            state = SgState(
                appearance=appearance,
                matrix=matrix,
                bbox=box,
                matrix_assertion=f"{namespace}assertion-{uuid.uuid4()}",
                bbox_assertion=f"{namespace}assertion-{uuid.uuid4()}" if box else None,
            )
            changes.append(
                {
                    "kind": "am-to-sg",
                    "space": space,
                    "parent": parent[PROP_IMPORT_IRI],
                    "am_appearance": helper[PROP_AM_APPEARANCE_IRI],
                    "appearance_type": helper[PROP_AM_APPEARANCE_TYPE],
                    "previous_bbox": helper.get(PROP_IMPORT_BBOX),
                    "old_link_assertion": f"{namespace}assertion-{uuid.uuid4()}",
                    "new_link_assertion": f"{namespace}assertion-{uuid.uuid4()}",
                    "old_bbox_assertion": f"{namespace}assertion-{uuid.uuid4()}",
                    "state": state.__dict__,
                }
            )
        else:
            previous = SgState.from_json(str(helper[PROP_SG_STATE]))
            if previous.matrix == matrix and previous.bbox == box:
                continue
            # A value that stands keeps the reifier that already names it.
            state = SgState(
                appearance=previous.appearance,
                matrix=matrix,
                bbox=box,
                matrix_assertion=(
                    previous.matrix_assertion
                    if previous.matrix == matrix
                    else f"{namespace}assertion-{uuid.uuid4()}"
                ),
                bbox_assertion=(
                    previous.bbox_assertion
                    if previous.bbox == box
                    else f"{namespace}assertion-{uuid.uuid4()}" if box else None
                ),
            )
            changes.append(
                {
                    "kind": "sg-to-sg",
                    "space": space,
                    "previous": previous.__dict__,
                    "state": state.__dict__,
                }
            )
        helper[PROP_SG_STATE] = state.to_json()

    if not changes:
        raise RuntimeError("Nothing changed since the last commit of the selected spaces")

    agent_iri = (
        agent
        if ":" in agent
        else f"{namespace}agent/{re.sub(r'[^A-Za-z0-9_-]+', '-', agent).strip('-') or 'blender-user'}"
    )
    record = {
        "activity": f"{namespace}activity-{activity_id}",
        "label": label,
        "agent": agent_iri,
        "agent_label": agent,
        "ended_at": time.isoformat().replace("+00:00", "Z"),
        "used": used or [],
        "changes": changes,
    }
    scene = bpy.context.scene
    log = read_log(scene)
    if frame_changes:
        # Named after the space whose box is stated, not the helper standing in.
        root_label = local_name_of(str(root[PROP_IMPORT_IRI]))
        log.append(
            {
                "activity": f"{namespace}activity-{uuid.uuid4()}",
                "label": f"Frame of {root_label} stated for SPOT-SG placements",
                "agent": agent_iri,
                "agent_label": agent,
                "ended_at": (time - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
                "used": [],
                "changes": frame_changes,
            }
        )
        root[PROP_SG_STATE] = json.dumps(
            {"bbox": frame_changes[0]["bbox"], "bbox_assertion": frame_changes[0]["bbox_assertion"]}
        )
    log.append(record)
    scene[SCENE_LOG] = json.dumps(log)
    return record


# --- Writing the dataset ---------------------------------------------------


def reifier(assertion: str, triple: tuple[str, str, str], **links: str) -> str:
    lines = [f"{term(assertion)}", "    a prov:Entity ;", f"    rdf:reifies <<( {' '.join(triple)} )>>"]
    for predicate, value in links.items():
        lines[-1] += " ;"
        lines.append(f"    prov:{predicate} {term(value)}")
    return "\n".join(lines) + " ."


def triple_text(triple: tuple[str, str, str]) -> str:
    return f"{' '.join(triple)} ."


def activity_entries(record: dict, number: int) -> list[str]:
    """The retraction and delta entries for one committed activity."""
    activity = record["activity"]
    retracted: list[tuple[str, str, str]] = []
    asserted: list[tuple[str, str, str]] = []
    reifiers: list[str] = []
    has_appearance = "spot:hasAppearance"
    has_bbox = "spot-sg:hasBoundingBox"
    has_matrix = "gom:hasTransformationMatrix"

    for change in record["changes"]:
        space = term(change["space"])
        if change["kind"] == "root-box":
            triple = (space, has_bbox, change["bbox"])
            asserted.append(triple)
            reifiers.append(reifier(change["bbox_assertion"], triple, wasGeneratedBy=activity))
            continue

        state = change["state"]
        appearance = term(state["appearance"])
        matrix_triple = (appearance, has_matrix, state["matrix"])
        bbox_triple = (space, has_bbox, state["bbox"]) if state["bbox"] else None

        if change["kind"] == "am-to-sg":
            old_link = (space, has_appearance, term(change["am_appearance"]))
            new_link = (space, has_appearance, appearance)
            retracted.append(old_link)
            asserted.extend(
                [
                    new_link,
                    (appearance, "a", term(change["appearance_type"])),
                    (appearance, "spot:appearsIn", term(change["parent"])),
                    matrix_triple,
                ]
            )
            # The input asserted the old link without saying who made it, so
            # its reifier only records the end of its validity.
            reifiers.append(reifier(change["old_link_assertion"], old_link, wasInvalidatedBy=activity))
            reifiers.append(
                reifier(
                    change["new_link_assertion"],
                    new_link,
                    wasGeneratedBy=activity,
                    wasRevisionOf=change["old_link_assertion"],
                )
            )
            reifiers.append(reifier(state["matrix_assertion"], matrix_triple, wasGeneratedBy=activity))
            if change.get("previous_bbox"):
                old_bbox = (space, has_bbox, change["previous_bbox"])
                retracted.append(old_bbox)
                reifiers.append(reifier(change["old_bbox_assertion"], old_bbox, wasInvalidatedBy=activity))
            if bbox_triple:
                asserted.append(bbox_triple)
                links = {"wasGeneratedBy": activity}
                if change.get("previous_bbox"):
                    links["wasRevisionOf"] = change["old_bbox_assertion"]
                reifiers.append(reifier(state["bbox_assertion"], bbox_triple, **links))
            continue

        previous = change["previous"]
        old_matrix = (appearance, has_matrix, previous["matrix"])
        if previous["matrix"] != state["matrix"]:
            retracted.append(old_matrix)
            asserted.append(matrix_triple)
            reifiers.append(f"{term(previous['matrix_assertion'])} prov:wasInvalidatedBy {term(activity)} .")
            reifiers.append(
                reifier(
                    state["matrix_assertion"],
                    matrix_triple,
                    wasGeneratedBy=activity,
                    wasRevisionOf=previous["matrix_assertion"],
                )
            )
        if previous["bbox"] != state["bbox"]:
            if previous["bbox"]:
                retracted.append((space, has_bbox, previous["bbox"]))
                reifiers.append(f"{term(previous['bbox_assertion'])} prov:wasInvalidatedBy {term(activity)} .")
            if bbox_triple:
                asserted.append(bbox_triple)
                links = {"wasGeneratedBy": activity}
                if previous["bbox"]:
                    links["wasRevisionOf"] = previous["bbox_assertion"]
                reifiers.append(reifier(state["bbox_assertion"], bbox_triple, **links))

    prefixes = "\n".join(f"@prefix {prefix}: <{namespace}> ." for prefix, namespace in PREFIXES.items())
    header = "\n".join(
        [
            "#" * 79,
            f"# Mutation: {record['label']}",
            f"#   actor:     {record['agent']}",
            f"#   committed: {record['ended_at']}",
            "#" * 79,
        ]
    )
    activity_lines = [
        term(activity),
        "    a prov:Activity ;",
        f"    rdfs:label {quote_turtle_string(record['label'])}@en ;",
        f"    prov:wasAssociatedWith {term(record['agent'])} ;",
    ]
    activity_lines.extend(f"    prov:used {term(source)} ;" for source in record["used"])
    activity_lines.append(f'    prov:endedAtTime "{record["ended_at"]}"^^xsd:dateTime .')
    agent_lines = (
        f"{term(record['agent'])} a prov:Agent ; "
        f"rdfs:label {quote_turtle_string(record['agent_label'])} ."
    )

    entries: list[str] = []
    if retracted:
        entries.append(
            "\n".join(
                [f"#!lbcf-entry retraction {number}", prefixes, "", header, ""]
                + [triple_text(triple) for triple in retracted]
            )
        )
    delta = (
        [f"#!lbcf-entry delta {number}", prefixes, "", header, ""]
        + [triple_text(triple) for triple in asserted]
        + ["", "\n".join(activity_lines), "", agent_lines, ""]
        + ["\n\n".join(reifiers)]
    )
    entries.append("\n".join(delta))
    return entries


def baseline_rdf_files(baseline: Path) -> list[tuple[Path, str]]:
    """The authored RDF files of the input, and where they go in the output."""
    if baseline.is_file():
        suffix = baseline.suffix if baseline.suffix in {".ttl", ".trig"} else ".ttl"
        return [(baseline, f"scene{suffix}")]
    rdf_dir = baseline / "rdf"
    if not rdf_dir.is_dir():
        raise RuntimeError(f"The dataset folder '{baseline}' has no rdf folder")
    return [
        (path, str(path.relative_to(rdf_dir)))
        for path in sorted(rdf_dir.rglob("*"))
        if path.is_file() and path.suffix in {".ttl", ".trig"}
    ]


def write_transition_dataset(
    output_dir: str | Path | None = None,
    project_name: str = "",
    helper_collection_name: str = "SPOT Scene",
) -> dict[str, object]:
    """Write the imported input with the committed transitions replayed onto it.

    The output is a new folder; the input is never changed. Without an
    explicit folder it is a fresh snapshot in `exports/<project>/`, as the
    dataset snapshot writes, so the graph gateway can select it by project.
    An input dataset folder's own mutation log is kept and the transitions
    are appended to it. A dump in the input is left out, as it would hide the
    appended entries.
    """
    if output_dir is None:
        from .dataset_snapshot import project_slug, project_snapshot_destination

        project = project_slug(project_name, helper_collection_name)
        destination, temporary, _timestamp = project_snapshot_destination(project)
        try:
            result = write_transition_dataset(temporary)
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return {**result, "output_dir": str(destination), "project": project}

    scene = bpy.context.scene
    baseline_value = scene.get(SCENE_BASELINE)
    if not baseline_value:
        raise RuntimeError("Import a dataset first; there is no baseline to build on")
    baseline = Path(str(baseline_value))
    if not baseline.exists():
        raise RuntimeError(f"The imported input '{baseline}' no longer exists")
    log = read_log(scene)
    if not log:
        raise RuntimeError("No SPOT-SG placements have been committed since the import")

    output = Path(output_dir).expanduser().resolve()
    if baseline.resolve() == output or output in baseline.resolve().parents:
        raise RuntimeError("Write the transition dataset to a folder outside the input")
    rdf_dir = output / "rdf"
    rdf_dir.mkdir(parents=True, exist_ok=True)

    existing_log = ""
    for source, relative in baseline_rdf_files(baseline):
        target = rdf_dir / relative
        if relative == "mutations.trig":
            existing_log = source.read_text(encoding="utf8")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    number = len(ENTRY_LINE.findall(existing_log))
    entries: list[str] = []
    for record in log:
        entries.extend(activity_entries(record, number))
        number += 1
    log_text = existing_log.rstrip("\n")
    log_text = (log_text + "\n\n" if log_text else "") + "\n\n".join(entries) + "\n"
    (rdf_dir / "mutations.trig").write_text(log_text, encoding="utf8")

    return {
        "output_dir": str(output),
        "activity_count": len(log),
        "change_count": sum(len(record["changes"]) for record in log),
    }
