from __future__ import annotations

import traceback
from pathlib import Path

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

from .bcf_authoring import (
    PROP_BCF_COMMENT_TEXT,
    PROP_BCF_COMPONENTS,
    PROP_BCF_DESCRIPTION,
    PROP_BCF_HAS_BITMAP,
    PROP_BCF_HAS_CLIPPING_PLANE,
    PROP_BCF_HAS_COLORING,
    PROP_BCF_HAS_COMMENT,
    PROP_BCF_HAS_COMPONENT_SELECTION,
    PROP_BCF_HAS_LINE,
    PROP_BCF_HAS_STATUS_HISTORY,
    PROP_BCF_ISSUE_ENABLED,
    PROP_BCF_TITLE,
    PROP_OBSERVED_DAMAGES,
    SPOT_PG_object_reference,
    helper_for_reference,
    is_bot_referent_object,
    is_component_object,
    is_damage_object,
    normalize_reference_object,
    register_object_properties,
    resolve_picture_owner_and_helper,
    unregister_object_properties,
)
from .build_spot_scene import (
    build_helper_scene,
    build_ifc_bounding_box,
    descendant_role_collections,
    find_ifc_bounding_box,
    find_ifc_project_collections,
    ifc_bounding_box_source_objects,
    ifc_bounding_box_target_collection,
)
from .export_spot_ttl import (
    DEFAULT_BASE_IRI,
    DEFAULT_LOCAL_PREFIX,
    EXPORT_PROFILE_SCENE_GRAPH,
    EXPORT_PROFILE_SPOT_ONLY,
    LEGACY_DEFAULT_BASE_IRI,
    LEGACY_HASH_BASE_IRI,
    default_output_path_for_profile,
    export_helper_scene,
)
from .import_spot_ttl import import_scene_graph_turtle
from .spot_transition import (
    PROP_SG_STATE,
    commit_sg_placements,
    is_placed_by_spot_am,
    read_log,
    write_transition_dataset,
)
from .dataset_snapshot import export_dataset_snapshot
from .topology_setup import (
    BOT_SETUP_CLASSES,
    BROT_SETUP_CLASSES,
    VOCABULARY_BOT,
    VOCABULARY_BROT,
    helper_camera,
    helper_topology_label,
    make_helper_camera,
    set_helper_topology_class,
)
from .export_spatial_history import (
    DEFAULT_HISTORY_START,
    PROP_SIMULATE_PLACEMENT_CHANGE,
    eligible_camera,
    eligible_plan,
    placement_change_owners,
)
from .spot_blender_common import (
    AM_ACCURACY_ITEMS,
    AM_AXIS_RELATION_ITEMS,
    AM_BOUNDARY_RELATION_ITEMS,
    AM_TARGET_AXIS_ITEMS,
    ASSET_AXIS_DIRECTION_ITEMS,
    BOUNDARY_POINT_SLOTS,
    HELPER_COLLECTION_MARKER,
    HELPER_COLLECTION_NAME,
    PROP_AM_ENABLED,
    PROP_IFC_FILE,
    PROP_SOURCE_ASSET_PATH,
    PROP_AM_USE_NORMALIZED_COORDINATES,
    PROP_AM_REFERENCE_ASSET,
    PROP_AM_REFERENCE_HELPER,
    PROP_ASSET_AXIS_X_DIRECTION,
    PROP_ASSET_AXIS_Y_DIRECTION,
    PROP_ASSET_AXIS_Z_DIRECTION,
    PROP_HELPER,
    PROP_LABEL,
    PROP_LOCAL_BBOX,
    PROP_PROXY_ROLE,
    PROP_REPRESENTS_LINKS,
    PROP_ROLE,
    PROP_SORT_KEY,
    represents_link_owner,
    PROP_SOURCE_COLLECTION,
    PROP_SPOT_CLASS,
    PROP_URI_SUFFIX,
    PROXY_ROLE_DOCUMENT,
    ROLE_DOCUMENT,
    ROLE_SITE,
    SPOT_CLASS_ASSET,
    SPOT_CLASS_ENTITY,
    SPOT_CLASS_POINT,
    Aabb,
    am_axis_accuracy_prop_name,
    am_axis_relation_prop_name,
    am_axis_target_prop_name,
    am_boundary_accuracy_prop_name,
    am_boundary_relation_prop_name,
    am_boundary_target_prop_name,
    boundary_point_slot_details,
    bbox_edges,
    bbox_vertices,
    collection_object_named_like_collection,
    distance_point_to_aabb,
    default_asset_axis_direction,
    find_helper_for_source_object,
    find_project_collection,
    find_source_object_for_helper,
    ifc_model_file_path,
    is_ifc_model_class,
    format_number,
    helper_am_enabled,
    human_name,
    helper_children,
    helper_local_bbox,
    helper_root_objects,
    local_bbox_from_world_bbox,
    parse_name_semantics,
    overlap_volume,
)

bl_info = {
    "name": "Spatial-Semantic Dataset Tools",
    "author": "Oliver Schulz",
    "version": (0, 2, 0),
    "blender": (5, 1, 0),
    "location": "F3 Search, 3D View Sidebar > SPOT",
    "description": "Author SPOT scenes and the multimodal building data that they anchor, and export them as RDF",
    "category": "Import-Export",
}


ORIGIN_ANCHOR_ITEMS = (
    (
        "CENTER",
        "Center",
        "Project from the asset-space bbox center in local X/Y",
    ),
    (
        "MIN_X_MIN_Y",
        "Min X / Min Y",
        "Project from the local minimum X and minimum Y corner",
    ),
    (
        "MIN_X_MAX_Y",
        "Min X / Max Y",
        "Project from the local minimum X and maximum Y corner",
    ),
    (
        "MAX_X_MIN_Y",
        "Max X / Min Y",
        "Project from the local maximum X and minimum Y corner",
    ),
    (
        "MAX_X_MAX_Y",
        "Max X / Max Y",
        "Project from the local maximum X and maximum Y corner",
    ),
)

SPOT_TEST_RESULTS_TEXT_NAME = "SPOT Test Results"
SPOT_TEST_EPSILON = 1e-6


def resolve_asset_root_helper(helper_collection_name: str) -> bpy.types.Object:
    collection = bpy.data.collections.get(helper_collection_name)
    if collection is None:
        raise RuntimeError(
            f"Helper collection '{helper_collection_name}' was not found"
        )
    if not collection.get(HELPER_COLLECTION_MARKER):
        raise RuntimeError(
            f"Collection '{helper_collection_name}' does not look like a SPOT helper collection",
        )

    roots = helper_root_objects(collection)
    asset_roots = [
        root
        for root in roots
        if root.get(PROP_ROLE) != ROLE_DOCUMENT
        and root.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
    ]
    if len(asset_roots) != 1:
        raise RuntimeError(
            f"Expected exactly one asset helper root in '{collection.name}', found {len(asset_roots)}",
        )

    return asset_roots[0]


def helper_world_bbox(helper: bpy.types.Object) -> Aabb:
    corners = [
        helper.matrix_world @ Vector(corner)
        for corner in bbox_vertices(helper_local_bbox(helper))
    ]
    return Aabb(
        min_corner=Vector(
            tuple(min(corner[index] for corner in corners) for index in range(3)),
        ),
        max_corner=Vector(
            tuple(max(corner[index] for corner in corners) for index in range(3)),
        ),
    )


def iter_helper_descendants(parent: bpy.types.Object) -> list[bpy.types.Object]:
    descendants: list[bpy.types.Object] = []
    for child in helper_children(parent):
        descendants.append(child)
        descendants.extend(iter_helper_descendants(child))
    return descendants


def project_origin_local_point(
    local_bbox: list[float],
    anchor: str,
) -> Vector:
    min_x, min_y, min_z, max_x, max_y, max_z = [float(value) for value in local_bbox]
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    center_z = (min_z + max_z) * 0.5

    if anchor == "CENTER":
        return Vector((center_x, center_y, center_z))
    if anchor == "MIN_X_MIN_Y":
        return Vector((min_x, min_y, center_z))
    if anchor == "MIN_X_MAX_Y":
        return Vector((min_x, max_y, center_z))
    if anchor == "MAX_X_MIN_Y":
        return Vector((max_x, min_y, center_z))
    if anchor == "MAX_X_MAX_Y":
        return Vector((max_x, max_y, center_z))
    raise RuntimeError(f"Unsupported asset-space origin anchor '{anchor}'")


def update_helper_bbox_mesh(
    helper: bpy.types.Object,
    local_bbox: list[float],
) -> None:
    if helper.type != "MESH" or helper.data is None:
        raise RuntimeError(f"Helper '{helper.name}' does not have a mesh to update")

    mesh = helper.data
    mesh.clear_geometry()
    mesh.from_pydata(bbox_vertices(local_bbox), bbox_edges(), [])
    mesh.update()


def place_cursor_from_asset_space(
    root: bpy.types.Object,
    anchor: str,
    z_offset: float,
    cursor: bpy.types.View3DCursor,
) -> Vector:
    local_point = project_origin_local_point(helper_local_bbox(root), anchor)
    world_point = root.matrix_world @ local_point
    world_point.z = float(z_offset)

    cursor.location = world_point
    cursor.rotation_mode = "QUATERNION"
    cursor.rotation_quaternion = root.matrix_world.to_quaternion()
    return world_point


def restore_descendant_world_matrices(
    parent: bpy.types.Object,
    saved_world_matrices: dict[str, Matrix],
) -> None:
    for child in helper_children(parent):
        saved_world = saved_world_matrices.get(child.name)
        if saved_world is None:
            continue
        child.matrix_parent_inverse = Matrix.Identity(4)
        child.matrix_basis = parent.matrix_world.inverted_safe() @ saved_world
        restore_descendant_world_matrices(child, saved_world_matrices)


def reanchor_asset_space_root(
    root: bpy.types.Object,
    new_origin: Vector,
) -> list[float]:
    world_bbox = helper_world_bbox(root)
    saved_world_matrices = {
        child.name: child.matrix_world.copy() for child in iter_helper_descendants(root)
    }

    new_root_matrix = root.matrix_world.copy()
    new_root_matrix.translation = Vector(new_origin)
    new_local_bbox = local_bbox_from_world_bbox(world_bbox, new_root_matrix)

    root.matrix_world = new_root_matrix
    update_helper_bbox_mesh(root, new_local_bbox)
    root[PROP_LOCAL_BBOX] = [float(value) for value in new_local_bbox]
    restore_descendant_world_matrices(root, saved_world_matrices)
    return [float(value) for value in new_local_bbox]


def helper_collection_objects(
    helper_collection_name: str,
    allowed_spot_classes: set[str] | None = None,
) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(helper_collection_name)
    if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
        return []

    helpers = [obj for obj in collection.objects if obj.get(PROP_HELPER)]
    if allowed_spot_classes is not None:
        helpers = [
            obj
            for obj in helpers
            if str(obj.get(PROP_SPOT_CLASS, "")) in allowed_spot_classes
        ]

    return sorted(
        helpers,
        key=lambda obj: str(obj.get(PROP_SORT_KEY, obj.name)),
    )


def resolve_test_helper_object(
    helper_collection_name: str,
    object_name: str,
) -> bpy.types.Object | None:
    if not object_name:
        return None

    helper = bpy.data.objects.get(object_name)
    if helper is None or not helper.get(PROP_HELPER):
        return None

    collection = bpy.data.collections.get(helper_collection_name)
    if collection is not None and collection.get(HELPER_COLLECTION_MARKER):
        if collection.objects.get(helper.name) is None:
            return None

    return helper


def helper_spatial_id(helper: bpy.types.Object) -> str:
    return str(helper.get(PROP_URI_SUFFIX, helper.name))


def helper_display_name(helper: bpy.types.Object) -> str:
    identifier = helper_spatial_id(helper)
    label = str(helper.get(PROP_LABEL, helper.name))
    return f"{identifier} ({label})" if label != identifier else identifier


def resolve_project_asset_source_object(
    context: bpy.types.Context,
    project_collection_name: str,
) -> bpy.types.Object | None:
    try:
        project_collection = find_project_collection(
            context.scene,
            project_collection_name or None,
        )
        _site_collection, building_collection, _storey_collections = (
            descendant_role_collections(project_collection)
        )
    except RuntimeError:
        return None
    return collection_object_named_like_collection(building_collection)


