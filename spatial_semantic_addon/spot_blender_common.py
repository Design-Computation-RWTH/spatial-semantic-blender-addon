from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import bpy
from mathutils import Matrix, Quaternion, Vector

HELPER_COLLECTION_NAME = "SPOT Scene"
HELPER_COLLECTION_MARKER = "spot_helper_scene"
HELPER_SCHEMA_VERSION = "1.0"
HELPER_PLACEHOLDER_HALF_EXTENT = 0.05

GEOMETRY_OBJECT_TYPES = {
    "CURVE",
    "FONT",
    "MESH",
    "META",
    "SURFACE",
    "VOLUME",
    "CURVES",
}

ROLE_SITE = "site"
ROLE_DOCUMENT = "document"
ROLE_BUILDING = "building"
ROLE_MODEL = "model"
ROLE_STOREY = "storey"
ROLE_SPACE = "space"
ROLE_ELEMENT = "element"

LENGTH_UNIT_METER = "meter"
LENGTH_UNIT_PIXEL = "pixel"

BLENDER_UNITS_PER_METER = 1.0
DOCUMENT_BLENDER_UNITS_PER_PIXEL = 0.001

QUDT_SCHEMA_IRI = "http://qudt.org/schema/qudt/"
QUDT_UNIT_IRI = "http://qudt.org/vocab/unit/"
QUDT_UNIT_METER = "unit:M"
QUDT_UNIT_PIXEL = "unit:PIXEL"

SPOT_CLASS_ASSET = "AssetSpace"
SPOT_CLASS_ENTITY = "EntitySpace"
SPOT_CLASS_VOLUME = "VolumeSpace"
SPOT_CLASS_POINT = "PointSpace"

DIRECTION_FRONT = "Front"
DIRECTION_REAR = "Rear"
DIRECTION_LEFT = "Left"
DIRECTION_RIGHT = "Right"
DIRECTION_TOP = "Top"
DIRECTION_BOTTOM = "Bottom"

ASSET_AXIS_DIRECTION_ITEMS = (
    (DIRECTION_FRONT, "Front", "Axis points to the front of the asset space"),
    (DIRECTION_REAR, "Rear", "Axis points to the rear of the asset space"),
    (DIRECTION_LEFT, "Left", "Axis points to the left of the asset space"),
    (DIRECTION_RIGHT, "Right", "Axis points to the right of the asset space"),
    (DIRECTION_TOP, "Top", "Axis points to the top of the asset space"),
    (DIRECTION_BOTTOM, "Bottom", "Axis points to the bottom of the asset space"),
)

DEFAULT_ASSET_AXIS_DIRECTIONS = {
    "X": DIRECTION_FRONT,
    "Y": DIRECTION_RIGHT,
    "Z": DIRECTION_TOP,
}

# A DocumentSpace is a sheet, not a body. Its frame follows the image
# convention the document proxies are already built with: X grows to the
# right across the sheet, Y grows downward, and Z points out of the page
# toward the reader.
DEFAULT_DOCUMENT_AXIS_DIRECTIONS = {
    "X": DIRECTION_RIGHT,
    "Y": DIRECTION_BOTTOM,
    "Z": DIRECTION_FRONT,
}

PLAN_KIND_FLOORPLAN = "floorplan"
PLAN_KIND_SECTION = "section"

BOT_CLASS_SITE = "Site"
BOT_CLASS_BUILDING = "Building"
BOT_CLASS_STOREY = "Storey"
BOT_CLASS_SPACE = "Space"
BOT_CLASS_ELEMENT = "Element"

PROP_HELPER = "spot_helper"
PROP_SCHEMA_VERSION = "spot_schema_version"
PROP_ROLE = "spot_role"
PROP_SPOT_CLASS = "spot_class"
PROP_BOT_CLASS = "spot_bot_class"
PROP_BEO_CLASS = "spot_beo_class"
PROP_DOT_CLASS = "spot_dot_class"
PROP_BOT_URI_SUFFIX = "spot_bot_uri_suffix"
PROP_URI_SUFFIX = "spot_uri_suffix"
PROP_LABEL = "spot_label"
PROP_SOURCE_NAME = "spot_source_name"
PROP_SOURCE_COLLECTION = "spot_source_collection"
PROP_SOURCE_IFC_CLASS = "spot_source_ifc_class"
PROP_SOURCE_DEFINITION_ID = "spot_source_ifc_definition_id"
PROP_SOURCE_ASSET_PATH = "spot_source_asset_path"
# Set on an IFC bounding box: the `IfcProject/...` collection it stands for.
PROP_IFC_BOUNDING_BOX_SOURCE = "spot_ifc_bounding_box_source"
# Set on an IFC bounding box: the file name of the IFC model in the assets,
# for example `Domhof 6.ifc`. It wins over the path inferred from the label.
PROP_IFC_FILE = "spot_ifc_file"
PROP_PLAN_KIND = "spot_plan_kind"
PROP_LOCAL_BBOX = "spot_local_bbox"
PROP_LENGTH_UNIT = "spot_length_unit"
PROP_SORT_KEY = "spot_sort_key"
PROP_SOURCE_COLLECTIONS = "spot_source_collections"
PROP_PROXY_ROLE = "spot_proxy_role"
PROP_SHARED_ENTITY_KEY = "spot_shared_entity_key"
# Further BOT referents a space also `spot:represents`, beyond its own: a
# floor plan that stands for a storey, say. Each entry points at the source
# object or collection of a helper that carries the BOT class. The list sits
# on the space's own source, see `represents_link_owner`.
PROP_REPRESENTS_LINKS = "spot_represents_links"
PROP_ASSET_AXIS_X_DIRECTION = "spot_asset_axis_x_direction"
PROP_ASSET_AXIS_Y_DIRECTION = "spot_asset_axis_y_direction"
PROP_ASSET_AXIS_Z_DIRECTION = "spot_asset_axis_z_direction"

DOCUMENT_COLLECTION_CANDIDATES = ("DocumentSpace", "DocumentSpaces")
PROXY_ROLE_DOCUMENT = "document"
PROXY_ROLE_PHYSICAL = "physical"

APPEARANCE_TARGET_ASSET = "asset"
APPEARANCE_TARGET_ENTITY = "entity"

PREFIX_IFC_PROJECT = "IfcProject/"
PREFIX_IFC_SITE = "IfcSite/"
PREFIX_IFC_BUILDING = "IfcBuilding/"
PREFIX_IFC_STOREY = "IfcBuildingStorey/"
PREFIX_IFC_SPACE = "IfcSpace/"
PREFIX_IFC_TYPE_PRODUCT = "IfcTypeProduct"

NAME_TAG_BOT = "BOT"
NAME_TAG_SPOT = "SPOT"
NAME_TAG_BEO = "BEO"
NAME_TAG_DOT = "DOT"
NAME_TAG_IFC = "IFC"
NAME_TAG_PART = "PART"
SUPPORTED_NAME_TAGS = {
    NAME_TAG_BOT,
    NAME_TAG_SPOT,
    NAME_TAG_BEO,
    NAME_TAG_DOT,
    NAME_TAG_IFC,
    NAME_TAG_PART,
}

BOT_CLASS_BY_TOKEN = {
    BOT_CLASS_SITE.lower(): BOT_CLASS_SITE,
    BOT_CLASS_BUILDING.lower(): BOT_CLASS_BUILDING,
    BOT_CLASS_STOREY.lower(): BOT_CLASS_STOREY,
    BOT_CLASS_SPACE.lower(): BOT_CLASS_SPACE,
    BOT_CLASS_ELEMENT.lower(): BOT_CLASS_ELEMENT,
}

SPOT_CLASS_BY_TOKEN = {
    "assetspace": "AssetSpace",
    "entityspace": "EntitySpace",
    "areaspace": "AreaSpace",
    "horizontalareaspace": "HorizontalAreaSpace",
    "verticalareaspace": "VerticalAreaSpace",
    "volumespace": "VolumeSpace",
    "pointspace": "PointSpace",
    "documentspace": "DocumentSpace",
}

SEMANTIC_NAME_PREFIXES = tuple(
    sorted(
        SPOT_CLASS_BY_TOKEN.values(),
        key=len,
        reverse=True,
    )
)

ASSET_AXIS_DIRECTION_PROP_BY_AXIS = {
    "X": PROP_ASSET_AXIS_X_DIRECTION,
    "Y": PROP_ASSET_AXIS_Y_DIRECTION,
    "Z": PROP_ASSET_AXIS_Z_DIRECTION,
}

AM_TARGET_AXIS_ITEMS = (
    ("X", "X", "Map to the X axis of the reference space"),
    ("Y", "Y", "Map to the Y axis of the reference space"),
    ("Z", "Z", "Map to the Z axis of the reference space"),
)

