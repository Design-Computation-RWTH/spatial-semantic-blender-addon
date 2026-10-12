from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse

import bpy

from .spot_transition import PROP_IMPORT_BBOX, PROP_IMPORT_IRI, local_name_of
from .stated_am import (
    appearance_predicates as stated_am_predicates,
    mapping_blocks as stated_am_blocks,
    stated_am,
)
from .bcf_authoring import (
    PROP_OBSERVED_DAMAGES,
    helper_for_exported_reference,
)

from .spot_blender_common import (
    helper_export_bbox,
    helper_export_frame,
    BOT_CLASS_BUILDING,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SITE,
    BOT_CLASS_SPACE,
    BOT_CLASS_STOREY,
    BROT_COMPONENT_CLASSES,
    BROT_NAMESPACE,
    BROT_ZONE_CLASSES,
    BOUNDARY_POINT_SLOTS,
    LENGTH_UNIT_METER,
    LENGTH_UNIT_PIXEL,
    HELPER_COLLECTION_MARKER,
    HELPER_COLLECTION_NAME,
    PROP_BEO_CLASS,
    PROP_BOT_CLASS,
    PROP_BROT_CLASS,
    PROP_BOT_URI_SUFFIX,
    PROP_DOT_CLASS,
    PROP_HELPER,
    PROP_OWN_ASSET,
    PROP_LABEL,
    PROP_LENGTH_UNIT,
    PROP_LOCAL_BBOX,
    PROP_PLAN_KIND,
    PROP_PROXY_ROLE,
    PROP_REPRESENTS_LINKS,
    PROP_ROLE,
    PREFIX_IFC_PROJECT,
    PROP_SOURCE_ASSET_PATH,
    PROP_SOURCE_COLLECTION,
    PROP_SOURCE_DEFINITION_ID,
    PROP_SOURCE_IFC_CLASS,
    PROP_SOURCE_NAME,
    PROP_SPOT_CLASS,
    PROP_SHARED_ENTITY_KEY,
    PROP_URI_SUFFIX,
    PROXY_ROLE_DOCUMENT,
    PROXY_ROLE_PHYSICAL,
    QUDT_SCHEMA_IRI,
    QUDT_UNIT_IRI,
    ROLE_DOCUMENT,
    ROLE_SITE,
    SPOT_CLASS_ASSET,
    SPOT_CLASS_ENTITY,
    boundary_point_slot_details,
    blender_script_argv,
    convert_bbox_from_blender_units,
    convert_matrix_between_units,
    default_document_axis_direction,
    default_output_path,
    derive_am_region,
    find_helper_for_source_object,
    find_source_object_for_helper,
    helper_source_collection_root,
    represents_link_owner,
    format_number,
    helper_am_axis_accuracy,
    helper_am_axis_relation,
    helper_am_axis_target,
    helper_am_enabled,
    helper_am_reference_asset_enabled,
    helper_am_reference_helper_name,
    helper_asset_axis_direction,
    helper_children,
    helper_has_referent,
    helper_local_bbox,
    helper_root_objects,
    human_name,
    infer_proxy_role_and_shared_base_name,
    matrix_to_column_major_literal,
    object_world_aabb,
    qudt_unit_term,
    quote_turtle_string,
    parse_name_semantics,
    slugify,
    stringify_bbox,
)

EXPORT_PROFILE_SCENE_GRAPH = "scene-graph"
EXPORT_PROFILE_SPOT_ONLY = "spot-only"
BOT_PREFIX_LINE = "@prefix bot: <https://w3id.org/bot#> ."
DEFAULT_BASE_IRI = "http://localhost:4000/scene/"
LEGACY_DEFAULT_BASE_IRI = "https://example.org/scene#"
LEGACY_HASH_BASE_IRI = "http://localhost:4000/scene#"
DEFAULT_LOCAL_PREFIX = "ex"
LOCAL_TERM_MARKER = "__SPOT_LOCAL_PREFIX__:"
RESERVED_LOCAL_PREFIXES = {
    "bcfowl",
    "beo",
    "bot",
    "dcat",
    "dcterms",
    "dot",
    "exif",
    "foaf",
    "gom",
    "prov",
    "qudt",
    "rdf",
    "rdfs",
    "reloc",
    "sosa",
    "spot",
    "spot-am",
    "spot-sg",
    "unit",
    "xsd",
}


SPACE_AXIS_DEFINITIONS = (
    ("X", "hasXAxis"),
    ("Y", "hasYAxis"),
    ("Z", "hasZAxis"),
)

IDENTITY_MATRIX_LITERAL = "[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"

DEFAULT_AXIS_DIRECTIONS = {
    "X": "Front",
    "Y": "Right",
    "Z": "Top",
}


@dataclass(frozen=True)
class ExportResourceRegistry:
    ordered_helpers: list[bpy.types.Object]
    exported_helpers: list[bpy.types.Object]
    export_suffixes: dict[str, str]
    bot_export_suffixes: dict[str, str]
    dot_export_suffixes: dict[str, str]
    proxy_pairings: list[tuple[bpy.types.Object, bpy.types.Object]]
    document_backed_helper_names: set[str]


def axis_index(axis_name: str) -> int:
    if axis_name == "X":
        return 0
    if axis_name == "Y":
        return 1
    return 2


def opposite_direction(direction: str) -> str | None:
    if direction == "Front":
        return "Rear"
    if direction == "Rear":
        return "Front"
    if direction == "Left":
        return "Right"
    if direction == "Right":
        return "Left"
    if direction == "Top":
        return "Bottom"
    if direction == "Bottom":
        return "Top"
    return None


def frame_axis_for_direction(
    frame: dict[str, str],
    direction: str,
) -> str | None:
    for axis_name in ("X", "Y", "Z"):
        configured_direction = frame.get(axis_name)
        if configured_direction == direction:
            return axis_name
        if opposite_direction(configured_direction or "") == direction:
            return axis_name
    return None


def resolve_directional_frame(helper: bpy.types.Object) -> dict[str, str]:
    current: bpy.types.Object | None = helper
    while current is not None:
        if (
            current.get(PROP_HELPER)
            and current.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
        ):
            return {
                "X": helper_asset_axis_direction(current, "X"),
                "Y": helper_asset_axis_direction(current, "Y"),
                "Z": helper_asset_axis_direction(current, "Z"),
            }
        current = current.parent
    return dict(DEFAULT_AXIS_DIRECTIONS)


def relation_axis_name(
    relation_name: str,
    frame: dict[str, str],
) -> str | None:
    directional_match = re.search(
        r"(Front|Rear|Left|Right|Top|Bottom)$",
        relation_name,
    )
    if directional_match is not None:
        return frame_axis_for_direction(frame, directional_match.group(1))
    if relation_name.endswith("VerticalCenter"):
        return frame_axis_for_direction(frame, "Top")
    if relation_name.endswith("TransversalCenter"):
        return frame_axis_for_direction(frame, "Right")
    if relation_name.endswith("LongitudinalCenter"):
        return frame_axis_for_direction(frame, "Front")
    return None


def normalized_boundary_coordinate_value(
    helper: bpy.types.Object,
    mapping_target: bpy.types.Object,
    slot: str,
    relation_name: str,
) -> float | None:
    source_box = object_world_aabb(helper)
    target_box = object_world_aabb(mapping_target)
    if source_box is None or target_box is None:
        return None

    frame = resolve_directional_frame(mapping_target)
    relation_axis = relation_axis_name(relation_name, frame)
    if relation_axis is None:
        return None

    index = axis_index(relation_axis)
    _slot_axis_name, bound_kind, _slot_label = boundary_point_slot_details(slot)
    source_coordinate = (
        source_box.min_corner[index]
        if bound_kind == "Min"
        else source_box.max_corner[index]
    )
    target_min = target_box.min_corner[index]
    target_max = target_box.max_corner[index]
    span = target_max - target_min
    if abs(span) <= 1e-9:
        return None
    return float((source_coordinate - target_min) / span)


def output_suffix_for_profile(export_profile: str) -> str:
    if export_profile == EXPORT_PROFILE_SCENE_GRAPH:
        return "_spot.ttl"
    if export_profile == EXPORT_PROFILE_SPOT_ONLY:
        return "_spot_topology.ttl"
    raise ValueError(f"Unsupported export profile: {export_profile}")


def default_output_path_for_profile(export_profile: str) -> Path:
    return default_output_path(output_suffix_for_profile(export_profile))


def is_scene_graph_profile(export_profile: str) -> bool:
    return export_profile == EXPORT_PROFILE_SCENE_GRAPH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the helper SPOT scene collection to Turtle.",
    )
    parser.add_argument(
        "--helper-collection",
        default=HELPER_COLLECTION_NAME,
        help="Name of the helper collection to export.",
    )
    parser.add_argument(
        "--output",
        help="Output Turtle file path. Defaults to <blend-name>_spot.ttl next to the .blend file.",
    )
    parser.add_argument(
        "--base-iri",
        default=DEFAULT_BASE_IRI,
        help="Base IRI for exported local identifiers.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_LOCAL_PREFIX,
        help="Turtle prefix for exported local identifiers (for example ex or scene).",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=6,
        help="Floating-point precision for matrices and bounding boxes.",
    )
    parser.add_argument(
        "--profile",
        choices=[EXPORT_PROFILE_SCENE_GRAPH, EXPORT_PROFILE_SPOT_ONLY],
        default=EXPORT_PROFILE_SCENE_GRAPH,
        help="Export profile: full SPOT scene graph or SPOT-only topology.",
    )
    parser.add_argument(
        "--asset-base-url",
        default="http://localhost:4000/assets/",
        help="Base URL used for exported plan/picture asset URLs.",
    )
    return parser.parse_args(blender_script_argv())