def resolve_asset_direction_helper(
    context: bpy.types.Context,
    project_collection_name: str,
    helper_collection_name: str,
) -> tuple[bpy.types.Object | None, bpy.types.Object | None, str | None]:
    helper = context.active_object
    collection = bpy.data.collections.get(helper_collection_name)
    asset_source = resolve_project_asset_source_object(context, project_collection_name)
    if asset_source is not None and (
        helper is None or not helper.get(PROP_HELPER) or helper == asset_source
    ):
        linked_helper = None
        if collection is not None and collection.get(HELPER_COLLECTION_MARKER):
            linked_helper = find_helper_for_source_object(asset_source, collection)
        return asset_source, linked_helper, None
    if (
        helper is not None
        and not helper.get(PROP_HELPER)
        and collection is not None
        and collection.get(HELPER_COLLECTION_MARKER)
    ):
        linked_helper = find_helper_for_source_object(helper, collection)
        if (
            linked_helper is not None
            and linked_helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
        ):
            return helper, linked_helper, None
    if (
        helper is not None
        and helper.get(PROP_HELPER)
        and helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
    ):
        if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
            source_object = find_source_object_for_helper(helper)
            return source_object or helper, helper, None
        if collection.objects.get(helper.name) is not None:
            source_object = find_source_object_for_helper(helper)
            return source_object or helper, helper, None

    try:
        asset_helper = resolve_asset_root_helper(helper_collection_name)
        source_object = find_source_object_for_helper(asset_helper)
        return source_object or asset_helper, asset_helper, None
    except RuntimeError as error:
        return None, None, str(error)


def ensure_asset_direction_defaults(helper: bpy.types.Object) -> None:
    for prop_name, axis_name in (
        (PROP_ASSET_AXIS_X_DIRECTION, "X"),
        (PROP_ASSET_AXIS_Y_DIRECTION, "Y"),
        (PROP_ASSET_AXIS_Z_DIRECTION, "Z"),
    ):
        current = getattr(helper, prop_name, None)
        if current:
            continue
        setattr(helper, prop_name, default_asset_axis_direction(axis_name))


def resolve_am_authoring_helper(
    context: bpy.types.Context,
    project_collection_name: str,
    helper_collection_name: str,
) -> tuple[bpy.types.Object | None, bpy.types.Object | None, str | None]:
    active_object = context.active_object
    collection = bpy.data.collections.get(helper_collection_name)
    asset_source = resolve_project_asset_source_object(context, project_collection_name)
    if active_object is None:
        return (
            None,
            None,
            "Select a source object or SPOT helper to configure SPOT-AM authoring",
        )

    linked_helper: bpy.types.Object | None = None
    owner = active_object
    if active_object.get(PROP_HELPER):
        linked_helper = active_object
        if collection is not None and collection.get(HELPER_COLLECTION_MARKER):
            if collection.objects.get(active_object.name) is None:
                return (
                    None,
                    None,
                    (
                        f"Active helper '{active_object.name}' is not part of '{helper_collection_name}'"
                    ),
                )
        owner = find_source_object_for_helper(active_object) or active_object
    else:
        if collection is not None and collection.get(HELPER_COLLECTION_MARKER):
            linked_helper = find_helper_for_source_object(active_object, collection)

    if (
        linked_helper is not None
        and linked_helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
    ) or owner == asset_source:
        return (
            None,
            None,
            "Use the Asset Space Directions box for AssetSpace configuration",
        )

    return owner, linked_helper, None


def vector_to_text(vector: Vector) -> str:
    return (
        f"{format_number(vector.x)}, {format_number(vector.y)}, "
        f"{format_number(vector.z)}"
    )


def vector_to_wkt_point(vector: Vector) -> str:
    return (
        f"POINT Z ({format_number(vector.x)} {format_number(vector.y)} "
        f"{format_number(vector.z)})"
    )


def axis_gap(
    left_min: float,
    left_max: float,
    right_min: float,
    right_max: float,
    epsilon: float = SPOT_TEST_EPSILON,
) -> float:
    if left_max < right_min - epsilon:
        return right_min - left_max
    if right_max < left_min - epsilon:
        return left_min - right_max
    return 0.0


def axis_overlap(
    left_min: float,
    left_max: float,
    right_min: float,
    right_max: float,
) -> float:
    return min(left_max, right_max) - max(left_min, right_min)