AM_AXIS_RELATION_ITEMS = (
    (
        "Codirectional",
        "Codirectional",
        "Source axis points in the same direction as the target axis",
    ),
    (
        "Antiparallel",
        "Antiparallel",
        "Source axis points in the opposite direction of the target axis",
    ),
)

AM_ACCURACY_ITEMS = (
    ("Exact", "Exact", "The mapping matches the reference precisely"),
    ("Approximate", "Approximate", "The mapping is close but not exact"),
    ("Broad", "Broad", "The mapping is intentionally broad or coarse"),
)

AM_BOUNDARY_RELATION_ITEMS = (
    ("", "None", "Do not export a boundary-point mapping for this slot"),
    (
        "containedInFront",
        "containedInFront",
        "Contained in the front area of the target",
    ),
    ("containedInRear", "containedInRear", "Contained in the rear area of the target"),
    ("containedInLeft", "containedInLeft", "Contained in the left area of the target"),
    (
        "containedInRight",
        "containedInRight",
        "Contained in the right area of the target",
    ),
    ("containedInTop", "containedInTop", "Contained in the top area of the target"),
    (
        "containedInBottom",
        "containedInBottom",
        "Contained in the bottom area of the target",
    ),
    (
        "containedInVerticalCenter",
        "containedInVerticalCenter",
        "Contained in the vertical center area of the target",
    ),
    (
        "containedInTransversalCenter",
        "containedInTransversalCenter",
        "Contained in the transversal center area of the target",
    ),
    (
        "containedInLongitudinalCenter",
        "containedInLongitudinalCenter",
        "Contained in the longitudinal center area of the target",
    ),
    ("disjointFront", "disjointFront", "Disjoint in front of the target"),
    ("disjointRear", "disjointRear", "Disjoint behind the target"),
    ("disjointLeft", "disjointLeft", "Disjoint left of the target"),
    ("disjointRight", "disjointRight", "Disjoint right of the target"),
    ("disjointTop", "disjointTop", "Disjoint above the target"),
    ("disjointBottom", "disjointBottom", "Disjoint below the target"),
    ("meetFront", "meetFront", "Meets the front boundary of the target"),
    ("meetRear", "meetRear", "Meets the rear boundary of the target"),
    ("meetLeft", "meetLeft", "Meets the left boundary of the target"),
    ("meetRight", "meetRight", "Meets the right boundary of the target"),
    ("meetTop", "meetTop", "Meets the top boundary of the target"),
    ("meetBottom", "meetBottom", "Meets the bottom boundary of the target"),
)

PROP_AM_ENABLED = "spot_am_enabled"
PROP_AM_USE_NORMALIZED_COORDINATES = "spot_am_use_normalized_coordinates"
PROP_AM_REFERENCE_HELPER = "spot_am_reference_helper"
PROP_AM_REFERENCE_ASSET = "spot_am_reference_asset"
PROP_HELPER_AM_REFERENCE_ASSET = "spot_helper_am_reference_asset"
PROP_AM_AXIS_X_TARGET = "spot_am_axis_x_target"
PROP_AM_AXIS_Y_TARGET = "spot_am_axis_y_target"
PROP_AM_AXIS_Z_TARGET = "spot_am_axis_z_target"
PROP_AM_AXIS_X_RELATION = "spot_am_axis_x_relation"
PROP_AM_AXIS_Y_RELATION = "spot_am_axis_y_relation"
PROP_AM_AXIS_Z_RELATION = "spot_am_axis_z_relation"
PROP_AM_AXIS_X_ACCURACY = "spot_am_axis_x_accuracy"
PROP_AM_AXIS_Y_ACCURACY = "spot_am_axis_y_accuracy"
PROP_AM_AXIS_Z_ACCURACY = "spot_am_axis_z_accuracy"

BOUNDARY_POINT_SLOTS = (
    ("X_MIN", "X", "Min", "X min"),
    ("X_MAX", "X", "Max", "X max"),
    ("Y_MIN", "Y", "Min", "Y min"),
    ("Y_MAX", "Y", "Max", "Y max"),
    ("Z_MIN", "Z", "Min", "Z min"),
    ("Z_MAX", "Z", "Max", "Z max"),
)

PROP_AM_BOUNDARY_TARGET_BY_SLOT = {
    slot: f"spot_am_{slot.lower()}_target_helper"
    for slot, _axis, _kind, _label in BOUNDARY_POINT_SLOTS
}
PROP_AM_BOUNDARY_RELATION_BY_SLOT = {
    slot: f"spot_am_{slot.lower()}_relation"
    for slot, _axis, _kind, _label in BOUNDARY_POINT_SLOTS
}
PROP_AM_BOUNDARY_ACCURACY_BY_SLOT = {
    slot: f"spot_am_{slot.lower()}_accuracy"
    for slot, _axis, _kind, _label in BOUNDARY_POINT_SLOTS
}

PROP_AM_AXIS_TARGET_BY_AXIS = {
    "X": PROP_AM_AXIS_X_TARGET,
    "Y": PROP_AM_AXIS_Y_TARGET,
    "Z": PROP_AM_AXIS_Z_TARGET,
}
PROP_AM_AXIS_RELATION_BY_AXIS = {
    "X": PROP_AM_AXIS_X_RELATION,
    "Y": PROP_AM_AXIS_Y_RELATION,
    "Z": PROP_AM_AXIS_Z_RELATION,
}
PROP_AM_AXIS_ACCURACY_BY_AXIS = {
    "X": PROP_AM_AXIS_X_ACCURACY,
    "Y": PROP_AM_AXIS_Y_ACCURACY,
    "Z": PROP_AM_AXIS_Z_ACCURACY,
}

IFC_TO_BOT_CLASS = {
    "IfcSpace": BOT_CLASS_SPACE,
}

IFC_TO_BEO_CLASS = {
    "IfcBeam": "Beam",
    "IfcBuildingElement": "BuildingElement",
    "IfcBuildingElementPart": "BuildingElementPart",
    "IfcChimney": "Chimney",
    "IfcColumn": "Column",
    "IfcCovering": "Covering",
    "IfcCurtainWall": "CurtainWall",
    "IfcDoor": "Door",
    "IfcFooting": "Footing",
    "IfcMember": "Member",
    "IfcPile": "Pile",
    "IfcPlate": "Plate",
    "IfcRailing": "Railing",
    "IfcRamp": "Ramp",
    "IfcRampFlight": "RampFlight",
    "IfcRoof": "Roof",
    "IfcShadingDevice": "ShadingDevice",
    "IfcSlab": "Slab",
    "IfcStair": "Stair",
    "IfcWall": "Wall",
    "IfcWallStandardCase": "Wall",
    "IfcWindow": "Window",
}


@dataclass(frozen=True)
class Aabb:
    min_corner: Vector
    max_corner: Vector

    @property
    def center(self) -> Vector:
        return (self.min_corner + self.max_corner) * 0.5

    @property
    def size(self) -> Vector:
        return self.max_corner - self.min_corner

    def corners(self) -> list[Vector]:
        min_x, min_y, min_z = self.min_corner
        max_x, max_y, max_z = self.max_corner
        return [
            Vector((min_x, min_y, min_z)),
            Vector((min_x, min_y, max_z)),
            Vector((min_x, max_y, min_z)),
            Vector((min_x, max_y, max_z)),
            Vector((max_x, min_y, min_z)),
            Vector((max_x, min_y, max_z)),
            Vector((max_x, max_y, min_z)),
            Vector((max_x, max_y, max_z)),
        ]


@dataclass
class SpaceNodeSpec:
    role: str
    spot_class: str
    bot_class: str | None
    helper_name: str
    uri_suffix: str
    bot_uri_suffix: str | None
    label: str
    world_bbox: Aabb
    world_matrix: Matrix
    sort_key: str
    length_unit: str = LENGTH_UNIT_METER
    beo_class: str | None = None
    local_bbox: list[float] | None = None
    source_name: str | None = None
    source_collection: str | None = None
    source_ifc_class: str | None = None
    source_definition_id: int | None = None
    source_asset_path: str | None = None
    plan_kind: str | None = None
    # A point space located only approximately by SPOT-AM is drawn as the
    # region it may occupy rather than as a point marker.
    approximate_region: bool = False
    source_collections: list[str] = field(default_factory=list)
    children: list["SpaceNodeSpec"] = field(default_factory=list)


@dataclass(frozen=True)
class NameSemantics:
    raw_name: str
    base_name: str
    label: str
    ifc_class: str | None
    bot_class: str | None = None
    spot_class: str | None = None
    beo_class: str | None = None
    dot_class: str | None = None
    plan_kind: str | None = None
    asset_path: str | None = None
    proxy_role: str | None = None
    appearance_target: str | None = None
    shared_base_name: str | None = None
    shared_label: str | None = None
    shared_entity_key: str | None = None
    part_token: str | None = None