def ensure_helper_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        raise RuntimeError(f"Helper collection '{name}' was not found")
    if not collection.get(HELPER_COLLECTION_MARKER):
        raise RuntimeError(
            f"Collection '{name}' does not look like a SPOT helper collection",
        )
    return collection


def flatten_helper_tree(root: bpy.types.Object) -> list[bpy.types.Object]:
    ordered = [root]
    for child in helper_children(root):
        ordered.extend(flatten_helper_tree(child))
    return ordered


def appearance_id(child: bpy.types.Object, parent: bpy.types.Object) -> str:
    return f"appearance_{child[PROP_URI_SUFFIX]}_in_{parent[PROP_URI_SUFFIX]}"


def appearance_class(parent: bpy.types.Object) -> str:
    if parent[PROP_SPOT_CLASS] == "DocumentSpace":
        return "DocumentAppearance"
    return (
        "AssetAppearance"
        if parent[PROP_SPOT_CLASS] == "AssetSpace"
        else "EntityAppearance"
    )


def helper_proxy_role(helper: bpy.types.Object) -> str | None:
    raw = helper.get(PROP_PROXY_ROLE)
    return str(raw) if raw else None


def helper_shared_entity_key(helper: bpy.types.Object) -> str | None:
    raw = helper.get(PROP_SHARED_ENTITY_KEY)
    return str(raw) if raw else None


def is_document_proxy(helper: bpy.types.Object) -> bool:
    return helper_proxy_role(helper) == PROXY_ROLE_DOCUMENT


def is_physical_proxy(helper: bpy.types.Object) -> bool:
    return helper_proxy_role(helper) == PROXY_ROLE_PHYSICAL


def helper_display_label(helper: bpy.types.Object) -> str:
    source_name = helper.get(PROP_SOURCE_NAME)
    if source_name:
        proxy_role, shared_base_name = infer_proxy_role_and_shared_base_name(
            str(source_name)
        )
        if proxy_role is not None:
            return human_name(shared_base_name)

    label = helper.get(PROP_LABEL)
    if label:
        return str(label)
    return helper.name


def helper_export_base_name(helper: bpy.types.Object) -> str:
    source_name = helper.get(PROP_SOURCE_NAME)
    if source_name:
        proxy_role, shared_base_name = infer_proxy_role_and_shared_base_name(
            str(source_name)
        )
        if proxy_role is not None:
            return human_name(shared_base_name)
        return human_name(str(source_name))

    source_collection = helper.get(PROP_SOURCE_COLLECTION)
    if source_collection:
        return human_name(str(source_collection))

    label = helper.get(PROP_LABEL)
    if label:
        return str(label)

    return helper.name


def build_export_uri_suffixes(
    ordered_helpers: list[bpy.types.Object],
) -> dict[str, str]:
    suffixes: dict[str, str] = {}
    used: set[str] = set()

    for helper in ordered_helpers:
        base_name = helper_export_base_name(helper)
        base_slug = slugify(base_name)
        candidate = base_slug
        definition_id = helper.get(PROP_SOURCE_DEFINITION_ID)

        if candidate in used and definition_id is not None:
            candidate = f"{base_slug}_{definition_id}"

        index = 2
        while candidate in used:
            candidate = f"{base_slug}_{index}"
            index += 1

        used.add(candidate)
        suffixes[helper.name] = candidate

    return suffixes


def build_bot_export_uri_suffixes(
    ordered_helpers: list[bpy.types.Object],
    export_suffixes: dict[str, str],
) -> dict[str, str]:
    suffixes: dict[str, str] = {}
    used: set[str] = set()
    shared_suffixes_by_key: dict[tuple[str, str], str] = {}

    for helper in ordered_helpers:
        referent_class = helper.get(PROP_BOT_CLASS) or helper.get(PROP_BROT_CLASS)
        shared_entity_key = helper_shared_entity_key(helper)
        if referent_class and shared_entity_key:
            shared_key = (str(referent_class), shared_entity_key)
            shared_suffix = shared_suffixes_by_key.get(shared_key)
            if shared_suffix is None:
                base_suffix = str(
                    helper.get(
                        PROP_BOT_URI_SUFFIX,
                        f"ref_{export_suffixes[helper.name]}",
                    )
                )
                candidate = base_suffix
                index = 2
                while candidate in used:
                    candidate = f"{base_suffix}_{index}"
                    index += 1

                used.add(candidate)
                shared_suffixes_by_key[shared_key] = candidate
                shared_suffix = candidate

            suffixes[helper.name] = shared_suffix
            continue

        base_suffix = str(
            helper.get(PROP_BOT_URI_SUFFIX, f"ref_{export_suffixes[helper.name]}")
        )
        candidate = base_suffix
        index = 2
        while candidate in used:
            candidate = f"{base_suffix}_{index}"
            index += 1

        used.add(candidate)
        suffixes[helper.name] = candidate

    return suffixes


def helper_dot_class(helper: bpy.types.Object) -> str | None:
    explicit = helper.get(PROP_DOT_CLASS)
    if explicit:
        return str(explicit)

    source_name = helper.get(PROP_SOURCE_NAME)
    semantic_name = str(source_name) if source_name else helper_display_label(helper)
    return parse_name_semantics(semantic_name).dot_class


def build_dot_export_uri_suffixes(
    ordered_helpers: list[bpy.types.Object],
    export_suffixes: dict[str, str],
) -> dict[str, str]:
    suffixes: dict[str, str] = {}
    used: set[str] = set()
    shared_suffixes_by_key: dict[tuple[str, str], str] = {}

    for helper in ordered_helpers:
        dot_class = helper_dot_class(helper)
        if dot_class is None:
            continue

        shared_entity_key = helper_shared_entity_key(helper)
        if shared_entity_key:
            shared_key = (dot_class, shared_entity_key)
            shared_suffix = shared_suffixes_by_key.get(shared_key)
            if shared_suffix is not None:
                suffixes[helper.name] = shared_suffix
                continue
            base_suffix = f"ref_damage_{shared_entity_key}"
        else:
            base_suffix = f"ref_damage_{export_suffixes[helper.name]}"

        candidate = base_suffix
        index = 2
        while candidate in used:
            candidate = f"{base_suffix}_{index}"
            index += 1
        used.add(candidate)
        suffixes[helper.name] = candidate
        if shared_entity_key:
            shared_suffixes_by_key[(dot_class, shared_entity_key)] = candidate

    return suffixes


def build_proxy_pairings(
    ordered_helpers: list[bpy.types.Object],
) -> list[tuple[bpy.types.Object, bpy.types.Object]]:
    document_helpers_by_key: dict[str, bpy.types.Object] = {}
    physical_helpers_by_key: dict[str, bpy.types.Object] = {}

    for helper in ordered_helpers:
        proxy_role = helper_proxy_role(helper)
        if proxy_role is None:
            continue

        shared_entity_key = helper_shared_entity_key(helper)
        if shared_entity_key is None:
            raise RuntimeError(
                f"Helper '{helper.name}' is missing {PROP_SHARED_ENTITY_KEY}"
            )

        if proxy_role == PROXY_ROLE_DOCUMENT:
            existing = document_helpers_by_key.get(shared_entity_key)
            if existing is not None:
                raise RuntimeError(
                    "Duplicate document proxy helpers found for shared entity key "
                    f"'{shared_entity_key}': '{existing.name}' and '{helper.name}'"
                )
            document_helpers_by_key[shared_entity_key] = helper
            continue

        if proxy_role == PROXY_ROLE_PHYSICAL:
            existing = physical_helpers_by_key.get(shared_entity_key)
            if existing is not None:
                raise RuntimeError(
                    "Duplicate physical proxy helpers found for shared entity key "
                    f"'{shared_entity_key}': '{existing.name}' and '{helper.name}'"
                )
            physical_helpers_by_key[shared_entity_key] = helper
            continue

        raise RuntimeError(
            f"Unsupported proxy role '{proxy_role}' on helper '{helper.name}'"
        )

    missing_document_keys = sorted(
        key for key in physical_helpers_by_key if key not in document_helpers_by_key
    )
    if missing_document_keys:
        raise RuntimeError(
            "Missing document proxies for shared entity keys: "
            + ", ".join(missing_document_keys)
        )

    missing_physical_keys = sorted(
        key for key in document_helpers_by_key if key not in physical_helpers_by_key
    )
    if missing_physical_keys:
        raise RuntimeError(
            "Missing physical proxies for shared entity keys: "
            + ", ".join(missing_physical_keys)
        )

    return [
        (physical_helpers_by_key[key], document_helpers_by_key[key])
        for key in sorted(physical_helpers_by_key)
    ]


