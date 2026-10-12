from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import bpy
from mathutils import Matrix, Vector

from .spot_blender_common import (
    frame_without_scale,
    helper_export_bbox,
    helper_export_frame,
    APPEARANCE_TARGET_ASSET,
    AmDerivedRegion,
    BOT_CLASS_BUILDING,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SITE,
    BOT_CLASS_SPACE,
    BOT_CLASS_STOREY,
    DOCUMENT_COLLECTION_CANDIDATES,
    HELPER_COLLECTION_NAME,
    LENGTH_UNIT_METER,
    LENGTH_UNIT_PIXEL,
    PREFIX_IFC_PROJECT,
    PROP_ASSET_AXIS_X_DIRECTION,
    PROP_ASSET_AXIS_Y_DIRECTION,
    PROP_ASSET_AXIS_Z_DIRECTION,
    PROP_HELPER,
    PROP_IFC_BOUNDING_BOX_SOURCE,
    PROP_IFC_FILE,
    PROP_LOCAL_BBOX,
    PROP_ROLE,
    PROP_SPOT_CLASS,
    ROLE_BUILDING,
    ROLE_DOCUMENT,
    ROLE_ELEMENT,
    ROLE_SITE,
    ROLE_SPACE,
    ROLE_STOREY,
    PROXY_ROLE_DOCUMENT,
    PROXY_ROLE_PHYSICAL,
    SPOT_CLASS_ASSET,
    SPOT_CLASS_ENTITY,
    SPOT_CLASS_POINT,
    SPOT_CLASS_VOLUME,
    Aabb,
    NameSemantics,
    SpaceNodeSpec,
    append_name_tags,
    bbox_edges,
    bbox_vertices,
    boundary_point_slot_details,
    blender_script_argv,
    build_container_identifiers,
    build_element_identifiers,
    collection_descendants,
    collection_object_named_like_collection,
    create_helper_object,
    create_bbox_mesh,
    ensure_collection,
    ensure_unique_name,
    derive_am_region,
    find_helper_for_source_object,
    get_ifc_definition_id,
    helper_am_enabled,
    helper_children,
    helper_uses_directed_point_mesh,
    human_name,
    is_excluded_object,
    is_ifc_model_class,
    local_bbox_from_world_bbox,
    make_world_matrix,
    object_ifc_model_asset_path,
    object_local_bbox,
    object_texture_asset_path,
    object_world_aabb,
    overlap_volume,
    parse_name_semantics,
    distance_point_to_aabb,
    reset_helper_collection,
    role_order,
    slugify,
    union_aabbs,
)


@dataclass
class ElementSource:
    source_object: bpy.types.Object
    world_bbox: Aabb
    local_bbox: list[float]
    helper_world_matrix: Matrix
    source_collections: list[str]
    semantics: NameSemantics
    definition_id: int | None
    contributes_to_extent: bool

    @property
    def label(self) -> str:
        return self.semantics.label

    @property
    def ifc_class(self) -> str | None:
        return self.semantics.ifc_class

    @property
    def bot_class(self) -> str | None:
        return self.semantics.bot_class

    @property
    def spot_class(self) -> str:
        return self.semantics.spot_class or SPOT_CLASS_VOLUME

    @property
    def beo_class(self) -> str | None:
        return self.semantics.beo_class

    @property
    def plan_kind(self) -> str | None:
        return self.semantics.plan_kind

    @property
    def asset_path(self) -> str | None:
        return object_ifc_model_asset_path(self.source_object, self.semantics)

    @property
    def is_space(self) -> bool:
        return self.semantics.bot_class == BOT_CLASS_SPACE

    @property
    def is_ifc_backed(self) -> bool:
        return self.definition_id is not None or self.ifc_class is not None


@dataclass(frozen=True)
class SceneStructure:
    project_collection: bpy.types.Collection
    site_collection: bpy.types.Collection | None
    building_collection: bpy.types.Collection
    asset_root_object: bpy.types.Object | None
    storey_collections: list[bpy.types.Collection]
    direct_space_collections: list[bpy.types.Collection]
    storey_objects: list[bpy.types.Object]
    direct_space_objects: list[bpy.types.Object]


def preferred_extent_bbox(
    elements: list[ElementSource],
    *,
    allow_fallback_to_all: bool = False,
) -> Aabb | None:
    ifc_boxes = [
        element.world_bbox
        for element in elements
        if element.is_ifc_backed and element.contributes_to_extent
    ]
    if ifc_boxes:
        return union_aabbs(ifc_boxes)
    if allow_fallback_to_all:
        return union_aabbs(
            element.world_bbox for element in elements if element.contributes_to_extent
        )
    return None


def ifc_extent_boxes(elements: list[ElementSource]) -> list[Aabb]:
    return [
        element.world_bbox
        for element in elements
        if element.is_ifc_backed and element.contributes_to_extent
    ]


def semantics_contribute_to_extent(semantics: NameSemantics) -> bool:
    if semantics.plan_kind is not None:
        return False
    if semantics.spot_class == SPOT_CLASS_POINT:
        return False
    return not (semantics.asset_path or "").startswith("data/pictures/")


def is_visible_object(obj: bpy.types.Object) -> bool:
    try:
        return obj.visible_get(view_layer=bpy.context.view_layer)
    except TypeError:
        return obj.visible_get()
    except RuntimeError:
        return not obj.hide_get()


def should_include_object(
    obj: bpy.types.Object,
    *,
    visible_only: bool,
) -> bool:
    return not visible_only or is_visible_object(obj)


def is_spot_am_anchor_source(obj: bpy.types.Object) -> bool:
    if obj.get(PROP_HELPER):
        return False
    collection_names = [collection.name for collection in obj.users_collection]
    if any(name.startswith("IfcTypeProduct") for name in collection_names):
        return False
    if obj.type == "CAMERA":
        return False
    if not helper_am_enabled(obj):
        return False
    return object_local_bbox(obj) is None


def collection_geometry_bbox(collection: bpy.types.Collection) -> Aabb | None:
    boxes: list[Aabb] = []
    representative_object = collection_object_named_like_collection(collection)
    for obj in collection.all_objects:
        if is_excluded_object(obj):
            continue
        if representative_object is not None and obj == representative_object:
            continue
        if obj.type == "CAMERA":
            continue
        world_bbox = object_world_aabb(obj)
        if world_bbox is None:
            continue
        semantics = parse_name_semantics(
            obj.name,
            default_spot_class=SPOT_CLASS_VOLUME,
        )
        if not semantics_contribute_to_extent(semantics):
            continue
        boxes.append(world_bbox)
    return union_aabbs(boxes)


def semantic_object_bbox(obj: bpy.types.Object) -> Aabb | None:
    world_bbox = object_world_aabb(obj)
    if world_bbox is not None:
        return world_bbox
    if obj.type == "CAMERA":
        world_location = obj.matrix_world.translation.copy()
        return Aabb(
            min_corner=world_location.copy(),
            max_corner=world_location.copy(),
        )
    return None


def object_ancestor_names(obj: bpy.types.Object) -> list[str]:
    names: list[str] = []
    current = obj.parent
    while current is not None:
        names.append(current.name)
        current = current.parent
    return names


def semantic_container_root_candidate(obj: bpy.types.Object) -> bool:
    if obj.get(PROP_HELPER):
        return False
    collection_names = [collection.name for collection in obj.users_collection]
    if any(name.startswith("IfcTypeProduct") for name in collection_names):
        return False
    if obj.type == "CAMERA":
        return False
    return obj.type == "EMPTY" or object_local_bbox(obj) is not None


def root_source_object(
    root: bpy.types.Collection | bpy.types.Object,
) -> bpy.types.Object | None:
    if isinstance(root, bpy.types.Object):
        return root
    return collection_object_named_like_collection(root)


def root_source_collection_name(
    root: bpy.types.Collection | bpy.types.Object,
) -> str | None:
    if isinstance(root, bpy.types.Object):
        source_collections = sorted(
            collection.name for collection in root.users_collection
        )
        return source_collections[0] if source_collections else None
    return root.name


def root_source_collection_names(
    root: bpy.types.Collection | bpy.types.Object,
) -> list[str]:
    if isinstance(root, bpy.types.Object):
        return sorted(collection.name for collection in root.users_collection)
    return [root.name]


def root_world_bbox(root: bpy.types.Collection | bpy.types.Object) -> Aabb | None:
    if isinstance(root, bpy.types.Object):
        return semantic_object_bbox(root)
    return collection_geometry_bbox(root)


def unscaled_source_frame(
    obj: bpy.types.Object,
    local_bbox: list[float],
    semantics: NameSemantics,
) -> tuple[Matrix, list[float]]:
    """The helper frame and box a source object with a local box adopts.

    The physical helper of a plan region is measured in the pixels of its
    sheet, like its document proxy. Its scale is the drawing scale, so it
    stays in the frame, and both helpers keep the same unscaled box.
    """
    length_unit = (
        LENGTH_UNIT_PIXEL
        if semantics.proxy_role == PROXY_ROLE_PHYSICAL
        else LENGTH_UNIT_METER
    )
    helper_bbox, local_center = source_local_frame(
        helper_export_bbox(local_bbox, obj.matrix_world, length_unit), semantics
    )
    world_matrix = helper_export_frame(
        obj.matrix_world, length_unit
    ) @ Matrix.Translation(local_center)
    return world_matrix, helper_bbox


def root_helper_pose(
    root: bpy.types.Collection | bpy.types.Object,
    world_bbox: Aabb,
    semantics: NameSemantics | None = None,
) -> tuple[Matrix, list[float]]:
    if isinstance(root, bpy.types.Object):
        source_local_bbox = object_local_bbox(root)
        if source_local_bbox is not None:
            # A whole-model IFC box keeps its own origin even when it is
            # promoted to a container root; see `source_local_frame`.
            root_semantics = semantics or parse_name_semantics(
                root.name,
                default_spot_class=SPOT_CLASS_VOLUME,
            )
            return unscaled_source_frame(root, source_local_bbox, root_semantics)
        world_matrix = frame_without_scale(root.matrix_world)
        return world_matrix, local_bbox_from_world_bbox(world_bbox, world_matrix)

    world_matrix = make_world_matrix(world_bbox.center)
    return world_matrix, local_bbox_from_world_bbox(world_bbox, world_matrix)