def aabbs_contact_or_overlap(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return all(
        left.max_corner[index] >= right.min_corner[index] - epsilon
        and right.max_corner[index] >= left.min_corner[index] - epsilon
        for index in range(3)
    )


def point_inside_aabb(
    point: Vector,
    box: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return all(
        box.min_corner[index] - epsilon
        <= point[index]
        <= box.max_corner[index] + epsilon
        for index in range(3)
    )


def distance_between_aabbs(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> float:
    dx = axis_gap(
        left.min_corner.x,
        left.max_corner.x,
        right.min_corner.x,
        right.max_corner.x,
        epsilon,
    )
    dy = axis_gap(
        left.min_corner.y,
        left.max_corner.y,
        right.min_corner.y,
        right.max_corner.y,
        epsilon,
    )
    dz = axis_gap(
        left.min_corner.z,
        left.max_corner.z,
        right.min_corner.z,
        right.max_corner.z,
        epsilon,
    )
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def aabb_contains(
    outer: Aabb,
    inner: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return all(
        inner.min_corner[index] >= outer.min_corner[index] - epsilon
        and inner.max_corner[index] <= outer.max_corner[index] + epsilon
        for index in range(3)
    )


def aabbs_intersect(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return all(
        axis_overlap(
            left.min_corner[index],
            left.max_corner[index],
            right.min_corner[index],
            right.max_corner[index],
        )
        > epsilon
        for index in range(3)
    )


def aabbs_equal(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return all(
        abs(left.min_corner[index] - right.min_corner[index]) <= epsilon
        and abs(left.max_corner[index] - right.max_corner[index]) <= epsilon
        for index in range(3)
    )


def aabbs_touch(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    if not aabbs_contact_or_overlap(left, right, epsilon):
        return False

    return not aabbs_intersect(left, right, epsilon)


def aabbs_overlap(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    overlaps = [
        axis_overlap(
            left.min_corner[index],
            left.max_corner[index],
            right.min_corner[index],
            right.max_corner[index],
        )
        for index in range(3)
    ]
    return (
        all(overlap > epsilon for overlap in overlaps)
        and not aabb_contains(left, right, epsilon)
        and not aabb_contains(right, left, epsilon)
    )


def aabbs_disconnected(
    left: Aabb,
    right: Aabb,
    epsilon: float = SPOT_TEST_EPSILON,
) -> bool:
    return not aabbs_contact_or_overlap(left, right, epsilon)


def helper_entity_space_objects(helper_collection_name: str) -> list[bpy.types.Object]:
    return [
        obj
        for obj in helper_collection_objects(helper_collection_name)
        if str(obj.get(PROP_ROLE, "")) not in {ROLE_SITE, ROLE_DOCUMENT}
        and str(obj.get(PROP_PROXY_ROLE, "")) != PROXY_ROLE_DOCUMENT
    ]


def helper_space_objects(helper_collection_name: str) -> list[bpy.types.Object]:
    return [
        obj
        for obj in helper_collection_objects(helper_collection_name)
        if str(obj.get(PROP_PROXY_ROLE, "")) != PROXY_ROLE_DOCUMENT
    ]


def query_helpers_within_radius(
    reference: bpy.types.Object,
    candidates: list[bpy.types.Object],
    radius: float,
) -> list[tuple[bpy.types.Object, float]]:
    reference_box = helper_world_bbox(reference)
    results: list[tuple[bpy.types.Object, float]] = []
    for candidate in candidates:
        if candidate == reference:
            continue
        distance = distance_between_aabbs(reference_box, helper_world_bbox(candidate))
        if distance <= radius + SPOT_TEST_EPSILON:
            results.append((candidate, distance))

    return sorted(
        results,
        key=lambda entry: (entry[1], helper_spatial_id(entry[0])),
    )


def query_helpers_inside_reference(
    reference: bpy.types.Object,
    candidates: list[bpy.types.Object],
) -> list[bpy.types.Object]:
    reference_box = helper_world_bbox(reference)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate != reference
            and aabb_contains(reference_box, helper_world_bbox(candidate))
        ],
        key=helper_spatial_id,
    )


def query_helpers_containing_reference(
    reference: bpy.types.Object,
    candidates: list[bpy.types.Object],
) -> list[bpy.types.Object]:
    reference_box = helper_world_bbox(reference)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate != reference
            and aabb_contains(helper_world_bbox(candidate), reference_box)
        ],
        key=helper_spatial_id,
    )


def query_helpers_intersecting_reference(
    reference: bpy.types.Object,
    candidates: list[bpy.types.Object],
) -> list[bpy.types.Object]:
    reference_box = helper_world_bbox(reference)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate != reference
            and aabbs_intersect(reference_box, helper_world_bbox(candidate))
        ],
        key=helper_spatial_id,
    )


def query_helpers_disconnected_from_reference(
    reference: bpy.types.Object,
    candidates: list[bpy.types.Object],
) -> list[bpy.types.Object]:
    reference_box = helper_world_bbox(reference)
    return sorted(
        [
            candidate
            for candidate in candidates
            if candidate != reference
            and aabbs_disconnected(reference_box, helper_world_bbox(candidate))
        ],
        key=helper_spatial_id,
    )


def draw_test_slot(
    layout: bpy.types.UILayout,
    settings: bpy.types.PropertyGroup,
    prop_name: str,
    label: str,
    slot: str,
) -> None:
    row = layout.row(align=True)
    row.prop_search(settings, prop_name, bpy.data, "objects", text=label)
    operator = row.operator(
        "spot.use_active_helper_for_test",
        text="",
        icon="EYEDROPPER",
    )
    operator.slot = slot


def draw_helper_result_list(
    layout: bpy.types.UILayout,
    title: str,
    entries: list[tuple[bpy.types.Object, float]] | list[bpy.types.Object],
    limit: int,
) -> None:
    box = layout.box()
    box.label(text=title)
    if not entries:
        box.label(text="(none)")
        return

    shown = entries[: max(limit, 0)]
    for entry in shown:
        if isinstance(entry, tuple):
            helper, distance = entry
            box.label(
                text=f"{helper_spatial_id(helper)} | {format_number(distance)}",
            )
        else:
            box.label(text=helper_spatial_id(entry))

    remaining = len(entries) - len(shown)
    if remaining > 0:
        box.label(text=f"... and {remaining} more")


def build_spot_test_report(context: bpy.types.Context) -> str:
    settings = context.scene.spot_tools_settings
    left = resolve_test_helper_object(
        settings.helper_collection, settings.test_left_helper
    )
    right = resolve_test_helper_object(
        settings.helper_collection, settings.test_right_helper
    )
    reference = resolve_test_helper_object(
        settings.helper_collection,
        settings.test_reference_helper,
    )
    cursor = context.scene.cursor.location.copy()
    entity_helpers = helper_entity_space_objects(settings.helper_collection)
    all_helpers = helper_space_objects(settings.helper_collection)

    lines = [
        "SPOT Test Report",
        f"Helper collection: {settings.helper_collection}",
        "",
    ]

    if left is not None and right is not None:
        left_box = helper_world_bbox(left)
        right_box = helper_world_bbox(right)
        lines.extend(
            [
                "Pair Check",
                f"- left: {helper_display_name(left)}",
                f"- right: {helper_display_name(right)}",
                f"- distance: {format_number(distance_between_aabbs(left_box, right_box))}",
                f"- absolutePosition(right): {vector_to_wkt_point(right.matrix_world.translation)}",
                f"- contains(left, right): {str(aabb_contains(left_box, right_box)).lower()}",
                f"- inside(left, right): {str(aabb_contains(right_box, left_box)).lower()}",
                f"- intersects: {str(aabbs_intersect(left_box, right_box)).lower()}",
                f"- equals: {str(aabbs_equal(left_box, right_box)).lower()}",
                f"- touches: {str(aabbs_touch(left_box, right_box)).lower()}",
                f"- overlaps: {str(aabbs_overlap(left_box, right_box)).lower()}",
                f"- disconnected: {str(aabbs_disconnected(left_box, right_box)).lower()}",
                "",
            ],
        )
    else:
        lines.extend(["Pair Check", "- select both left and right helpers", ""])

    if right is not None:
        right_box = helper_world_bbox(right)
        lines.extend(
            [
                "Cursor Check",
                f"- cursor: {vector_to_text(cursor)}",
                f"- target: {helper_display_name(right)}",
                f"- cursor distance to target: {format_number(distance_point_to_aabb(cursor, right_box))}",
                f"- cursor inside target: {str(point_inside_aabb(cursor, right_box)).lower()}",
                "",
            ],
        )
    else:
        lines.extend(
            [
                "Cursor Check",
                "- select a right helper to compare against the cursor",
                "",
            ]
        )

    if reference is not None:
        within = query_helpers_within_radius(
            reference, entity_helpers, settings.test_radius
        )
        inside = query_helpers_inside_reference(reference, entity_helpers)
        containers = query_helpers_containing_reference(reference, all_helpers)
        intersects = query_helpers_intersecting_reference(reference, entity_helpers)
        disconnected = query_helpers_disconnected_from_reference(
            reference, entity_helpers
        )

        lines.extend(
            [
                "Reference Queries",
                f"- reference: {helper_display_name(reference)}",
                f"- radius: {format_number(settings.test_radius)}",
                f"- absolutePosition(reference): {vector_to_wkt_point(reference.matrix_world.translation)}",
                "- within radius:",
            ],
        )
        if within:
            lines.extend(
                f"  - {helper_display_name(helper)} | distance={format_number(distance)}"
                for helper, distance in within[: settings.test_limit]
            )
        else:
            lines.append("  - (none)")

        lines.append("- inside reference:")
        if inside:
            lines.extend(
                f"  - {helper_display_name(helper)}"
                for helper in inside[: settings.test_limit]
            )
        else:
            lines.append("  - (none)")

        lines.append("- containers of reference:")
        if containers:
            lines.extend(
                f"  - {helper_display_name(helper)}"
                for helper in containers[: settings.test_limit]
            )
        else:
            lines.append("  - (none)")

        lines.append("- intersects reference:")
        if intersects:
            lines.extend(
                f"  - {helper_display_name(helper)}"
                for helper in intersects[: settings.test_limit]
            )
        else:
            lines.append("  - (none)")

        lines.append("- disconnected from reference:")
        if disconnected:
            lines.extend(
                f"  - {helper_display_name(helper)}"
                for helper in disconnected[: settings.test_limit]
            )
        else:
            lines.append("  - (none)")
    else:
        lines.extend(
            [
                "Reference Queries",
                "- select a reference helper to list within-distance, inside, containers, intersections, and disconnected results",
            ],
        )

    return "\n".join(lines) + "\n"


class SPOT_PG_scene_settings(bpy.types.PropertyGroup):
    project_collection: StringProperty(
        name="Source Collection",
        description=(
            "Optional collection that Build reads its source objects from. Leave empty "
            "to find the AssetSpace collection automatically"
        ),
        default="",
    )
    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the SPOT helper collection to build, edit, and export",
        default=HELPER_COLLECTION_NAME,
    )
    visible_only_sources: BoolProperty(
        name="Visible Sources Only",
        description="Only use currently visible source objects when generating the helper scene",
        default=True,
    )
    base_iri: StringProperty(
        name="Base IRI",
        description="Base IRI used for exported local resources",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        description="Floating-point precision used for matrices and bounding boxes",
        default=6,
        min=0,
        max=12,
    )
    simulate_spatial_history: BoolProperty(
        name="Simulate spatial history",
        description="Generate opt-in registration history in rdf/spatial-history.trig",
        default=False,
    )
    spatial_history_start: StringProperty(
        name="History Start",
        description="ISO 8601 start time for simulated spatial registration activities",
        default=DEFAULT_HISTORY_START,
    )
    project_name: StringProperty(
        name="Project",
        description=(
            "Name of this file's project. Its dataset snapshots go into "
            "datasets/exports/<project>/. Empty uses the label of the root AssetSpace"
        ),
        default="",
    )
    provenance_agent: StringProperty(
        name="Agent",
        description=(
            "Who commits SPOT-SG placements: an IRI, or a name that becomes an "
            "agent IRI in the dataset's namespace"
        ),
        default="",
    )
    picture_damage_candidate: PointerProperty(
        name="Damage",
        description="DOT damage to add as a feature of interest of the selected picture observation",
        type=bpy.types.Object,
        poll=is_damage_object,
    )
    picture_component_candidate: PointerProperty(
        name="Component",
        description="BOT element to add as a BCF component of the selected issue",
        type=bpy.types.Object,
        poll=is_component_object,
    )
    represents_candidate: PointerProperty(
        name="Represents",
        description="BOT entity the selected space also represents",
        type=bpy.types.Object,
        poll=is_bot_referent_object,
    )
    bcf_project_guid: StringProperty(
        name="BCF Project GUID",
        default="",
    )
    bcf_project_title: StringProperty(
        name="BCF Project Title",
        default="",
    )
    bcf_author_name: StringProperty(
        name="BCF Test Author",
        default="",
    )
    bcf_author_identifier: StringProperty(
        name="BCF Test Author Identifier",
        default="",
    )
    asset_space_origin_anchor: EnumProperty(
        name="Anchor",
        description="Anchor on the asset-space bbox used for cursor projection",
        items=ORIGIN_ANCHOR_ITEMS,
        default="CENTER",
    )
    asset_space_origin_z: FloatProperty(
        name="Absolute Z",
        description="Scene-space Z coordinate used when projecting the asset-space origin cursor",
        default=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    test_left_helper: StringProperty(
        name="Left Helper",
        description="Helper object used as the left operand in SPOT test comparisons",
        default="",
    )
    test_right_helper: StringProperty(
        name="Right Helper",
        description="Helper object used as the right operand and cursor target in SPOT test comparisons",
        default="",
    )
    test_reference_helper: StringProperty(
        name="Reference Helper",
        description="Helper object used for within-distance and relation list queries in SPOT Test",
        default="",
    )
    test_radius: FloatProperty(
        name="Radius",
        description="Distance threshold used for Blender-side within-distance checks",
        default=5.0,
        min=0.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    test_limit: IntProperty(
        name="Preview Limit",
        description="Maximum number of matching helpers shown per result list in the SPOT Test panel and report",
        default=5,
        min=1,
        max=50,
    )


def add_picture_reference(
    context: bpy.types.Context,
    collection_property: str,
    candidate: bpy.types.Object | None,
) -> tuple[bool, str]:
    settings = context.scene.spot_tools_settings
    owner, _helper, error = resolve_picture_owner_and_helper(
        context, settings.helper_collection
    )
    if owner is None:
        return False, error or "No picture selected"
    reference = normalize_reference_object(candidate)
    if reference is None:
        return False, "Choose an object to add"
    collection = getattr(owner, collection_property)
    if any(entry.object == reference for entry in collection):
        return False, f"'{reference.name}' is already linked"
    entry = collection.add()
    entry.object = reference
    return True, reference.name


def remove_picture_reference(
    context: bpy.types.Context,
    collection_property: str,
    index: int,
) -> tuple[bool, str]:
    settings = context.scene.spot_tools_settings
    owner, _helper, error = resolve_picture_owner_and_helper(
        context, settings.helper_collection
    )
    if owner is None:
        return False, error or "No picture selected"
    collection = getattr(owner, collection_property)
    if index < 0 or index >= len(collection):
        return False, "The selected reference no longer exists"
    name = collection[index].object.name if collection[index].object else "missing reference"
    collection.remove(index)
    return True, name


class SPOT_PG_represents_link(bpy.types.PropertyGroup):
    """A BOT entity a space also represents, by its source object or collection."""

    object: PointerProperty(type=bpy.types.Object)
    collection: PointerProperty(type=bpy.types.Collection)


def represents_link_name(entry: SPOT_PG_represents_link) -> str | None:
    target = entry.collection or entry.object
    return target.name if target is not None else None


def resolve_represents_owner(
    context: bpy.types.Context,
) -> tuple[
    bpy.types.Object | bpy.types.Collection | None,
    bpy.types.Object | None,
    str | None,
]:
    """The selected space's helper and the source that holds its links."""
    active = context.active_object
    if active is None:
        return None, None, "Select a space or its SPOT helper"
    settings = context.scene.spot_tools_settings
    collection = bpy.data.collections.get(settings.helper_collection)
    if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
        return None, None, (
            f"SPOT helper collection '{settings.helper_collection}' was not found"
        )
    helper = helper_for_reference(active, collection)
    if helper is None:
        return None, None, "The selected object has no generated SPOT helper"
    return represents_link_owner(helper), helper, None


PLACE_IN_ACTIVE_ASSET_SPACE_DESCRIPTION = (
    "Place the imported root's contents inside the active AssetSpace helper "
    "instead of replacing the helper collection. Its box and axis directions "
    "resolve SPOT-AM relations to the imported root"
)


def import_anchor_from_context(
    operator: bpy.types.Operator,
    context: bpy.types.Context,
) -> tuple[bool, bpy.types.Object | None]:
    """The active AssetSpace helper to import into, when the option asks for one."""
    if not operator.place_in_active_asset_space:
        return True, None
    anchor = context.active_object
    if (
        anchor is None
        or not anchor.get(PROP_HELPER)
        or anchor.get(PROP_SPOT_CLASS) != SPOT_CLASS_ASSET
    ):
        operator.report(
            {"ERROR"},
            "Make an AssetSpace helper active to import into it",
        )
        return False, None
    return True, anchor


def report_spot_am_flattened(
    operator: bpy.types.Operator,
    context: bpy.types.Context,
) -> None:
    """Warn that an export stated imported SPOT-AM regions as SPOT-SG."""
    count = sum(
        1
        for obj in context.scene.objects
        if obj.get(PROP_HELPER) and is_placed_by_spot_am(obj)
    )
    if count:
        operator.report(
            {"WARNING"},
            (
                f"{count} spaces are still placed by imported SPOT-AM relations; this "
                "export states their derived regions as SPOT-SG. Use Export "
                "Transition Dataset to keep SPOT-AM and record what was refined"
            ),
        )


def operator_error_message(error: Exception) -> str:
    """
    A message that still says something once Blender shows it in the status bar.

    `str()` on a KeyError yields the bare key and nothing else, so a lookup
    that misses reads as an unexplained name. The exception type is always
    included, and the full traceback goes to the system console.
    """
    traceback.print_exc()
    text = str(error)
    if isinstance(error, KeyError):
        return f"KeyError: {text} was not found"
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


class SPOT_OT_add_picture_damage(bpy.types.Operator):
    bl_idname = "spot.add_picture_damage"
    bl_label = "Add Damage"
    bl_description = "Add the chosen DOT damage as a feature of interest of this picture observation"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        candidate = settings.picture_damage_candidate
        if candidate is not None and not is_damage_object(settings, candidate):
            self.report({"ERROR"}, f"'{candidate.name}' is not an exported DOT damage")
            return {"CANCELLED"}
        added, message = add_picture_reference(
            context, PROP_OBSERVED_DAMAGES, candidate
        )
        if not added:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        settings.picture_damage_candidate = None
        self.report({"INFO"}, f"Linked damage '{message}'")
        return {"FINISHED"}


class SPOT_OT_remove_picture_damage(bpy.types.Operator):
    bl_idname = "spot.remove_picture_damage"
    bl_label = "Remove Damage"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=0, min=0)

    def execute(self, context: bpy.types.Context) -> set[str]:
        removed, message = remove_picture_reference(
            context, PROP_OBSERVED_DAMAGES, self.index
        )
        if not removed:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed damage '{message}'")
        return {"FINISHED"}


class SPOT_OT_add_picture_component(bpy.types.Operator):
    bl_idname = "spot.add_picture_component"
    bl_label = "Add Component"
    bl_description = "Add the chosen BOT element as a component of this BCF test issue"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        candidate = settings.picture_component_candidate
        if candidate is not None and not is_component_object(settings, candidate):
            self.report({"ERROR"}, f"'{candidate.name}' is not an exported BOT element")
            return {"CANCELLED"}
        added, message = add_picture_reference(context, PROP_BCF_COMPONENTS, candidate)
        if not added:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        settings.picture_component_candidate = None
        self.report({"INFO"}, f"Linked component '{message}'")
        return {"FINISHED"}


class SPOT_OT_remove_picture_component(bpy.types.Operator):
    bl_idname = "spot.remove_picture_component"
    bl_label = "Remove Component"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=0, min=0)

    def execute(self, context: bpy.types.Context) -> set[str]:
        removed, message = remove_picture_reference(
            context, PROP_BCF_COMPONENTS, self.index
        )
        if not removed:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed component '{message}'")
        return {"FINISHED"}


class SPOT_OT_add_represents_link(bpy.types.Operator):
    bl_idname = "spot.add_represents_link"
    bl_label = "Add Represented Entity"
    bl_description = "Let the selected space also represent the chosen BOT entity"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        owner, helper, error = resolve_represents_owner(context)
        if owner is None:
            self.report({"ERROR"}, error or "No space selected")
            return {"CANCELLED"}
        candidate = settings.represents_candidate
        if candidate is None or not is_bot_referent_object(settings, candidate):
            self.report({"ERROR"}, "Choose an object whose helper has a BOT class")
            return {"CANCELLED"}
        target_helper = helper_for_reference(candidate)
        if target_helper == helper:
            self.report({"ERROR"}, "A space cannot link to its own BOT entity")
            return {"CANCELLED"}
        # Store the target's source rather than its helper, which a rebuild
        # replaces.
        reference = represents_link_owner(target_helper)
        links = getattr(owner, PROP_REPRESENTS_LINKS)
        if any(reference in (entry.object, entry.collection) for entry in links):
            self.report({"ERROR"}, f"'{reference.name}' is already linked")
            return {"CANCELLED"}
        entry = links.add()
        if isinstance(reference, bpy.types.Collection):
            entry.collection = reference
        else:
            entry.object = reference
        settings.represents_candidate = None
        self.report({"INFO"}, f"'{owner.name}' now also represents '{reference.name}'")
        return {"FINISHED"}


class SPOT_OT_remove_represents_link(bpy.types.Operator):
    bl_idname = "spot.remove_represents_link"
    bl_label = "Remove Represented Entity"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=0, min=0)

    def execute(self, context: bpy.types.Context) -> set[str]:
        owner, _helper, error = resolve_represents_owner(context)
        if owner is None:
            self.report({"ERROR"}, error or "No space selected")
            return {"CANCELLED"}
        links = getattr(owner, PROP_REPRESENTS_LINKS)
        if self.index >= len(links):
            self.report({"ERROR"}, "The selected link no longer exists")
            return {"CANCELLED"}
        name = represents_link_name(links[self.index]) or "missing link"
        links.remove(self.index)
        self.report({"INFO"}, f"Removed '{name}'")
        return {"FINISHED"}