def turtle_block(subject: str, predicates: list[str]) -> str:
    if not predicates:
        raise ValueError(f"No predicates supplied for Turtle subject '{subject}'")

    lines = [f"{subject} {predicates[0]} ;"]
    for predicate in predicates[1:-1]:
        lines.append(f"    {predicate} ;")
    if len(predicates) == 1:
        lines[0] = lines[0][:-2] + " ."
    else:
        lines.append(f"    {predicates[-1]} .")
    return "\n".join(lines)


def axis_uri_suffix(space_suffix: str, axis_name: str) -> str:
    return f"{space_suffix}_{axis_name.lower()}_axis"


def is_asset_space(helper: bpy.types.Object) -> bool:
    return helper[PROP_SPOT_CLASS] == SPOT_CLASS_ASSET


def is_document_space(helper: bpy.types.Object) -> bool:
    return helper[PROP_SPOT_CLASS] == "DocumentSpace"


def is_point_space(helper: bpy.types.Object) -> bool:
    return helper[PROP_SPOT_CLASS] == "PointSpace"


def is_ifc_model_space(helper: bpy.types.Object) -> bool:
    """
    Whether this space stands for a whole IFC model rather than one element of
    one. Only such a space owns the IFC file as a distribution.
    """
    return helper.get(PROP_SOURCE_IFC_CLASS) == PREFIX_IFC_PROJECT.rstrip("/")


def helper_spot_types(helper: bpy.types.Object) -> list[str]:
    spot_class = str(helper[PROP_SPOT_CLASS])
    if spot_class == SPOT_CLASS_ASSET:
        return [spot_class]
    if spot_class == SPOT_CLASS_ENTITY:
        return [SPOT_CLASS_ENTITY]
    if spot_class == "DocumentSpace":
        return [spot_class]
    return [spot_class, SPOT_CLASS_ENTITY]


def helper_plan_kind(helper: bpy.types.Object) -> str | None:
    raw = helper.get(PROP_PLAN_KIND)
    return str(raw) if raw else None


def helper_asset_path(helper: bpy.types.Object) -> str | None:
    raw = helper.get(PROP_SOURCE_ASSET_PATH)
    return str(raw) if raw else None


def helper_distribution_id(space_suffix: str) -> str:
    return f"distribution_{space_suffix}"


def helper_media_type(helper: bpy.types.Object) -> str | None:
    asset_path = helper_asset_path(helper)
    if not asset_path:
        return None
    suffix = Path(asset_path).suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".ifc":
        # The STEP physical file media type. IFC-SPF is a STEP exchange file,
        # and this is what the gateway serves `.ifc` bytes as.
        return "application/x-step"
    return None


def asset_filename_for_helper(helper: bpy.types.Object) -> str | None:
    # Only source documents, photographs, whole IFC models and spaces whose
    # file the graph linked own media distributions. Older helper scenes may
    # still contain inferred paths on extracted area spaces; ignore those
    # instead of publishing one nonexistent asset per area.
    if (
        not is_point_space(helper)
        and helper.get(PROP_SPOT_CLASS) != "DocumentSpace"
        and not is_ifc_model_space(helper)
        and not helper.get(PROP_OWN_ASSET)
    ):
        return None
    asset_path = helper_asset_path(helper)
    if not asset_path:
        return None
    return Path(urlparse(asset_path.replace("\\", "/")).path).name or None


def asset_url_for_helper(helper: bpy.types.Object, asset_base_url: str) -> str | None:
    filename = asset_filename_for_helper(helper)
    if not filename:
        return None
    if is_ifc_model_space(helper):
        category = "ifc"
    elif is_point_space(helper):
        category = "images"
    else:
        category = "plans"
    base = asset_base_url.rstrip("/")
    # A file name such as `Domhof 6 Export.ifc` is valid on disk, but a space
    # is not valid in an IRI, so the URL carries the percent-encoded name.
    return f"{base}/{category}/{quote(filename)}"


def perspective_picture_camera(
    helper: bpy.types.Object,
) -> bpy.types.Object | None:
    """
    Return the perspective camera of an image-backed PointSpace: the helper
    itself when the SPOT Setup panel made it a camera, else its source.
    """
    if not is_point_space(helper) or asset_filename_for_helper(helper) is None:
        return None

    source_object = (
        helper if helper.type == "CAMERA" else find_source_object_for_helper(helper)
    )
    if source_object is None or source_object.type != "CAMERA":
        return None

    camera_data = getattr(source_object, "data", None)
    if camera_data is None or camera_data.type != "PERSP":
        # Orthographic and panoramic capture semantics are intentionally not
        # exported until their projection parameters have an explicit contract.
        return None
    return source_object


def camera_vertical_field_of_view(camera: bpy.types.Object) -> float:
    angle = float(getattr(camera.data, "angle_y", 0.0))
    degrees = math.degrees(angle)
    return degrees if math.isfinite(degrees) and degrees > 0.0 else 60.0


def render_aspect_ratio() -> float:
    scene = bpy.context.scene
    if scene is None:
        return 16.0 / 9.0

    render = scene.render
    width = float(render.resolution_x) * float(render.pixel_aspect_x)
    height = float(render.resolution_y) * float(render.pixel_aspect_y)
    aspect_ratio = width / height if height > 0.0 else 0.0
    if math.isfinite(aspect_ratio) and aspect_ratio > 0.0:
        return aspect_ratio
    return 16.0 / 9.0


def double_literal(value: float, precision: int) -> str:
    return f'{quote_turtle_string(format_number(value, precision))}^^xsd:double'


def linked_damage_suffixes(
    camera: bpy.types.Object,
    dot_export_suffixes: dict[str, str],
) -> list[str]:
    suffixes: set[str] = set()
    for entry in getattr(camera, PROP_OBSERVED_DAMAGES, []):
        reference = entry.object
        if reference is None:
            raise RuntimeError(
                f"Picture camera '{camera.name}' contains a missing damage reference"
            )
        helper = helper_for_exported_reference(
            reference, set(dot_export_suffixes)
        )
        if helper is None or helper.name not in dot_export_suffixes:
            raise RuntimeError(
                f"Damage '{reference.name}' is not part of the exported helper scene"
            )
        if helper_dot_class(helper) != "Damage":
            raise RuntimeError(
                f"Picture camera '{camera.name}' references non-damage object '{reference.name}'"
            )
        suffixes.add(dot_export_suffixes[helper.name])
    return sorted(suffixes)


def export_picture_observation_blocks(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    dot_export_suffixes: dict[str, str],
    precision: int,
) -> str:
    """Export standalone SOSA capture semantics for a perspective photograph."""
    camera = perspective_picture_camera(helper)
    if camera is None:
        return ""

    space_id = export_suffixes[helper.name]
    sensor_id = f"sensor_{space_id}"
    capture_id = f"capture_{space_id}"
    snapshot_id = f"snapshot_{space_id}"
    distribution_id = helper_distribution_id(space_id)
    label = helper_display_label(helper)

    camera_data = camera.data
    damage_suffixes = linked_damage_suffixes(camera, dot_export_suffixes)
    focal_length = float(getattr(camera_data, "lens", 50.0)) / 1000.0
    if not math.isfinite(focal_length) or focal_length <= 0.0:
        focal_length = 0.05

    dof = getattr(camera_data, "dof", None)
    f_number = float(getattr(dof, "aperture_fstop", 2.8))
    if not math.isfinite(f_number) or f_number <= 0.0:
        f_number = 2.8

    sensor_block = turtle_block(
        f"{LOCAL_TERM_MARKER}{sensor_id}",
        [
            "a sosa:Sensor",
            f"dcterms:title {quote_turtle_string(f'Camera sensor for {label}')}",
        ],
    )
    capture_predicates = [
        "a sosa:Observation, prov:Activity",
        f"sosa:madeBySensor {LOCAL_TERM_MARKER}{sensor_id}",
        f"sosa:hasResult {LOCAL_TERM_MARKER}{snapshot_id}",
    ]
    if damage_suffixes:
        capture_predicates.append(
            "sosa:hasFeatureOfInterest "
            + ", ".join(
                f"{LOCAL_TERM_MARKER}{suffix}" for suffix in damage_suffixes
            )
        )
    capture_predicates.extend(
        [
            f"prov:used {LOCAL_TERM_MARKER}{sensor_id}",
            f"exif:focalLength {double_literal(focal_length, precision)}",
            f"exif:fNumber {double_literal(f_number, precision)}",
            "bcfOWL:hasFieldOfView "
            f"{double_literal(camera_vertical_field_of_view(camera), precision)}",
            f"bcfOWL:hasAspectRatio {double_literal(render_aspect_ratio(), precision)}",
        ]
    )
    capture_block = turtle_block(
        f"{LOCAL_TERM_MARKER}{capture_id}",
        capture_predicates,
    )
    snapshot_predicates = [
        "a dcat:Dataset, sosa:Result, foaf:Image",
        f"dcterms:title {quote_turtle_string(label)}",
        f"dcat:distribution {LOCAL_TERM_MARKER}{distribution_id}",
        f"prov:wasGeneratedBy {LOCAL_TERM_MARKER}{capture_id}",
    ]
    if damage_suffixes:
        snapshot_predicates.append(
            "foaf:depicts "
            + ", ".join(
                f"{LOCAL_TERM_MARKER}{suffix}" for suffix in damage_suffixes
            )
        )
    snapshot_block = turtle_block(
        f"{LOCAL_TERM_MARKER}{snapshot_id}",
        snapshot_predicates,
    )
    pose_statement = (
        f"{LOCAL_TERM_MARKER}{space_id} spot:represents "
        f"{LOCAL_TERM_MARKER}{capture_id} ."
    )
    return "\n\n".join(
        [sensor_block, capture_block, snapshot_block, pose_statement]
    )