def blender_units_per_length_unit(length_unit: str) -> float:
    if length_unit == LENGTH_UNIT_PIXEL:
        return DOCUMENT_BLENDER_UNITS_PER_PIXEL
    return BLENDER_UNITS_PER_METER


def qudt_unit_term(length_unit: str) -> str:
    if length_unit == LENGTH_UNIT_PIXEL:
        return QUDT_UNIT_PIXEL
    return QUDT_UNIT_METER


def length_unit_scale_matrix(length_unit: str) -> Matrix:
    scale = blender_units_per_length_unit(length_unit)
    return Matrix.Diagonal((scale, scale, scale, 1.0))


def convert_matrix_between_units(
    matrix: Matrix,
    child_length_unit: str,
    parent_length_unit: str,
) -> Matrix:
    parent_from_blender = length_unit_scale_matrix(parent_length_unit).inverted_safe()
    blender_from_child = length_unit_scale_matrix(child_length_unit)
    return parent_from_blender @ matrix @ blender_from_child


def convert_bbox_from_blender_units(
    bbox: Sequence[float],
    length_unit: str,
) -> list[float]:
    scale = blender_units_per_length_unit(length_unit)
    if scale == 0:
        raise RuntimeError(f"Length unit '{length_unit}' has zero Blender scale")
    return [float(value) / scale for value in bbox]


def asset_axis_direction_prop_name(axis_name: str) -> str:
    try:
        return ASSET_AXIS_DIRECTION_PROP_BY_AXIS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported asset axis '{axis_name}'") from error


def default_asset_axis_direction(axis_name: str) -> str:
    try:
        return DEFAULT_ASSET_AXIS_DIRECTIONS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported asset axis '{axis_name}'") from error


def default_document_axis_direction(axis_name: str) -> str:
    try:
        return DEFAULT_DOCUMENT_AXIS_DIRECTIONS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported document axis '{axis_name}'") from error


def helper_asset_axis_direction(helper: bpy.types.Object, axis_name: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    prop_name = asset_axis_direction_prop_name(axis_name)
    value = getattr(owner, prop_name, None)
    if value:
        return str(value)
    return default_asset_axis_direction(axis_name)


def am_axis_target_prop_name(axis_name: str) -> str:
    try:
        return PROP_AM_AXIS_TARGET_BY_AXIS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM axis '{axis_name}'") from error


def am_axis_relation_prop_name(axis_name: str) -> str:
    try:
        return PROP_AM_AXIS_RELATION_BY_AXIS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM axis '{axis_name}'") from error


def am_axis_accuracy_prop_name(axis_name: str) -> str:
    try:
        return PROP_AM_AXIS_ACCURACY_BY_AXIS[axis_name]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM axis '{axis_name}'") from error


def am_boundary_target_prop_name(slot: str) -> str:
    try:
        return PROP_AM_BOUNDARY_TARGET_BY_SLOT[slot]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM boundary slot '{slot}'") from error


def am_boundary_relation_prop_name(slot: str) -> str:
    try:
        return PROP_AM_BOUNDARY_RELATION_BY_SLOT[slot]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM boundary slot '{slot}'") from error


def am_boundary_accuracy_prop_name(slot: str) -> str:
    try:
        return PROP_AM_BOUNDARY_ACCURACY_BY_SLOT[slot]
    except KeyError as error:
        raise RuntimeError(f"Unsupported AM boundary slot '{slot}'") from error


def helper_am_enabled(helper: bpy.types.Object) -> bool:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    return bool(getattr(owner, PROP_AM_ENABLED, False))


def spec_uses_directed_point_mesh(spec: SpaceNodeSpec) -> bool:
    if spec.spot_class != SPOT_CLASS_POINT:
        return False

    if spec.approximate_region:
        return False

    source_object = None
    if spec.source_name is not None:
        candidate = bpy.data.objects.get(spec.source_name)
        if candidate is not None and not candidate.get(PROP_HELPER):
            source_object = candidate

    if source_object is not None and source_object.type == "CAMERA":
        return True
    if source_object is not None and bool(
        getattr(source_object, PROP_AM_ENABLED, False)
    ):
        return False
    return True


def helper_uses_directed_point_mesh(helper: bpy.types.Object) -> bool:
    if helper.get(PROP_SPOT_CLASS) != SPOT_CLASS_POINT:
        return False

    source_object = find_source_object_for_helper(helper)
    if source_object is not None and source_object.type == "CAMERA":
        return True
    if helper_am_enabled(helper):
        return False
    return True


def helper_am_use_normalized_coordinates(helper: bpy.types.Object) -> bool:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    return bool(getattr(owner, PROP_AM_USE_NORMALIZED_COORDINATES, False))


def helper_am_reference_asset_enabled(helper: bpy.types.Object) -> bool:
    helper_override = helper.get(PROP_HELPER_AM_REFERENCE_ASSET)
    if helper_override is not None:
        return bool(helper_override)

    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    return bool(getattr(owner, PROP_AM_REFERENCE_ASSET, False))


def helper_am_reference_helper_name(helper: bpy.types.Object) -> str | None:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, PROP_AM_REFERENCE_HELPER, None)
    if value:
        return str(value)
    return None


def helper_am_axis_target(helper: bpy.types.Object, axis_name: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_axis_target_prop_name(axis_name), None)
    if value:
        return str(value)
    return axis_name


def helper_am_axis_relation(helper: bpy.types.Object, axis_name: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_axis_relation_prop_name(axis_name), None)
    if value:
        return str(value)
    return "Codirectional"


def helper_am_axis_accuracy(helper: bpy.types.Object, axis_name: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_axis_accuracy_prop_name(axis_name), None)
    if value:
        return str(value)
    return "Exact"


def helper_am_boundary_target_helper_name(
    helper: bpy.types.Object,
    slot: str,
) -> str | None:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_boundary_target_prop_name(slot), None)
    if value:
        return str(value)
    return None


def helper_am_boundary_relation(helper: bpy.types.Object, slot: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_boundary_relation_prop_name(slot), None)
    if value:
        return str(value)
    return ""


def helper_am_boundary_accuracy(helper: bpy.types.Object, slot: str) -> str:
    source_object = find_source_object_for_helper(helper)
    owner = source_object or helper
    value = getattr(owner, am_boundary_accuracy_prop_name(slot), None)
    if value:
        return str(value)
    return "Approximate"


def boundary_point_slot_details(slot: str) -> tuple[str, str, str]:
    for candidate_slot, axis_name, bound_kind, label in BOUNDARY_POINT_SLOTS:
        if candidate_slot == slot:
            return axis_name, bound_kind, label
    raise RuntimeError(f"Unsupported AM boundary slot '{slot}'")


@dataclass(frozen=True)
class AmBoundaryInterval:
    slot: str
    axis_name: str
    relation: str
    accuracy: str
    target_helper: bpy.types.Object
    lower_value: float
    upper_value: float


@dataclass(frozen=True)
class AmDerivedAxisRegion:
    axis_name: str
    lower_value: float
    upper_value: float
    lower_interval: AmBoundaryInterval
    upper_interval: AmBoundaryInterval


@dataclass(frozen=True)
class AmDerivedRegion:
    default_target: bpy.types.Object
    min_corner: Vector
    max_corner: Vector
    axis_regions: dict[str, AmDerivedAxisRegion]


def axis_index(axis_name: str) -> int:
    if axis_name == "X":
        return 0
    if axis_name == "Y":
        return 1
    return 2


def opposite_direction(direction: str) -> str | None:
    if direction == DIRECTION_FRONT:
        return DIRECTION_REAR
    if direction == DIRECTION_REAR:
        return DIRECTION_FRONT
    if direction == DIRECTION_LEFT:
        return DIRECTION_RIGHT
    if direction == DIRECTION_RIGHT:
        return DIRECTION_LEFT
    if direction == DIRECTION_TOP:
        return DIRECTION_BOTTOM
    if direction == DIRECTION_BOTTOM:
        return DIRECTION_TOP
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
    return {
        "X": default_asset_axis_direction("X"),
        "Y": default_asset_axis_direction("Y"),
        "Z": default_asset_axis_direction("Z"),
    }


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
        return frame_axis_for_direction(frame, DIRECTION_TOP)
    if relation_name.endswith("TransversalCenter"):
        return frame_axis_for_direction(frame, DIRECTION_RIGHT)
    if relation_name.endswith("LongitudinalCenter"):
        return frame_axis_for_direction(frame, DIRECTION_FRONT)
    return None