def root_semantics(
    root: bpy.types.Collection | bpy.types.Object,
    *,
    default_bot_class: str | None = None,
    default_spot_class: str | None = None,
) -> NameSemantics:
    return parse_name_semantics(
        root.name,
        default_bot_class=default_bot_class,
        default_spot_class=default_spot_class,
    )


def centered_local_bbox(local_bbox: list[float]) -> tuple[list[float], Vector]:
    min_corner = Vector(local_bbox[:3])
    max_corner = Vector(local_bbox[3:])
    center = (min_corner + max_corner) * 0.5
    centered_min = min_corner - center
    centered_max = max_corner - center
    return [*centered_min, *centered_max], center


def source_local_frame(
    local_bbox: list[float],
    semantics: NameSemantics,
) -> tuple[list[float], Vector]:
    """
    The local bounding box and origin shift a source object's helper adopts.

    Almost every helper puts its origin on the centre of its own box. The
    origin of a room or an element is arbitrary, a centred one keeps the
    exported numbers small and symmetric, and nothing outside the scene
    depends on where it sits.

    A whole-model IFC box is the exception, and centring it is wrong rather
    than merely different. Its origin *is* its meaning: it carries the IFC
    file's own frame so that a BCF Viewpoint exported against this space
    (ADR 0015) lands where a tool reading the same IFC file puts it. Shifting
    that origin to the box centre offsets every reframed Viewpoint by the
    vector from the IFC root to that centre, which for an off-origin model is
    metres. So the model box keeps the origin `build_ifc_bounding_box` gave
    it, and keeps the asymmetric box that goes with it, even when that box
    does not contain its own origin.
    """
    if is_ifc_model_class(semantics.ifc_class):
        return list(local_bbox), Vector((0.0, 0.0, 0.0))
    return centered_local_bbox(local_bbox)


DEFAULT_AXIS_DIRECTIONS = {
    "X": "Front",
    "Y": "Right",
    "Z": "Top",
}


def is_view_like_name(name: str) -> bool:
    return human_name(name).lower().startswith("view_")


def is_direct_space_semantics(semantics: NameSemantics) -> bool:
    if semantics.plan_kind is not None:
        return True
    return semantics.spot_class in {
        "DocumentSpace",
        "HorizontalAreaSpace",
        "VerticalAreaSpace",
        SPOT_CLASS_ENTITY,
        SPOT_CLASS_POINT,
    }


def helper_depth(helper: bpy.types.Object) -> int:
    depth = 0
    current = helper.parent
    while current is not None:
        depth += 1
        current = current.parent
    return depth


def iter_helper_descendants(parent: bpy.types.Object) -> list[bpy.types.Object]:
    descendants: list[bpy.types.Object] = []
    for child in helper_children(parent):
        descendants.append(child)
        descendants.extend(iter_helper_descendants(child))
    return descendants


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


def apply_helper_world_bbox(
    helper: bpy.types.Object,
    world_bbox: Aabb,
) -> None:
    saved_world_matrices = {
        child.name: child.matrix_world.copy()
        for child in iter_helper_descendants(helper)
    }
    new_world_matrix = Matrix.Translation(world_bbox.center)
    new_local_bbox = local_bbox_from_world_bbox(world_bbox, new_world_matrix)
    if helper.parent is None:
        helper.matrix_world = new_world_matrix
    else:
        helper.matrix_parent_inverse = Matrix.Identity(4)
        helper.matrix_basis = (
            helper.parent.matrix_world.inverted_safe() @ new_world_matrix
        )
    update_helper_bbox_mesh(helper, new_local_bbox)
    helper[PROP_LOCAL_BBOX] = [float(value) for value in new_local_bbox]
    restore_descendant_world_matrices(helper, saved_world_matrices)


PROP_AM_PREVIEW_BBOX = "spot_am_preview_bbox"


def pointspace_preview_bbox_name(helper: bpy.types.Object) -> str:
    return f"{helper.name}::am-preview"


def find_pointspace_preview_bbox(helper: bpy.types.Object) -> bpy.types.Object | None:
    for child in helper.children:
        if child.get(PROP_AM_PREVIEW_BBOX):
            return child
    return None


def ensure_pointspace_preview_bbox(
    helper_collection: bpy.types.Collection,
    helper: bpy.types.Object,
    world_bbox: Aabb,
) -> bpy.types.Object:
    preview = find_pointspace_preview_bbox(helper)
    if preview is None:
        preview_mesh = create_bbox_mesh(
            f"{pointspace_preview_bbox_name(helper)}::bbox",
            [-0.5, -0.5, -0.5, 0.5, 0.5, 0.5],
        )
        preview = bpy.data.objects.new(
            pointspace_preview_bbox_name(helper), preview_mesh
        )
        preview.display_type = "WIRE"
        preview.hide_render = True
        preview[PROP_AM_PREVIEW_BBOX] = True
        helper_collection.objects.link(preview)
        preview.parent = helper

    preview_world_matrix = Matrix.Translation(world_bbox.center)
    local_bbox = local_bbox_from_world_bbox(world_bbox, preview_world_matrix)
    preview.matrix_parent_inverse = Matrix.Identity(4)
    preview.matrix_basis = helper.matrix_world.inverted_safe() @ preview_world_matrix
    update_helper_bbox_mesh(preview, local_bbox)
    preview[PROP_LOCAL_BBOX] = [float(value) for value in local_bbox]
    return preview


def derive_am_helper_world_bbox(
    helper: bpy.types.Object,
) -> tuple[Aabb | None, list[str]]:
    derived_region, warnings = derive_am_region(helper)
    if derived_region is None:
        return None, warnings
    return (
        Aabb(
            min_corner=derived_region.min_corner.copy(),
            max_corner=derived_region.max_corner.copy(),
        ),
        warnings,
    )


def apply_am_helper_bounds(
    helper_collection: bpy.types.Collection,
) -> list[str]:
    warnings: list[str] = []
    am_helpers = sorted(
        [
            helper
            for helper in helper_collection.objects
            if helper.get(PROP_HELPER) and helper_am_enabled(helper)
        ],
        key=lambda helper: (helper_depth(helper), helper.name),
    )
    if not am_helpers:
        return warnings

    for _pass_index in range(3):
        changed = False
        for helper in am_helpers:
            derived_bbox, helper_warnings = derive_am_helper_world_bbox(helper)
            for warning in helper_warnings:
                if warning not in warnings:
                    warnings.append(warning)
            if derived_bbox is None:
                continue

            if helper_uses_directed_point_mesh(helper):
                preview = ensure_pointspace_preview_bbox(
                    helper_collection,
                    helper,
                    derived_bbox,
                )
                preview_bbox = object_world_aabb(preview)
                if preview_bbox is None:
                    changed = True
                    continue
                delta = max(
                    abs(
                        float(
                            derived_bbox.min_corner[index]
                            - preview_bbox.min_corner[index]
                        )
                    )
                    for index in range(3)
                )
                delta = max(
                    delta,
                    max(
                        abs(
                            float(
                                derived_bbox.max_corner[index]
                                - preview_bbox.max_corner[index]
                            )
                        )
                        for index in range(3)
                    ),
                )
                if delta > 1e-6:
                    changed = True
                continue

            current_bbox = object_world_aabb(helper)
            if current_bbox is not None:
                delta = max(
                    abs(
                        float(
                            derived_bbox.min_corner[index]
                            - current_bbox.min_corner[index]
                        )
                    )
                    for index in range(3)
                )
                delta = max(
                    delta,
                    max(
                        abs(
                            float(
                                derived_bbox.max_corner[index]
                                - current_bbox.max_corner[index]
                            )
                        )
                        for index in range(3)
                    ),
                )
                if delta <= 1e-6:
                    continue

            apply_helper_world_bbox(helper, derived_bbox)
            bpy.context.view_layer.update()
            changed = True
        if not changed:
            break
    return warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a helper SPOT scene hierarchy inside the current Blender file.",
    )
    parser.add_argument(
        "--project-collection",
        help="Optional explicit source root collection name. IFC project collections still work, but semantic AssetSpace roots are also supported.",
    )
    parser.add_argument(
        "--helper-collection",
        default=HELPER_COLLECTION_NAME,
        help="Name of the helper collection that will be created or replaced.",
    )
    parser.add_argument(
        "--save-blend",
        help="Optional output .blend path to save after building the helper scene.",
    )
    parser.add_argument(
        "--all-objects",
        action="store_true",
        help="Include hidden objects instead of limiting helper generation to currently visible sources.",
    )
    return parser.parse_args(blender_script_argv())


def collection_semantics(
    collection: bpy.types.Collection,
    *,
    default_bot_class: str | None = None,
    default_spot_class: str | None = None,
) -> NameSemantics:
    return parse_name_semantics(
        collection.name,
        default_bot_class=default_bot_class,
        default_spot_class=default_spot_class,
    )


def semantic_asset_candidates(
    scene: bpy.types.Scene,
) -> list[bpy.types.Collection]:
    candidates: list[bpy.types.Collection] = []
    for collection in collection_descendants(scene.collection):
        semantics = collection_semantics(collection)
        if semantics.spot_class == SPOT_CLASS_ASSET:
            candidates.append(collection)
    return candidates


def pick_semantic_asset_collection(
    scene: bpy.types.Scene,
) -> bpy.types.Collection | None:
    candidates = semantic_asset_candidates(scene)
    if not candidates:
        return None

    named_candidates = [
        collection
        for collection in candidates
        if collection_semantics(collection).label != SPOT_CLASS_ASSET
    ]
    if len(named_candidates) == 1:
        return named_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        "Found multiple semantic AssetSpace collections; set --project-collection explicitly: "
        f"{[collection.name for collection in candidates]}"
    )