def is_plan_space(helper: bpy.types.Object) -> bool:
    return helper_plan_kind(helper) is not None


def helper_length_unit(
    helper: bpy.types.Object,
    document_backed_helper_names: set[str],
) -> str:
    if helper_proxy_role(helper) == PROXY_ROLE_DOCUMENT:
        return LENGTH_UNIT_PIXEL
    explicit_unit = helper.get(PROP_LENGTH_UNIT)
    if explicit_unit:
        return str(explicit_unit)
    if helper.get(PROP_ROLE) == ROLE_DOCUMENT:
        return LENGTH_UNIT_PIXEL
    if helper.name in document_backed_helper_names:
        return LENGTH_UNIT_PIXEL
    return LENGTH_UNIT_METER


def helper_local_bbox_optional(helper: bpy.types.Object) -> list[float] | None:
    raw = helper.get(PROP_LOCAL_BBOX)
    if raw is None:
        return None
    return [float(value) for value in raw]


def exported_bbox_for_helper(
    helper: bpy.types.Object,
    length_unit: str,
) -> list[float] | None:
    if is_point_space(helper):
        return None

    bbox = helper_local_bbox_optional(helper)
    if bbox is None:
        return None

    bbox = helper_export_bbox(bbox, helper.matrix_world, length_unit)
    bbox = convert_bbox_from_blender_units(bbox, length_unit)
    if is_plan_space(helper):
        bbox[2] = 0.0
        bbox[5] = 0.0
    return bbox


def exported_matrix_literal(
    helper: bpy.types.Object,
    parent: bpy.types.Object,
    precision: int,
    document_backed_helper_names: set[str],
) -> str:
    # A helper in metres states placement only: its scale goes into its box
    # (see exported_bbox_for_helper), so a box scaled by hand is stated in
    # metres. A helper in pixels keeps the drawing scale in its frame.
    helper_unit = helper_length_unit(helper, document_backed_helper_names)
    parent_unit = helper_length_unit(parent, document_backed_helper_names)
    relative = helper_export_frame(parent.matrix_world, parent_unit).inverted_safe() @ (
        helper_export_frame(helper.matrix_world, helper_unit)
    )
    matrix = convert_matrix_between_units(relative, helper_unit, parent_unit)
    return matrix_to_column_major_literal(matrix, precision)


def represents_link_target(
    entry,
    exported_helper_names: set[str],
) -> bpy.types.Object | None:
    if entry.collection is not None:
        for helper_name in sorted(exported_helper_names):
            helper = bpy.data.objects.get(helper_name)
            if (
                helper is not None
                and helper_source_collection_root(helper) == entry.collection
            ):
                return helper
        return None
    return helper_for_exported_reference(entry.object, exported_helper_names)


def represented_link_bot_suffixes(
    helper: bpy.types.Object,
    bot_export_suffixes: dict[str, str],
) -> list[str]:
    """
    The BOT referents of other helpers that this space also represents, such
    as the storey a floor plan stands for.
    """
    owner = represents_link_owner(helper)
    suffixes: list[str] = []
    for entry in getattr(owner, PROP_REPRESENTS_LINKS, []):
        reference = entry.collection or entry.object
        if reference is None:
            raise RuntimeError(
                f"'{owner.name}' contains a missing represents link"
            )
        target = represents_link_target(entry, set(bot_export_suffixes))
        if target is None:
            raise RuntimeError(
                f"'{owner.name}' represents '{reference.name}', which is not "
                "part of the exported helper scene"
            )
        if not helper_has_referent(target):
            raise RuntimeError(
                f"'{owner.name}' represents '{reference.name}', which has no "
                "BOT or BROT class"
            )
        suffix = bot_export_suffixes[target.name]
        if suffix not in suffixes:
            suffixes.append(suffix)
    return suffixes


def export_space_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    bot_export_suffixes: dict[str, str],
    dot_export_suffixes: dict[str, str],
    precision: int,
    include_bbox: bool,
    document_backed_helper_names: set[str],
    axis_helper_names: set[str] = frozenset(),
) -> str:
    label_literal = quote_turtle_string(helper_display_label(helper))
    length_unit = helper_length_unit(helper, document_backed_helper_names)
    predicates = [
        *(f"a spot:{spot_type}" for spot_type in helper_spot_types(helper)),
        f"rdfs:label {label_literal}",
        f"qudt:hasUnit {qudt_unit_term(length_unit)}",
    ]
    # A DOT class wins over a BOT class rather than joining it: damage is not
    # building topology, so a space that stands for damage represents only the
    # `dot:Damage`. `parse_name_semantics` already clears `bot_class` for these,
    # so this also covers a helper carrying a stale `::BOT=` tag from an older
    # scene.
    own_bot_suffix = None
    if helper_has_referent(helper) and not helper_dot_class(helper):
        own_bot_suffix = bot_export_suffixes[helper.name]
        predicates.append(f"spot:represents {LOCAL_TERM_MARKER}{own_bot_suffix}")
    for link_suffix in represented_link_bot_suffixes(helper, bot_export_suffixes):
        if link_suffix != own_bot_suffix:
            predicates.append(f"spot:represents {LOCAL_TERM_MARKER}{link_suffix}")
    if helper_dot_class(helper):
        predicates.append(
            f"spot:represents {LOCAL_TERM_MARKER}{dot_export_suffixes[helper.name]}"
        )
    if include_bbox and stated_am(helper) is not None:
        # A placement SPOT-AM states keeps the box the input gave, if any, as
        # written: the region the importer derived states nothing, and a
        # commit's log retracts exactly that box.
        imported_bbox = helper.get(PROP_IMPORT_BBOX)
        if imported_bbox:
            predicates.append(f"spot-sg:hasBoundingBox {imported_bbox}")
    elif include_bbox and not helper_am_enabled(helper):
        exported_bbox = exported_bbox_for_helper(helper, length_unit)
        if exported_bbox is not None:
            bbox_literal = stringify_bbox(exported_bbox, precision)
            predicates.append(
                f"spot-sg:hasBoundingBox {quote_turtle_string(bbox_literal)}^^spot-sg:boundingBoxArray",
            )
    if (
        is_asset_space(helper)
        or is_document_space(helper)
        or helper_am_enabled(helper)
        or helper.name in axis_helper_names
    ):
        space_suffix = export_suffixes[helper.name]
        for axis_name, predicate in SPACE_AXIS_DEFINITIONS:
            predicates.append(
                f"spot:{predicate} {LOCAL_TERM_MARKER}{axis_uri_suffix(space_suffix, axis_name)}"
            )
    return turtle_block(f"{LOCAL_TERM_MARKER}{export_suffixes[helper.name]}", predicates)


def export_distribution_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    asset_base_url: str,
) -> str:
    asset_url = asset_url_for_helper(helper, asset_base_url)
    if asset_url is None:
        return ""

    # A model file is a document like a plan sheet: the file is its own
    # Document Space, which owns the distribution, while the Volume Space
    # stays the physical situation and only appears in that document.
    if is_ifc_model_space(helper):
        owner_suffix = ifc_model_document_suffix(helper, export_suffixes)
    else:
        owner_suffix = export_suffixes[helper.name]
    distribution_id = helper_distribution_id(owner_suffix)
    media_type = helper_media_type(helper)
    label = helper_display_label(helper)
    distribution_block = turtle_block(
        f"{LOCAL_TERM_MARKER}{distribution_id}",
        [
            "a dcat:Distribution",
            f"rdfs:label {quote_turtle_string(f'{label} distribution')}",
            f"dcat:accessURL <{asset_url}>",
            f"dcat:downloadURL <{asset_url}>",
            f"dcat:mediaType {quote_turtle_string(media_type or 'application/octet-stream')}",
            f"dcterms:format {quote_turtle_string(media_type or 'application/octet-stream')}",
            f"dcterms:title {quote_turtle_string(label)}",
        ],
    )

    return "\n".join(
        [
            f"{LOCAL_TERM_MARKER}{owner_suffix} "
            f"dcat:distribution {LOCAL_TERM_MARKER}{distribution_id} .",
            "",
            distribution_block,
        ]
    )


def has_model_document(
    helper: bpy.types.Object,
    asset_base_url: str,
    document_backed_helper_names: set[str],
) -> bool:
    """Whether a whole-model IFC space is exported with its model file."""
    return (
        is_ifc_model_space(helper)
        and helper.name not in document_backed_helper_names
        and asset_url_for_helper(helper, asset_base_url) is not None
    )