class SPOT_OT_build_helper_scene(bpy.types.Operator):
    bl_idname = "spot.build_helper_scene"
    bl_label = "SPOT: Build Helper Scene"
    bl_description = (
        "Create or replace the SPOT Scene helper hierarchy from Blender geometry"
    )
    bl_options = {"REGISTER", "UNDO"}

    project_collection: StringProperty(
        name="Project Collection",
        description="Optional explicit source root collection name",
        default="",
    )
    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection that will be created or replaced",
        default=HELPER_COLLECTION_NAME,
    )
    visible_only_sources: BoolProperty(
        name="Visible Sources Only",
        description="Only use currently visible source objects when generating the helper scene",
        default=True,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = build_helper_scene(
                project_collection_name=self.project_collection or None,
                helper_collection_name=self.helper_collection,
                visible_only=self.visible_only_sources,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Created {result['helper_collection_name']} with "
                f"{result['resolved_storey_count']} storeys"
            ),
        )
        if result["warning_count"]:
            self.report(
                {"WARNING"},
                f"{result['warning_count']} elements used geometry fallback for storey assignment",
            )
        return {"FINISHED"}


class SPOT_OT_import_turtle_as(ImportHelper, bpy.types.Operator):
    bl_idname = "spot.import_turtle_as"
    bl_label = "SPOT: Import Scene Graph Turtle"
    bl_description = "Import a SPOT scene-graph Turtle file and rebuild the SPOT helper scene in Blender"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ttl"
    filter_glob: StringProperty(default="*.ttl", options={"HIDDEN"})

    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to create or replace",
        default=HELPER_COLLECTION_NAME,
    )
    place_in_active_asset_space: BoolProperty(
        name="Place Inside Active AssetSpace",
        description=PLACE_IN_ACTIVE_ASSET_SPACE_DESCRIPTION,
        default=False,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        valid, anchor = import_anchor_from_context(self, context)
        if not valid:
            return {"CANCELLED"}
        try:
            result = import_scene_graph_turtle(
                input_path=self.filepath,
                helper_collection_name=self.helper_collection,
                anchor_helper=anchor,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {result['helper_count']} SPOT helpers from "
                f"{result['input_path']}"
            ),
        )
        warnings = result["warnings"]
        for warning in warnings[:5]:
            self.report({"WARNING"}, warning)
        if len(warnings) > 5:
            self.report(
                {"WARNING"},
                f"{len(warnings) - 5} additional import warnings were written to the console",
            )
            for warning in warnings[5:]:
                print(f"SPOT import warning: {warning}")
        return {"FINISHED"}


class SPOT_OT_import_dataset_folder(bpy.types.Operator):
    bl_idname = "spot.import_dataset_folder"
    bl_label = "SPOT: Import Dataset Folder"
    bl_description = (
        "Import current spatial data from all Turtle and TriG files in an RDF "
        "dataset folder"
    )
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="Dataset Folder",
        description="Folder that contains the RDF dataset",
        subtype="DIR_PATH",
    )
    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to create or replace",
        default=HELPER_COLLECTION_NAME,
    )
    place_in_active_asset_space: BoolProperty(
        name="Place Inside Active AssetSpace",
        description=PLACE_IN_ACTIVE_ASSET_SPACE_DESCRIPTION,
        default=False,
    )

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        valid, anchor = import_anchor_from_context(self, context)
        if not valid:
            return {"CANCELLED"}
        try:
            result = import_scene_graph_turtle(
                input_path=self.directory,
                helper_collection_name=self.helper_collection,
                anchor_helper=anchor,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {result['helper_count']} SPOT helpers from "
                f"{len(result['input_files'])} dataset files"
            ),
        )
        warnings = result["warnings"]
        for warning in warnings[:5]:
            self.report({"WARNING"}, warning)
        if len(warnings) > 5:
            self.report(
                {"WARNING"},
                f"{len(warnings) - 5} additional import warnings were written to the console",
            )
            for warning in warnings[5:]:
                print(f"SPOT import warning: {warning}")
        return {"FINISHED"}