def classify_semantic_child_collections(
    parent_collection: bpy.types.Collection,
) -> tuple[
    list[bpy.types.Collection],
    list[bpy.types.Collection],
    list[bpy.types.Collection],
]:
    semantic_asset_children: list[bpy.types.Collection] = []
    storey_collections: list[bpy.types.Collection] = []
    direct_space_collections: list[bpy.types.Collection] = []

    for collection in sorted(parent_collection.children, key=lambda value: value.name):
        semantics = collection_semantics(collection)
        if semantics.spot_class == SPOT_CLASS_ASSET:
            semantic_asset_children.append(collection)
            continue
        if semantics.label in DOCUMENT_COLLECTION_CANDIDATES:
            continue
        if semantics.bot_class == BOT_CLASS_STOREY:
            storey_collections.append(collection)
            continue
        if semantics.bot_class == BOT_CLASS_SPACE:
            direct_space_collections.append(collection)
            continue
        if is_direct_space_semantics(semantics):
            direct_space_collections.append(collection)
            continue
        if is_view_like_name(collection.name):
            direct_space_collections.append(collection)
            continue
        if semantics.spot_class == SPOT_CLASS_VOLUME:
            storey_collections.append(collection)
            continue
        if semantics.spot_class in {SPOT_CLASS_ENTITY, SPOT_CLASS_POINT}:
            direct_space_collections.append(collection)

    return semantic_asset_children, storey_collections, direct_space_collections


def classify_semantic_descendant_collections(
    parent_collection: bpy.types.Collection,
) -> tuple[list[bpy.types.Collection], list[bpy.types.Collection]]:
    storey_collections: list[bpy.types.Collection] = []
    direct_space_collections: list[bpy.types.Collection] = []

    for collection in collection_descendants(parent_collection)[1:]:
        semantics = collection_semantics(collection)
        if semantics.spot_class == SPOT_CLASS_ASSET:
            continue
        if semantics.label in DOCUMENT_COLLECTION_CANDIDATES:
            continue
        if semantics.bot_class == BOT_CLASS_STOREY:
            storey_collections.append(collection)
            continue
        if semantics.bot_class == BOT_CLASS_SPACE:
            direct_space_collections.append(collection)
            continue
        if semantics.spot_class == SPOT_CLASS_VOLUME:
            storey_collections.append(collection)
            continue
        if is_direct_space_semantics(semantics) or is_view_like_name(collection.name):
            direct_space_collections.append(collection)

    return storey_collections, direct_space_collections


def classify_semantic_object_roots(
    parent_collection: bpy.types.Collection,
) -> tuple[list[bpy.types.Object], list[bpy.types.Object]]:
    storey_objects: list[bpy.types.Object] = []
    direct_space_objects: list[bpy.types.Object] = []

    for obj in sorted(parent_collection.all_objects, key=lambda value: value.name):
        if not semantic_container_root_candidate(obj):
            continue
        semantics = parse_name_semantics(obj.name)
        if semantics.spot_class == SPOT_CLASS_ASSET:
            continue
        if semantics.label in DOCUMENT_COLLECTION_CANDIDATES:
            continue
        if semantics.bot_class == BOT_CLASS_STOREY:
            storey_objects.append(obj)
            continue
        if semantics.bot_class == BOT_CLASS_SPACE:
            direct_space_objects.append(obj)
            continue
        if is_direct_space_semantics(semantics) or is_view_like_name(obj.name):
            direct_space_objects.append(obj)

    return storey_objects, direct_space_objects


def find_asset_root_object(
    building_collection: bpy.types.Collection,
) -> bpy.types.Object | None:
    candidates: list[bpy.types.Object] = []
    for obj in sorted(building_collection.all_objects, key=lambda value: value.name):
        if not semantic_container_root_candidate(obj):
            continue
        semantics = parse_name_semantics(obj.name)
        if semantics.spot_class == SPOT_CLASS_ASSET:
            candidates.append(obj)

    if not candidates:
        return None

    named_candidates = [
        obj
        for obj in candidates
        if parse_name_semantics(obj.name).label != SPOT_CLASS_ASSET
    ]
    if len(named_candidates) == 1:
        return named_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        "Found multiple semantic AssetSpace objects under "
        f"'{building_collection.name}': {[obj.name for obj in candidates]}"
    )


def find_ifc_project_collections(
    scene: bpy.types.Scene,
) -> list[bpy.types.Collection]:
    """
    Every collection in the scene whose name starts with `IfcProject/`.

    This is a name check only. It finds a Bonsai/IfcOpenShell import by the
    collection naming convention those tools use, not by calling into either
    add-on, so it still works with Bonsai disabled -- the common case once an
    IFC has already been imported and the add-on is no longer needed.
    """
    return [
        collection
        for collection in collection_descendants(scene.collection)
        if collection.name.startswith(PREFIX_IFC_PROJECT)
    ]


def ifc_bounding_box_source_objects(
    roots: Iterable[bpy.types.Collection | bpy.types.Object],
) -> list[bpy.types.Object]:
    """
    Expands a mix of collections and objects into the geometry objects a
    bounding box should cover.

    A collection contributes every object linked under it (`all_objects`
    already recurses into child collections). An EMPTY that instances a
    collection stands for that collection's contents. Any other object is
    used directly.
    """
    seen: dict[str, bpy.types.Object] = {}
    for root in roots:
        if isinstance(root, bpy.types.Collection):
            for member in root.all_objects:
                seen.setdefault(member.name, member)
            continue
        if root.type == "EMPTY" and root.instance_collection is not None:
            for member in root.instance_collection.all_objects:
                seen.setdefault(member.name, member)
            continue
        seen.setdefault(root.name, root)
    return list(seen.values())


def build_ifc_bounding_box(
    source_objects: Iterable[bpy.types.Object],
    *,
    root_object: bpy.types.Object | None = None,
    label: str = "IFC Model",
    collection_name: str = "IFC Bounding Boxes",
    collection: bpy.types.Collection | None = None,
    source_name: str | None = None,
    ifc_file: str | None = None,
) -> bpy.types.Object:
    """
    Creates one bounding-box mesh covering an IFC source hierarchy, named
    `VolumeSpace_<label>::IFC=IfcProject` so the scene build reads it as a
    normal source volume that is classified as a whole IFC model.

    Re-parenting a whole Bonsai/IfcOpenShell import into the AssetSpace
    collection by hand does not scale. This produces the one object that has
    to move instead: a single box standing in for the whole IFC model's
    extent.

    The box spans the placed geometry only, skipping what the scene build
    skips too: type objects and objects without geometry. Its origin sits on
    the IFC root, which may lie outside the box when the model is placed away
    from its origin. That origin carries the IFC file's frame, so moving the
    box moves the whole model in the asset space.

    The box is linked into `collection` when one is given, otherwise into the
    collection named `collection_name`. `source_name` records which
    `IfcProject/...` collection the box stands for, so a second run can find
    the box again instead of adding a duplicate. `ifc_file` is the file name
    of the model in the assets; the export links that file instead of one
    guessed from the label.
    """
    boxes = [
        box
        for box in (
            object_world_aabb(obj)
            for obj in source_objects
            if not is_excluded_object(obj)
        )
        if box is not None
    ]
    if not boxes:
        raise RuntimeError(
            "No geometry was found to build a bounding box from",
        )

    # The full root frame, not only its position: an IFC root whose
    # `IfcAxis2Placement3D` carries a rotation defines a rotated frame, and a
    # translation-only matrix would silently drop it. Scale is dropped on
    # purpose -- an IFC length unit is handled by the unit conversion, not by
    # a scaled placement.
    if root_object is not None:
        root_location = root_object.matrix_world.translation.copy()
        root_rotation = root_object.matrix_world.to_quaternion()
    else:
        root_location = Vector((0.0, 0.0, 0.0))
        root_rotation = None

    world_bbox = union_aabbs(boxes)
    assert world_bbox is not None

    world_matrix = make_world_matrix(root_location, root_rotation)
    if collection is None:
        collection = ensure_collection(bpy.context.scene, collection_name)

    # Follows the scene's own naming convention: SPOT class, then label,
    # then classification tags. The `VolumeSpace_` prefix is what
    # `parse_name_semantics` reads for the SPOT class, so no `::SPOT=` tag is
    # added; `::IFC=IfcProject` records that this box stands for a whole IFC
    # model rather than a modelled space.
    name = ensure_unique_name(
        append_name_tags(
            f"{SPOT_CLASS_VOLUME}_{slugify(label)}",
            ifc_class=PREFIX_IFC_PROJECT.rstrip("/"),
        ),
        {obj.name for obj in bpy.data.objects},
    )
    local_bbox = local_bbox_from_world_bbox(world_bbox, world_matrix)
    bbox_object = bpy.data.objects.new(name, create_bbox_mesh(f"{name}::bbox", local_bbox))
    bbox_object.display_type = "WIRE"
    bbox_object.hide_render = True
    bbox_object.matrix_world = world_matrix
    if source_name is not None:
        bbox_object[PROP_IFC_BOUNDING_BOX_SOURCE] = source_name
    if ifc_file:
        bbox_object[PROP_IFC_FILE] = Path(ifc_file).name
    collection.objects.link(bbox_object)

    return bbox_object


def find_ifc_bounding_box(
    project_collection: bpy.types.Collection,
) -> bpy.types.Object | None:
    """The bounding box an earlier run built for `project_collection`, if any."""
    return next(
        (
            obj
            for obj in bpy.data.objects
            if obj.get(PROP_IFC_BOUNDING_BOX_SOURCE) == project_collection.name
        ),
        None,
    )


def ifc_bounding_box_target_collection(
    scene: bpy.types.Scene,
) -> bpy.types.Collection | None:
    """
    The AssetSpace collection a new IFC bounding box is sorted into.

    Linking the box there directly saves the manual drag: the scene build
    reads it as a building-level source volume, and the box's placement in
    the asset space becomes the translation of the whole IFC model. Returns
    `None` when the scene has no AssetSpace collection, or more than one, so
    the caller can fall back to a separate collection.
    """
    try:
        return pick_semantic_asset_collection(scene)
    except RuntimeError:
        return None