def ifc_model_document_suffix(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
) -> str:
    """
    The Document Space standing for the file of a whole-model IFC space.

    It is named after the Volume Space, so `volumespace-domhof` is in the file
    `documentspace-domhof`. Volume Space suffixes are unique, and so are these.
    """
    space_suffix = export_suffixes[helper.name]
    name = space_suffix.removeprefix("volumespace-")
    suffix = f"documentspace-{name}"
    if suffix in export_suffixes.values():
        raise RuntimeError(
            f"The model file of '{helper.name}' would be exported as "
            f"'{suffix}', which another helper already uses"
        )
    return suffix


def export_ifc_model_document_space_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    precision: int,
    include_bbox: bool,
    document_backed_helper_names: set[str],
) -> str:
    """
    The Document Space of the model file behind a whole-model IFC space.

    The Volume Space box is stated around the IFC root, in the file's frame
    and unit, so the same box is the extent of the model in file coordinates.
    """
    document_suffix = ifc_model_document_suffix(helper, export_suffixes)
    length_unit = helper_length_unit(helper, document_backed_helper_names)
    predicates = [
        "a spot:DocumentSpace",
        f"rdfs:label {quote_turtle_string(helper_display_label(helper))}",
        f"qudt:hasUnit {qudt_unit_term(length_unit)}",
    ]
    if include_bbox:
        exported_bbox = exported_bbox_for_helper(helper, length_unit)
        if exported_bbox is not None:
            bbox_literal = stringify_bbox(exported_bbox, precision)
            predicates.append(
                f"spot-sg:hasBoundingBox {quote_turtle_string(bbox_literal)}^^spot-sg:boundingBoxArray",
            )
    for axis_name, predicate in SPACE_AXIS_DEFINITIONS:
        predicates.append(
            f"spot:{predicate} {LOCAL_TERM_MARKER}{axis_uri_suffix(document_suffix, axis_name)}"
        )
    # The image frame of a plan sheet (Y pointing down) is not the frame of a
    # model file, so the axes of a model file carry no direction.
    axis_blocks = space_axis_blocks(
        document_suffix,
        helper_display_label(helper),
        lambda _axis_name: None,
    )
    return "\n\n".join(
        [
            turtle_block(f"{LOCAL_TERM_MARKER}{document_suffix}", predicates),
            axis_blocks,
        ]
    )


def export_ifc_model_document_appearance_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    include_transform: bool,
) -> str:
    """
    The appearance of a whole-model IFC space in its model file.

    The Volume Space origin is the IFC root and its unit is the file's, so
    the space sits in the file with the identity matrix.
    """
    space_suffix = export_suffixes[helper.name]
    document_suffix = ifc_model_document_suffix(helper, export_suffixes)
    app_id = f"appearance_{space_suffix}_in_{document_suffix}"
    predicates = [
        "a spot:DocumentAppearance",
        f"spot:appearsIn {LOCAL_TERM_MARKER}{document_suffix}",
    ]
    if include_transform:
        predicates.append(
            f"gom:hasTransformationMatrix {quote_turtle_string(IDENTITY_MATRIX_LITERAL)}^^gom:columnMajorArray",
        )
    return "\n".join(
        [
            f"{LOCAL_TERM_MARKER}{space_suffix} "
            f"spot:hasAppearance {LOCAL_TERM_MARKER}{app_id} .",
            "",
            turtle_block(f"{LOCAL_TERM_MARKER}{app_id}", predicates),
        ],
    )


def export_space_axis_blocks(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    axis_helper_names: set[str] = frozenset(),
) -> str:
    if (
        not is_asset_space(helper)
        and not is_document_space(helper)
        and not helper_am_enabled(helper)
        and helper.name not in axis_helper_names
    ):
        return ""

    # Only Asset and Document Spaces carry a direction definition; the axes
    # of an AM-enabled space exist to hang boundary points off.
    if is_asset_space(helper):
        direction_for = lambda axis_name: helper_asset_axis_direction(helper, axis_name)
    elif is_document_space(helper):
        direction_for = default_document_axis_direction
    else:
        direction_for = lambda _axis_name: None
    return space_axis_blocks(
        export_suffixes[helper.name],
        helper_display_label(helper),
        direction_for,
    )


def space_axis_blocks(
    space_suffix: str,
    label: str,
    direction_for: Callable[[str], str | None],
) -> str:
    axis_blocks: list[str] = []
    for axis_name, _predicate in SPACE_AXIS_DEFINITIONS:
        predicates = [
            "a spot:Axis",
            f"rdfs:label {quote_turtle_string(f'{axis_name} axis of {label}')}",
        ]
        direction = direction_for(axis_name)
        if direction:
            predicates.append(f"spot:hasDirection spot:{direction}")
        axis_blocks.append(
            turtle_block(
                f"{LOCAL_TERM_MARKER}{axis_uri_suffix(space_suffix, axis_name)}",
                predicates,
            )
        )

    return "\n\n".join(axis_blocks)


def resolve_helper_by_name(name: str | None) -> bpy.types.Object | None:
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None
    if obj.get(PROP_HELPER):
        return obj
    return find_helper_for_source_object(obj)


def resolve_asset_space_helper(helper: bpy.types.Object) -> bpy.types.Object:
    current: bpy.types.Object | None = helper
    asset_helper: bpy.types.Object | None = None
    while current is not None and current.get(PROP_HELPER):
        if current.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET:
            asset_helper = current
        current = current.parent
    if asset_helper is None:
        raise RuntimeError(
            f"Helper '{helper.name}' is not linked to an AssetSpace helper"
        )
    return asset_helper


def resolve_appearance_target_helper(helper: bpy.types.Object) -> bpy.types.Object:
    if helper_am_reference_asset_enabled(helper):
        return resolve_asset_space_helper(helper)

    reference_helper = resolve_helper_by_name(helper_am_reference_helper_name(helper))
    if reference_helper is not None:
        return reference_helper

    if helper.parent is None or not helper.parent.get(PROP_HELPER):
        raise RuntimeError(f"Helper '{helper.name}' is missing a helper parent")
    return helper.parent


def appearance_subject_id(
    helper: bpy.types.Object,
    target_helper: bpy.types.Object,
    export_suffixes: dict[str, str],
) -> str:
    return (
        f"appearance_{export_suffixes[helper.name]}_in_"
        f"{export_suffixes[target_helper.name]}"
    )


def am_axis_mapping_id(app_id: str, axis_name: str) -> str:
    return f"{app_id}_AM_{axis_name}"


def am_boundary_mapping_id(app_id: str, slot: str) -> str:
    return f"{app_id}_BPM_{slot}"


def am_source_point_id(space_suffix: str, slot: str) -> str:
    axis_name, bound_kind, _label = boundary_point_slot_details(slot)
    return f"{space_suffix}_{axis_name.lower()}_{bound_kind.lower()}_boundary_point"


def synthesized_boundary_slots(
    helper: bpy.types.Object,
) -> list[tuple[str, str, str, bpy.types.Object, str]]:
    derived_region, _warnings = derive_am_region(helper)
    if derived_region is None:
        return []

    synthesized_slots: list[tuple[str, str, str, bpy.types.Object, str]] = []
    for axis_name in ("X", "Y", "Z"):
        axis_region = derived_region.axis_regions.get(axis_name)
        if axis_region is None:
            continue
        synthesized_slots.append(
            (
                f"{axis_name}_MIN",
                axis_region.lower_interval.relation,
                axis_region.lower_interval.accuracy,
                axis_region.lower_interval.target_helper,
                f"{axis_name} min",
            )
        )
        synthesized_slots.append(
            (
                f"{axis_name}_MAX",
                axis_region.upper_interval.relation,
                axis_region.upper_interval.accuracy,
                axis_region.upper_interval.target_helper,
                f"{axis_name} max",
            )
        )
    return synthesized_slots


def export_am_source_boundary_blocks(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
) -> str:
    if not helper_am_enabled(helper):
        return ""

    space_suffix = export_suffixes[helper.name]
    blocks: list[str] = []
    for (
        slot,
        _relation,
        _accuracy,
        _target_helper,
        slot_label,
    ) in synthesized_boundary_slots(helper):
        axis_name, bound_kind, slot_label = boundary_point_slot_details(slot)
        point_id = am_source_point_id(space_suffix, slot)
        point_type = "MinBoundaryPoint" if bound_kind == "Min" else "MaxBoundaryPoint"
        blocks.append(
            f"{LOCAL_TERM_MARKER}{axis_uri_suffix(space_suffix, axis_name)} "
            f"spot-am:hasBoundaryPoint {LOCAL_TERM_MARKER}{point_id} ."
        )
        blocks.append(
            turtle_block(
                f"{LOCAL_TERM_MARKER}{point_id}",
                [
                    f"a spot-am:{point_type}",
                    f"rdfs:label {quote_turtle_string(f'{slot_label} boundary point of {helper_display_label(helper)}')}",
                ],
            )
        )

    return "\n\n".join(blocks)