def relation_fraction_interval(
    relation_name: str,
    frame: dict[str, str],
) -> tuple[str, float, float] | None:
    axis_name = relation_axis_name(relation_name, frame)
    if axis_name is None:
        return None

    positive_direction = frame.get(axis_name, default_asset_axis_direction(axis_name))
    opposite = opposite_direction(positive_direction)

    if relation_name.startswith("containedIn"):
        if relation_name.endswith("VerticalCenter"):
            return axis_name, 1.0 / 3.0, 2.0 / 3.0
        if relation_name.endswith("TransversalCenter"):
            return axis_name, 1.0 / 3.0, 2.0 / 3.0
        if relation_name.endswith("LongitudinalCenter"):
            return axis_name, 1.0 / 3.0, 2.0 / 3.0
        for direction in (
            DIRECTION_FRONT,
            DIRECTION_REAR,
            DIRECTION_LEFT,
            DIRECTION_RIGHT,
            DIRECTION_TOP,
            DIRECTION_BOTTOM,
        ):
            if not relation_name.endswith(direction):
                continue
            if direction == positive_direction:
                return axis_name, 2.0 / 3.0, 1.0
            if direction == opposite:
                return axis_name, 0.0, 1.0 / 3.0
            return None

    if relation_name.startswith("meet"):
        for direction in (
            DIRECTION_FRONT,
            DIRECTION_REAR,
            DIRECTION_LEFT,
            DIRECTION_RIGHT,
            DIRECTION_TOP,
            DIRECTION_BOTTOM,
        ):
            if not relation_name.endswith(direction):
                continue
            if direction == positive_direction:
                return axis_name, 1.0, 1.0
            if direction == opposite:
                return axis_name, 0.0, 0.0
            return None

    return None


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


def resolve_am_reference_helper(helper: bpy.types.Object) -> bpy.types.Object:
    if helper_am_reference_asset_enabled(helper):
        return resolve_asset_space_helper(helper)

    reference_helper = resolve_helper_by_name(helper_am_reference_helper_name(helper))
    if reference_helper is not None:
        return reference_helper

    if helper.parent is None or not helper.parent.get(PROP_HELPER):
        raise RuntimeError(f"Helper '{helper.name}' is missing a helper parent")
    return helper.parent


def derive_am_region(
    helper: bpy.types.Object,
) -> tuple[AmDerivedRegion | None, list[str]]:
    if not helper_am_enabled(helper):
        return None, []

    warnings: list[str] = []
    try:
        default_target = resolve_am_reference_helper(helper)
    except RuntimeError as error:
        return None, [str(error)]

    default_target_box = object_world_aabb(default_target)
    if default_target_box is None:
        return None, [
            f"Could not derive SPOT-AM bounds for '{helper.name}' because reference helper '{default_target.name}' has no geometry"
        ]

    intervals_by_axis: dict[str, list[AmBoundaryInterval]] = {"X": [], "Y": [], "Z": []}
    for slot, _axis_name, _bound_kind, _slot_label in BOUNDARY_POINT_SLOTS:
        relation = helper_am_boundary_relation(helper, slot)
        if not relation:
            continue

        target_override = resolve_helper_by_name(
            helper_am_boundary_target_helper_name(helper, slot)
        )
        mapping_target = target_override or default_target
        target_box = object_world_aabb(mapping_target)
        if target_box is None:
            warnings.append(
                f"Skipped SPOT-AM boundary '{slot}' for '{helper.name}' because target '{mapping_target.name}' has no geometry"
            )
            continue

        frame = resolve_directional_frame(mapping_target)
        relation_interval = relation_fraction_interval(relation, frame)
        if relation_interval is None:
            warnings.append(
                f"Skipped SPOT-AM boundary '{slot}' for '{helper.name}' because relation '{relation}' is not supported for coarse helper bounds"
            )
            continue

        axis_name, lower_fraction, upper_fraction = relation_interval
        index = axis_index(axis_name)
        span = float(target_box.max_corner[index] - target_box.min_corner[index])
        lower_value = float(target_box.min_corner[index] + span * lower_fraction)
        upper_value = float(target_box.min_corner[index] + span * upper_fraction)
        intervals_by_axis[axis_name].append(
            AmBoundaryInterval(
                slot=slot,
                axis_name=axis_name,
                relation=relation,
                accuracy=helper_am_boundary_accuracy(helper, slot),
                target_helper=mapping_target,
                lower_value=lower_value,
                upper_value=upper_value,
            )
        )

    if not any(intervals_by_axis.values()):
        return None, warnings

    min_corner = default_target_box.min_corner.copy()
    max_corner = default_target_box.max_corner.copy()
    axis_regions: dict[str, AmDerivedAxisRegion] = {}
    for axis_name in ("X", "Y", "Z"):
        axis_intervals = intervals_by_axis[axis_name]
        if not axis_intervals:
            continue
        lower_interval = min(axis_intervals, key=lambda interval: interval.lower_value)
        upper_interval = max(axis_intervals, key=lambda interval: interval.upper_value)
        index = axis_index(axis_name)
        min_corner[index] = lower_interval.lower_value
        max_corner[index] = upper_interval.upper_value
        axis_regions[axis_name] = AmDerivedAxisRegion(
            axis_name=axis_name,
            lower_value=lower_interval.lower_value,
            upper_value=upper_interval.upper_value,
            lower_interval=lower_interval,
            upper_interval=upper_interval,
        )

    return (
        AmDerivedRegion(
            default_target=default_target,
            min_corner=min_corner,
            max_corner=max_corner,
            axis_regions=axis_regions,
        ),
        warnings,
    )


def infer_plan_kind_and_asset_path(base_name: str) -> tuple[str | None, str | None]:
    human = human_name(base_name)
    normalized = human.lower()
    if normalized.startswith("floorplan_"):
        file_stem = human[len("Floorplan_") :]
        if not file_stem:
            return PLAN_KIND_FLOORPLAN, None
        return PLAN_KIND_FLOORPLAN, f"data/plans/{file_stem}.png"
    if normalized.startswith("section_"):
        file_stem = human[len("Section_") :]
        if not file_stem:
            return PLAN_KIND_SECTION, None
        return PLAN_KIND_SECTION, f"data/plans/{file_stem}.png"

    semantic_spot_class, semantic_label = infer_semantic_spot_class_and_label(base_name)
    if semantic_spot_class == "HorizontalAreaSpace":
        # An area extracted from a drawing uses the containing DocumentSpace's
        # media. Do not invent one image filename per semantic area.
        return PLAN_KIND_FLOORPLAN, None
    if semantic_spot_class == "VerticalAreaSpace":
        return PLAN_KIND_SECTION, None
    if semantic_spot_class == "DocumentSpace":
        if not semantic_label:
            return None, None
        return None, f"data/plans/{semantic_label}.png"

    return None, None


def infer_picture_asset_path(base_name: str) -> str | None:
    human = human_name(base_name)
    semantic_spot_class, semantic_label = infer_semantic_spot_class_and_label(base_name)
    picture_name = semantic_label if semantic_spot_class == SPOT_CLASS_POINT else human
    normalized = picture_name.lower()
    if normalized.startswith("camera"):
        file_stem = picture_name[len("camera") :].lstrip(" _-:/")
    elif normalized.startswith("picture"):
        file_stem = picture_name[len("picture") :].lstrip(" _-:/")
        if file_stem:
            file_stem = f"picture-{file_stem.lstrip('-_ ')}"
    else:
        return None
    if not file_stem:
        return None

    stem_path = Path(file_stem)
    suffix = stem_path.suffix.lower()
    if suffix and suffix[1:].isdigit():
        # Blender duplicate suffixes like .001 are not file extensions.
        file_stem = stem_path.stem
        suffix = ""

    file_stem = file_stem.lower()

    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return f"data/pictures/{file_stem}"

    return f"data/pictures/{file_stem}.jpg"


def is_ifc_model_class(ifc_class: str | None) -> bool:
    """
    Whether an IFC class names a whole model rather than one element in one.

    A box tagged `::IFC=IfcProject` stands for a whole IFC file. That makes it
    the one helper whose origin is not the scene's to choose: it carries the
    IFC file's own frame, so an external tool reading the same file agrees
    with it.
    """
    return ifc_class == PREFIX_IFC_PROJECT.rstrip("/")


def infer_ifc_model_asset_path(base_name: str, ifc_class: str | None) -> str | None:
    """
    The IFC file a whole-model bounding box stands for.

    Only a box tagged `::IFC=IfcProject` names a model file; every other
    IFC-backed helper is one element inside such a model and shares the file
    rather than owning a distribution of its own.
    """
    if not is_ifc_model_class(ifc_class):
        return None
    _, semantic_label = infer_semantic_spot_class_and_label(base_name)
    file_stem = slugify(semantic_label or human_name(base_name))
    if not file_stem:
        return None
    return f"data/ifc/{file_stem}.ifc"