def descendant_role_collections(
    project_collection: bpy.types.Collection,
) -> tuple[
    bpy.types.Collection | None,
    bpy.types.Collection,
    list[bpy.types.Collection],
]:
    structure = resolve_scene_structure(
        bpy.context.scene,
        explicit_name=project_collection.name,
    )
    return (
        structure.site_collection,
        structure.building_collection,
        structure.storey_collections,
    )


def resolve_scene_structure(
    scene: bpy.types.Scene,
    explicit_name: str | None = None,
) -> SceneStructure:
    storey_objects: list[bpy.types.Object] = []
    direct_space_objects: list[bpy.types.Object] = []
    asset_root_object: bpy.types.Object | None = None
    if explicit_name:
        explicit_collection = bpy.data.collections.get(explicit_name)
        if explicit_collection is None:
            raise RuntimeError(f"Project collection '{explicit_name}' was not found")
        explicit_semantics = collection_semantics(explicit_collection)
        if explicit_semantics.spot_class == SPOT_CLASS_ASSET:
            building_collection = explicit_collection
            project_collection = explicit_collection
            site_collection = None
        else:
            project_collection = explicit_collection
            descendants = collection_descendants(project_collection)[1:]
            site_collection = next(
                (
                    collection
                    for collection in descendants
                    if collection_semantics(
                        collection,
                        default_bot_class=BOT_CLASS_SITE,
                        default_spot_class=SPOT_CLASS_ENTITY,
                    ).bot_class
                    == BOT_CLASS_SITE
                ),
                None,
            )
            building_collection = next(
                (
                    collection
                    for collection in descendants
                    if collection_semantics(
                        collection,
                        default_bot_class=BOT_CLASS_BUILDING,
                        default_spot_class=SPOT_CLASS_ASSET,
                    ).spot_class
                    == SPOT_CLASS_ASSET
                ),
                None,
            )
            if building_collection is None:
                raise RuntimeError(
                    f"Could not resolve an AssetSpace root under '{project_collection.name}'"
                )
        descendants = collection_descendants(building_collection)[1:]
    else:
        building_collection = pick_semantic_asset_collection(scene)
        if building_collection is not None:
            project_collection = building_collection
            site_collection = None
            descendants = collection_descendants(building_collection)[1:]
        else:
            from .spot_blender_common import find_project_collection

            project_collection = find_project_collection(scene)
            descendants = collection_descendants(project_collection)[1:]
            site_collection = None
            building_collection = None
            for collection in descendants:
                semantics = collection_semantics(
                    collection,
                    default_bot_class=BOT_CLASS_BUILDING,
                    default_spot_class=SPOT_CLASS_ASSET,
                )
                if semantics.spot_class == SPOT_CLASS_ASSET:
                    building_collection = collection
                    break
            if building_collection is None:
                raise RuntimeError(
                    f"Could not resolve an AssetSpace root under '{project_collection.name}'"
                )
            for collection in descendants:
                semantics = collection_semantics(
                    collection,
                    default_bot_class=BOT_CLASS_SITE,
                    default_spot_class=SPOT_CLASS_ENTITY,
                )
                if semantics.bot_class == BOT_CLASS_SITE:
                    site_collection = collection
                    break

    (
        semantic_asset_children,
        storey_collections,
        direct_space_collections,
    ) = classify_semantic_child_collections(building_collection)

    if not storey_collections and not direct_space_collections:
        named_asset_children = [
            collection
            for collection in semantic_asset_children
            if collection_semantics(collection).label != SPOT_CLASS_ASSET
        ]
        nested_asset_candidates = named_asset_children or semantic_asset_children
        if len(nested_asset_candidates) == 1:
            building_collection = nested_asset_candidates[0]
            descendants = collection_descendants(building_collection)[1:]
            (
                _semantic_asset_children,
                storey_collections,
                direct_space_collections,
            ) = classify_semantic_child_collections(building_collection)

    if not storey_collections:
        legacy_storeys = [
            collection
            for collection in descendants
            if collection_semantics(
                collection,
                default_bot_class=BOT_CLASS_STOREY,
                default_spot_class=SPOT_CLASS_VOLUME,
            ).bot_class
            == BOT_CLASS_STOREY
        ]
        storey_collections = sorted(legacy_storeys, key=lambda value: value.name)

    if not storey_collections and not direct_space_collections:
        (
            storey_collections,
            direct_space_collections,
        ) = classify_semantic_descendant_collections(building_collection)

    if not storey_collections and not direct_space_collections:
        storey_objects, direct_space_objects = classify_semantic_object_roots(
            building_collection,
        )
        asset_root_object = find_asset_root_object(building_collection)

    if asset_root_object is None:
        asset_root_object = find_asset_root_object(building_collection)

    # An AssetSpace without storeys or spaces is still a valid scene: its
    # elements, and any IFC bounding box, hang directly off the asset root.
    if (
        not storey_collections
        and not direct_space_collections
        and not storey_objects
        and not direct_space_objects
        and asset_root_object is None
        and not any(
            should_include_object(obj, visible_only=False)
            for obj in building_collection.all_objects
        )
    ):
        raise RuntimeError(
            f"The AssetSpace '{building_collection.name}' contains nothing to build "
            "helpers from"
        )

    return SceneStructure(
        project_collection=project_collection,
        site_collection=site_collection,
        building_collection=building_collection,
        asset_root_object=asset_root_object,
        storey_collections=storey_collections,
        direct_space_collections=direct_space_collections,
        storey_objects=storey_objects,
        direct_space_objects=direct_space_objects,
    )


def collect_candidate_elements(
    project_collection: bpy.types.Collection,
    *,
    visible_only: bool = True,
) -> list[ElementSource]:
    elements: list[ElementSource] = []
    for obj in sorted(project_collection.all_objects, key=lambda value: value.name):
        if not should_include_object(obj, visible_only=visible_only):
            continue
        if is_excluded_object(obj) and not is_spot_am_anchor_source(obj):
            continue
        if obj.type == "CAMERA":
            semantics = parse_name_semantics(
                obj.name,
                default_spot_class=SPOT_CLASS_POINT,
            )
            world_location = obj.matrix_world.translation.copy()
            world_bbox = Aabb(
                min_corner=world_location.copy(),
                max_corner=world_location.copy(),
            )
            elements.append(
                ElementSource(
                    source_object=obj,
                    world_bbox=world_bbox,
                    local_bbox=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    helper_world_matrix=frame_without_scale(obj.matrix_world),
                    source_collections=sorted(
                        collection.name for collection in obj.users_collection
                    ),
                    semantics=semantics,
                    definition_id=get_ifc_definition_id(obj),
                    contributes_to_extent=semantics_contribute_to_extent(semantics),
                ),
            )
            continue
        if is_spot_am_anchor_source(obj):
            semantics = parse_name_semantics(
                obj.name,
                default_spot_class=SPOT_CLASS_VOLUME,
            )
            world_location = obj.matrix_world.translation.copy()
            world_bbox = Aabb(
                min_corner=world_location.copy(),
                max_corner=world_location.copy(),
            )
            elements.append(
                ElementSource(
                    source_object=obj,
                    world_bbox=world_bbox,
                    local_bbox=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    helper_world_matrix=frame_without_scale(obj.matrix_world),
                    source_collections=sorted(
                        collection.name for collection in obj.users_collection
                    ),
                    semantics=semantics,
                    definition_id=get_ifc_definition_id(obj),
                    contributes_to_extent=semantics_contribute_to_extent(semantics),
                ),
            )
            continue
        local_bbox = object_local_bbox(obj)
        if local_bbox is None:
            continue
        world_bbox = object_world_aabb(obj)
        if world_bbox is None:
            continue
        semantics = parse_name_semantics(
            obj.name,
            default_spot_class=SPOT_CLASS_VOLUME,
        )
        helper_world_matrix, helper_bbox = unscaled_source_frame(
            obj, local_bbox, semantics
        )
        elements.append(
            ElementSource(
                source_object=obj,
                world_bbox=world_bbox,
                local_bbox=helper_bbox,
                helper_world_matrix=helper_world_matrix,
                source_collections=sorted(
                    collection.name for collection in obj.users_collection
                ),
                semantics=semantics,
                definition_id=get_ifc_definition_id(obj),
                contributes_to_extent=semantics_contribute_to_extent(semantics),
            ),
        )
    return elements


def find_document_collection(scene: bpy.types.Scene) -> bpy.types.Collection | None:
    for collection in collection_descendants(scene.collection):
        if collection.name in DOCUMENT_COLLECTION_CANDIDATES:
            return collection
    return None


def is_descendant_of_object(
    obj: bpy.types.Object,
    ancestor: bpy.types.Object,
) -> bool:
    current = obj.parent
    while current is not None:
        if current == ancestor:
            return True
        current = current.parent
    return False


def document_root_objects(
    document_collection: bpy.types.Collection,
    *,
    visible_only: bool = True,
) -> list[bpy.types.Object]:
    roots = [
        obj
        for obj in document_collection.objects
        if obj.parent is None
        and not is_excluded_object(obj)
        and should_include_object(obj, visible_only=visible_only)
    ]
    return sorted(roots, key=lambda obj: obj.name)