def export_am_mapping_blocks(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    precision: int,
) -> str:
    if not helper_am_enabled(helper):
        return ""

    target_helper = resolve_appearance_target_helper(helper)
    app_id = appearance_subject_id(helper, target_helper, export_suffixes)
    blocks: list[str] = []

    for axis_name in ("X", "Y", "Z"):
        target_axis = helper_am_axis_target(helper, axis_name)
        relation = helper_am_axis_relation(helper, axis_name)
        accuracy = helper_am_axis_accuracy(helper, axis_name)
        blocks.append(
            turtle_block(
                f"{LOCAL_TERM_MARKER}{am_axis_mapping_id(app_id, axis_name)}",
                [
                    "a spot-am:AxisMapping",
                    f"spot-am:hasAccuracy spot-am:{accuracy}",
                    f"spot-am:hasAxisRelationType spot-am:{relation}",
                    f"spot-am:hasSourceAxis {LOCAL_TERM_MARKER}{axis_uri_suffix(export_suffixes[helper.name], axis_name)}",
                    f"spot-am:hasTargetAxis {LOCAL_TERM_MARKER}{axis_uri_suffix(export_suffixes[target_helper.name], target_axis)}",
                ],
            )
        )

    for (
        slot,
        relation,
        accuracy,
        mapping_target,
        _slot_label,
    ) in synthesized_boundary_slots(helper):
        if mapping_target.name not in export_suffixes:
            raise RuntimeError(
                f"Boundary-point target '{mapping_target.name}' for helper '{helper.name}' is not exportable"
            )
        predicates = [
            "a spot-am:BoundaryPointMapping",
            f"reloc:{relation} {LOCAL_TERM_MARKER}{export_suffixes[mapping_target.name]}",
            f"spot-am:hasAccuracy spot-am:{accuracy}",
            f"spot-am:hasSourcePoint {LOCAL_TERM_MARKER}{am_source_point_id(export_suffixes[helper.name], slot)}",
        ]
        blocks.append(
            turtle_block(
                f"{LOCAL_TERM_MARKER}{am_boundary_mapping_id(app_id, slot)}",
                predicates,
            )
        )

    return "\n\n".join(blocks)


def export_bot_block(
    helper: bpy.types.Object,
    bot_export_suffixes: dict[str, str],
) -> str:
    """The referent a space represents, typed only with the classes it has."""
    bot_class = helper.get(PROP_BOT_CLASS)
    brot_class = helper.get(PROP_BROT_CLASS)
    if not bot_class and not brot_class:
        raise RuntimeError(f"Helper '{helper.name}' has neither a BOT nor a BROT class")

    predicates: list[str] = []
    if bot_class:
        predicates.append(f"a bot:{bot_class}")
    beo_class = helper.get(PROP_BEO_CLASS)
    if beo_class:
        predicates.append(f"a beo:{beo_class}")
    if brot_class:
        predicates.append(f"a brot:{brot_class}")
    label_literal = quote_turtle_string(helper_display_label(helper))
    predicates.append(f"rdfs:label {label_literal}")

    return turtle_block(
        f"{LOCAL_TERM_MARKER}{bot_export_suffixes[helper.name]}", predicates
    )


def export_dot_block(
    helper: bpy.types.Object,
    dot_export_suffixes: dict[str, str],
) -> str:
    dot_class = helper_dot_class(helper)
    if dot_class is None:
        raise RuntimeError(f"Helper '{helper.name}' is missing DOT semantics")
    return turtle_block(
        f"{LOCAL_TERM_MARKER}{dot_export_suffixes[helper.name]}",
        [
            f"a dot:{dot_class}",
            f"rdfs:label {quote_turtle_string(helper_display_label(helper))}",
        ],
    )


def stated_am_sources(
    ordered_helpers: list[bpy.types.Object],
    proxy_pairings: list[tuple[bpy.types.Object, bpy.types.Object]],
) -> set[str]:
    """The helpers whose space a stated SPOT-AM placement locates."""
    sources = {helper.name for helper in ordered_helpers if stated_am(helper)}
    sources.update(
        physical.name
        for physical, document in proxy_pairings
        if stated_am(document)
    )
    return sources


def stated_am_target_suffix(local_name: str, export_suffixes: dict[str, str]) -> str:
    """
    The exported space an imported SPOT-AM relation points at: the helper
    the space was imported as, or the AssetSpace an import was placed in,
    which stands for the imported root.
    """
    for helper_name, suffix in export_suffixes.items():
        helper = bpy.data.objects.get(helper_name)
        if helper is None or is_document_proxy(helper):
            continue
        if str(helper.get(PROP_URI_SUFFIX, "")) == local_name:
            return suffix
        imported = helper.get(PROP_IMPORT_IRI)
        if imported and local_name_of(str(imported)) == local_name:
            return suffix
    raise RuntimeError(
        f"A stated SPOT-AM relation points at '{local_name}', which is not "
        "part of the exported helper scene"
    )


def stated_am_parts(
    app_id: str,
    source: bpy.types.Object,
    carrier: bpy.types.Object,
    target: bpy.types.Object,
    export_suffixes: dict[str, str],
) -> tuple[list[str], str]:
    """
    The appearance predicates and the blocks of the SPOT-AM placement that
    `carrier` keeps for `source` in `target`, or nothing.
    """
    description = stated_am(carrier)
    if description is None:
        return [], ""
    if is_asset_space(target):
        direction_of = lambda axis_name: helper_asset_axis_direction(target, axis_name)
    elif is_document_space(target):
        direction_of = default_document_axis_direction
    else:
        direction_of = lambda _axis_name: None

    def target_axis_for(axis: dict) -> tuple[str, bool]:
        """The target axis that points the way the input's does, and whether
        it points the other way, which reverses the axis relation."""
        direction = axis.get("target_direction")
        if direction:
            for axis_name in ("X", "Y", "Z"):
                if direction_of(axis_name) == direction:
                    return axis_name, False
            for axis_name in ("X", "Y", "Z"):
                if direction_of(axis_name) == opposite_direction(direction):
                    return axis_name, True
        return axis["target"], False

    return (
        stated_am_predicates(app_id, description, LOCAL_TERM_MARKER),
        stated_am_blocks(
            app_id,
            description,
            export_suffixes[source.name],
            helper_display_label(source),
            export_suffixes[target.name],
            lambda local_name: stated_am_target_suffix(local_name, export_suffixes),
            LOCAL_TERM_MARKER,
            turtle_block,
            target_axis_for,
        ),
    )


def export_appearance_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    precision: int,
    include_transform: bool,
    document_backed_helper_names: set[str],
) -> str:
    parent = resolve_appearance_target_helper(helper)
    app_id = appearance_subject_id(helper, parent, export_suffixes)
    predicates = [
        f"a spot:{appearance_class(parent)}",
        f"spot:appearsIn {LOCAL_TERM_MARKER}{export_suffixes[parent.name]}",
    ]
    # A placement the input states with SPOT-AM alone is written as stated.
    stated_predicates, stated_blocks = stated_am_parts(
        app_id, helper, helper, parent, export_suffixes
    )
    predicates.extend(stated_predicates)
    if include_transform and not helper_am_enabled(helper) and not stated_predicates:
        matrix_literal = exported_matrix_literal(
            helper,
            parent,
            precision,
            document_backed_helper_names,
        )
        predicates.append(
            f"gom:hasTransformationMatrix {quote_turtle_string(matrix_literal)}^^gom:columnMajorArray",
        )
    if helper_am_enabled(helper):
        predicates.append(
            "spot-am:hasAxisMapping "
            + ",\n        ".join(
                f"{LOCAL_TERM_MARKER}{am_axis_mapping_id(app_id, axis_name)}"
                for axis_name in ("X", "Y", "Z")
            )
        )
        boundary_mapping_ids = [
            f"{LOCAL_TERM_MARKER}{am_boundary_mapping_id(app_id, slot)}"
            for slot, _relation, _accuracy, _target_helper, _slot_label in synthesized_boundary_slots(
                helper
            )
        ]
        if boundary_mapping_ids:
            predicates.append(
                "spot-am:hasBoundaryPointMapping "
                + ",\n        ".join(boundary_mapping_ids)
            )

    return "\n".join(
        [
            f"{LOCAL_TERM_MARKER}{export_suffixes[helper.name]} "
            f"spot:hasAppearance {LOCAL_TERM_MARKER}{app_id} .",
            "",
            turtle_block(f"{LOCAL_TERM_MARKER}{app_id}", predicates),
            *(["", stated_blocks] if stated_blocks else []),
        ],
    )