def ifc_model_file_path(file_name: str) -> str:
    """The source asset path of an IFC model file, from its file name."""
    return f"data/ifc/{Path(file_name).name}"


def object_ifc_model_asset_path(
    obj: bpy.types.Object,
    semantics: NameSemantics,
) -> str | None:
    """
    The IFC file a whole-model bounding box stands for.

    The label of a box comes from the `IfcProject` name, which is often an
    export default such as `Projekt-001` rather than the file name. An
    explicit `PROP_IFC_FILE` on the box therefore wins over the path inferred
    from the label.
    """
    if not is_ifc_model_class(semantics.ifc_class):
        return semantics.asset_path
    file_name = str(obj.get(PROP_IFC_FILE) or "").strip()
    if file_name:
        return ifc_model_file_path(file_name)
    return semantics.asset_path


def material_image_source_paths(material: bpy.types.Material) -> list[Path]:
    if not material.use_nodes or material.node_tree is None:
        return []

    preferred: list[Path] = []
    fallback: list[Path] = []

    for node in material.node_tree.nodes:
        if node.type != "TEX_IMAGE":
            continue

        image = getattr(node, "image", None)
        if image is None:
            continue

        raw_path = image.filepath_raw or image.filepath
        if not raw_path:
            continue

        source_path = Path(bpy.path.abspath(raw_path)).expanduser()
        if not source_path.exists():
            continue

        color_output = node.outputs.get("Color")
        if color_output is not None and color_output.is_linked:
            preferred.append(source_path)
            continue

        fallback.append(source_path)

    return preferred or fallback


def object_texture_asset_path(obj: bpy.types.Object) -> str | None:
    for material_slot in obj.material_slots:
        material = material_slot.material
        if material is None:
            continue

        for image_path in material_image_source_paths(material):
            # Local texture paths only provide a filename hint. Published RDF
            # resolves that filename below the central datasets/assets route,
            # so additional texture nodes or old source directories are not a
            # scene-build ambiguity.
            return image_path.name

    return None


def infer_proxy_role_and_shared_base_name(base_name: str) -> tuple[str | None, str]:
    human = human_name(base_name)
    match = re.match(r"^(?P<prefix>.+?)_(?P<role>DA|EA|AA)_(?P<suffix>.+)$", human)
    if match is None:
        return None, human

    role_token = match.group("role")
    shared_base_name = f"{match.group('prefix')}_{match.group('suffix')}"
    if role_token == "DA":
        return PROXY_ROLE_DOCUMENT, shared_base_name
    return PROXY_ROLE_PHYSICAL, shared_base_name


def infer_proxy_appearance_target(base_name: str) -> str | None:
    human = human_name(base_name)
    match = re.match(r"^(?P<prefix>.+?)_(?P<role>DA|EA|AA)_(?P<suffix>.+)$", human)
    if match is None:
        return None

    role_token = match.group("role")
    if role_token == "AA":
        return APPEARANCE_TARGET_ASSET
    if role_token == "EA":
        return APPEARANCE_TARGET_ENTITY
    return None


def blender_script_argv(argv: Sequence[str] | None = None) -> list[str]:
    values = list(sys.argv if argv is None else argv)
    if "--" not in values:
        return []
    separator = values.index("--")
    return values[separator + 1 :]


def split_name_and_tags(name: str) -> tuple[str, dict[str, str]]:
    parts = name.split("::")
    cut = len(parts)
    tags: dict[str, str] = {}

    while cut > 1:
        token = parts[cut - 1]
        if "=" not in token:
            break
        key, value = token.split("=", 1)
        normalized_key = key.strip().upper()
        if normalized_key not in SUPPORTED_NAME_TAGS:
            break
        tags[normalized_key] = value.strip()
        cut -= 1

    return "::".join(parts[:cut]), tags


def strip_name_tags(name: str) -> str:
    return split_name_and_tags(name)[0]