def collect_document_proxy_elements(
    document_root: bpy.types.Object,
    document_collection: bpy.types.Collection,
    *,
    visible_only: bool = True,
) -> list[ElementSource]:
    elements: list[ElementSource] = []
    document_objects = [
        obj
        for obj in document_collection.all_objects
        if obj != document_root and is_descendant_of_object(obj, document_root)
    ]
    for obj in sorted(document_objects, key=lambda value: value.name):
        if not should_include_object(obj, visible_only=visible_only):
            continue
        if is_excluded_object(obj) and not is_spot_am_anchor_source(obj):
            continue
        if obj.type == "CAMERA":
            semantics = parse_name_semantics(
                obj.name,
                default_spot_class=SPOT_CLASS_POINT,
            )
            if semantics.proxy_role != PROXY_ROLE_DOCUMENT:
                continue
            world_location = obj.matrix_world.translation.copy()
            world_bbox = Aabb(
                min_corner=world_location.copy(),
                max_corner=world_location.copy(),
            )
            elements.append(
                ElementSource(
                    source_object=obj,
                    world_bbox=world_bbox,
                    local_bbox=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    helper_world_matrix=obj.matrix_world.copy(),
                    source_collections=sorted(
                        collection.name for collection in obj.users_collection
                    ),
                    semantics=semantics,
                    definition_id=get_ifc_definition_id(obj),
                    contributes_to_extent=semantics_contribute_to_extent(semantics),
                ),
            )
            continue
        if is_spot_am_anchor_source(obj):
            semantics = parse_name_semantics(
                obj.name,
                default_spot_class=SPOT_CLASS_VOLUME,
            )
            if semantics.proxy_role != PROXY_ROLE_DOCUMENT:
                continue
            world_location = obj.matrix_world.translation.copy()
            world_bbox = Aabb(
                min_corner=world_location.copy(),
                max_corner=world_location.copy(),
            )
            elements.append(
                ElementSource(
                    source_object=obj,
                    world_bbox=world_bbox,
                    local_bbox=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    helper_world_matrix=obj.matrix_world.copy(),
                    source_collections=sorted(
                        collection.name for collection in obj.users_collection
                    ),
                    semantics=semantics,
                    definition_id=get_ifc_definition_id(obj),
                    contributes_to_extent=semantics_contribute_to_extent(semantics),
                ),
            )
            continue
        local_bbox = object_local_bbox(obj)
        if local_bbox is None:
            continue
        world_bbox = object_world_aabb(obj)
        if world_bbox is None:
            continue
        semantics = parse_name_semantics(
            obj.name,
            default_spot_class=SPOT_CLASS_VOLUME,
        )
        if semantics.proxy_role != PROXY_ROLE_DOCUMENT:
            continue
        # A document proxy shares its entity with a physical helper, and both
        # place the one exported bounding box of that entity. So the proxy has
        # to use the same local frame: the origin on the centre of its own
        # region, whatever origin the source crop was drawn around.
        centered_bbox, local_center = centered_local_bbox(local_bbox)
        elements.append(
            ElementSource(
                source_object=obj,
                world_bbox=world_bbox,
                local_bbox=centered_bbox,
                helper_world_matrix=obj.matrix_world @ Matrix.Translation(local_center),
                source_collections=sorted(
                    collection.name for collection in obj.users_collection
                ),
                semantics=semantics,
                definition_id=get_ifc_definition_id(obj),
                contributes_to_extent=semantics_contribute_to_extent(semantics),
            ),
        )
    return elements


def choose_storey_for_element(
    element: ElementSource,
    storey_boxes: dict[str, Aabb],
) -> str:
    if not storey_boxes:
        raise RuntimeError(
            f"Could not assign element '{element.source_object.name}' because no storey boxes are available",
        )

    center = element.world_bbox.center
    best_name: str | None = None
    best_metric: tuple[int, float, float, float, str] | None = None

    for storey_name, storey_box in storey_boxes.items():
        overlap = overlap_volume(element.world_bbox, storey_box)
        distance = distance_point_to_aabb(center, storey_box)
        z_delta = abs(center.z - storey_box.center.z)
        metric = (
            1 if overlap > 0 else 0,
            overlap,
            -distance,
            -z_delta,
            storey_name,
        )
        if best_metric is None or metric > best_metric:
            best_metric = metric
            best_name = storey_name

    if best_name is None:
        raise RuntimeError(
            f"Failed to resolve a storey for '{element.source_object.name}'"
        )
    return best_name


def assign_elements(
    elements: list[ElementSource],
    site_collection: bpy.types.Collection | None,
    building_collection: bpy.types.Collection,
    storey_roots: list[bpy.types.Collection | bpy.types.Object],
    direct_space_roots: list[bpy.types.Collection | bpy.types.Object] | None = None,
) -> tuple[
    list[ElementSource],
    list[ElementSource],
    dict[str, list[ElementSource]],
    dict[str, list[ElementSource]],
    list[str],
]:
    direct_space_roots = direct_space_roots or []
    storey_names = {root.name for root in storey_roots}
    direct_space_names = {root.name for root in direct_space_roots}
    direct_storey_assignments: dict[str, list[ElementSource]] = {
        root.name: [] for root in storey_roots
    }
    direct_space_assignments: dict[str, list[ElementSource]] = {
        root.name: [] for root in direct_space_roots
    }
    site_direct: list[ElementSource] = []
    building_direct: list[ElementSource] = []
    unresolved: list[ElementSource] = []

    for element in elements:
        ancestor_names = object_ancestor_names(element.source_object)
        direct_space_memberships = [
            name for name in ancestor_names if name in direct_space_names
        ]
        if not direct_space_memberships:
            direct_space_memberships = [
                name
                for name in element.source_collections
                if name in direct_space_names
            ]
        if len(direct_space_memberships) == 1:
            direct_space_assignments[direct_space_memberships[0]].append(element)
            continue
        if len(direct_space_memberships) > 1:
            unresolved.append(element)
            continue
        memberships = [name for name in ancestor_names if name in storey_names]
        if not memberships:
            memberships = [
                name for name in element.source_collections if name in storey_names
            ]
        if len(memberships) == 1:
            direct_storey_assignments[memberships[0]].append(element)
            continue
        if len(memberships) > 1:
            unresolved.append(element)
            continue
        if (
            site_collection is not None
            and site_collection.name in element.source_collections
        ):
            site_direct.append(element)
            continue
        if building_collection.name in element.source_collections:
            building_direct.append(element)
            continue
        unresolved.append(element)

    warnings: list[str] = []
    storey_boxes: dict[str, Aabb] = {}
    for storey_root in storey_roots:
        box_parts: list[Aabb] = []
        root_bbox_value = root_world_bbox(storey_root)
        if root_bbox_value is not None:
            box_parts.append(root_bbox_value)
        assigned_bbox = preferred_extent_bbox(
            direct_storey_assignments[storey_root.name],
            allow_fallback_to_all=True,
        )
        if assigned_bbox is not None:
            box_parts.append(assigned_bbox)
        if box_parts:
            storey_boxes[storey_root.name] = union_aabbs(box_parts)

    for element in unresolved:
        assigned_storey = choose_storey_for_element(element, storey_boxes)
        direct_storey_assignments[assigned_storey].append(element)
        updated_box = preferred_extent_bbox(
            direct_storey_assignments[assigned_storey],
            allow_fallback_to_all=True,
        )
        if updated_box is not None:
            storey_boxes[assigned_storey] = updated_box
        warnings.append(
            f"Assigned '{element.source_object.name}' to '{assigned_storey}' using geometry fallback",
        )

    return (
        site_direct,
        building_direct,
        direct_storey_assignments,
        direct_space_assignments,
        warnings,
    )


def make_container_space_node(
    root: bpy.types.Collection | bpy.types.Object,
    assigned_nodes: list[ElementSource],
    parent_prefix: str,
    helper_name_registry: set[str],
    uri_suffix_registry: set[str],
    bot_uri_suffix_registry: set[str],
    *,
    default_spot_class: str,
    sort_order: int,
) -> SpaceNodeSpec | None:
    # No default BOT class: a direct space is a room only when its name says
    # so. Plans, documents and pictures land here too.
    semantics = root_semantics(
        root,
        default_spot_class=(
            SPOT_CLASS_VOLUME if is_view_like_name(root.name) else default_spot_class
        ),
    )
    world_bbox = preferred_extent_bbox(assigned_nodes, allow_fallback_to_all=True)
    root_bbox_value = root_world_bbox(root)
    if world_bbox is None:
        world_bbox = root_bbox_value
    elif root_bbox_value is not None:
        world_bbox = union_aabbs([world_bbox, root_bbox_value])
    if world_bbox is None:
        return None

    source_object = root_source_object(root)
    helper_name, uri_suffix = build_container_identifiers(
        ROLE_SPACE,
        semantics.label,
        get_ifc_definition_id(source_object),
        bot_class=semantics.bot_class,
        spot_class=semantics.spot_class,
        part_token=semantics.part_token,
    )
    helper_name = ensure_unique_name(helper_name, helper_name_registry)
    uri_suffix = unique_token(uri_suffix, uri_suffix_registry)
    if semantics.bot_class is not None and semantics.shared_entity_key is not None:
        bot_uri_suffix = f"ref_{semantics.shared_entity_key}"
    else:
        bot_uri_suffix = unique_token(f"ref_{uri_suffix}", bot_uri_suffix_registry)
    world_matrix, local_bbox = root_helper_pose(root, world_bbox, semantics)
    node = SpaceNodeSpec(
        role=ROLE_SPACE,
        spot_class=semantics.spot_class or default_spot_class,
        bot_class=semantics.bot_class,
        brot_class=semantics.brot_class,
        helper_name=helper_name,
        uri_suffix=uri_suffix,
        bot_uri_suffix=bot_uri_suffix,
        label=semantics.label,
        world_bbox=world_bbox,
        world_matrix=world_matrix,
        local_bbox=local_bbox,
        length_unit=(
            LENGTH_UNIT_PIXEL
            if semantics.plan_kind is not None
            or (semantics.asset_path or "").startswith("data/pictures/")
            else LENGTH_UNIT_METER
        ),
        sort_key=f"{parent_prefix}:{sort_order}:{slugify(root.name)}",
        source_name=source_object.name if source_object is not None else root.name,
        source_collection=root_source_collection_name(root),
        source_ifc_class=semantics.ifc_class,
        source_definition_id=get_ifc_definition_id(source_object),
        source_asset_path=(
            object_ifc_model_asset_path(source_object, semantics)
            if source_object is not None
            else semantics.asset_path
        ),
        plan_kind=semantics.plan_kind,
        source_collections=root_source_collection_names(root),
    )
    for element in sorted(assigned_nodes, key=lambda value: value.source_object.name):
        node_builder = make_space_node if element.is_space else make_element_node
        node.children.append(
            node_builder(
                element,
                parent_prefix=node.sort_key,
                helper_name_registry=helper_name_registry,
                uri_suffix_registry=uri_suffix_registry,
                bot_uri_suffix_registry=bot_uri_suffix_registry,
            )
        )
    node.children.sort(
        key=lambda child: (role_order(child.role), child.sort_key, child.helper_name)
    )
    return node