def export_document_proxy_appearance_block(
    physical_helper: bpy.types.Object,
    document_helper: bpy.types.Object,
    export_suffixes: dict[str, str],
    precision: int,
    include_transform: bool,
    document_backed_helper_names: set[str],
) -> str:
    if document_helper.parent is None or not document_helper.parent.get(PROP_HELPER):
        raise RuntimeError(
            f"Document proxy helper '{document_helper.name}' is missing a helper parent"
        )

    document_parent = document_helper.parent
    app_id = (
        f"appearance_{export_suffixes[physical_helper.name]}_in_"
        f"{export_suffixes[document_parent.name]}_via_{export_suffixes[document_helper.name]}"
    )
    predicates = [
        "a spot:DocumentAppearance",
        f"spot:appearsIn {LOCAL_TERM_MARKER}{export_suffixes[document_parent.name]}",
    ]
    stated_predicates, stated_blocks = stated_am_parts(
        app_id, physical_helper, document_helper, document_parent, export_suffixes
    )
    predicates.extend(stated_predicates)
    if include_transform and not stated_predicates:
        matrix_literal = exported_matrix_literal(
            document_helper,
            document_parent,
            precision,
            document_backed_helper_names,
        )
        predicates.append(
            f"gom:hasTransformationMatrix {quote_turtle_string(matrix_literal)}^^gom:columnMajorArray",
        )

    return "\n".join(
        [
            f"{LOCAL_TERM_MARKER}{export_suffixes[physical_helper.name]} "
            f"spot:hasAppearance {LOCAL_TERM_MARKER}{app_id} .",
            "",
            turtle_block(f"{LOCAL_TERM_MARKER}{app_id}", predicates),
            *(["", stated_blocks] if stated_blocks else []),
        ],
    )


def export_hierarchy_block(
    helper: bpy.types.Object,
    export_suffixes: dict[str, str],
) -> str:
    if helper.parent is None or not helper.parent.get(PROP_HELPER):
        raise RuntimeError(f"Helper '{helper.name}' is missing a helper parent")

    parent = helper.parent
    return "\n".join(
        [
            f"{LOCAL_TERM_MARKER}{export_suffixes[helper.name]} "
            f"spot:hasSuperSpace {LOCAL_TERM_MARKER}{export_suffixes[parent.name]} .",
            f"{LOCAL_TERM_MARKER}{export_suffixes[parent.name]} "
            f"spot:hasSubspace {LOCAL_TERM_MARKER}{export_suffixes[helper.name]} .",
        ],
    )


def require_bot_suffix(
    helper: bpy.types.Object,
    bot_export_suffixes: dict[str, str],
) -> str:
    """
    The helper's BOT URI suffix, or an error that says which helper is missing.

    Suffixes are keyed by helper name and are built by walking the helper tree
    from its roots. A helper that the walk never reaches -- one whose parent
    chain leaves the helper collection -- has no entry, and the bare KeyError
    that a direct lookup raises names the helper without saying what went
    wrong with it.
    """
    suffix = bot_export_suffixes.get(helper.name)
    if suffix is None:
        raise RuntimeError(
            f"Helper '{helper.name}' has no BOT URI suffix. It carries a BOT "
            "class but was not reached when walking the helper tree, so check "
            "that it is parented under a helper root in the helper collection.",
        )
    return suffix


def bot_relation_for_child(helper: bpy.types.Object) -> str | None:
    """
    The BOT relation from the parent's referent to this helper's, or None.

    Both referents must carry a BOT class: a space without one is no BOT
    entity, whatever it contains. An element holds sub-elements only; a zone
    holds zones, spaces and elements.
    """
    parent = helper.parent
    if parent is None:
        raise RuntimeError(f"Helper '{helper.name}' is missing a helper parent")

    bot_class = str(helper.get(PROP_BOT_CLASS, ""))
    parent_bot_class = str(parent.get(PROP_BOT_CLASS, ""))
    if not bot_class or not parent_bot_class:
        return None
    if parent_bot_class == BOT_CLASS_ELEMENT:
        return "bot:hasSubElement" if bot_class == BOT_CLASS_ELEMENT else None
    if bot_class == BOT_CLASS_BUILDING and parent_bot_class == BOT_CLASS_SITE:
        return "bot:hasBuilding"
    if bot_class == BOT_CLASS_STOREY and parent_bot_class == BOT_CLASS_BUILDING:
        return "bot:hasStorey"
    if bot_class == BOT_CLASS_SPACE:
        return "bot:hasSpace"
    if bot_class == BOT_CLASS_ELEMENT:
        return "bot:hasElement"
    return "bot:containsZone"


def brot_relation_for_child(helper: bpy.types.Object) -> str | None:
    """
    The BROT relation from the parent's referent to this helper's, or None.

    Both referents must carry a BROT class, and only pairs that BROT's
    domains and ranges admit get one: Joint and Coating are no
    `brot:Component`, so a zone holds them through no BROT relation.
    """
    parent = helper.parent
    if parent is None:
        return None
    brot_class = str(helper.get(PROP_BROT_CLASS, ""))
    parent_brot_class = str(parent.get(PROP_BROT_CLASS, ""))
    if parent_brot_class in BROT_ZONE_CLASSES:
        if brot_class in BROT_ZONE_CLASSES:
            return "brot:containsZone"
        if brot_class == "Component":
            return "brot:containsComponent"
    if parent_brot_class == "Component":
        return {
            "Component": "brot:aggregates",
            "Joint": "brot:hasJoint",
            "Coating": "brot:hasCoating",
        }.get(brot_class)
    return None


def build_export_resource_registry(
    root_helpers: list[bpy.types.Object],
) -> ExportResourceRegistry:
    ordered_helpers: list[bpy.types.Object] = []
    for root_helper in root_helpers:
        ordered_helpers.extend(flatten_helper_tree(root_helper))

    proxy_pairings = build_proxy_pairings(ordered_helpers)
    document_backed_helper_names = {
        physical_helper.name for physical_helper, _document_helper in proxy_pairings
    }
    exported_helpers = [
        helper for helper in ordered_helpers if not is_document_proxy(helper)
    ]
    export_suffixes = build_export_uri_suffixes(ordered_helpers)
    bot_export_suffixes = build_bot_export_uri_suffixes(
        ordered_helpers, export_suffixes
    )
    dot_export_suffixes = build_dot_export_uri_suffixes(
        ordered_helpers, export_suffixes
    )
    return ExportResourceRegistry(
        ordered_helpers=ordered_helpers,
        exported_helpers=exported_helpers,
        export_suffixes=export_suffixes,
        bot_export_suffixes=bot_export_suffixes,
        dot_export_suffixes=dot_export_suffixes,
        proxy_pairings=proxy_pairings,
        document_backed_helper_names=document_backed_helper_names,
    )