class SPOT_OT_commit_sg_placement(bpy.types.Operator):
    bl_idname = "spot.commit_sg_placement"
    bl_label = "SPOT: Commit SPOT-SG Placement"
    bl_description = (
        "Record the current placement of the selected imported spaces as SPOT-SG. "
        "A space SPOT-AM placed moves to SPOT-SG; one committed before is revised. "
        "Both are recorded as one provenance activity"
    )
    bl_options = {"REGISTER", "UNDO"}

    label: StringProperty(
        name="Label",
        description="What was done, as the activity's label",
        default="Placement refined with SPOT-SG",
    )
    agent: StringProperty(
        name="Agent",
        description="Who placed the spaces: an IRI, or a name",
        default="",
    )
    used: StringProperty(
        name="Used Sources",
        description="Comma-separated IRIs of the sources the placement is based on, such as a plan",
        default="",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return any(obj.get(PROP_HELPER) for obj in context.selected_objects)

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        if not self.agent:
            self.agent = context.scene.spot_tools_settings.provenance_agent
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not self.agent.strip():
            self.report({"ERROR"}, "Name the agent who placed the spaces")
            return {"CANCELLED"}
        helpers = [obj for obj in context.selected_objects if obj.get(PROP_HELPER)]
        try:
            record = commit_sg_placements(
                helpers,
                agent=self.agent.strip(),
                label=self.label.strip() or "Placement refined with SPOT-SG",
                used=[value.strip() for value in self.used.split(",") if value.strip()],
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}
        context.scene.spot_tools_settings.provenance_agent = self.agent.strip()
        moved = sum(1 for change in record["changes"] if change["kind"] == "am-to-sg")
        revised = sum(1 for change in record["changes"] if change["kind"] == "sg-to-sg")
        self.report(
            {"INFO"},
            f"Committed {moved} SPOT-AM to SPOT-SG transitions and {revised} revisions",
        )
        return {"FINISHED"}


class SPOT_OT_export_transition_dataset(bpy.types.Operator):
    bl_idname = "spot.export_transition_dataset"
    bl_label = "SPOT: Export Transition Dataset"
    bl_description = (
        "Write the imported input as a dataset snapshot in datasets/exports/<project>/ "
        "whose mutation log holds the committed SPOT-SG placements and their provenance"
    )
    bl_options = {"REGISTER"}

    directory: StringProperty(
        name="Output Folder",
        description="Folder to write into instead of a snapshot in datasets/exports",
        subtype="DIR_PATH",
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        try:
            result = write_transition_dataset(
                self.directory or None,
                project_name=settings.project_name,
                helper_collection_name=settings.helper_collection,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            (
                f"Wrote {result['activity_count']} activities with "
                f"{result['change_count']} changes to {result['output_dir']}"
            ),
        )
        return {"FINISHED"}


class SPOT_OT_export_turtle_default(bpy.types.Operator):
    bl_idname = "spot.export_turtle_default"
    bl_label = "SPOT: Export Scene Graph Turtle"
    bl_description = "Export the SPOT helper scene as full SPOT/SPOT-SG Turtle using the default output path"
    bl_options = {"REGISTER"}

    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to export",
        default=HELPER_COLLECTION_NAME,
    )
    base_iri: StringProperty(
        name="Base IRI",
        description="Base IRI used for exported local resources",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        description="Floating-point precision used for matrices and bounding boxes",
        default=6,
        min=0,
        max=12,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = export_helper_scene(
                helper_collection_name=self.helper_collection,
                base_iri=self.base_iri,
                local_prefix=self.local_prefix,
                precision=self.precision,
                export_profile=EXPORT_PROFILE_SCENE_GRAPH,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported scene graph Turtle to {result['output_path']}")
        report_spot_am_flattened(self, context)
        return {"FINISHED"}


class SPOT_OT_export_turtle_as(ExportHelper, bpy.types.Operator):
    bl_idname = "spot.export_turtle_as"
    bl_label = "SPOT: Export Scene Graph Turtle As"
    bl_description = "Export the SPOT helper scene as full SPOT/SPOT-SG Turtle and choose the destination path"
    bl_options = {"REGISTER"}

    filename_ext = ".ttl"
    filter_glob: StringProperty(default="*.ttl", options={"HIDDEN"})

    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to export",
        default=HELPER_COLLECTION_NAME,
    )
    base_iri: StringProperty(
        name="Base IRI",
        description="Base IRI used for exported local resources",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        description="Floating-point precision used for matrices and bounding boxes",
        default=6,
        min=0,
        max=12,
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        if not self.filepath:
            self.filepath = str(
                default_output_path_for_profile(EXPORT_PROFILE_SCENE_GRAPH)
            )
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = export_helper_scene(
                helper_collection_name=self.helper_collection,
                output_path=self.filepath,
                base_iri=self.base_iri,
                local_prefix=self.local_prefix,
                precision=self.precision,
                export_profile=EXPORT_PROFILE_SCENE_GRAPH,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported scene graph Turtle to {result['output_path']}")
        report_spot_am_flattened(self, context)
        return {"FINISHED"}


class SPOT_OT_export_dataset_snapshot(bpy.types.Operator):
    bl_idname = "spot.export_dataset_snapshot"
    bl_label = "SPOT: Export Dataset Snapshot"
    bl_description = "Atomically export deterministic scene RDF into datasets/exports; shared asset bytes stay in datasets/assets"
    bl_options = {"REGISTER"}

    helper_collection: StringProperty(
        name="Helper Collection",
        default=HELPER_COLLECTION_NAME,
    )
    base_iri: StringProperty(
        name="Base IRI",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        default=6,
        min=0,
        max=12,
    )
    simulate_spatial_history: BoolProperty(default=False)
    spatial_history_start: StringProperty(default=DEFAULT_HISTORY_START)

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = export_dataset_snapshot(
                project_name=context.scene.spot_tools_settings.project_name,
                helper_collection_name=self.helper_collection,
                base_iri=self.base_iri,
                local_prefix=self.local_prefix,
                precision=self.precision,
                simulate_spatial_history=self.simulate_spatial_history,
                spatial_history_start=self.spatial_history_start,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Exported RDF dataset snapshot to {result['output_path']}",
        )
        return {"FINISHED"}


class SPOT_OT_export_topology_turtle_default(bpy.types.Operator):
    bl_idname = "spot.export_topology_turtle_default"
    bl_label = "SPOT: Export Topology Turtle"
    bl_description = "Export the SPOT helper scene as SPOT-only topology Turtle using the default output path"
    bl_options = {"REGISTER"}

    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to export",
        default=HELPER_COLLECTION_NAME,
    )
    base_iri: StringProperty(
        name="Base IRI",
        description="Base IRI used for exported local resources",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        description="Floating-point precision used for matrices and bounding boxes",
        default=6,
        min=0,
        max=12,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = export_helper_scene(
                helper_collection_name=self.helper_collection,
                base_iri=self.base_iri,
                local_prefix=self.local_prefix,
                precision=self.precision,
                export_profile=EXPORT_PROFILE_SPOT_ONLY,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported topology Turtle to {result['output_path']}")
        return {"FINISHED"}


class SPOT_OT_export_topology_turtle_as(ExportHelper, bpy.types.Operator):
    bl_idname = "spot.export_topology_turtle_as"
    bl_label = "SPOT: Export Topology Turtle As"
    bl_description = "Export the SPOT helper scene as SPOT-only topology Turtle and choose the destination path"
    bl_options = {"REGISTER"}

    filename_ext = ".ttl"
    filter_glob: StringProperty(default="*.ttl", options={"HIDDEN"})

    helper_collection: StringProperty(
        name="Helper Collection",
        description="Name of the helper collection to export",
        default=HELPER_COLLECTION_NAME,
    )
    base_iri: StringProperty(
        name="Base IRI",
        description="Base IRI used for exported local resources",
        default=DEFAULT_BASE_IRI,
    )
    local_prefix: StringProperty(
        name="Prefix",
        description="Turtle prefix used for exported local resources",
        default=DEFAULT_LOCAL_PREFIX,
    )
    precision: IntProperty(
        name="Precision",
        description="Floating-point precision used for matrices and bounding boxes",
        default=6,
        min=0,
        max=12,
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        if not self.filepath:
            self.filepath = str(
                default_output_path_for_profile(EXPORT_PROFILE_SPOT_ONLY)
            )
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            result = export_helper_scene(
                helper_collection_name=self.helper_collection,
                output_path=self.filepath,
                base_iri=self.base_iri,
                local_prefix=self.local_prefix,
                precision=self.precision,
                export_profile=EXPORT_PROFILE_SPOT_ONLY,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported topology Turtle to {result['output_path']}")
        return {"FINISHED"}


class SPOT_OT_project_asset_space_origin(bpy.types.Operator):
    bl_idname = "spot.project_asset_space_origin"
    bl_label = "SPOT: Project Asset Space Origin"
    bl_description = "Project the 3D cursor from the asset-space bbox using the selected anchor and absolute scene Z"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        try:
            root = resolve_asset_root_helper(settings.helper_collection)
            location = place_cursor_from_asset_space(
                root,
                settings.asset_space_origin_anchor,
                settings.asset_space_origin_z,
                context.scene.cursor,
            )
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                "Projected asset-space origin cursor to "
                f"({location.x:.3f}, {location.y:.3f}, {location.z:.3f})"
            ),
        )
        return {"FINISHED"}


class SPOT_OT_set_asset_space_origin(bpy.types.Operator):
    bl_idname = "spot.set_asset_space_origin"
    bl_label = "SPOT: Set Asset Space Origin"
    bl_description = "Move the site helper origin to the 3D cursor while preserving the world-space positions of all child helpers"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        try:
            root = resolve_asset_root_helper(settings.helper_collection)
            cursor_location = context.scene.cursor.location.copy()
            reanchor_asset_space_root(root, cursor_location)
            context.view_layer.update()
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Set asset-space origin to ({cursor_location.x:.3f}, "
                f"{cursor_location.y:.3f}, {cursor_location.z:.3f})"
            ),
        )
        return {"FINISHED"}


def loaded_ifc_file_name(context: bpy.types.Context) -> str | None:
    """The file name of the IFC that Bonsai loaded, if Bonsai is present."""
    bim_properties = getattr(context.scene, "BIMProperties", None)
    file_path = str(getattr(bim_properties, "ifc_file", "") or "").strip()
    return Path(file_path).name if file_path else None


def active_ifc_bounding_box(context: bpy.types.Context) -> bpy.types.Object | None:
    """
    The IFC bounding box the active object stands for: the box itself, or the
    box behind an active helper.
    """
    active_object = context.active_object
    if active_object is None:
        return None
    source_object = find_source_object_for_helper(active_object) or active_object
    if source_object.get(PROP_HELPER):
        return None
    if not is_ifc_model_class(parse_name_semantics(source_object.name).ifc_class):
        return None
    return source_object


class SPOT_OT_set_ifc_file(ImportHelper, bpy.types.Operator):
    bl_idname = "spot.set_ifc_file"
    bl_label = "SPOT: Set IFC File"
    bl_description = (
        "Choose the IFC file that the active IFC bounding box stands for. The "
        "export links this file name below /assets/ifc/"
    )
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ifc"
    filter_glob: StringProperty(default="*.ifc", options={"HIDDEN"})

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return active_ifc_bounding_box(context) is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        bbox_object = active_ifc_bounding_box(context)
        if bbox_object is None:
            self.report({"ERROR"}, "Select an IFC bounding box first")
            return {"CANCELLED"}
        file_name = Path(self.filepath).name
        if not file_name:
            self.report({"ERROR"}, "Choose an IFC file")
            return {"CANCELLED"}
        bbox_object[PROP_IFC_FILE] = file_name
        # Keep a helper that was built already in step, so an export without
        # a rebuild links the new file too.
        helper = find_helper_for_source_object(bbox_object)
        if helper is not None:
            helper[PROP_SOURCE_ASSET_PATH] = ifc_model_file_path(file_name)
        self.report({"INFO"}, f"'{bbox_object.name}' now links '{file_name}'")
        return {"FINISHED"}


class SPOT_OT_create_ifc_bounding_box(bpy.types.Operator):
    bl_idname = "spot.create_ifc_bounding_box"
    bl_label = "SPOT: Create IFC Bounding Box"
    bl_description = (
        "Build one EntitySpace bounding box per 'IfcProject/...' collection "
        "in the scene, spanning its geometry and its IFC root point, and sort "
        "it into the AssetSpace collection. Move the selected box into place; "
        "its placement is exported as the IFC model's translation"
    )
    bl_options = {"REGISTER", "UNDO"}

    collection_name: StringProperty(
        name="Fallback Collection",
        description=(
            "Collection the generated bounding boxes are linked into when the "
            "scene has no single AssetSpace collection"
        ),
        default="IFC Bounding Boxes",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        project_collections = find_ifc_project_collections(context.scene)
        if not project_collections:
            self.report(
                {"ERROR"},
                "No 'IfcProject/...' collection was found in the scene. This "
                "looks for the collection by name only, so it works whether "
                "or not Bonsai is enabled.",
            )
            return {"CANCELLED"}

        asset_collection = ifc_bounding_box_target_collection(context.scene)
        # Bonsai holds one loaded file. Its name is the real asset name, which
        # the `IfcProject` name (often `Projekt-001`) is not. With several
        # projects it is unclear which one the file belongs to.
        ifc_file = (
            loaded_ifc_file_name(context) if len(project_collections) == 1 else None
        )
        created: list[bpy.types.Object] = []
        existing: list[bpy.types.Object] = []
        try:
            for project_collection in project_collections:
                # A second run must not add a box at the original position
                # next to one already moved into place: both would export.
                previous = find_ifc_bounding_box(project_collection)
                if previous is not None:
                    existing.append(previous)
                    continue
                root_object = collection_object_named_like_collection(
                    project_collection
                )
                source_objects = ifc_bounding_box_source_objects(
                    [project_collection]
                )
                bbox_object = build_ifc_bounding_box(
                    source_objects,
                    root_object=root_object,
                    label=(
                        Path(ifc_file).stem
                        if ifc_file
                        else human_name(project_collection.name)
                    ),
                    collection_name=self.collection_name,
                    collection=asset_collection,
                    source_name=project_collection.name,
                    ifc_file=ifc_file,
                )
                created.append(bbox_object)
        except Exception as error:
            self.report({"ERROR"}, operator_error_message(error))
            return {"CANCELLED"}

        # Select the boxes so they can be moved into place right away. The
        # view layer has to sync the new objects before they can be selected.
        context.view_layer.update()
        selectable = [
            obj for obj in created + existing if obj.name in context.view_layer.objects
        ]
        if selectable:
            for obj in context.view_layer.objects.selected:
                obj.select_set(False)
            for obj in selectable:
                obj.select_set(True)
            context.view_layer.objects.active = selectable[0]

        messages: list[str] = []
        if created:
            target_name = (
                asset_collection.name
                if asset_collection is not None
                else self.collection_name
            )
            messages.append(
                f"Created {len(created)} bounding box(es) in '{target_name}': "
                f"{', '.join(obj.name for obj in created)}."
            )
            if asset_collection is None:
                messages.append(
                    "No single AssetSpace collection was found, so drag them "
                    "into the AssetSpace collection."
                )
        if existing:
            messages.append(
                "Kept existing bounding box(es): "
                f"{', '.join(obj.name for obj in existing)}."
            )
        if created and not ifc_file:
            messages.append(
                "The IFC file name is unknown: use 'Set IFC File' to choose it."
            )
        messages.append(
            "Move the selected box into place, then rebuild the helper scene."
        )
        self.report({"INFO"}, " ".join(messages))
        return {"FINISHED"}


def selected_setup_helpers(context: bpy.types.Context) -> list[bpy.types.Object]:
    """The helpers of the selected objects, each once, active first."""
    settings = context.scene.spot_tools_settings
    collection = bpy.data.collections.get(settings.helper_collection)
    if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
        return []
    candidates = [context.active_object, *context.selected_objects]
    helpers: list[bpy.types.Object] = []
    for candidate in candidates:
        helper = helper_for_reference(candidate, collection)
        if helper is not None and helper not in helpers:
            helpers.append(helper)
    return helpers


class SPOT_OT_set_topology_class(bpy.types.Operator):
    bl_idname = "spot.set_topology_class"
    bl_label = "SPOT: Set Topology Class"
    bl_description = (
        "Set the BOT or BROT class of the entity each selected space represents"
    )
    bl_options = {"REGISTER", "UNDO"}

    vocabulary: EnumProperty(
        name="Vocabulary",
        items=(
            (VOCABULARY_BOT, "BOT", "Set a BOT class"),
            (VOCABULARY_BROT, "BROT", "Set a BROT class"),
            ("NONE", "None", "Clear the class"),
        ),
        default="NONE",
        # Blender otherwise reuses the last values for a button that sets
        # none, and Clear Class would repeat the previous Set as.
        options={"SKIP_SAVE"},
    )
    class_name: StringProperty(
        name="Class",
        description="Class to set; empty clears the class",
        default="",
        options={"SKIP_SAVE"},
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        helpers = selected_setup_helpers(context)
        if not helpers:
            self.report({"ERROR"}, "Select one or more spaces or their SPOT helpers")
            return {"CANCELLED"}
        changed = 0
        for helper in helpers:
            try:
                set_helper_topology_class(
                    helper, self.vocabulary, self.class_name or None
                )
            except ValueError as error:
                self.report({"WARNING"}, str(error))
                continue
            changed += 1
        if self.class_name:
            message = f"Set {changed} space(s) to {self.vocabulary.lower()}:{self.class_name}"
        else:
            message = f"Cleared the class of {changed} space(s)"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SPOT_OT_make_camera(bpy.types.Operator):
    bl_idname = "spot.make_camera"
    bl_label = "SPOT: Set as Camera"
    bl_description = (
        "Give each selected PointSpace a perspective camera, so it exports as "
        "a photograph with a sosa:Observation"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(
        name="Image",
        description="The photograph; needed when the PointSpace has no image yet",
        subtype="FILE_PATH",
        options={"SKIP_SAVE"},
    )
    filter_image: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        helpers = [
            helper
            for helper in selected_setup_helpers(context)
            if helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_POINT
        ]
        if len(helpers) == 1 and not helpers[0].get(PROP_SOURCE_ASSET_PATH):
            context.window_manager.fileselect_add(self)
            return {"RUNNING_MODAL"}
        return self.execute(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        helpers = [
            helper
            for helper in selected_setup_helpers(context)
            if helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_POINT
        ]
        if not helpers:
            self.report({"ERROR"}, "Select one or more PointSpaces or their helpers")
            return {"CANCELLED"}
        image_path = (
            Path(bpy.path.abspath(self.filepath)).name if self.filepath else None
        )
        made = 0
        for helper in helpers:
            was_active = context.view_layer.objects.active == helper
            try:
                camera = make_helper_camera(
                    helper, image_path if len(helpers) == 1 else None
                )
            except ValueError as error:
                self.report({"WARNING"}, str(error))
                continue
            camera.select_set(True)
            if was_active:
                context.view_layer.objects.active = camera
            made += 1
        self.report({"INFO"}, f"{made} PointSpace(s) are now cameras")
        return {"FINISHED"} if made else {"CANCELLED"}


class SPOT_OT_use_active_helper_for_test(bpy.types.Operator):
    bl_idname = "spot.use_active_helper_for_test"
    bl_label = "SPOT: Use Active Helper for Test"
    bl_description = (
        "Copy the active SPOT helper object into one of the SPOT Test slots"
    )
    bl_options = {"REGISTER", "UNDO"}

    slot: EnumProperty(
        name="Slot",
        items=(
            ("LEFT", "Left", "Assign the active helper to the left test slot"),
            ("RIGHT", "Right", "Assign the active helper to the right test slot"),
            (
                "REFERENCE",
                "Reference",
                "Assign the active helper to the reference test slot",
            ),
        ),
        default="RIGHT",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        helper = context.active_object
        if helper is None or not helper.get(PROP_HELPER):
            self.report({"ERROR"}, "Select an active SPOT helper object first")
            return {"CANCELLED"}

        collection = bpy.data.collections.get(settings.helper_collection)
        if collection is not None and collection.get(HELPER_COLLECTION_MARKER):
            if collection.objects.get(helper.name) is None:
                self.report(
                    {"ERROR"},
                    f"Active helper '{helper.name}' is not part of '{settings.helper_collection}'",
                )
                return {"CANCELLED"}

        if self.slot == "LEFT":
            settings.test_left_helper = helper.name
        elif self.slot == "RIGHT":
            settings.test_right_helper = helper.name
        else:
            settings.test_reference_helper = helper.name

        self.report(
            {"INFO"}, f"Assigned '{helper.name}' to the {self.slot.lower()} test slot"
        )
        return {"FINISHED"}


class SPOT_OT_write_test_report(bpy.types.Operator):
    bl_idname = "spot.write_test_report"
    bl_label = "SPOT: Write Test Report"
    bl_description = (
        "Write the current Blender-side SPOT test results into a Blender text datablock"
    )
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.spot_tools_settings
        collection = bpy.data.collections.get(settings.helper_collection)
        if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
            self.report(
                {"ERROR"},
                f"Helper collection '{settings.helper_collection}' was not found or is not a SPOT helper scene",
            )
            return {"CANCELLED"}

        report = build_spot_test_report(context)
        text = bpy.data.texts.get(SPOT_TEST_RESULTS_TEXT_NAME)
        if text is None:
            text = bpy.data.texts.new(SPOT_TEST_RESULTS_TEXT_NAME)
        text.clear()
        text.write(report)

        self.report({"INFO"}, f"Wrote Blender-side SPOT test report to '{text.name}'")
        return {"FINISHED"}


class VIEW3D_PT_spot_tools(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_spot_tools"
    bl_label = "SPOT"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SPOT"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        settings = context.scene.spot_tools_settings

        helper_box = layout.box()
        helper_box.label(text="Helper Scene")
        helper_box.prop(settings, "project_name", text="Project")
        helper_box.prop_search(
            settings,
            "project_collection",
            bpy.data,
            "collections",
            text="Source Collection",
        )
        helper_box.prop_search(
            settings,
            "helper_collection",
            bpy.data,
            "collections",
            text="Helper",
        )
        helper_box.prop(settings, "visible_only_sources")
        helper_row = helper_box.row(align=True)
        build_op = helper_row.operator(
            SPOT_OT_build_helper_scene.bl_idname,
            text="Build",
        )
        build_op.project_collection = settings.project_collection
        build_op.helper_collection = settings.helper_collection
        build_op.visible_only_sources = settings.visible_only_sources
        import_op = helper_row.operator(
            SPOT_OT_import_turtle_as.bl_idname,
            text="Import TTL",
        )
        import_op.helper_collection = settings.helper_collection
        dataset_import = helper_box.operator(
            SPOT_OT_import_dataset_folder.bl_idname,
            text="Import Dataset Folder",
        )
        dataset_import.helper_collection = settings.helper_collection

        transition_box = layout.box()
        transition_box.label(text="SPOT-AM to SPOT-SG")
        am_count = sum(
            1 for obj in context.scene.objects if obj.get(PROP_HELPER) and is_placed_by_spot_am(obj)
        )
        sg_count = sum(
            1
            for obj in context.scene.objects
            if obj.get(PROP_HELPER) and PROP_SG_STATE in obj and obj.parent is not None
        )
        transition_box.label(
            text=(
                f"{am_count} placed by SPOT-AM, {sg_count} committed, "
                f"{len(read_log(context.scene))} activities"
            ),
        )
        transition_box.prop(settings, "provenance_agent")
        transition_box.operator(
            SPOT_OT_commit_sg_placement.bl_idname,
            text="Commit Selected as SPOT-SG",
        )
        transition_box.operator(
            SPOT_OT_export_transition_dataset.bl_idname,
            text="Export Transition Dataset",
        )

        bbox_box = layout.box()
        bbox_box.label(text="IFC Bounding Box")
        bbox_box.label(
            text="Finds every 'IfcProject/...' collection in the scene:",
        )
        bbox_box.operator(
            SPOT_OT_create_ifc_bounding_box.bl_idname,
            text="Create Bounding Box",
        )
        bbox_box.label(
            text="Sorts the boxes into the AssetSpace collection.",
        )
        bbox_box.label(
            text="Move a box into place, then rebuild and export.",
        )
        ifc_box = active_ifc_bounding_box(context)
        if ifc_box is not None:
            bbox_box.label(text=f"Box: {human_name(ifc_box.name)}")
            if ifc_box.get(PROP_IFC_FILE):
                bbox_box.prop(ifc_box, f'["{PROP_IFC_FILE}"]', text="IFC File")
            else:
                bbox_box.label(
                    text="IFC file unknown, guessed from the box label",
                    icon="ERROR",
                )
            bbox_box.operator(SPOT_OT_set_ifc_file.bl_idname, text="Set IFC File...")

        origin_box = layout.box()
        origin_box.label(text="Asset Space Origin")
        origin_box.prop(settings, "asset_space_origin_anchor")
        origin_box.prop(settings, "asset_space_origin_z")

        try:
            root = resolve_asset_root_helper(settings.helper_collection)
            root_location = root.matrix_world.translation
            origin_box.label(
                text=(
                    f"Site origin: {root_location.x:.3f}, {root_location.y:.3f}, "
                    f"{root_location.z:.3f}"
                ),
            )
        except RuntimeError as error:
            origin_box.label(text=str(error), icon="ERROR")

        origin_box.operator(
            SPOT_OT_project_asset_space_origin.bl_idname,
            text=SPOT_OT_project_asset_space_origin.bl_label,
        )
        origin_box.operator(
            SPOT_OT_set_asset_space_origin.bl_idname,
            text="Set Asset Space Origin",
        )

        asset_axis_box = layout.box()
        asset_axis_box.label(text="Asset Space Directions")
        asset_axis_box.label(
            text="Defines the reference frame for RELOC front/rear/right/left/top/bottom",
        )

        direction_target, direction_helper, direction_error = (
            resolve_asset_direction_helper(
                context,
                settings.project_collection,
                settings.helper_collection,
            )
        )
        if direction_target is None:
            asset_axis_box.label(
                text=direction_error or "No AssetSpace helper available",
                icon="ERROR",
            )
        else:
            ensure_asset_direction_defaults(direction_target)
            asset_axis_box.label(
                text=f"Source: {direction_target.name}",
            )
            if direction_helper is not None:
                asset_axis_box.label(
                    text=f"Linked AssetSpace: {helper_display_name(direction_helper)}",
                )
            asset_axis_box.prop(
                direction_target,
                PROP_ASSET_AXIS_X_DIRECTION,
                text="X axis",
            )
            asset_axis_box.prop(
                direction_target,
                PROP_ASSET_AXIS_Y_DIRECTION,
                text="Y axis",
            )
            asset_axis_box.prop(
                direction_target,
                PROP_ASSET_AXIS_Z_DIRECTION,
                text="Z axis",
            )

        represents_box = layout.box()
        represents_box.label(text="Also Represents")
        represents_owner, represents_helper, represents_error = (
            resolve_represents_owner(context)
        )
        if represents_owner is None:
            represents_box.label(
                text=represents_error or "No space selected", icon="INFO"
            )
        else:
            represents_box.label(text=f"Source: {represents_owner.name}")
            represents_box.label(
                text=f"Own class: {helper_topology_label(represents_helper)}"
            )
            add_row = represents_box.row(align=True)
            add_row.prop(settings, "represents_candidate", text="")
            add_row.operator(
                SPOT_OT_add_represents_link.bl_idname, text="Add", icon="ADD"
            )
            links = getattr(represents_owner, PROP_REPRESENTS_LINKS)
            if not links:
                represents_box.label(text="No further BOT entities", icon="INFO")
            for index, entry in enumerate(links):
                row = represents_box.row(align=True)
                name = represents_link_name(entry)
                row.label(
                    text=name or "Missing link",
                    icon="ERROR" if name is None else "NONE",
                )
                remove = row.operator(
                    SPOT_OT_remove_represents_link.bl_idname, text="", icon="X"
                )
                remove.index = index

        am_box = layout.box()
        am_box.label(text="SPOT-AM Authoring")
        am_box.label(
            text="Author boundary-point mappings on the selected source object",
        )
        am_box.label(
            text="Partial boundary mappings synthesize a fuzzy region for preview and export",
        )

        am_owner, am_helper, am_error = resolve_am_authoring_helper(
            context,
            settings.project_collection,
            settings.helper_collection,
        )
        if am_owner is None:
            am_box.label(text=am_error or "No helper selected", icon="INFO")
        else:
            am_box.label(text=f"Source: {am_owner.name}")
            am_box.label(
                text=(
                    f"Linked helper: {helper_display_name(am_helper)}"
                    if am_helper is not None
                    else "Linked helper: not generated yet"
                )
            )
            am_box.prop(am_owner, PROP_AM_ENABLED, text="Use SPOT-AM export")

            if bool(getattr(am_owner, PROP_AM_ENABLED, False)):
                normalized_row = am_box.row()
                normalized_row.enabled = False
                normalized_row.prop(
                    am_owner,
                    PROP_AM_USE_NORMALIZED_COORDINATES,
                    text="Include normalized coordinates (future)",
                )
                am_box.prop(
                    am_owner, PROP_AM_REFERENCE_ASSET, text="Reference AssetSpace"
                )
                if not getattr(am_owner, PROP_AM_REFERENCE_ASSET, False):
                    am_box.prop_search(
                        am_owner,
                        PROP_AM_REFERENCE_HELPER,
                        bpy.data,
                        "objects",
                        text="Reference object",
                    )

                axis_box = am_box.box()
                axis_box.label(text="Axis mappings")
                for axis_name in ("X", "Y", "Z"):
                    row = axis_box.row(align=True)
                    row.label(text=axis_name)
                    row.prop(
                        am_owner,
                        am_axis_target_prop_name(axis_name),
                        text="Target",
                    )
                    row.prop(
                        am_owner,
                        am_axis_relation_prop_name(axis_name),
                        text="Relation",
                    )
                    row.prop(
                        am_owner,
                        am_axis_accuracy_prop_name(axis_name),
                        text="Accuracy",
                    )

                boundary_box = am_box.box()
                boundary_box.label(text="Boundary-point mappings")
                boundary_box.label(
                    text="Unset slots leave the fuzzy region broad on that axis",
                )
                for slot, _axis_name, _bound_kind, _slot_label in BOUNDARY_POINT_SLOTS:
                    _axis_name, _bound_kind, slot_label = boundary_point_slot_details(
                        slot
                    )
                    slot_box = boundary_box.box()
                    slot_box.label(text=slot_label)
                    slot_box.prop_search(
                        am_owner,
                        am_boundary_target_prop_name(slot),
                        bpy.data,
                        "objects",
                        text="Target object override",
                    )
                    row = slot_box.row(align=True)
                    row.prop(
                        am_owner,
                        am_boundary_relation_prop_name(slot),
                        text="Relation",
                    )
                    row.prop(
                        am_owner,
                        am_boundary_accuracy_prop_name(slot),
                        text="Accuracy",
                    )

        issue_box = layout.box()
        issue_box.label(text="Picture Observation / BCF Issue")
        picture_owner, picture_helper, picture_error = resolve_picture_owner_and_helper(
            context, settings.helper_collection
        )
        if picture_owner is None:
            issue_box.label(text=picture_error or "No picture selected", icon="INFO")
        else:
            issue_box.label(text=f"Picture: {picture_owner.name}")
            issue_box.label(text=f"PointSpace: {helper_display_name(picture_helper)}")

            damages_box = issue_box.box()
            damages_box.label(text="Observed damages")
            damage_add_row = damages_box.row(align=True)
            damage_add_row.prop(settings, "picture_damage_candidate", text="")
            damage_add_row.operator(
                SPOT_OT_add_picture_damage.bl_idname,
                text="Add",
                icon="ADD",
            )
            damages = getattr(picture_owner, PROP_OBSERVED_DAMAGES)
            if not damages:
                damages_box.label(text="No damage features of interest", icon="INFO")
            for index, entry in enumerate(damages):
                row = damages_box.row(align=True)
                row.label(text=entry.object.name if entry.object else "Missing damage", icon="ERROR" if entry.object is None else "NONE")
                remove = row.operator(
                    SPOT_OT_remove_picture_damage.bl_idname,
                    text="",
                    icon="X",
                )
                remove.index = index

            issue_box.prop(picture_owner, PROP_BCF_ISSUE_ENABLED)
            if getattr(picture_owner, PROP_BCF_ISSUE_ENABLED):
                issue_box.prop(picture_owner, PROP_BCF_TITLE)
                issue_box.prop(picture_owner, PROP_BCF_DESCRIPTION)
                issue_box.prop(picture_owner, PROP_BCF_HAS_COMMENT)
                if getattr(picture_owner, PROP_BCF_HAS_COMMENT):
                    issue_box.prop(picture_owner, PROP_BCF_COMMENT_TEXT)
                issue_box.prop(picture_owner, PROP_BCF_HAS_STATUS_HISTORY)
                issue_box.prop(picture_owner, PROP_BCF_HAS_COMPONENT_SELECTION)
                issue_box.prop(picture_owner, PROP_BCF_HAS_COLORING)
                issue_box.prop(picture_owner, PROP_BCF_HAS_CLIPPING_PLANE)
                issue_box.prop(picture_owner, PROP_BCF_HAS_LINE)
                issue_box.prop(picture_owner, PROP_BCF_HAS_BITMAP)

                components_box = issue_box.box()
                components_box.label(text="BCF components")
                component_add_row = components_box.row(align=True)
                component_add_row.prop(
                    settings, "picture_component_candidate", text=""
                )
                component_add_row.operator(
                    SPOT_OT_add_picture_component.bl_idname,
                    text="Add",
                    icon="ADD",
                )
                components = getattr(picture_owner, PROP_BCF_COMPONENTS)
                if not components:
                    components_box.label(text="No BOT elements linked", icon="INFO")
                for index, entry in enumerate(components):
                    row = components_box.row(align=True)
                    row.label(text=entry.object.name if entry.object else "Missing component", icon="ERROR" if entry.object is None else "NONE")
                    remove = row.operator(
                        SPOT_OT_remove_picture_component.bl_idname,
                        text="",
                        icon="X",
                    )
                    remove.index = index

        provenance_box = layout.box()
        provenance_box.label(text="Spatial Provenance Simulation")
        provenance_box.prop(settings, "simulate_spatial_history")
        if settings.simulate_spatial_history:
            provenance_box.prop(settings, "spatial_history_start")

        selected = context.active_object
        selected_helper = None
        if selected is not None:
            if selected.get(PROP_HELPER):
                selected_helper = selected
            else:
                selected_helper = find_helper_for_source_object(selected)
                if selected_helper is None:
                    for candidate in bpy.data.objects:
                        if not candidate.get(PROP_HELPER):
                            continue
                        source_collection = candidate.get(PROP_SOURCE_COLLECTION)
                        if source_collection and any(
                            collection.name == str(source_collection)
                            for collection in selected.users_collection
                        ):
                            selected_helper = candidate
                            break
        if selected_helper is not None and (
            eligible_plan(selected_helper) or eligible_camera(selected_helper)
        ):
            placement_owners = placement_change_owners(selected_helper)
            for placement_owner in placement_owners:
                provenance_box.prop(
                    placement_owner,
                    PROP_SIMULATE_PLACEMENT_CHANGE,
                    text=(
                        "Simulate placement change"
                        if len(placement_owners) == 1
                        else f"{placement_owner.name}: placement change"
                    ),
                )
        else:
            provenance_box.label(
                text="Select an eligible plan, camera, or its SPOT helper",
                icon="INFO",
            )

        export_box = layout.box()
        export_box.label(text="Export")
        export_box.prop(settings, "base_iri")
        export_box.prop(settings, "local_prefix")
        export_box.prop(settings, "precision")

        graph_row = export_box.row(align=True)
        graph_default = graph_row.operator(
            SPOT_OT_export_turtle_default.bl_idname,
            text="Scene Graph",
        )
        graph_default.helper_collection = settings.helper_collection
        graph_default.base_iri = settings.base_iri
        graph_default.local_prefix = settings.local_prefix
        graph_default.precision = settings.precision

        graph_as = graph_row.operator(
            SPOT_OT_export_turtle_as.bl_idname,
            text="As...",
        )
        graph_as.helper_collection = settings.helper_collection
        graph_as.base_iri = settings.base_iri
        graph_as.local_prefix = settings.local_prefix
        graph_as.precision = settings.precision

        topology_row = export_box.row(align=True)
        topology_default = topology_row.operator(
            SPOT_OT_export_topology_turtle_default.bl_idname,
            text="Topology",
        )
        topology_default.helper_collection = settings.helper_collection
        topology_default.base_iri = settings.base_iri
        topology_default.local_prefix = settings.local_prefix
        topology_default.precision = settings.precision

        topology_as = topology_row.operator(
            SPOT_OT_export_topology_turtle_as.bl_idname,
            text="As...",
        )
        topology_as.helper_collection = settings.helper_collection
        topology_as.base_iri = settings.base_iri
        topology_as.local_prefix = settings.local_prefix
        topology_as.precision = settings.precision

        snapshot = export_box.operator(
            SPOT_OT_export_dataset_snapshot.bl_idname,
            text="Dataset Snapshot",
        )
        snapshot.helper_collection = settings.helper_collection
        snapshot.base_iri = settings.base_iri
        snapshot.local_prefix = settings.local_prefix
        snapshot.precision = settings.precision
        snapshot.simulate_spatial_history = settings.simulate_spatial_history
        snapshot.spatial_history_start = settings.spatial_history_start


class VIEW3D_PT_spot_setup(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_spot_setup"
    bl_label = "SPOT Setup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SPOT"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        helpers = selected_setup_helpers(context)
        status = layout.box()
        if not helpers:
            status.label(text="Select spaces or their SPOT helpers", icon="INFO")
        else:
            status.label(text=f"{helper_display_name(helpers[0])}")
            status.label(text=f"SPOT: {helpers[0].get(PROP_SPOT_CLASS, 'none')}")
            status.label(text=f"Represents: {helper_topology_label(helpers[0])}")
            camera = helper_camera(helpers[0])
            if camera is not None:
                status.label(text=f"Camera: {camera.name}", icon="CAMERA_DATA")
            if len(helpers) > 1:
                status.label(text=f"{len(helpers)} spaces selected")

        for title, classes, vocabulary in (
            ("Set as BOT", BOT_SETUP_CLASSES, VOCABULARY_BOT),
            ("Set as BROT (bridge)", BROT_SETUP_CLASSES, VOCABULARY_BROT),
        ):
            box = layout.box()
            box.enabled = bool(helpers)
            box.label(text=title)
            grid = box.grid_flow(columns=2, even_columns=True, align=True)
            for class_name in classes:
                operator = grid.operator(
                    SPOT_OT_set_topology_class.bl_idname,
                    text=class_name,
                )
                operator.vocabulary = vocabulary
                operator.class_name = class_name

        clear_row = layout.row()
        clear_row.enabled = bool(helpers)
        clear = clear_row.operator(
            SPOT_OT_set_topology_class.bl_idname,
            text="Clear Class",
            icon="X",
        )
        clear.vocabulary = "NONE"
        clear.class_name = ""

        camera_box = layout.box()
        camera_box.label(text="Photograph")
        camera_row = camera_box.row()
        camera_row.enabled = any(
            helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_POINT for helper in helpers
        )
        camera_row.operator(
            SPOT_OT_make_camera.bl_idname,
            text="Set as Camera",
            icon="CAMERA_DATA",
        )


class VIEW3D_PT_spot_test(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_spot_test"
    bl_label = "SPOT Test"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SPOT"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        settings = context.scene.spot_tools_settings
        collection = bpy.data.collections.get(settings.helper_collection)

        if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
            layout.label(
                text=(
                    f"Helper collection '{settings.helper_collection}' is not available "
                    "for SPOT Test"
                ),
                icon="ERROR",
            )
            return

        left = resolve_test_helper_object(
            settings.helper_collection, settings.test_left_helper
        )
        right = resolve_test_helper_object(
            settings.helper_collection, settings.test_right_helper
        )
        reference = resolve_test_helper_object(
            settings.helper_collection,
            settings.test_reference_helper,
        )
        cursor = context.scene.cursor.location.copy()

        pair_box = layout.box()
        pair_box.label(text="Pair and Cursor Checks")
        draw_test_slot(pair_box, settings, "test_left_helper", "Left", "LEFT")
        draw_test_slot(pair_box, settings, "test_right_helper", "Right", "RIGHT")

        if left is not None and right is not None:
            left_box = helper_world_bbox(left)
            right_box = helper_world_bbox(right)
            pair_box.label(
                text=f"distance: {format_number(distance_between_aabbs(left_box, right_box))}"
            )
            pair_box.label(
                text=f"absolutePosition(right): {vector_to_wkt_point(right.matrix_world.translation)}",
            )
            pair_box.label(
                text=f"contains(left, right): {str(aabb_contains(left_box, right_box)).lower()}"
            )
            pair_box.label(
                text=f"inside(left, right): {str(aabb_contains(right_box, left_box)).lower()}"
            )
            pair_box.label(
                text=f"intersects: {str(aabbs_intersect(left_box, right_box)).lower()}"
            )
            pair_box.label(
                text=f"equals: {str(aabbs_equal(left_box, right_box)).lower()}"
            )
            pair_box.label(
                text=f"touches: {str(aabbs_touch(left_box, right_box)).lower()}"
            )
            pair_box.label(
                text=f"overlaps: {str(aabbs_overlap(left_box, right_box)).lower()}"
            )
            pair_box.label(
                text=f"disconnected: {str(aabbs_disconnected(left_box, right_box)).lower()}"
            )
        else:
            pair_box.label(
                text="Pick left and right helpers to mirror the pair-wise queries",
                icon="INFO",
            )

        if right is not None:
            right_box = helper_world_bbox(right)
            pair_box.separator()
            pair_box.label(text=f"cursor: {vector_to_text(cursor)}")
            pair_box.label(
                text=(
                    f"cursor distance -> right: {format_number(distance_point_to_aabb(cursor, right_box))}"
                ),
            )
            pair_box.label(
                text=f"cursor inside right: {str(point_inside_aabb(cursor, right_box)).lower()}",
            )

        reference_box = layout.box()
        reference_box.label(text="Reference Queries")
        draw_test_slot(
            reference_box,
            settings,
            "test_reference_helper",
            "Reference",
            "REFERENCE",
        )
        reference_box.prop(settings, "test_radius")
        reference_box.prop(settings, "test_limit")

        if reference is not None:
            entity_helpers = helper_entity_space_objects(settings.helper_collection)
            all_helpers = helper_space_objects(settings.helper_collection)

            draw_helper_result_list(
                reference_box,
                "Within Radius",
                query_helpers_within_radius(
                    reference, entity_helpers, settings.test_radius
                ),
                settings.test_limit,
            )
            draw_helper_result_list(
                reference_box,
                "Inside Reference",
                query_helpers_inside_reference(reference, entity_helpers),
                settings.test_limit,
            )
            draw_helper_result_list(
                reference_box,
                "Containers of Reference",
                query_helpers_containing_reference(reference, all_helpers),
                settings.test_limit,
            )
            draw_helper_result_list(
                reference_box,
                "Intersects Reference",
                query_helpers_intersecting_reference(reference, entity_helpers),
                settings.test_limit,
            )
            draw_helper_result_list(
                reference_box,
                "Disconnected from Reference",
                query_helpers_disconnected_from_reference(reference, entity_helpers),
                settings.test_limit,
            )
        else:
            reference_box.label(
                text="Pick a reference helper to preview the list-style queries",
                icon="INFO",
            )

        layout.operator(
            SPOT_OT_write_test_report.bl_idname,
            text="Write SPOT Test Report",
        )


class TOPBAR_MT_spot_tools(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_spot_tools"
    bl_label = "SPOT"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.operator(
            SPOT_OT_import_turtle_as.bl_idname,
            text=SPOT_OT_import_turtle_as.bl_label,
        )
        layout.separator()
        layout.operator(
            SPOT_OT_build_helper_scene.bl_idname,
            text=SPOT_OT_build_helper_scene.bl_label,
        )
        layout.separator()
        layout.operator(
            SPOT_OT_project_asset_space_origin.bl_idname,
            text=SPOT_OT_project_asset_space_origin.bl_label,
        )
        layout.operator(
            SPOT_OT_set_asset_space_origin.bl_idname,
            text=SPOT_OT_set_asset_space_origin.bl_label,
        )
        layout.separator()
        layout.operator(
            SPOT_OT_export_turtle_default.bl_idname,
            text=SPOT_OT_export_turtle_default.bl_label,
        )
        layout.operator(
            SPOT_OT_export_turtle_as.bl_idname,
            text=SPOT_OT_export_turtle_as.bl_label,
        )
        layout.operator(
            SPOT_OT_export_dataset_snapshot.bl_idname,
            text=SPOT_OT_export_dataset_snapshot.bl_label,
        )
        layout.separator()
        layout.operator(
            SPOT_OT_export_topology_turtle_default.bl_idname,
            text=SPOT_OT_export_topology_turtle_default.bl_label,
        )
        layout.operator(
            SPOT_OT_export_topology_turtle_as.bl_idname,
            text=SPOT_OT_export_topology_turtle_as.bl_label,
        )


def menu_func_file(self: bpy.types.TOPBAR_MT_file, context: bpy.types.Context) -> None:
    self.layout.separator()
    self.layout.menu(TOPBAR_MT_spot_tools.bl_idname)


def menu_func_export(
    self: bpy.types.TOPBAR_MT_file_export, context: bpy.types.Context
) -> None:
    self.layout.operator(
        SPOT_OT_export_turtle_as.bl_idname,
        text="SPOT Scene Graph (.ttl)",
    )
    self.layout.operator(
        SPOT_OT_export_dataset_snapshot.bl_idname,
        text="SPOT Dataset Snapshot",
    )
    self.layout.operator(
        SPOT_OT_export_topology_turtle_as.bl_idname,
        text="SPOT Topology (.ttl)",
    )


def menu_func_import(
    self: bpy.types.TOPBAR_MT_file_import, context: bpy.types.Context
) -> None:
    self.layout.operator(
        SPOT_OT_import_turtle_as.bl_idname,
        text="SPOT Scene Graph (.ttl)",
    )
    self.layout.operator(
        SPOT_OT_import_dataset_folder.bl_idname,
        text="SPOT Dataset Folder",
    )


class SPOT_AP_addon_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    datasets_dir: StringProperty(
        name="Datasets Directory",
        description="Directory that receives dataset snapshots below its exports folder",
        subtype="DIR_PATH",
        default="",
    )

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.prop(self, "datasets_dir")


CLASSES = (
    SPOT_AP_addon_preferences,
    SPOT_PG_object_reference,
    SPOT_PG_represents_link,
    SPOT_PG_scene_settings,
    SPOT_OT_add_picture_damage,
    SPOT_OT_remove_picture_damage,
    SPOT_OT_add_picture_component,
    SPOT_OT_remove_picture_component,
    SPOT_OT_add_represents_link,
    SPOT_OT_remove_represents_link,
    SPOT_OT_build_helper_scene,
    SPOT_OT_import_turtle_as,
    SPOT_OT_import_dataset_folder,
    SPOT_OT_commit_sg_placement,
    SPOT_OT_export_transition_dataset,
    SPOT_OT_export_turtle_default,
    SPOT_OT_export_turtle_as,
    SPOT_OT_export_dataset_snapshot,
    SPOT_OT_export_topology_turtle_default,
    SPOT_OT_export_topology_turtle_as,
    SPOT_OT_set_ifc_file,
    SPOT_OT_create_ifc_bounding_box,
    SPOT_OT_project_asset_space_origin,
    SPOT_OT_set_asset_space_origin,
    SPOT_OT_set_topology_class,
    SPOT_OT_make_camera,
    SPOT_OT_use_active_helper_for_test,
    SPOT_OT_write_test_report,
    VIEW3D_PT_spot_tools,
    VIEW3D_PT_spot_setup,
    VIEW3D_PT_spot_test,
    TOPBAR_MT_spot_tools,
)


def register_class_safe(cls: type[bpy.types.Struct]) -> None:
    try:
        bpy.utils.register_class(cls)
    except ValueError:
        pass


def unregister_class_safe(cls: type[bpy.types.Struct]) -> None:
    try:
        bpy.utils.unregister_class(cls)
    except RuntimeError:
        pass


def register() -> None:
    for cls in CLASSES:
        register_class_safe(cls)
    register_object_properties()
    for owner_type in (bpy.types.Object, bpy.types.Collection):
        if hasattr(owner_type, PROP_REPRESENTS_LINKS):
            delattr(owner_type, PROP_REPRESENTS_LINKS)
        setattr(
            owner_type,
            PROP_REPRESENTS_LINKS,
            CollectionProperty(
                name="Also represents",
                description="Further BOT entities this space represents, such as the storey a floor plan stands for",
                type=SPOT_PG_represents_link,
            ),
        )
    for owner_type in (bpy.types.Object, bpy.types.Collection):
        if hasattr(owner_type, PROP_SIMULATE_PLACEMENT_CHANGE):
            delattr(owner_type, PROP_SIMULATE_PLACEMENT_CHANGE)
        setattr(
            owner_type,
            PROP_SIMULATE_PLACEMENT_CHANGE,
            BoolProperty(
                name="Simulate placement change",
                description="Add one synthetic placement correction when spatial history simulation is enabled",
                default=False,
            ),
        )
    if hasattr(bpy.types.Object, PROP_AM_ENABLED):
        delattr(bpy.types.Object, PROP_AM_ENABLED)
    bpy.types.Object.spot_am_enabled = BoolProperty(
        name="Use SPOT-AM export",
        description="Export this source object through SPOT-AM boundary-point mappings with synthesized fuzzy-region bounds instead of SPOT-SG geometry",
        default=False,
    )
    if hasattr(bpy.types.Object, PROP_AM_USE_NORMALIZED_COORDINATES):
        delattr(bpy.types.Object, PROP_AM_USE_NORMALIZED_COORDINATES)
    bpy.types.Object.spot_am_use_normalized_coordinates = BoolProperty(
        name="Export normalized coordinates",
        description="Reserved for future normalized-coordinate export; currently ignored by the fuzzy-region workflow",
        default=False,
    )
    if hasattr(bpy.types.Object, PROP_AM_REFERENCE_HELPER):
        delattr(bpy.types.Object, PROP_AM_REFERENCE_HELPER)
    if hasattr(bpy.types.Object, PROP_AM_REFERENCE_ASSET):
        delattr(bpy.types.Object, PROP_AM_REFERENCE_ASSET)
    bpy.types.Object.spot_am_reference_helper = StringProperty(
        name="SPOT-AM reference object",
        description="Primary reference object used for the SPOT-AM appearance and boundary mappings",
        default="",
    )
    bpy.types.Object.spot_am_reference_asset = BoolProperty(
        name="Reference AssetSpace",
        description="Use the exported AssetSpace as the primary SPOT-AM reference target",
        default=False,
    )
    for axis_name in ("X", "Y", "Z"):
        target_prop = am_axis_target_prop_name(axis_name)
        relation_prop = am_axis_relation_prop_name(axis_name)
        accuracy_prop = am_axis_accuracy_prop_name(axis_name)
        for prop_name in (target_prop, relation_prop, accuracy_prop):
            if hasattr(bpy.types.Object, prop_name):
                delattr(bpy.types.Object, prop_name)
        setattr(
            bpy.types.Object,
            target_prop,
            EnumProperty(
                name=f"{axis_name} target axis",
                description=f"Target axis for the source {axis_name} axis",
                items=AM_TARGET_AXIS_ITEMS,
                default=axis_name,
            ),
        )
        setattr(
            bpy.types.Object,
            relation_prop,
            EnumProperty(
                name=f"{axis_name} relation",
                description=f"Axis relation type for the source {axis_name} axis",
                items=AM_AXIS_RELATION_ITEMS,
                default="Codirectional",
            ),
        )
        setattr(
            bpy.types.Object,
            accuracy_prop,
            EnumProperty(
                name=f"{axis_name} accuracy",
                description=f"Accuracy for the source {axis_name} axis mapping",
                items=AM_ACCURACY_ITEMS,
                default="Exact",
            ),
        )
    for slot, _axis_name, _bound_kind, slot_label in BOUNDARY_POINT_SLOTS:
        target_prop = am_boundary_target_prop_name(slot)
        relation_prop = am_boundary_relation_prop_name(slot)
        accuracy_prop = am_boundary_accuracy_prop_name(slot)
        for prop_name in (target_prop, relation_prop, accuracy_prop):
            if hasattr(bpy.types.Object, prop_name):
                delattr(bpy.types.Object, prop_name)
        setattr(
            bpy.types.Object,
            target_prop,
            StringProperty(
                name=f"{slot_label} target override",
                description=f"Optional target object override for the {slot_label} boundary-point mapping",
                default="",
            ),
        )
        setattr(
            bpy.types.Object,
            relation_prop,
            EnumProperty(
                name=f"{slot_label} relation",
                description=f"RELOC relation for the {slot_label} boundary-point mapping",
                items=AM_BOUNDARY_RELATION_ITEMS,
                default="",
            ),
        )
        setattr(
            bpy.types.Object,
            accuracy_prop,
            EnumProperty(
                name=f"{slot_label} accuracy",
                description=f"Accuracy for the {slot_label} boundary-point mapping",
                items=AM_ACCURACY_ITEMS,
                default="Approximate",
            ),
        )
    for prop_name, axis_name in (
        (PROP_ASSET_AXIS_X_DIRECTION, "X"),
        (PROP_ASSET_AXIS_Y_DIRECTION, "Y"),
        (PROP_ASSET_AXIS_Z_DIRECTION, "Z"),
    ):
        if hasattr(bpy.types.Object, prop_name):
            delattr(bpy.types.Object, prop_name)
        setattr(
            bpy.types.Object,
            prop_name,
            EnumProperty(
                name=f"{axis_name} axis direction",
                description=(
                    f"Direction semantics for the positive {axis_name} axis of an AssetSpace helper"
                ),
                items=ASSET_AXIS_DIRECTION_ITEMS,
                default=default_asset_axis_direction(axis_name),
            ),
        )
    if hasattr(bpy.types.Scene, "spot_tools_settings"):
        del bpy.types.Scene.spot_tools_settings
    bpy.types.Scene.spot_tools_settings = PointerProperty(type=SPOT_PG_scene_settings)
    if hasattr(bpy.data, "scenes"):
        for scene in bpy.data.scenes:
            settings = scene.spot_tools_settings
            if settings.base_iri in {
                LEGACY_DEFAULT_BASE_IRI,
                LEGACY_HASH_BASE_IRI,
            }:
                settings.base_iri = DEFAULT_BASE_IRI
    try:
        bpy.types.TOPBAR_MT_file.remove(menu_func_file)
    except ValueError:
        pass
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except ValueError:
        pass
    try:
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    except ValueError:
        pass
    bpy.types.TOPBAR_MT_file.append(menu_func_file)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister() -> None:
    unregister_object_properties()
    for owner_type in (bpy.types.Object, bpy.types.Collection):
        for prop_name in (PROP_SIMULATE_PLACEMENT_CHANGE, PROP_REPRESENTS_LINKS):
            if hasattr(owner_type, prop_name):
                delattr(owner_type, prop_name)
    if hasattr(bpy.types.Scene, "spot_tools_settings"):
        del bpy.types.Scene.spot_tools_settings
    for prop_name in (
        PROP_AM_ENABLED,
        PROP_AM_USE_NORMALIZED_COORDINATES,
        PROP_AM_REFERENCE_ASSET,
        PROP_AM_REFERENCE_HELPER,
    ):
        if hasattr(bpy.types.Object, prop_name):
            delattr(bpy.types.Object, prop_name)
    for axis_name in ("X", "Y", "Z"):
        for prop_name in (
            am_axis_target_prop_name(axis_name),
            am_axis_relation_prop_name(axis_name),
            am_axis_accuracy_prop_name(axis_name),
        ):
            if hasattr(bpy.types.Object, prop_name):
                delattr(bpy.types.Object, prop_name)
    for slot, _axis_name, _bound_kind, _slot_label in BOUNDARY_POINT_SLOTS:
        for prop_name in (
            am_boundary_target_prop_name(slot),
            am_boundary_relation_prop_name(slot),
            am_boundary_accuracy_prop_name(slot),
        ):
            if hasattr(bpy.types.Object, prop_name):
                delattr(bpy.types.Object, prop_name)
    for prop_name in (
        PROP_ASSET_AXIS_X_DIRECTION,
        PROP_ASSET_AXIS_Y_DIRECTION,
        PROP_ASSET_AXIS_Z_DIRECTION,
    ):
        if hasattr(bpy.types.Object, prop_name):
            delattr(bpy.types.Object, prop_name)
    try:
        bpy.types.TOPBAR_MT_file.remove(menu_func_file)
    except ValueError:
        pass
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except ValueError:
        pass
    try:
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    except ValueError:
        pass
    for cls in reversed(CLASSES):
        unregister_class_safe(cls)


if __name__ == "__main__":
    register()