def make_element_node(
    element: ElementSource,
    parent_prefix: str,
    helper_name_registry: set[str],
    uri_suffix_registry: set[str],
    bot_uri_suffix_registry: set[str],
) -> SpaceNodeSpec:
    helper_name, uri_suffix = build_element_identifiers(
        element.source_object.name,
        element.ifc_class,
        element.definition_id,
        bot_class=element.bot_class,
        spot_class=element.spot_class,
        beo_class=element.beo_class,
        part_token=element.semantics.part_token,
    )
    helper_name = ensure_unique_name(helper_name, helper_name_registry)
    uri_suffix = unique_token(uri_suffix, uri_suffix_registry)
    if (
        element.bot_class is not None
        and element.semantics.shared_entity_key is not None
    ):
        bot_uri_suffix = f"ref_{element.semantics.shared_entity_key}"
    else:
        bot_uri_suffix = unique_token(f"ref_{uri_suffix}", bot_uri_suffix_registry)
    sort_suffix = slugify(element.source_object.name)
    ifc_class = element.ifc_class or "Element"
    identifier = (
        element.definition_id if element.definition_id is not None else sort_suffix
    )
    return SpaceNodeSpec(
        role=ROLE_ELEMENT,
        spot_class=element.spot_class,
        bot_class=element.bot_class,
        brot_class=element.semantics.brot_class,
        helper_name=helper_name,
        uri_suffix=uri_suffix,
        bot_uri_suffix=bot_uri_suffix,
        label=element.label,
        world_bbox=element.world_bbox,
        world_matrix=element.helper_world_matrix.copy(),
        length_unit=(
            LENGTH_UNIT_PIXEL
            if element.semantics.proxy_role
            in {PROXY_ROLE_PHYSICAL, PROXY_ROLE_DOCUMENT}
            else LENGTH_UNIT_METER
        ),
        beo_class=element.beo_class,
        local_bbox=list(element.local_bbox),
        sort_key=f"{parent_prefix}:300:{slugify(ifc_class)}:{identifier}:{sort_suffix}",
        source_name=element.source_object.name,
        source_collection=(
            element.source_collections[0] if element.source_collections else None
        ),
        source_ifc_class=element.ifc_class,
        source_definition_id=element.definition_id,
        source_asset_path=element.asset_path,
        plan_kind=element.plan_kind,
        source_collections=element.source_collections,
    )


def make_space_node(
    element: ElementSource,
    parent_prefix: str,
    helper_name_registry: set[str],
    uri_suffix_registry: set[str],
    bot_uri_suffix_registry: set[str],
) -> SpaceNodeSpec:
    helper_name, uri_suffix = build_container_identifiers(
        ROLE_SPACE,
        element.label,
        element.definition_id,
        bot_class=element.bot_class,
        spot_class=element.spot_class,
        part_token=element.semantics.part_token,
    )
    helper_name = ensure_unique_name(helper_name, helper_name_registry)
    uri_suffix = unique_token(uri_suffix, uri_suffix_registry)
    if (
        element.bot_class is not None
        and element.semantics.shared_entity_key is not None
    ):
        bot_uri_suffix = f"ref_{element.semantics.shared_entity_key}"
    else:
        bot_uri_suffix = unique_token(f"ref_{uri_suffix}", bot_uri_suffix_registry)
    identifier = (
        element.definition_id
        if element.definition_id is not None
        else slugify(element.label)
    )
    return SpaceNodeSpec(
        role=ROLE_SPACE,
        spot_class=element.spot_class,
        bot_class=element.bot_class,
        brot_class=element.semantics.brot_class,
        helper_name=helper_name,
        uri_suffix=uri_suffix,
        bot_uri_suffix=bot_uri_suffix,
        label=element.label,
        world_bbox=element.world_bbox,
        world_matrix=element.helper_world_matrix.copy(),
        length_unit=(
            LENGTH_UNIT_PIXEL
            if element.semantics.proxy_role
            in {PROXY_ROLE_PHYSICAL, PROXY_ROLE_DOCUMENT}
            else LENGTH_UNIT_METER
        ),
        local_bbox=list(element.local_bbox),
        sort_key=f"{parent_prefix}:250:{identifier}:{slugify(element.label)}",
        source_name=element.source_object.name,
        source_collection=(
            element.source_collections[0] if element.source_collections else None
        ),
        source_ifc_class=element.ifc_class,
        source_definition_id=element.definition_id,
        source_asset_path=element.asset_path,
        plan_kind=element.plan_kind,
        source_collections=element.source_collections,
    )


def unique_token(base_name: str, registry: set[str]) -> str:
    candidate = base_name
    index = 2
    while candidate in registry:
        candidate = f"{base_name}_{index:03d}"
        index += 1
    registry.add(candidate)
    return candidate