def write_turtle(
    root_helpers: list[bpy.types.Object],
    output_path: Path,
    base_iri: str,
    precision: int,
    export_profile: str,
    asset_base_url: str,
    local_prefix: str,
) -> None:
    local_prefix = normalize_local_prefix(local_prefix)
    registry = build_export_resource_registry(root_helpers)
    ordered_helpers = registry.ordered_helpers
    proxy_pairings = registry.proxy_pairings
    document_backed_helper_names = registry.document_backed_helper_names
    exported_helpers = registry.exported_helpers
    export_suffixes = registry.export_suffixes
    bot_export_suffixes = registry.bot_export_suffixes
    dot_export_suffixes = registry.dot_export_suffixes
    include_bbox = is_scene_graph_profile(export_profile)
    include_transform = is_scene_graph_profile(export_profile)
    include_beo = any(helper.get(PROP_BEO_CLASS) for helper in exported_helpers)
    include_brot = any(helper.get(PROP_BROT_CLASS) for helper in exported_helpers)
    include_bot = any(helper.get(PROP_BOT_CLASS) for helper in exported_helpers)
    axis_helper_names = stated_am_sources(ordered_helpers, proxy_pairings)
    include_am = bool(axis_helper_names) or any(
        helper_am_enabled(helper) for helper in exported_helpers
    )
    include_dot = any(helper_dot_class(helper) for helper in exported_helpers)
    include_picture_observations = any(
        perspective_picture_camera(helper) is not None
        for helper in exported_helpers
    )

    lines: list[str] = [
        f"@prefix {local_prefix}: <{base_iri}> .",
        "@prefix spot: <https://w3id.org/spot#> .",
        BOT_PREFIX_LINE,
        f"@prefix qudt: <{QUDT_SCHEMA_IRI}> .",
        f"@prefix unit: <{QUDT_UNIT_IRI}> .",
        "@prefix dcat: <http://www.w3.org/ns/dcat#> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
        "",
    ]
    if include_beo:
        lines[2:2] = ["@prefix beo: <https://pi.pauwel.be/voc/buildingelement#> ."]
    if include_bbox:
        insertion_index = 3 if include_beo else 2
        lines[insertion_index:insertion_index] = [
            "@prefix spot-sg: <https://w3id.org/spot/sg#> .",
            "@prefix gom: <https://w3id.org/gom#> .",
        ]
    if include_am:
        insertion_index = 3 if include_beo else 2
        if include_bbox:
            insertion_index += 2
        lines[insertion_index:insertion_index] = [
            "@prefix spot-am: <https://w3id.org/spot/am#> .",
            "@prefix reloc: <https://w3id.org/reloc#> .",
        ]
    if include_picture_observations:
        lines[-2:-2] = [
            "@prefix bcfOWL: <https://w3id.org/bcfOWL#> .",
            "@prefix exif: <http://www.w3.org/2003/12/exif/ns#> .",
            "@prefix foaf: <http://xmlns.com/foaf/0.1/> .",
            "@prefix prov: <http://www.w3.org/ns/prov#> .",
            "@prefix sosa: <http://www.w3.org/ns/sosa/> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        ]
    if include_dot:
        lines[-2:-2] = ["@prefix dot: <https://w3id.org/dot#> ."]
    if include_brot:
        lines[-2:-2] = [f"@prefix brot: <{BROT_NAMESPACE}> ."]
    if not include_bot:
        lines.remove(BOT_PREFIX_LINE)

    emitted_bot_suffixes: set[str] = set()
    emitted_dot_suffixes: set[str] = set()
    emitted_bot_hierarchy: set[tuple[str, str, str]] = set()

    for helper in exported_helpers:
        lines.append(
            export_space_block(
                helper,
                export_suffixes,
                bot_export_suffixes,
                dot_export_suffixes,
                precision,
                include_bbox=include_bbox,
                document_backed_helper_names=document_backed_helper_names,
                axis_helper_names=axis_helper_names,
            )
        )
        lines.append("")
        axis_blocks = export_space_axis_blocks(
            helper, export_suffixes, axis_helper_names
        )
        if axis_blocks:
            lines.append(axis_blocks)
            lines.append("")
        am_source_boundary_blocks = export_am_source_boundary_blocks(
            helper,
            export_suffixes,
        )
        if am_source_boundary_blocks:
            lines.append(am_source_boundary_blocks)
            lines.append("")
        distribution_block = ""
        # A plan laid out on a sheet uses the sheet's file, unless the graph
        # gave the plan a file of its own.
        if helper.name not in document_backed_helper_names or helper.get(
            PROP_OWN_ASSET
        ):
            distribution_block = export_distribution_block(
                helper,
                export_suffixes,
                asset_base_url,
            )
        if has_model_document(helper, asset_base_url, document_backed_helper_names):
            lines.append(
                export_ifc_model_document_space_block(
                    helper,
                    export_suffixes,
                    precision,
                    include_bbox=include_bbox,
                    document_backed_helper_names=document_backed_helper_names,
                )
            )
            lines.append("")
        if distribution_block:
            lines.append(distribution_block)
            lines.append("")
        picture_observation_blocks = export_picture_observation_blocks(
            helper,
            export_suffixes,
            dot_export_suffixes,
            precision,
        )
        if picture_observation_blocks:
            lines.append(picture_observation_blocks)
            lines.append("")

    for helper in exported_helpers:
        if not helper_has_referent(helper):
            continue
        bot_suffix = require_bot_suffix(helper, bot_export_suffixes)
        if bot_suffix in emitted_bot_suffixes:
            continue
        emitted_bot_suffixes.add(bot_suffix)
        lines.append(export_bot_block(helper, bot_export_suffixes))
        lines.append("")

    for helper in exported_helpers:
        dot_suffix = dot_export_suffixes.get(helper.name)
        if dot_suffix is None or dot_suffix in emitted_dot_suffixes:
            continue
        emitted_dot_suffixes.add(dot_suffix)
        lines.append(export_dot_block(helper, dot_export_suffixes))
        lines.append("")

    for helper in exported_helpers:
        if helper.parent is None:
            continue
        lines.append(
            export_appearance_block(
                helper,
                export_suffixes,
                precision,
                include_transform=include_transform,
                document_backed_helper_names=document_backed_helper_names,
            )
        )
        lines.append("")
        am_mapping_blocks = export_am_mapping_blocks(
            helper,
            export_suffixes,
            precision,
        )
        if am_mapping_blocks:
            lines.append(am_mapping_blocks)
            lines.append("")

    for helper in exported_helpers:
        if not has_model_document(
            helper, asset_base_url, document_backed_helper_names
        ):
            continue
        lines.append(
            export_ifc_model_document_appearance_block(
                helper,
                export_suffixes,
                include_transform=include_transform,
            )
        )
        lines.append("")

    for physical_helper, document_helper in proxy_pairings:
        lines.append(
            export_document_proxy_appearance_block(
                physical_helper,
                document_helper,
                export_suffixes,
                precision,
                include_transform=include_transform,
                document_backed_helper_names=document_backed_helper_names,
            )
        )
        lines.append("")

    for helper in exported_helpers:
        parent = helper.parent
        if parent is None or not parent.get(PROP_HELPER):
            continue
        for predicate in (
            bot_relation_for_child(helper),
            brot_relation_for_child(helper),
        ):
            if predicate is None:
                continue
            parent_suffix = require_bot_suffix(parent, bot_export_suffixes)
            child_suffix = require_bot_suffix(helper, bot_export_suffixes)
            hierarchy_key = (parent_suffix, predicate, child_suffix)
            if hierarchy_key in emitted_bot_hierarchy:
                continue
            emitted_bot_hierarchy.add(hierarchy_key)
            lines.append(
                f"{LOCAL_TERM_MARKER}{parent_suffix} {predicate} "
                f"{LOCAL_TERM_MARKER}{child_suffix} ."
            )
            lines.append("")

    if export_profile == EXPORT_PROFILE_SPOT_ONLY:
        for helper in exported_helpers:
            if helper.parent is None:
                continue
            lines.append(export_hierarchy_block(helper, export_suffixes))
            lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    turtle = "\n".join(lines).replace(LOCAL_TERM_MARKER, f"{local_prefix}:")
    output_path.write_text(turtle.rstrip() + "\n", encoding="utf8")


def normalize_local_prefix(value: str) -> str:
    prefix = value.strip()
    if prefix.endswith(":"):
        prefix = prefix[:-1]
    if not re.fullmatch(r"[A-Za-z](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?", prefix):
        raise ValueError(
            "Prefix must start with a letter, must not end with '.', and may contain only letters, digits, '.', '_' or '-'"
        )
    if prefix.lower() in RESERVED_LOCAL_PREFIXES:
        raise ValueError(f"Prefix '{prefix}' conflicts with an exporter vocabulary prefix")
    return prefix


def export_helper_scene(
    helper_collection_name: str = HELPER_COLLECTION_NAME,
    output_path: str | Path | None = None,
    base_iri: str = DEFAULT_BASE_IRI,
    precision: int = 6,
    export_profile: str = EXPORT_PROFILE_SCENE_GRAPH,
    asset_base_url: str = "http://localhost:4000/assets/",
    local_prefix: str = DEFAULT_LOCAL_PREFIX,
) -> dict[str, object]:
    if base_iri in {LEGACY_DEFAULT_BASE_IRI, LEGACY_HASH_BASE_IRI}:
        base_iri = DEFAULT_BASE_IRI
    collection = ensure_helper_collection(helper_collection_name)
    roots = helper_root_objects(collection)
    if not roots:
        raise RuntimeError(
            f"Expected at least one helper root in '{collection.name}', found 0",
        )

    asset_roots = [
        root
        for root in roots
        if root.get(PROP_ROLE) != ROLE_DOCUMENT
        and root.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
    ]
    if len(asset_roots) != 1:
        raise RuntimeError(
            f"Expected exactly one asset helper root, found {len(asset_roots)}",
        )
    unexpected_root_roles = sorted(
        {
            str(root.get(PROP_ROLE))
            for root in roots
            if root.get(PROP_ROLE) != ROLE_DOCUMENT
            and root.get(PROP_SPOT_CLASS) != SPOT_CLASS_ASSET
        }
    )
    if unexpected_root_roles:
        raise RuntimeError(
            "Unsupported helper root roles found during export: "
            + ", ".join(unexpected_root_roles)
        )

    root = asset_roots[0]

    resolved_output_path = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else default_output_path_for_profile(export_profile).resolve()
    )
    write_turtle(
        roots,
        resolved_output_path,
        base_iri,
        precision,
        export_profile,
        asset_base_url,
        local_prefix,
    )

    helper_count = len([obj for obj in collection.objects if obj.get(PROP_HELPER)])
    root_world_location = tuple(float(value) for value in root.matrix_world.translation)
    return {
        "output_path": str(resolved_output_path),
        "export_profile": export_profile,
        "base_iri": base_iri,
        "local_prefix": normalize_local_prefix(local_prefix),
        "helper_collection_name": collection.name,
        "helper_count": helper_count,
        "root_helper_name": root.name,
        "root_helper_names": [helper.name for helper in roots],
        "root_world_location": root_world_location,
    }


def print_export_summary(result: dict[str, object]) -> None:
    print(
        f"Exported {result['helper_count']} helper nodes as {result['export_profile']} to {result['output_path']}"
    )
    root_helper_names = result.get("root_helper_names") or [result["root_helper_name"]]
    print("Root helpers: " + ", ".join(str(name) for name in root_helper_names))
    location = result["root_world_location"]
    print(
        "Root helper world location:"
        f" ({location[0]:.6f}, {location[1]:.6f}, {location[2]:.6f})",
    )
    print(
        "Root is exported as the logical graph origin because it has no parent appearance"
    )


def main() -> None:
    args = parse_args()
    result = export_helper_scene(
        helper_collection_name=args.helper_collection,
        output_path=args.output,
        base_iri=args.base_iri,
        local_prefix=args.prefix,
        precision=args.precision,
        export_profile=args.profile,
        asset_base_url=args.asset_base_url,
    )
    print_export_summary(result)


if __name__ == "__main__":
    main()