def strip_known_prefix(value: str, prefixes: Sequence[str]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def normalize_bot_class(value: str | None) -> str | None:
    if not value:
        return None
    normalized = strip_known_prefix(value.strip(), ("bot:",)).lower()
    return BOT_CLASS_BY_TOKEN.get(normalized)


def normalize_spot_class(value: str | None) -> str | None:
    if not value:
        return None
    normalized = strip_known_prefix(value.strip(), ("spot:",)).lower()
    return SPOT_CLASS_BY_TOKEN.get(normalized) or value.strip()


def normalize_beo_class(value: str | None) -> str | None:
    if not value:
        return None
    normalized = strip_known_prefix(value.strip(), ("beo:",))
    return normalized or None


def normalize_dot_class(value: str | None) -> str | None:
    if not value:
        return None
    normalized = strip_known_prefix(value.strip(), ("dot:",)).lower()
    return "Damage" if normalized == "damage" else None


def infer_dot_class(base_name: str) -> str | None:
    name = human_name(base_name)
    if re.match(r"^damage(?:$|[-_ /])", name, re.IGNORECASE):
        return "Damage"
    return None


def human_name(name: str) -> str:
    base_name = strip_name_tags(name)
    return base_name.rsplit("/", 1)[1] if "/" in base_name else base_name


def normalize_ifc_class(value: str | None) -> str | None:
    """
    The IFC class named by an explicit `::IFC=` tag.

    The value is kept as written apart from an optional `ifc:` prefix and
    surrounding space. IFC has hundreds of classes and this repository
    pins no list of them, so validating against one here would reject valid
    classes rather than catch typos.
    """
    if not value:
        return None
    normalized = strip_known_prefix(value.strip(), ("ifc:", "IFC:"))
    return normalized or None


def infer_ifc_class(name: str) -> str | None:
    base_name = strip_name_tags(name)
    if "/" not in base_name:
        return None
    prefix = base_name.split("/", 1)[0].strip()
    return prefix or None


def infer_semantic_spot_class_and_label(base_name: str) -> tuple[str | None, str]:
    human = human_name(base_name)
    if not human:
        return None, human

    for candidate in SEMANTIC_NAME_PREFIXES:
        if human == candidate:
            return candidate, human

        candidate_prefix = f"{candidate}_"
        if human.startswith(candidate_prefix):
            label = human[len(candidate_prefix) :]
            return candidate, label or human

    return None, human


def infer_bot_class(ifc_class: str | None) -> str | None:
    """
    The BOT class an IFC class states outright. Only `IfcSpace` does: a BOT
    class is never inferred from where a thing sits in the hierarchy.
    """
    if ifc_class is None:
        return None
    return IFC_TO_BOT_CLASS.get(ifc_class)


def infer_beo_class(ifc_class: str | None) -> str | None:
    if ifc_class is None:
        return None
    return IFC_TO_BEO_CLASS.get(ifc_class)


def normalize_part_token(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    return normalized or None


def parse_name_semantics(
    name: str,
    default_bot_class: str | None = None,
    default_spot_class: str | None = None,
    default_beo_class: str | None = None,
) -> NameSemantics:
    base_name, tags = split_name_and_tags(name)
    proxy_role, shared_base_name = infer_proxy_role_and_shared_base_name(base_name)
    appearance_target = infer_proxy_appearance_target(base_name)
    asset_base_name = shared_base_name if proxy_role is not None else base_name
    plan_kind, asset_path = infer_plan_kind_and_asset_path(asset_base_name)
    if asset_path is None:
        asset_path = infer_picture_asset_path(asset_base_name)
    # An explicit `::IFC=` tag beats the `IfcWall/...` prefix inference, so a
    # name that carries no IFC prefix can still declare its IFC class.
    ifc_class = normalize_ifc_class(tags.get(NAME_TAG_IFC)) or infer_ifc_class(
        base_name
    )
    if asset_path is None:
        asset_path = infer_ifc_model_asset_path(asset_base_name, ifc_class)
    semantic_spot_class, semantic_label = infer_semantic_spot_class_and_label(
        asset_base_name,
    )
    part_token = normalize_part_token(tags.get(NAME_TAG_PART))
    bot_class = (
        normalize_bot_class(tags.get(NAME_TAG_BOT))
        or infer_bot_class(ifc_class)
        or default_bot_class
    )
    spot_class = normalize_spot_class(tags.get(NAME_TAG_SPOT))
    if spot_class is None:
        if plan_kind == PLAN_KIND_FLOORPLAN:
            spot_class = "HorizontalAreaSpace"
        elif plan_kind == PLAN_KIND_SECTION:
            spot_class = "VerticalAreaSpace"
        elif asset_path is not None and asset_path.startswith("data/pictures/"):
            spot_class = SPOT_CLASS_POINT
        else:
            spot_class = semantic_spot_class or default_spot_class
    beo_class = (
        normalize_beo_class(tags.get(NAME_TAG_BEO))
        or default_beo_class
        or infer_beo_class(ifc_class)
    )
    if bot_class != BOT_CLASS_ELEMENT:
        beo_class = (
            None
            if NAME_TAG_BEO not in tags and default_beo_class is None
            else beo_class
        )
    dot_class = (
        normalize_dot_class(tags.get(NAME_TAG_DOT))
        or infer_dot_class(asset_base_name)
        or infer_dot_class(semantic_label)
    )
    if dot_class is not None:
        # Damage is not part of the building topology. An explicit `::BOT=`
        # tag or a caller's default would otherwise make the space represent
        # both a `dot:Damage` and a BOT entity, putting the damage into the
        # BOT hierarchy as though it were a room inside a room. Where the
        # damage sits is already said by its appearance: `spot:appearsIn` the
        # containing Volume Space, refined by SPOT-AM.
        bot_class = None

    if proxy_role is not None:
        shared_entity_key = slugify(shared_base_name)
    elif part_token is not None:
        shared_entity_key = slugify(base_name)
    else:
        shared_entity_key = None

    return NameSemantics(
        raw_name=name,
        base_name=base_name,
        label=semantic_label,
        ifc_class=ifc_class,
        bot_class=bot_class,
        spot_class=spot_class,
        beo_class=beo_class,
        dot_class=dot_class,
        plan_kind=plan_kind,
        asset_path=asset_path,
        proxy_role=proxy_role,
        appearance_target=appearance_target,
        shared_base_name=shared_base_name if proxy_role is not None else None,
        shared_label=(
            infer_semantic_spot_class_and_label(shared_base_name)[1]
            if proxy_role is not None
            else semantic_label if part_token is not None else None
        ),
        shared_entity_key=shared_entity_key,
        part_token=part_token,
    )


def append_name_tags(
    base_name: str,
    bot_class: str | None = None,
    spot_class: str | None = None,
    beo_class: str | None = None,
    part_token: str | None = None,
    dot_class: str | None = None,
    ifc_class: str | None = None,
) -> str:
    tags: list[str] = []
    if bot_class:
        tags.append(f"{NAME_TAG_BOT}={bot_class}")
    if spot_class:
        tags.append(f"{NAME_TAG_SPOT}={spot_class}")
    if beo_class:
        tags.append(f"{NAME_TAG_BEO}={beo_class}")
    if dot_class:
        tags.append(f"{NAME_TAG_DOT}={dot_class}")
    if ifc_class:
        tags.append(f"{NAME_TAG_IFC}={ifc_class}")
    if part_token:
        tags.append(f"{NAME_TAG_PART}={part_token}")
    if not tags:
        return base_name
    return f"{base_name}::{'::'.join(tags)}"


def slugify(text: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode(
            "ascii",
        )
    )
    lowered = normalized.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "unnamed"


def role_order(role: str) -> int:
    return {
        ROLE_SITE: 0,
        ROLE_DOCUMENT: 1,
        ROLE_BUILDING: 2,
        ROLE_MODEL: 3,
        ROLE_STOREY: 4,
        ROLE_SPACE: 5,
        ROLE_ELEMENT: 6,
    }.get(role, 99)


def format_number(value: float, precision: int = 6) -> str:
    rounded = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    if rounded == "-0":
        return "0"
    return rounded or "0"


def format_number_list(values: Iterable[float], precision: int = 6) -> str:
    return "[" + ",".join(format_number(value, precision) for value in values) + "]"


def matrix_to_column_major_values(matrix: Matrix) -> list[float]:
    return [matrix[row][column] for column in range(4) for row in range(4)]


def matrix_to_column_major_literal(matrix: Matrix, precision: int = 6) -> str:
    return format_number_list(matrix_to_column_major_values(matrix), precision)


def stringify_bbox(values: Sequence[float], precision: int = 6) -> str:
    return format_number_list(values, precision)


def quote_turtle_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def is_geometry_object(obj: bpy.types.Object) -> bool:
    return obj.type in GEOMETRY_OBJECT_TYPES


def has_collection_prefix(
    obj: bpy.types.Object,
    prefixes: Sequence[str],
) -> bool:
    names = [collection.name for collection in obj.users_collection]
    return any(any(name.startswith(prefix) for prefix in prefixes) for name in names)


def is_excluded_object(obj: bpy.types.Object) -> bool:
    if obj.get(PROP_HELPER):
        return True
    collection_names = [collection.name for collection in obj.users_collection]
    if any(name.startswith(PREFIX_IFC_TYPE_PRODUCT) for name in collection_names):
        return True
    if obj.type == "CAMERA":
        return False
    if not is_geometry_object(obj):
        return True
    return False


def object_world_aabb(obj: bpy.types.Object) -> Aabb | None:
    if not is_geometry_object(obj) or not hasattr(obj, "bound_box"):
        return None

    try:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    except Exception:
        return None

    min_corner = Vector(
        tuple(min(corner[index] for corner in corners) for index in range(3))
    )
    max_corner = Vector(
        tuple(max(corner[index] for corner in corners) for index in range(3))
    )
    if any(max_corner[index] < min_corner[index] for index in range(3)):
        return None
    return Aabb(min_corner=min_corner, max_corner=max_corner)


def object_local_bbox(obj: bpy.types.Object) -> list[float] | None:
    if not is_geometry_object(obj) or not hasattr(obj, "bound_box"):
        return None

    try:
        corners = [Vector(corner) for corner in obj.bound_box]
    except Exception:
        return None

    min_corner = [min(corner[index] for corner in corners) for index in range(3)]
    max_corner = [max(corner[index] for corner in corners) for index in range(3)]
    if any(max_corner[index] < min_corner[index] for index in range(3)):
        return None
    return [*min_corner, *max_corner]


def union_aabbs(boxes: Iterable[Aabb]) -> Aabb | None:
    items = list(boxes)
    if not items:
        return None

    min_corner = Vector(
        tuple(min(box.min_corner[index] for box in items) for index in range(3)),
    )
    max_corner = Vector(
        tuple(max(box.max_corner[index] for box in items) for index in range(3)),
    )
    return Aabb(min_corner=min_corner, max_corner=max_corner)


def overlap_length(min_a: float, max_a: float, min_b: float, max_b: float) -> float:
    return max(0.0, min(max_a, max_b) - max(min_a, min_b))


def overlap_volume(left: Aabb, right: Aabb) -> float:
    overlap_x = overlap_length(
        left.min_corner.x,
        left.max_corner.x,
        right.min_corner.x,
        right.max_corner.x,
    )
    overlap_y = overlap_length(
        left.min_corner.y,
        left.max_corner.y,
        right.min_corner.y,
        right.max_corner.y,
    )
    overlap_z = overlap_length(
        left.min_corner.z,
        left.max_corner.z,
        right.min_corner.z,
        right.max_corner.z,
    )
    return overlap_x * overlap_y * overlap_z


def distance_point_to_aabb(point: Vector, box: Aabb) -> float:
    squared = 0.0
    for index in range(3):
        value = point[index]
        if value < box.min_corner[index]:
            squared += (box.min_corner[index] - value) ** 2
        elif value > box.max_corner[index]:
            squared += (value - box.max_corner[index]) ** 2
    return squared**0.5


def local_bbox_from_world_bbox(
    world_bbox: Aabb, helper_world_matrix: Matrix
) -> list[float]:
    inverse_matrix = helper_world_matrix.inverted()
    local_corners = [inverse_matrix @ corner for corner in world_bbox.corners()]
    min_corner = [min(corner[index] for corner in local_corners) for index in range(3)]
    max_corner = [max(corner[index] for corner in local_corners) for index in range(3)]
    return [*min_corner, *max_corner]


def make_world_matrix(location: Vector, rotation: Quaternion | None = None) -> Matrix:
    matrix = (rotation or Quaternion((1.0, 0.0, 0.0, 0.0))).to_matrix().to_4x4()
    matrix.translation = location
    return matrix


def frame_without_scale(matrix: Matrix) -> Matrix:
    """A source object's location and rotation, without its scale.

    A helper's box is written out in metres as it stands. A scaled source, say
    a default cube stretched to a bridge's length, would otherwise hand its
    scale to the helper, which keeps a 2 m box inside a stretched frame: the
    export then states a 2 m bridge, and every child inherits the stretch.
    """
    location, rotation, _scale = matrix.decompose()
    return make_world_matrix(location, rotation)


def scaled_local_bbox(local_bbox: list[float], matrix: Matrix) -> list[float]:
    """Carry a source object's scale from its frame into its local box."""
    scale = matrix.to_scale()
    corners = [
        [local_bbox[index] * scale[index], local_bbox[index + 3] * scale[index]]
        for index in range(3)
    ]
    return [min(pair) for pair in corners] + [max(pair) for pair in corners]


def ensure_collection(
    scene: bpy.types.Scene,
    name: str,
) -> bpy.types.Collection:
    existing = bpy.data.collections.get(name)
    if existing is not None:
        return existing

    created = bpy.data.collections.new(name)
    scene.collection.children.link(created)
    return created


def remove_collection_with_objects(collection: bpy.types.Collection) -> None:
    for child in list(collection.children):
        remove_collection_with_objects(child)
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(collection)


def reset_helper_collection(
    scene: bpy.types.Scene,
    name: str = HELPER_COLLECTION_NAME,
) -> bpy.types.Collection:
    existing = bpy.data.collections.get(name)
    if existing is not None:
        remove_collection_with_objects(existing)
    collection = bpy.data.collections.new(name)
    collection[HELPER_COLLECTION_MARKER] = True
    collection[PROP_SCHEMA_VERSION] = HELPER_SCHEMA_VERSION
    scene.collection.children.link(collection)
    return collection


def collection_descendants(
    collection: bpy.types.Collection,
) -> list[bpy.types.Collection]:
    descendants: list[bpy.types.Collection] = []
    stack = [collection]
    while stack:
        current = stack.pop()
        descendants.append(current)
        stack.extend(reversed(list(current.children)))
    return descendants


def find_project_collection(
    scene: bpy.types.Scene,
    explicit_name: str | None = None,
) -> bpy.types.Collection:
    if explicit_name:
        collection = bpy.data.collections.get(explicit_name)
        if collection is None:
            raise RuntimeError(f"Project collection '{explicit_name}' was not found")
        return collection

    for collection in collection_descendants(scene.collection):
        if collection.name.startswith(PREFIX_IFC_PROJECT):
            return collection

    raise RuntimeError("No IFC project collection was found in the open Blender scene")


def collection_object_named_like_collection(
    collection: bpy.types.Collection,
) -> bpy.types.Object | None:
    exact = bpy.data.objects.get(collection.name)
    if exact is not None:
        return exact
    for obj in collection.objects:
        if obj.type == "EMPTY":
            return obj
    return None


def get_ifc_definition_id(obj: bpy.types.Object | None) -> int | None:
    if obj is None:
        return None
    properties = obj.get("BIMObjectProperties")
    if properties is None or not hasattr(properties, "keys"):
        return None
    value = properties.get("ifc_definition_id")
    if isinstance(value, int):
        return value
    return None


def source_definition_id_for_helper(helper: bpy.types.Object) -> int | None:
    value = helper.get(PROP_SOURCE_DEFINITION_ID)
    if isinstance(value, int):
        return value
    return None


def source_name_for_helper(helper: bpy.types.Object) -> str | None:
    value = helper.get(PROP_SOURCE_NAME)
    if value:
        return str(value)
    return None


def helper_matches_source_object(
    helper: bpy.types.Object,
    source_object: bpy.types.Object,
) -> bool:
    helper_definition_id = source_definition_id_for_helper(helper)
    source_definition_id = get_ifc_definition_id(source_object)
    if (
        helper_definition_id is not None
        and source_definition_id is not None
        and helper_definition_id == source_definition_id
    ):
        return True

    helper_source_name = source_name_for_helper(helper)
    return helper_source_name == source_object.name


def find_source_object_for_helper(
    helper: bpy.types.Object,
) -> bpy.types.Object | None:
    if not helper.get(PROP_HELPER):
        return None

    helper_definition_id = source_definition_id_for_helper(helper)
    if helper_definition_id is not None:
        for candidate in bpy.data.objects:
            if candidate.get(PROP_HELPER):
                continue
            if get_ifc_definition_id(candidate) == helper_definition_id:
                return candidate

    helper_source_name = source_name_for_helper(helper)
    if helper_source_name:
        candidate = bpy.data.objects.get(helper_source_name)
        if candidate is not None and not candidate.get(PROP_HELPER):
            return candidate

    return None


def helper_source_collection_root(
    helper: bpy.types.Object,
) -> bpy.types.Collection | None:
    """The collection a helper was built from, when that collection is its root."""
    source_name = helper.get(PROP_SOURCE_NAME)
    if not source_name or helper.get(PROP_SOURCE_COLLECTION) != source_name:
        return None
    return bpy.data.collections.get(str(source_name))


def represents_link_owner(
    helper: bpy.types.Object,
) -> bpy.types.Object | bpy.types.Collection:
    """
    Where a helper's represents links live: its source object, else the
    collection it was built from, so the links survive a helper rebuild. A
    helper with neither, such as an imported one, holds them itself.
    """
    return (
        find_source_object_for_helper(helper)
        or helper_source_collection_root(helper)
        or helper
    )


def find_helper_for_source_object(
    source_object: bpy.types.Object,
    helper_collection: bpy.types.Collection | None = None,
) -> bpy.types.Object | None:
    candidates = (
        helper_collection.objects if helper_collection is not None else bpy.data.objects
    )
    for candidate in candidates:
        if not candidate.get(PROP_HELPER):
            continue
        if helper_matches_source_object(candidate, source_object):
            return candidate
    return None


def short_identifier(
    definition_id: int | None,
    fallback: str,
) -> str:
    return str(definition_id) if definition_id is not None else slugify(fallback)


def ensure_unique_name(base_name: str, taken: set[str]) -> str:
    candidate = base_name
    index = 2
    while candidate in taken:
        candidate = f"{base_name}.{index:03d}"
        index += 1
    taken.add(candidate)
    return candidate


def build_container_identifiers(
    role: str,
    label: str,
    definition_id: int | None,
    index: int | None = None,
    bot_class: str | None = None,
    spot_class: str | None = None,
    part_token: str | None = None,
) -> tuple[str, str]:
    slug = slugify(label)
    identifier = short_identifier(definition_id, label)
    part_slug = slugify(part_token) if part_token else None
    helper_part_suffix = f"-part-{part_slug}" if part_slug else ""
    uri_part_suffix = f"_part_{part_slug}" if part_slug else ""
    if role == ROLE_SITE:
        return (
            append_name_tags(
                f"SPOT::Entity::Site::{identifier}-{slug}{helper_part_suffix}",
                bot_class=bot_class or BOT_CLASS_SITE,
                spot_class=spot_class or SPOT_CLASS_ENTITY,
                part_token=part_token,
            ),
            f"site_{identifier}_{slug}{uri_part_suffix}",
        )
    if role == ROLE_DOCUMENT:
        return (
            append_name_tags(
                f"SPOT::Document::Root::{identifier}-{slug}{helper_part_suffix}",
                spot_class=spot_class or "DocumentSpace",
                part_token=part_token,
            ),
            f"document_{identifier}_{slug}{uri_part_suffix}",
        )
    if role == ROLE_BUILDING:
        return (
            append_name_tags(
                f"SPOT::Asset::Building::{identifier}-{slug}{helper_part_suffix}",
                bot_class=bot_class or BOT_CLASS_BUILDING,
                spot_class=spot_class or SPOT_CLASS_ASSET,
                part_token=part_token,
            ),
            f"building_{identifier}_{slug}{uri_part_suffix}",
        )
    if role == ROLE_MODEL:
        return (
            append_name_tags(
                f"SPOT::Entity::Model::{identifier}-{slug}{helper_part_suffix}",
                bot_class=bot_class,
                spot_class=spot_class or SPOT_CLASS_VOLUME,
                part_token=part_token,
            ),
            f"model_{identifier}_{slug}{uri_part_suffix}",
        )
    if role == ROLE_STOREY:
        prefix = f"{index:02d}-" if index is not None else ""
        return (
            append_name_tags(
                f"SPOT::Entity::Storey::{prefix}{identifier}-{slug}{helper_part_suffix}",
                bot_class=bot_class or BOT_CLASS_STOREY,
                spot_class=spot_class or SPOT_CLASS_VOLUME,
                part_token=part_token,
            ),
            f"storey_{prefix}{identifier}_{slug}{uri_part_suffix}".replace("-", "_"),
        )
    if role == ROLE_SPACE:
        return (
            append_name_tags(
                f"SPOT::Entity::Space::{identifier}-{slug}{helper_part_suffix}",
                bot_class=bot_class,
                spot_class=spot_class or SPOT_CLASS_VOLUME,
                part_token=part_token,
            ),
            f"space_{identifier}_{slug}{uri_part_suffix}",
        )
    raise ValueError(f"Unsupported container role: {role}")


def build_element_identifiers(
    source_name: str,
    ifc_class: str | None,
    definition_id: int | None,
    bot_class: str | None = None,
    spot_class: str | None = None,
    beo_class: str | None = None,
    part_token: str | None = None,
) -> tuple[str, str]:
    source_label = human_name(source_name)
    object_slug = slugify(source_label)
    identifier = short_identifier(definition_id, source_label)
    part_slug = slugify(part_token) if part_token else None
    helper_part_suffix = f"-part-{part_slug}" if part_slug else ""
    uri_part_suffix = f"_part_{part_slug}" if part_slug else ""
    return (
        append_name_tags(
            f"SPOT::Entity::Element::{identifier}-{object_slug}{helper_part_suffix}",
            bot_class=bot_class,
            spot_class=spot_class or SPOT_CLASS_VOLUME,
            beo_class=beo_class,
            part_token=part_token,
        ),
        f"element_{identifier}_{object_slug}{uri_part_suffix}",
    )


def helper_display_size(box: Aabb) -> float:
    size = max(box.size.x, box.size.y, box.size.z)
    if size <= 0:
        return 0.25
    return max(size * 0.05, 0.25)


def bbox_vertices(local_bbox: Sequence[float]) -> list[tuple[float, float, float]]:
    min_x, min_y, min_z, max_x, max_y, max_z = [float(value) for value in local_bbox]
    return [
        (min_x, min_y, min_z),
        (min_x, min_y, max_z),
        (min_x, max_y, min_z),
        (min_x, max_y, max_z),
        (max_x, min_y, min_z),
        (max_x, min_y, max_z),
        (max_x, max_y, min_z),
        (max_x, max_y, max_z),
    ]


def bbox_edges() -> list[tuple[int, int]]:
    return [
        (0, 1),
        (0, 2),
        (0, 4),
        (1, 3),
        (1, 5),
        (2, 3),
        (2, 6),
        (3, 7),
        (4, 5),
        (4, 6),
        (5, 7),
        (6, 7),
    ]


def create_bbox_mesh(
    mesh_name: str,
    local_bbox: Sequence[float],
) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.from_pydata(bbox_vertices(local_bbox), bbox_edges(), [])
    mesh.update()
    return mesh


def create_pointspace_mesh(mesh_name: str, length: float = 0.8) -> bpy.types.Mesh:
    mesh = bpy.data.meshes.new(mesh_name)
    arrow_head_offset = max(length * 0.22, 0.1)
    arrow_head_half_width = max(length * 0.1, 0.05)
    tip_z = -length
    shoulder_z = tip_z + arrow_head_offset
    vertices = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, tip_z),
        (arrow_head_half_width, 0.0, shoulder_z),
        (-arrow_head_half_width, 0.0, shoulder_z),
        (0.0, arrow_head_half_width, shoulder_z),
        (0.0, -arrow_head_half_width, shoulder_z),
    ]
    edges = [
        (0, 1),
        (1, 2),
        (1, 3),
        (1, 4),
        (1, 5),
    ]
    mesh.from_pydata(vertices, edges, [])
    mesh.update()
    return mesh