def build_scene_specs(
    site_collection: bpy.types.Collection | None,
    building_collection: bpy.types.Collection,
    asset_root_object: bpy.types.Object | None,
    storey_roots: list[bpy.types.Collection | bpy.types.Object],
    direct_space_roots: list[bpy.types.Collection | bpy.types.Object],
    site_direct_spaces: list[ElementSource],
    building_direct_spaces: list[ElementSource],
    storey_space_assignments: dict[str, list[ElementSource]],
    direct_space_assignments: dict[str, list[ElementSource]],
    site_direct: list[ElementSource],
    building_direct: list[ElementSource],
    storey_assignments: dict[str, list[ElementSource]],
    helper_name_registry: set[str] | None = None,
    uri_suffix_registry: set[str] | None = None,
    bot_uri_suffix_registry: set[str] | None = None,
) -> SpaceNodeSpec:
    helper_name_registry = (
        helper_name_registry if helper_name_registry is not None else set()
    )
    uri_suffix_registry = (
        uri_suffix_registry if uri_suffix_registry is not None else set()
    )
    bot_uri_suffix_registry = (
        bot_uri_suffix_registry if bot_uri_suffix_registry is not None else set()
    )

    storey_entries: list[
        tuple[
            bpy.types.Collection | bpy.types.Object,
            list[ElementSource],
            Aabb,
            Aabb | None,
        ]
    ] = []
    for storey_root in storey_roots:
        assigned_spaces = sorted(
            storey_space_assignments.get(storey_root.name, []),
            key=lambda element: element.source_object.name,
        )
        assigned = sorted(
            storey_assignments.get(storey_root.name, []),
            key=lambda element: element.source_object.name,
        )
        assigned_nodes = [*assigned_spaces, *assigned]
        world_bbox = preferred_extent_bbox(
            assigned_nodes,
            allow_fallback_to_all=True,
        )
        root_bbox_value = root_world_bbox(storey_root)
        if world_bbox is None:
            world_bbox = root_bbox_value
        elif root_bbox_value is not None:
            world_bbox = union_aabbs([world_bbox, root_bbox_value])
        if world_bbox is None:
            continue
        extent_bbox = preferred_extent_bbox(assigned_nodes)
        if extent_bbox is None:
            extent_bbox = root_bbox_value
        elif root_bbox_value is not None:
            extent_bbox = union_aabbs([extent_bbox, root_bbox_value])
        storey_entries.append((storey_root, assigned_nodes, world_bbox, extent_bbox))

    storey_entries.sort(
        key=lambda entry: (entry[2].center.z, entry[0].name),
    )

    building_box_parts = [entry[3] for entry in storey_entries if entry[3] is not None]
    building_box_parts.extend(ifc_extent_boxes(building_direct_spaces))
    building_box_parts.extend(ifc_extent_boxes(building_direct))
    for root in direct_space_roots:
        semantics = root_semantics(root)
        if not semantics_contribute_to_extent(semantics):
            continue
        direct_bbox = root_world_bbox(root)
        if direct_bbox is not None:
            building_box_parts.append(direct_bbox)
    building_root: bpy.types.Collection | bpy.types.Object = (
        asset_root_object if asset_root_object is not None else building_collection
    )
    building_bbox = union_aabbs(building_box_parts)
    if building_bbox is None:
        building_bbox = union_aabbs([entry[2] for entry in storey_entries])
    # An AssetSpace without storeys takes its extent from its own root object,
    # or else from the elements placed directly in it.
    if building_bbox is None:
        building_bbox = root_world_bbox(building_root)
    if building_bbox is None:
        building_bbox = preferred_extent_bbox(
            [*building_direct_spaces, *building_direct],
            allow_fallback_to_all=True,
        )
    if building_bbox is None:
        raise RuntimeError(
            "Could not build a building bounding box from the resolved geometry"
        )
    building_source = root_source_object(building_root)
    building_semantics = root_semantics(
        building_root,
        default_bot_class=BOT_CLASS_BUILDING,
        default_spot_class=SPOT_CLASS_ASSET,
    )
    building_world_matrix, building_local_bbox = root_helper_pose(
        building_root,
        building_bbox,
    )

    building_helper_name, building_uri_suffix = build_container_identifiers(
        ROLE_BUILDING,
        building_semantics.label,
        get_ifc_definition_id(building_source),
        bot_class=building_semantics.bot_class,
        spot_class=building_semantics.spot_class,
    )
    building_helper_name = ensure_unique_name(
        building_helper_name, helper_name_registry
    )
    building_uri_suffix = unique_token(building_uri_suffix, uri_suffix_registry)
    building_bot_uri_suffix = unique_token(
        f"ref_{building_uri_suffix}", bot_uri_suffix_registry
    )

    building_spec = SpaceNodeSpec(
        role=ROLE_BUILDING,
        spot_class=building_semantics.spot_class or SPOT_CLASS_ASSET,
        bot_class=building_semantics.bot_class or BOT_CLASS_BUILDING,
        brot_class=building_semantics.brot_class,
        helper_name=building_helper_name,
        uri_suffix=building_uri_suffix,
        bot_uri_suffix=building_bot_uri_suffix,
        label=building_semantics.label,
        world_bbox=building_bbox,
        world_matrix=building_world_matrix,
        local_bbox=building_local_bbox,
        length_unit=LENGTH_UNIT_METER,
        sort_key="100:building",
        source_name=(
            building_source.name if building_source is not None else building_root.name
        ),
        source_collection=root_source_collection_name(building_root),
        source_ifc_class=building_semantics.ifc_class,
        source_definition_id=get_ifc_definition_id(building_source),
        source_collections=root_source_collection_names(building_root),
    )

    storey_parent = building_spec

    storey_specs_by_name: dict[str, SpaceNodeSpec] = {}
    direct_space_specs_by_name: dict[str, SpaceNodeSpec] = {}

    for index, (storey_root, assigned_nodes, storey_bbox, _) in enumerate(
        storey_entries, start=1
    ):
        storey_source = root_source_object(storey_root)
        storey_semantics = root_semantics(
            storey_root,
            default_bot_class=BOT_CLASS_STOREY,
            default_spot_class=SPOT_CLASS_VOLUME,
        )
        storey_helper_name, storey_uri_suffix = build_container_identifiers(
            ROLE_STOREY,
            storey_semantics.label,
            get_ifc_definition_id(storey_source),
            index=index,
            bot_class=storey_semantics.bot_class,
            spot_class=storey_semantics.spot_class,
        )
        storey_helper_name = ensure_unique_name(
            storey_helper_name, helper_name_registry
        )
        storey_uri_suffix = unique_token(storey_uri_suffix, uri_suffix_registry)
        storey_bot_uri_suffix = unique_token(
            f"ref_{storey_uri_suffix}", bot_uri_suffix_registry
        )

        storey_world_matrix, storey_local_bbox = root_helper_pose(
            storey_root,
            storey_bbox,
        )
        storey_spec = SpaceNodeSpec(
            role=ROLE_STOREY,
            spot_class=storey_semantics.spot_class or SPOT_CLASS_VOLUME,
            bot_class=storey_semantics.bot_class or BOT_CLASS_STOREY,
            brot_class=storey_semantics.brot_class,
            helper_name=storey_helper_name,
            uri_suffix=storey_uri_suffix,
            bot_uri_suffix=storey_bot_uri_suffix,
            label=storey_semantics.label,
            world_bbox=storey_bbox,
            world_matrix=storey_world_matrix,
            local_bbox=storey_local_bbox,
            length_unit=LENGTH_UNIT_METER,
            sort_key=f"200:{index:02d}:{slugify(storey_root.name)}",
            source_name=(
                storey_source.name if storey_source is not None else storey_root.name
            ),
            source_collection=root_source_collection_name(storey_root),
            source_ifc_class=storey_semantics.ifc_class,
            source_definition_id=get_ifc_definition_id(storey_source),
            source_collections=root_source_collection_names(storey_root),
        )
        for element in assigned_nodes:
            node_builder = make_space_node if element.is_space else make_element_node
            storey_spec.children.append(
                node_builder(
                    element,
                    parent_prefix=storey_spec.sort_key,
                    helper_name_registry=helper_name_registry,
                    uri_suffix_registry=uri_suffix_registry,
                    bot_uri_suffix_registry=bot_uri_suffix_registry,
                ),
            )
        storey_parent.children.append(storey_spec)
        storey_specs_by_name[storey_root.name] = storey_spec

    direct_space_specs: list[
        tuple[bpy.types.Collection | bpy.types.Object, SpaceNodeSpec]
    ] = []
    for root in direct_space_roots:
        direct_space_spec = make_container_space_node(
            root,
            direct_space_assignments.get(root.name, []),
            building_spec.sort_key,
            helper_name_registry,
            uri_suffix_registry,
            bot_uri_suffix_registry,
            default_spot_class=SPOT_CLASS_ENTITY,
            sort_order=225,
        )
        if direct_space_spec is None:
            continue
        direct_space_specs.append((root, direct_space_spec))
        direct_space_specs_by_name[root.name] = direct_space_spec

    for root, direct_space_spec in direct_space_specs:
        parent_node = building_spec
        if isinstance(root, bpy.types.Object):
            for ancestor_name in object_ancestor_names(root):
                parent_node = direct_space_specs_by_name.get(
                    ancestor_name,
                    storey_specs_by_name.get(ancestor_name, parent_node),
                )
                if parent_node is not building_spec:
                    break
        parent_node.children.append(direct_space_spec)

    for element in sorted(
        building_direct_spaces, key=lambda value: value.source_object.name
    ):
        building_spec.children.append(
            make_space_node(
                element,
                parent_prefix=building_spec.sort_key,
                helper_name_registry=helper_name_registry,
                uri_suffix_registry=uri_suffix_registry,
                bot_uri_suffix_registry=bot_uri_suffix_registry,
            ),
        )

    for element in sorted(building_direct, key=lambda value: value.source_object.name):
        building_spec.children.append(
            make_element_node(
                element,
                parent_prefix=building_spec.sort_key,
                helper_name_registry=helper_name_registry,
                uri_suffix_registry=uri_suffix_registry,
                bot_uri_suffix_registry=bot_uri_suffix_registry,
            ),
        )

    if site_collection is not None:
        site_box_parts = [building_bbox]
        site_box_parts.extend(ifc_extent_boxes(site_direct_spaces))
        site_box_parts.extend(ifc_extent_boxes(site_direct))
        site_bbox = union_aabbs(site_box_parts)
        if site_bbox is None:
            raise RuntimeError(
                "Could not build a site bounding box from the resolved geometry"
            )

        site_source = collection_object_named_like_collection(site_collection)
        site_semantics = parse_name_semantics(
            site_collection.name,
            default_bot_class=BOT_CLASS_SITE,
            default_spot_class=SPOT_CLASS_ENTITY,
        )
        site_world_matrix = make_world_matrix(site_bbox.center)
        site_helper_name, site_uri_suffix = build_container_identifiers(
            ROLE_SITE,
            site_semantics.label,
            get_ifc_definition_id(site_source),
            bot_class=site_semantics.bot_class,
            spot_class=site_semantics.spot_class,
        )
        site_helper_name = ensure_unique_name(site_helper_name, helper_name_registry)
        site_uri_suffix = unique_token(site_uri_suffix, uri_suffix_registry)
        site_bot_uri_suffix = unique_token(
            f"ref_{site_uri_suffix}", bot_uri_suffix_registry
        )
        site_spec = SpaceNodeSpec(
            role=ROLE_SITE,
            spot_class=site_semantics.spot_class or SPOT_CLASS_ENTITY,
            bot_class=site_semantics.bot_class or BOT_CLASS_SITE,
            brot_class=site_semantics.brot_class,
            helper_name=site_helper_name,
            uri_suffix=site_uri_suffix,
            bot_uri_suffix=site_bot_uri_suffix,
            label=site_semantics.label,
            world_bbox=site_bbox,
            world_matrix=site_world_matrix,
            local_bbox=local_bbox_from_world_bbox(
                site_bbox,
                site_world_matrix,
            ),
            length_unit=LENGTH_UNIT_METER,
            sort_key="000:site",
            source_name=(
                site_source.name if site_source is not None else site_collection.name
            ),
            source_collection=site_collection.name,
            source_ifc_class=site_semantics.ifc_class,
            source_definition_id=get_ifc_definition_id(site_source),
            source_collections=[site_collection.name],
        )
        for element in sorted(
            site_direct_spaces, key=lambda value: value.source_object.name
        ):
            site_spec.children.append(
                make_space_node(
                    element,
                    parent_prefix=site_spec.sort_key,
                    helper_name_registry=helper_name_registry,
                    uri_suffix_registry=uri_suffix_registry,
                    bot_uri_suffix_registry=bot_uri_suffix_registry,
                ),
            )

        for element in sorted(site_direct, key=lambda value: value.source_object.name):
            site_spec.children.append(
                make_element_node(
                    element,
                    parent_prefix=site_spec.sort_key,
                    helper_name_registry=helper_name_registry,
                    uri_suffix_registry=uri_suffix_registry,
                    bot_uri_suffix_registry=bot_uri_suffix_registry,
                ),
            )

        site_spec.children.sort(
            key=lambda node: (role_order(node.role), node.sort_key, node.helper_name)
        )
        building_spec.children.append(site_spec)

    building_spec.children.sort(
        key=lambda node: (role_order(node.role), node.sort_key, node.helper_name)
    )

    def sort_descendants(node: SpaceNodeSpec) -> None:
        node.children.sort(
            key=lambda child: (
                role_order(child.role),
                child.sort_key,
                child.helper_name,
            )
        )
        for child in node.children:
            sort_descendants(child)

    sort_descendants(building_spec)

    return building_spec


def build_document_specs(
    document_collection: bpy.types.Collection,
    helper_name_registry: set[str],
    uri_suffix_registry: set[str],
    bot_uri_suffix_registry: set[str],
    *,
    visible_only: bool = True,
) -> list[SpaceNodeSpec]:
    specs: list[SpaceNodeSpec] = []
    for index, document_root in enumerate(
        document_root_objects(document_collection, visible_only=visible_only), start=1
    ):
        proxy_elements = collect_document_proxy_elements(
            document_root,
            document_collection,
            visible_only=visible_only,
        )
        root_semantics = parse_name_semantics(
            document_root.name,
            default_spot_class="DocumentSpace",
        )
        document_asset_path = (
            object_texture_asset_path(document_root) or root_semantics.asset_path
        )
        root_world_bbox = object_world_aabb(document_root)
        if root_world_bbox is None:
            root_world_bbox = union_aabbs(
                element.world_bbox for element in proxy_elements
            )
        if root_world_bbox is None:
            continue

        root_local_bbox = object_local_bbox(document_root)
        if root_local_bbox is not None:
            helper_local_bbox = list(root_local_bbox)
            helper_world_matrix = document_root.matrix_world.copy()
        else:
            helper_world_matrix = document_root.matrix_world.copy()
            helper_local_bbox = local_bbox_from_world_bbox(
                root_world_bbox,
                helper_world_matrix,
            )

        helper_name, uri_suffix = build_container_identifiers(
            ROLE_DOCUMENT,
            root_semantics.label,
            get_ifc_definition_id(document_root),
            spot_class=root_semantics.spot_class,
        )
        helper_name = ensure_unique_name(helper_name, helper_name_registry)
        uri_suffix = unique_token(uri_suffix, uri_suffix_registry)

        root_spec = SpaceNodeSpec(
            role=ROLE_DOCUMENT,
            spot_class=root_semantics.spot_class or "DocumentSpace",
            bot_class=None,
            helper_name=helper_name,
            uri_suffix=uri_suffix,
            bot_uri_suffix=None,
            label=root_semantics.label,
            world_bbox=root_world_bbox,
            world_matrix=helper_world_matrix,
            length_unit=LENGTH_UNIT_PIXEL,
            local_bbox=helper_local_bbox,
            sort_key=f"050:document:{index:02d}:{slugify(document_root.name)}",
            source_name=document_root.name,
            source_collection=document_collection.name,
            source_ifc_class=root_semantics.ifc_class,
            source_definition_id=get_ifc_definition_id(document_root),
            source_asset_path=document_asset_path,
            source_collections=sorted(
                collection.name for collection in document_root.users_collection
            ),
        )

        for element in proxy_elements:
            root_spec.children.append(
                make_element_node(
                    element,
                    parent_prefix=root_spec.sort_key,
                    helper_name_registry=helper_name_registry,
                    uri_suffix_registry=uri_suffix_registry,
                    bot_uri_suffix_registry=bot_uri_suffix_registry,
                )
            )

        root_spec.children.sort(
            key=lambda node: (role_order(node.role), node.sort_key, node.helper_name)
        )
        specs.append(root_spec)

    return specs


def instantiate_helpers(
    helper_collection: bpy.types.Collection,
    node: SpaceNodeSpec,
    parent: bpy.types.Object | None = None,
    parent_world_matrix: Matrix | None = None,
) -> bpy.types.Object:
    helper = create_helper_object(
        helper_collection,
        node,
        parent=parent,
        parent_world_matrix=parent_world_matrix,
    )
    for child in node.children:
        instantiate_helpers(
            helper_collection,
            child,
            parent=helper,
            parent_world_matrix=node.world_matrix,
        )
    return helper


def save_blend_file(filepath: str) -> None:
    output_path = Path(filepath).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path), copy=False)


def build_helper_scene(
    project_collection_name: str | None = None,
    helper_collection_name: str = HELPER_COLLECTION_NAME,
    save_blend_path: str | None = None,
    visible_only: bool = True,
) -> dict[str, object]:
    scene = bpy.context.scene
    structure = resolve_scene_structure(scene, project_collection_name)
    project_collection = structure.project_collection
    site_collection = structure.site_collection
    building_collection = structure.building_collection
    asset_root_object = structure.asset_root_object
    storey_collections = structure.storey_collections
    direct_space_collections = structure.direct_space_collections
    storey_objects = structure.storey_objects
    direct_space_objects = structure.direct_space_objects
    storey_roots: list[bpy.types.Collection | bpy.types.Object] = [
        *storey_collections,
        *storey_objects,
    ]
    direct_space_roots: list[bpy.types.Collection | bpy.types.Object] = [
        *direct_space_collections,
        *direct_space_objects,
    ]
    elements = collect_candidate_elements(
        project_collection,
        visible_only=visible_only,
    )
    container_root_objects = {
        obj.name
        for obj in [
            root_source_object(building_collection),
            (
                root_source_object(site_collection)
                if site_collection is not None
                else None
            ),
            asset_root_object,
            *storey_objects,
            *direct_space_objects,
        ]
        if obj is not None
    }
    elements = [
        element
        for element in elements
        if element.source_object.name not in container_root_objects
    ]
    spaces = [element for element in elements if element.is_space]
    geometry_elements = [element for element in elements if not element.is_space]

    asset_direct_spaces = [
        element
        for element in spaces
        if element.semantics.appearance_target == APPEARANCE_TARGET_ASSET
    ]
    routed_spaces = [
        element
        for element in spaces
        if element.semantics.appearance_target != APPEARANCE_TARGET_ASSET
    ]
    asset_direct_elements = [
        element
        for element in geometry_elements
        if element.semantics.appearance_target == APPEARANCE_TARGET_ASSET
    ]
    routed_geometry_elements = [
        element
        for element in geometry_elements
        if element.semantics.appearance_target != APPEARANCE_TARGET_ASSET
    ]

    (
        site_direct_spaces,
        building_direct_spaces,
        storey_space_assignments,
        direct_space_assignments,
        space_warnings,
    ) = assign_elements(
        routed_spaces,
        site_collection,
        building_collection,
        storey_roots,
        direct_space_roots,
    )
    building_direct_spaces.extend(asset_direct_spaces)

    (
        site_direct,
        building_direct,
        storey_assignments,
        direct_element_assignments,
        warnings,
    ) = assign_elements(
        routed_geometry_elements,
        site_collection,
        building_collection,
        storey_roots,
        direct_space_roots,
    )
    building_direct.extend(asset_direct_elements)
    helper_name_registry: set[str] = set()
    uri_suffix_registry: set[str] = set()
    bot_uri_suffix_registry: set[str] = set()

    root_spec = build_scene_specs(
        site_collection,
        building_collection,
        asset_root_object,
        storey_roots,
        direct_space_roots,
        site_direct_spaces,
        building_direct_spaces,
        storey_space_assignments,
        {
            root.name: [
                *direct_space_assignments.get(root.name, []),
                *direct_element_assignments.get(root.name, []),
            ]
            for root in direct_space_roots
        },
        site_direct,
        building_direct,
        storey_assignments,
        helper_name_registry=helper_name_registry,
        uri_suffix_registry=uri_suffix_registry,
        bot_uri_suffix_registry=bot_uri_suffix_registry,
    )
    document_collection = find_document_collection(scene)
    document_specs = (
        build_document_specs(
            document_collection,
            helper_name_registry,
            uri_suffix_registry,
            bot_uri_suffix_registry,
            visible_only=visible_only,
        )
        if document_collection is not None
        else []
    )
    helper_collection = reset_helper_collection(scene, helper_collection_name)
    instantiate_helpers(helper_collection, root_spec)
    for document_spec in document_specs:
        instantiate_helpers(helper_collection, document_spec)
    bpy.context.view_layer.update()
    warnings.extend(apply_am_helper_bounds(helper_collection))
    bpy.context.view_layer.update()

    helper_count = len(
        [obj for obj in helper_collection.objects if obj.get(PROP_HELPER)],
    )
    root_world_center = tuple(float(value) for value in root_spec.world_bbox.center)
    result: dict[str, object] = {
        "helper_collection_name": helper_collection.name,
        "project_collection_name": project_collection.name,
        "visible_only": visible_only,
        "site_collection_name": (
            site_collection.name if site_collection is not None else None
        ),
        "building_collection_name": building_collection.name,
        # Count the storey helpers that were actually built, rather than the
        # storeys that happened to receive element assignments.
        "resolved_storey_count": sum(
            1
            for obj in helper_collection.objects
            if obj.get(PROP_HELPER) and str(obj.get(PROP_ROLE, "")) == ROLE_STOREY
        ),
        "site_space_count": len(site_direct_spaces),
        "building_space_count": len(building_direct_spaces),
        "storey_space_counts": {
            storey_name: len(values)
            for storey_name, values in sorted(storey_space_assignments.items())
            if values
        },
        "site_element_count": len(site_direct),
        "building_element_count": len(building_direct),
        "storey_element_counts": {
            storey_name: len(values)
            for storey_name, values in sorted(storey_assignments.items())
            if values
        },
        "direct_space_counts": {
            collection.name: len(
                [
                    *direct_space_assignments.get(collection.name, []),
                    *direct_element_assignments.get(collection.name, []),
                ]
            )
            for collection in direct_space_collections
        },
        "root_helper_name": root_spec.helper_name,
        "root_helper_names": [
            root_spec.helper_name,
            *[spec.helper_name for spec in document_specs],
        ],
        "document_collection_name": (
            document_collection.name if document_collection is not None else None
        ),
        "document_root_count": len(document_specs),
        "root_world_center": root_world_center,
        "warning_count": len(space_warnings) + len(warnings),
        "warnings": [*space_warnings, *warnings],
        "helper_count": helper_count,
        "saved_blend_path": None,
    }

    if save_blend_path:
        save_blend_file(save_blend_path)
        result["saved_blend_path"] = str(
            Path(save_blend_path).expanduser().resolve(),
        )

    return result


def print_build_summary(result: dict[str, object]) -> None:
    print(f"Created helper collection '{result['helper_collection_name']}'")
    print(f"Project collection: {result['project_collection_name']}")
    print(f"Site collection: {result['site_collection_name']}")
    print(f"Building collection: {result['building_collection_name']}")
    print(f"Resolved storeys: {result['resolved_storey_count']}")
    print("Direct space counts:", result["direct_space_counts"])
    print(f"Site-level spaces: {result['site_space_count']}")
    print(f"Building-level spaces: {result['building_space_count']}")
    print("Storey space counts:", result["storey_space_counts"])
    print(f"Site-level elements: {result['site_element_count']}")
    print(f"Building-level elements: {result['building_element_count']}")
    print("Storey element counts:", result["storey_element_counts"])
    print(f"Scene graph root helper: {result['root_helper_name']}")
    center_x, center_y, center_z = result["root_world_center"]
    print(f"Root world center: ({center_x:.6f}, {center_y:.6f}, {center_z:.6f})")
    print(f"Warnings: {result['warning_count']}")
    for warning in result["warnings"]:
        print(f"WARNING: {warning}")
    if result["saved_blend_path"]:
        print(f"Saved helper scene to {result['saved_blend_path']}")


def main() -> None:
    args = parse_args()
    result = build_helper_scene(
        project_collection_name=args.project_collection,
        helper_collection_name=args.helper_collection,
        save_blend_path=args.save_blend,
        visible_only=not args.all_objects,
    )
    print_build_summary(result)


if __name__ == "__main__":
    main()