def placeholder_local_bbox(
    half_extent: float = HELPER_PLACEHOLDER_HALF_EXTENT,
) -> list[float]:
    return [
        -half_extent,
        -half_extent,
        -half_extent,
        half_extent,
        half_extent,
        half_extent,
    ]


def create_helper_object(
    collection: bpy.types.Collection,
    spec: SpaceNodeSpec,
    parent: bpy.types.Object | None = None,
    parent_world_matrix: Matrix | None = None,
) -> bpy.types.Object:
    stored_local_bbox = list(spec.local_bbox) if spec.local_bbox is not None else None
    mesh_local_bbox = stored_local_bbox
    if spec_uses_directed_point_mesh(spec):
        helper_mesh = create_pointspace_mesh(f"{spec.helper_name}::point")
    else:
        if mesh_local_bbox is None:
            mesh_local_bbox = placeholder_local_bbox()
        helper_mesh = create_bbox_mesh(f"{spec.helper_name}::bbox", mesh_local_bbox)
    helper = bpy.data.objects.new(spec.helper_name, helper_mesh)
    helper.display_type = "WIRE"
    helper.hide_render = True
    collection.objects.link(helper)

    if parent is None:
        helper.matrix_world = spec.world_matrix.copy()
    else:
        helper.parent = parent
        helper.matrix_parent_inverse = Matrix.Identity(4)
        if parent_world_matrix is None:
            raise RuntimeError(
                f"Missing parent_world_matrix while creating helper '{spec.helper_name}'",
            )
        helper.matrix_basis = parent_world_matrix.inverted_safe() @ spec.world_matrix

    helper[PROP_HELPER] = True
    helper[PROP_SCHEMA_VERSION] = HELPER_SCHEMA_VERSION
    helper[PROP_ROLE] = spec.role
    helper[PROP_SPOT_CLASS] = spec.spot_class
    if spec.bot_class is not None:
        helper[PROP_BOT_CLASS] = spec.bot_class
    if spec.beo_class is not None:
        helper[PROP_BEO_CLASS] = spec.beo_class
    semantic_source_name = spec.source_name or spec.label
    dot_class = parse_name_semantics(semantic_source_name).dot_class
    if dot_class is not None:
        helper[PROP_DOT_CLASS] = dot_class
    helper[PROP_URI_SUFFIX] = spec.uri_suffix
    if spec.bot_uri_suffix is not None:
        helper[PROP_BOT_URI_SUFFIX] = spec.bot_uri_suffix
    helper[PROP_LABEL] = spec.label
    helper[PROP_LENGTH_UNIT] = spec.length_unit
    helper[PROP_SORT_KEY] = spec.sort_key
    if stored_local_bbox is not None:
        helper[PROP_LOCAL_BBOX] = stored_local_bbox
    if spec.spot_class == SPOT_CLASS_ASSET:
        for axis_name in ("X", "Y", "Z"):
            setattr(
                helper,
                asset_axis_direction_prop_name(axis_name),
                default_asset_axis_direction(axis_name),
            )

    am_reference_asset = False
    if spec.source_name is not None:
        appearance_target = infer_proxy_appearance_target(spec.source_name)
        if appearance_target == APPEARANCE_TARGET_ASSET:
            am_reference_asset = True
        elif appearance_target != APPEARANCE_TARGET_ENTITY:
            source_object = bpy.data.objects.get(spec.source_name)
            if source_object is not None and not source_object.get(PROP_HELPER):
                am_reference_asset = bool(
                    getattr(source_object, PROP_AM_REFERENCE_ASSET, False)
                )
    helper[PROP_HELPER_AM_REFERENCE_ASSET] = am_reference_asset

    if spec.source_name is not None:
        helper[PROP_SOURCE_NAME] = spec.source_name
    if spec.source_collection is not None:
        helper[PROP_SOURCE_COLLECTION] = spec.source_collection
    if spec.source_ifc_class is not None:
        helper[PROP_SOURCE_IFC_CLASS] = spec.source_ifc_class
    if spec.source_definition_id is not None:
        helper[PROP_SOURCE_DEFINITION_ID] = spec.source_definition_id
    if spec.source_asset_path is not None:
        helper[PROP_SOURCE_ASSET_PATH] = spec.source_asset_path
    elif PROP_SOURCE_ASSET_PATH in helper:
        # Rebuilding must remove stale inferred media references when a space
        # is now correctly represented by its containing DocumentSpace.
        del helper[PROP_SOURCE_ASSET_PATH]
    if spec.plan_kind is not None:
        helper[PROP_PLAN_KIND] = spec.plan_kind
    if spec.source_name is not None:
        semantics = parse_name_semantics(spec.source_name)
        if semantics.proxy_role is not None:
            helper[PROP_PROXY_ROLE] = semantics.proxy_role
        if semantics.shared_entity_key is not None:
            helper[PROP_SHARED_ENTITY_KEY] = semantics.shared_entity_key
    if spec.source_collections:
        helper[PROP_SOURCE_COLLECTIONS] = json.dumps(
            spec.source_collections, ensure_ascii=True
        )

    return helper


def helper_local_bbox(helper: bpy.types.Object) -> list[float]:
    raw = helper.get(PROP_LOCAL_BBOX)
    if raw is None:
        raise RuntimeError(
            f"Helper object '{helper.name}' is missing {PROP_LOCAL_BBOX}"
        )
    return [float(value) for value in raw]


def helper_children(parent: bpy.types.Object) -> list[bpy.types.Object]:
    children = [child for child in parent.children if child.get(PROP_HELPER)]
    return sorted(children, key=lambda child: child.get(PROP_SORT_KEY, child.name))


def helper_root_objects(collection: bpy.types.Collection) -> list[bpy.types.Object]:
    roots = [
        obj for obj in collection.objects if obj.get(PROP_HELPER) and obj.parent is None
    ]
    return sorted(roots, key=lambda obj: obj.get(PROP_SORT_KEY, obj.name))


def default_output_path(suffix: str = "_spot.ttl") -> Path:
    mainfile = Path(bpy.data.filepath) if bpy.data.filepath else None
    if mainfile is None or str(mainfile) == "":
        return Path.cwd() / f"scene{suffix}"
    return mainfile.with_name(f"{mainfile.stem}{suffix}")
