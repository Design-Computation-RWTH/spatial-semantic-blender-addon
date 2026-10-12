from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Matrix, Vector


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

import spatial_semantic_addon
from spatial_semantic_addon.build_spot_scene import (
    build_helper_scene,
    build_ifc_bounding_box,
    collect_document_proxy_elements,
    find_ifc_project_collections,
    ifc_bounding_box_source_objects,
    unscaled_source_frame,
)
from spatial_semantic_addon.export_spot_ttl import (
    LOCAL_TERM_MARKER,
    export_distribution_block,
    export_helper_scene,
    export_ifc_model_document_appearance_block,
    export_ifc_model_document_space_block,
    exported_matrix_literal,
    export_space_axis_blocks,
    export_space_block,
    exported_bbox_for_helper,
)
from spatial_semantic_addon.import_spot_ttl import import_scene_graph_turtle
from spatial_semantic_addon.spot_blender_common import (
    PROP_AM_ENABLED,
    PROP_BOT_CLASS,
    PROP_HELPER,
    PROP_IFC_FILE,
    PROP_LABEL,
    PROP_LENGTH_UNIT,
    PROP_LOCAL_BBOX,
    PROP_PLAN_KIND,
    PROP_REPRESENTS_LINKS,
    PROP_SOURCE_ASSET_PATH,
    PROP_SOURCE_IFC_CLASS,
    PROP_SPOT_CLASS,
    LENGTH_UNIT_PIXEL,
    bbox_vertices,
    collection_object_named_like_collection,
    human_name,
    matrix_to_column_major_literal,
    parse_name_semantics,
)


def rectangle_object(
    name: str,
    collection: bpy.types.Collection,
    corners: list[tuple[float, float, float]],
) -> bpy.types.Object:
    """A flat rectangle whose local corners state where its origin sits."""
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(corners, [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def box_object(
    name: str,
    collection: bpy.types.Collection,
    location: tuple[float, float, float],
    size: float = 1.0,
) -> bpy.types.Object:
    """A small cube standing in for an IFC element's geometry."""
    half = size / 2.0
    corners = [
        (x, y, z)
        for x in (-half, half)
        for y in (-half, half)
        for z in (-half, half)
    ]
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(corners, [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    collection.objects.link(obj)
    return obj


def build_ifc_like_scene(
    *, asset_root_location: tuple[float, float, float]
) -> bpy.types.Collection:
    """
    A minimal Bonsai-shaped scene: an AssetSpace collection holding an
    IfcProject root empty and one storey with two element boxes. The root
    empty sits away from the geometry so placement tests are not vacuous.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    building_collection = bpy.data.collections.new("AssetSpace_Building")
    bpy.context.scene.collection.children.link(building_collection)

    asset_root = bpy.data.objects.new("AssetSpace_Model", None)
    building_collection.objects.link(asset_root)
    asset_root.location = asset_root_location

    storey_collection = bpy.data.collections.new(
        "IfcBuildingStorey/Level 1::SPOT=VolumeSpace"
    )
    building_collection.children.link(storey_collection)

    box_object("IfcWall/Wall 1", storey_collection, (0.0, 0.0, 0.0))
    box_object("IfcWall/Wall 2", storey_collection, (2.0, 0.0, 0.0))

    bpy.context.view_layer.update()
    return building_collection


def test_build_mirrors_the_asset_space_structure() -> None:
    """
    The build converts the asset space as authored: every element becomes its
    own helper, storeys stay parented to the building, and no whole-model
    container is invented in between.
    """
    building_collection = build_ifc_like_scene(asset_root_location=(100.0, 200.0, 0.0))
    storey_collection = building_collection.children[0]
    box_object("VolumeSpace_Room::BOT=Space", storey_collection, (8.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    helper_names = [obj.name for obj in helper_collection.objects]

    assert not [name for name in helper_names if "SPOT::Entity::Model::" in name], (
        helper_names
    )
    # Both walls stay individual elements instead of folding into one space.
    element_helpers = [
        name for name in helper_names if "SPOT::Entity::Element::" in name
    ]
    assert len(element_helpers) == 2, helper_names
    assert result["resolved_storey_count"] == 1, result["resolved_storey_count"]

    # The storey hangs off the building, and the BOT space off the storey,
    # exactly as the source collections nest them.
    (storey_helper,) = [
        obj
        for obj in helper_collection.objects
        if "SPOT::Entity::Storey::" in obj.name
    ]
    (building_helper,) = [
        obj
        for obj in helper_collection.objects
        if "SPOT::Asset::Building::" in obj.name
    ]
    (room_helper,) = [
        obj for obj in helper_collection.objects if "room" in obj.name
    ]
    assert storey_helper.parent == building_helper, storey_helper.parent
    assert room_helper.parent == storey_helper, room_helper.parent
    print("Build mirrors the asset space structure test passed")


def test_find_ifc_project_collections_is_name_only() -> None:
    """
    Discovery must work from the 'IfcProject/...' collection name alone, with
    no dependency on Bonsai's paired root object or on anything being
    selected -- Bonsai is frequently disabled once an IFC has already been
    imported, and the collection is still there either way.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    first = bpy.data.collections.new("IfcProject/Project A")
    bpy.context.scene.collection.children.link(first)
    second = bpy.data.collections.new("IfcProject/Project B")
    bpy.context.scene.collection.children.link(second)
    unrelated = bpy.data.collections.new("Some Other Collection")
    bpy.context.scene.collection.children.link(unrelated)

    found = find_ifc_project_collections(bpy.context.scene)

    assert {collection.name for collection in found} == {first.name, second.name}
    print("Find IFC project collections test passed")


def world_bbox_of_object(obj) -> tuple[list[float], list[float]]:
    """The world-space region a bounding-box mesh covers."""
    corners = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    return (
        [round(min(corner[axis] for corner in corners), 6) for axis in range(3)],
        [round(max(corner[axis] for corner in corners), 6) for axis in range(3)],
    )


def test_ifc_bounding_box_naming_follows_the_scene_convention() -> None:
    """
    The generated object follows the same naming convention as the rest of
    the scene: SPOT class, then label, then classification tags. It is a
    VolumeSpace, it is classified as a whole IFC model rather than as a
    modelled space, and it is a single mesh rather than a parented pair.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)

    box_object("IfcWall/Wall 1", ifc_collection, (0.0, 0.0, 0.0))
    box_object("IfcWall/Wall 2", ifc_collection, (2.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    source_objects = ifc_bounding_box_source_objects([ifc_collection])
    assert {obj.name for obj in source_objects} == {"IfcWall/Wall 1", "IfcWall/Wall 2"}

    bbox_object = build_ifc_bounding_box(
        source_objects,
        root_object=None,
        label="My Project",
        collection_name="IFC Bounding Boxes",
    )

    assert bbox_object.type == "MESH", bbox_object.type
    assert bbox_object.parent is None
    assert bbox_object.name.startswith("VolumeSpace_my-project"), bbox_object.name
    assert "AssetSpace" not in bbox_object.name, bbox_object.name

    semantics = parse_name_semantics(bbox_object.name)
    assert semantics.spot_class == "VolumeSpace", bbox_object.name
    assert semantics.label == "my-project", semantics.label
    # Classified as a whole IFC model, and carrying no BOT class: no BOT
    # class describes an IFC file, and asserting one would put this box in
    # the viewer's bot:Space queries.
    assert semantics.ifc_class == "IfcProject", semantics.ifc_class
    assert semantics.bot_class is None, semantics.bot_class

    target = bpy.data.collections["IFC Bounding Boxes"]
    assert len(target.objects) == 1, list(target.objects)
    print("IFC bounding box naming test passed")


def test_ifc_tag_overrides_the_prefix_inference() -> None:
    """
    An explicit `::IFC=` tag states the IFC class for a name that carries no
    `IfcWall/...` prefix, and beats the prefix when both are present.
    """
    tagged = parse_name_semantics("VolumeSpace_model::IFC=IfcProject")
    assert tagged.ifc_class == "IfcProject", tagged.ifc_class
    assert tagged.spot_class == "VolumeSpace", tagged.spot_class

    # The tag wins over the prefix rather than the other way round.
    both = parse_name_semantics("IfcWall/Wall 1::IFC=IfcProject")
    assert both.ifc_class == "IfcProject", both.ifc_class

    # Without a tag the prefix still applies, unchanged.
    prefixed = parse_name_semantics("IfcWall/Wall 1")
    assert prefixed.ifc_class == "IfcWall", prefixed.ifc_class
    print("IFC tag override test passed")


def test_ifc_bounding_box_spans_only_the_geometry() -> None:
    """
    The box spans the placed geometry alone. The IFC root point does not
    stretch it, and neither do type objects, which are metadata rather than
    placed geometry. The origin still sits on the IFC root, so the box carries
    the IFC frame even though it does not contain its own origin.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)

    ifc_root = bpy.data.objects.new("IfcProject/My Project", None)
    ifc_collection.objects.link(ifc_root)
    ifc_root.location = (500.0, 500.0, 0.0)

    # Geometry sits far from the root, so a box that reached the root would
    # fail the extent assertion below.
    box_object("IfcWall/Wall 1", ifc_collection, (0.0, 0.0, 0.0))
    box_object("IfcWall/Wall 2", ifc_collection, (2.0, 0.0, 0.0))

    # A type object far away, the way Bonsai keeps types beside the model.
    type_collection = bpy.data.collections.new("IfcTypeProduct/Wall Types")
    ifc_collection.children.link(type_collection)
    box_object("IfcWallType/Basic Wall", type_collection, (-1000.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    root_object = collection_object_named_like_collection(ifc_collection)
    assert root_object is ifc_root

    bbox_object = build_ifc_bounding_box(
        ifc_bounding_box_source_objects([ifc_collection]),
        root_object=root_object,
        label="My Project",
        collection_name="IFC Bounding Boxes",
    )

    # The origin sits on the IFC root.
    assert tuple(round(value, 6) for value in bbox_object.matrix_world.translation) == (
        500.0,
        500.0,
        0.0,
    )

    # The box spans the two walls and nothing else.
    minimum, maximum = world_bbox_of_object(bbox_object)
    assert minimum == [-0.5, -0.5, -0.5], minimum
    assert maximum == [2.5, 0.5, 0.5], maximum
    print("IFC bounding box geometry-only extent test passed")


def test_dragging_the_bounding_box_into_asset_space_converts_it_verbatim() -> None:
    """
    End to end, with Bonsai disabled: discover the disconnected
    'IfcProject/...' collection by name only, build its bounding box, link
    that box into the AssetSpace collection the way a manual drag-and-drop
    would, and confirm the build converts that box into the SPOT scene
    unchanged -- same shape, same origin, no extra container around it.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)
    box_object("IfcWall/Wall 1", ifc_collection, (500.0, 500.0, 0.0))
    box_object("IfcWall/Wall 2", ifc_collection, (502.0, 500.0, 0.0))
    bpy.context.view_layer.update()

    (found_collection,) = find_ifc_project_collections(bpy.context.scene)
    assert found_collection is ifc_collection

    bbox_object = build_ifc_bounding_box(
        ifc_bounding_box_source_objects([found_collection]),
        root_object=collection_object_named_like_collection(found_collection),
        label=human_name(found_collection.name),
        collection_name="IFC Bounding Boxes",
    )

    building_collection = bpy.data.collections.new("AssetSpace_Building")
    bpy.context.scene.collection.children.link(building_collection)
    storey_collection = bpy.data.collections.new(
        "IfcBuildingStorey/Level 1::SPOT=VolumeSpace"
    )
    building_collection.children.link(storey_collection)
    box_object("VolumeSpace_Room::BOT=Space", storey_collection, (0.0, 0.0, 0.0))

    # The manual drag-and-drop: move the one box into AssetSpace_Building.
    bpy.data.collections["IFC Bounding Boxes"].objects.unlink(bbox_object)
    building_collection.objects.link(bbox_object)
    bpy.context.view_layer.update()

    source_minimum, source_maximum = world_bbox_of_object(bbox_object)

    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    helper_names = [obj.name for obj in helper_collection.objects]

    # The box becomes one helper of its own. Nothing wraps it, and no
    # whole-model container is invented around the storeys.
    box_helpers = [name for name in helper_names if "my-project" in name]
    assert len(box_helpers) == 1, helper_names
    assert not [name for name in helper_names if "SPOT::Entity::Model::" in name], (
        helper_names
    )

    # The box keeps the exact region it covered in the asset space.
    box_helper = helper_collection.objects[box_helpers[0]]
    helper_minimum, helper_maximum = world_bbox_of_object(box_helper)
    assert helper_minimum == source_minimum, (helper_minimum, source_minimum)
    assert helper_maximum == source_maximum, (helper_maximum, source_maximum)

    # And it keeps its origin. Every other helper is centred on its own box,
    # but this one carries the IFC file's frame: a BCF Viewpoint exported
    # against this space (ADR 0015) has to land where a tool reading the same
    # IFC file puts it. Centring the origin would offset every such Viewpoint
    # by the vector from the IFC root to the box centre.
    assert tuple(
        round(value, 6) for value in box_helper.matrix_world.translation
    ) == tuple(round(value, 6) for value in bbox_object.matrix_world.translation), (
        box_helper.matrix_world.translation,
        bbox_object.matrix_world.translation,
    )
    print("Bounding box converted verbatim test passed")


def test_ifc_model_space_keeps_an_asymmetric_box_around_the_ifc_origin() -> None:
    """
    The exported box of a whole-model IFC space is stated around the IFC
    root, so for a model whose geometry does not straddle that root the box
    is asymmetric.

    This is the shape that carries the frame. A symmetric box is the
    signature of an origin moved to the box centre, which is what every other
    helper does and what silently offset every reframed BCF Viewpoint by the
    vector from the IFC root to that centre.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)

    ifc_root = bpy.data.objects.new("IfcProject/My Project", None)
    ifc_collection.objects.link(ifc_root)
    ifc_root.location = (0.0, 0.0, 0.0)

    # Geometry entirely on the +X side of the root, as in a real model whose
    # origin sits at a survey point rather than inside the building.
    box_object("IfcWall/Wall 1", ifc_collection, (10.0, 0.0, 0.0))
    box_object("IfcWall/Wall 2", ifc_collection, (20.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    bbox_object = build_ifc_bounding_box(
        ifc_bounding_box_source_objects([ifc_collection]),
        root_object=collection_object_named_like_collection(ifc_collection),
        label="My Project",
        collection_name="IFC Bounding Boxes",
    )

    building_collection = bpy.data.collections.new("AssetSpace_Building")
    bpy.context.scene.collection.children.link(building_collection)
    storey_collection = bpy.data.collections.new(
        "IfcBuildingStorey/Level 1::SPOT=VolumeSpace"
    )
    building_collection.children.link(storey_collection)
    box_object("VolumeSpace_Room::BOT=Space", storey_collection, (0.0, 0.0, 0.0))

    bpy.data.collections["IFC Bounding Boxes"].objects.unlink(bbox_object)
    building_collection.objects.link(bbox_object)
    bpy.context.view_layer.update()

    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    (box_helper_name,) = [
        obj.name for obj in helper_collection.objects if "my-project" in obj.name
    ]
    box_helper = helper_collection.objects[box_helper_name]

    # The origin is the IFC root, not the centre of the geometry.
    assert tuple(
        round(value, 6) for value in box_helper.matrix_world.translation
    ) == (0.0, 0.0, 0.0), box_helper.matrix_world.translation

    # The exported box is the one the graph carries, and it is asymmetric:
    # it covers only the geometry on +X, away from its own origin.
    local_bbox = [round(float(value), 6) for value in box_helper[PROP_LOCAL_BBOX]]
    assert local_bbox[0] == 9.5, local_bbox
    assert local_bbox[3] == 20.5, local_bbox
    print("IFC model space asymmetric box test passed")


def test_ifc_bounding_box_keeps_a_rotated_root_frame() -> None:
    """
    An IFC root whose placement carries a rotation defines a rotated frame.
    A translation-only placement would drop it and leave every reframed
    Viewpoint rotated about the root.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)

    ifc_root = bpy.data.objects.new("IfcProject/My Project", None)
    ifc_collection.objects.link(ifc_root)
    ifc_root.location = (5.0, 0.0, 0.0)
    ifc_root.rotation_euler = (0.0, 0.0, 1.5707963267948966)  # 90 degrees about Z

    box_object("IfcWall/Wall 1", ifc_collection, (10.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    bbox_object = build_ifc_bounding_box(
        ifc_bounding_box_source_objects([ifc_collection]),
        root_object=collection_object_named_like_collection(ifc_collection),
        label="My Project",
        collection_name="IFC Bounding Boxes",
    )

    # The box carries the root's rotation, not only its position.
    assert tuple(round(value, 6) for value in bbox_object.matrix_world.translation) == (
        5.0,
        0.0,
        0.0,
    ), bbox_object.matrix_world.translation
    rotation = bbox_object.matrix_world.to_quaternion()
    expected = ifc_root.matrix_world.to_quaternion()
    assert all(
        round(rotation[index] - expected[index], 6) == 0 for index in range(4)
    ), (rotation, expected)

    # The box still covers the geometry despite being stated in a rotated
    # frame, so the rotation was applied to the frame and not to the extent.
    minimum, maximum = world_bbox_of_object(bbox_object)
    assert minimum[0] <= 9.5 and maximum[0] >= 10.5, (minimum, maximum)
    print("IFC bounding box rotated root test passed")


def world_bbox_of_placement(element) -> tuple[list[float], list[float]]:
    """The region the exported bounding box covers once it is placed."""
    corners = [
        element.helper_world_matrix @ Vector(corner)
        for corner in bbox_vertices(element.local_bbox)
    ]
    return (
        [round(min(corner[axis] for corner in corners), 6) for axis in range(3)],
        [round(max(corner[axis] for corner in corners), 6) for axis in range(3)],
    )


def test_document_proxy_uses_the_same_local_frame_as_other_helpers() -> None:
    """
    A document proxy and its physical twin place the one bounding box the
    entity exports, so both have to describe it in the same local frame: the
    origin on the centre of the region. A plan crop is normally drawn around a
    corner, and that corner must not leak into the placement.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Document")
    bpy.context.scene.collection.children.link(collection)

    sheet = rectangle_object(
        "GC 030",
        collection,
        [(0.0, -6.0, 0.0), (14.0, 0.0, 0.0)],
    )

    # The crop is drawn from its top left corner, two by one units wide, and
    # its origin sits on that corner rather than in the middle.
    crop = rectangle_object(
        "GC 030_DA_Dachgeschoss",
        collection,
        [(0.0, -1.0, 0.0), (2.0, 0.0, 0.0)],
    )
    crop.parent = sheet
    crop.matrix_parent_inverse = Matrix.Identity(4)
    crop.matrix_basis = Matrix.Translation((0.5, -0.25, 0.0))
    bpy.context.view_layer.update()

    elements = collect_document_proxy_elements(sheet, collection, visible_only=False)

    assert len(elements) == 1, [element.source_object.name for element in elements]
    element = elements[0]

    # The bounding box is centred on the helper origin, as for every other
    # helper the scene build produces.
    assert element.local_bbox == [-1.0, -0.5, 0.0, 1.0, 0.5, 0.0], element.local_bbox

    # Placing that box still covers the region the crop occupies on the sheet.
    minimum, maximum = world_bbox_of_placement(element)
    assert minimum == [0.5, -1.25, 0.0], minimum
    assert maximum == [2.5, -0.25, 0.0], maximum
    print("Document proxy local frame test passed")


def test_document_proxy_origin_already_centred_is_kept() -> None:
    """A crop drawn around its own centre keeps the placement it had."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Document")
    bpy.context.scene.collection.children.link(collection)

    sheet = rectangle_object(
        "GC 030",
        collection,
        [(0.0, -6.0, 0.0), (14.0, 0.0, 0.0)],
    )
    crop = rectangle_object(
        "GC 030_DA_Dachgeschoss",
        collection,
        [(-1.0, -0.5, 0.0), (1.0, 0.5, 0.0)],
    )
    crop.parent = sheet
    crop.matrix_parent_inverse = Matrix.Identity(4)
    crop.matrix_basis = Matrix.Translation((1.5, -0.75, 0.0))
    bpy.context.view_layer.update()

    element = collect_document_proxy_elements(sheet, collection, visible_only=False)[0]

    assert element.local_bbox == [-1.0, -0.5, 0.0, 1.0, 0.5, 0.0], element.local_bbox
    assert tuple(
        round(value, 6) for value in element.helper_world_matrix.translation
    ) == (1.5, -0.75, 0.0)
    print("Centred document proxy test passed")


# 6.38 mm per pixel: the drawing scale of the Domhof plan GC_030. A pixel is
# 0.001 Blender units, so the helper of a plan region is scaled by 6.38.
DRAWING_SCALE = 6.38
REGION_BOX = [-1.505, -1.004, 0.0, 1.505, 1.004, 0.0]


def test_physical_plan_helper_keeps_the_drawing_scale() -> None:
    """
    The physical helper of a plan region shares its box with the document
    proxy, which lies unscaled on the sheet. So the drawing scale stays in the
    frame, and the box stays the one the proxy has.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Asset")
    bpy.context.scene.collection.children.link(collection)
    region = rectangle_object(
        "GC 030_AA_Dachgeschoss",
        collection,
        [tuple(REGION_BOX[:3]), tuple(REGION_BOX[3:])],
    )
    region.matrix_basis = Matrix.Translation((2.0, 3.0, 0.0)) @ Matrix.Scale(
        DRAWING_SCALE, 4
    )
    bpy.context.view_layer.update()

    world_matrix, bbox = unscaled_source_frame(
        region, list(REGION_BOX), parse_name_semantics(region.name)
    )

    assert [round(value, 6) for value in bbox] == REGION_BOX, bbox
    assert [round(value, 6) for value in world_matrix.to_scale()] == [
        DRAWING_SCALE
    ] * 3, world_matrix
    print("Physical plan helper scale test passed")


def test_pixel_helper_exports_its_box_in_sheet_pixels() -> None:
    """
    A plan region placed in the building states its box in the pixels of its
    sheet, and its matrix carries the drawing scale from pixels to metres.
    Moving that scale into the box would make the region 6.38 times too large
    on its sheet.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    asset = bpy.data.objects.new("Asset", None)
    region = bpy.data.objects.new("Region", None)
    for obj in (asset, region):
        bpy.context.scene.collection.objects.link(obj)
    region.parent = asset
    region[PROP_SPOT_CLASS] = "HorizontalAreaSpace"
    region[PROP_LENGTH_UNIT] = LENGTH_UNIT_PIXEL
    region[PROP_LOCAL_BBOX] = REGION_BOX
    region.matrix_basis = Matrix.Translation((2.0, 3.0, 0.0)) @ Matrix.Scale(
        DRAWING_SCALE, 4
    )
    bpy.context.view_layer.update()

    bbox = exported_bbox_for_helper(region, LENGTH_UNIT_PIXEL)
    assert [round(value, 6) for value in bbox] == [
        -1505.0,
        -1004.0,
        0.0,
        1505.0,
        1004.0,
        0.0,
    ], bbox

    matrix = json.loads(exported_matrix_literal(region, asset, 6, set()))
    assert [matrix[0], matrix[5], matrix[10]] == [0.00638] * 3, matrix
    assert matrix[12:15] == [2.0, 3.0, 0.0], matrix
    print("Pixel helper export test passed")


def test_document_space_axes_declare_the_sheet_frame() -> None:
    """
    The plan geometry a document exports is built with Y growing downward. That
    convention used to live only in the exported numbers, so a consumer reading
    the graph could not tell it apart from an accident. The DocumentSpace now
    states its own frame.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Document")
    bpy.context.scene.collection.children.link(collection)

    helper = bpy.data.objects.new("documentspace-gc-030", None)
    collection.objects.link(helper)
    helper[PROP_HELPER] = True
    helper[PROP_SPOT_CLASS] = "DocumentSpace"
    helper[PROP_LABEL] = "GC_030"

    suffixes = {helper.name: "documentspace-gc-030"}

    space_block = export_space_block(
        helper,
        suffixes,
        {},
        {},
        6,
        include_bbox=False,
        document_backed_helper_names=set(),
    )
    for axis_name in ("X", "Y", "Z"):
        expected = f"spot:has{axis_name}Axis"
        assert expected in space_block, space_block

    axis_blocks = export_space_axis_blocks(helper, suffixes)

    # X to the right across the sheet, Y downward, Z out of the page.
    assert "spot:hasDirection spot:Right" in axis_blocks, axis_blocks
    assert "spot:hasDirection spot:Bottom" in axis_blocks, axis_blocks
    assert "spot:hasDirection spot:Front" in axis_blocks, axis_blocks
    print("Document space axis export test passed")


def test_am_space_axes_carry_no_direction() -> None:
    """
    Only Asset and Document Spaces get a direction definition. An AM-enabled
    space still exports axes, because its boundary points hang off them.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # `spot_am_enabled` is a registered Blender property, not a custom one.
    spatial_semantic_addon.register()
    collection = bpy.data.collections.new("Spaces")
    bpy.context.scene.collection.children.link(collection)

    helper = bpy.data.objects.new("pointspace-damage-1", None)
    collection.objects.link(helper)
    helper[PROP_HELPER] = True
    helper[PROP_SPOT_CLASS] = "PointSpace"
    helper[PROP_LABEL] = "Damage-1"
    setattr(helper, PROP_AM_ENABLED, True)

    axis_blocks = export_space_axis_blocks(helper, {helper.name: "pointspace-damage-1"})

    assert "a spot:Axis" in axis_blocks, axis_blocks
    assert "spot:hasDirection" not in axis_blocks, axis_blocks
    print("AM space axis direction test passed")


def test_damage_represents_no_bot_space() -> None:
    """
    Damage is not building topology. A space standing for damage represents the
    `dot:Damage` alone, never a `bot:Space` beside it — otherwise the damage
    joins `bot:hasSpace` as though it were a room inside a room. Where it sits
    is said by its appearance, not by a BOT referent.
    """
    damage = parse_name_semantics("Damage-1", default_bot_class="Space")
    assert damage.dot_class == "Damage", damage.dot_class
    assert damage.bot_class is None, damage.bot_class

    # An explicit `::BOT=` tag does not resurrect the twin either.
    tagged = parse_name_semantics("Damage-1::BOT=Space")
    assert tagged.bot_class is None, tagged.bot_class

    # A room named like a room still gets its BOT class, unchanged.
    room = parse_name_semantics("1-Kueche", default_bot_class="Space")
    assert room.dot_class is None, room.dot_class
    assert room.bot_class == "Space", room.bot_class
    print("Damage represents no BOT space test passed")


def test_bot_space_is_never_assigned_by_position() -> None:
    """
    A space is a `bot:Space` only when its name says so: an explicit `::BOT=`
    tag or an `IfcSpace/` prefix. Plans and untagged spaces get no BOT class.
    """
    plan = parse_name_semantics("Floorplan_EG")
    assert plan.plan_kind == "floorplan", plan.plan_kind
    assert plan.bot_class is None, plan.bot_class

    room = parse_name_semantics("IfcSpace/Room 1")
    assert room.bot_class == "Space", room.bot_class

    tagged = parse_name_semantics("EntitySpace_Zone::BOT=Space")
    assert tagged.bot_class == "Space", tagged.bot_class
    assert parse_name_semantics("EntitySpace_Zone").bot_class is None
    print("BOT space by name only test passed")


def build_scene_with_plan() -> bpy.types.Collection:
    """The IFC-like scene plus a floor plan beside the storey and a room in it."""
    building_collection = build_ifc_like_scene(asset_root_location=(0.0, 0.0, 0.0))
    storey_collection = building_collection.children[0]
    box_object("IfcSpace/Room 1", storey_collection, (6.0, 0.0, 0.0))
    plan_collection = bpy.data.collections.new("Floorplan_EG")
    building_collection.children.link(plan_collection)
    rectangle_object(
        "Sheet",
        plan_collection,
        [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0)],
    )
    bpy.context.view_layer.update()
    return building_collection


def test_plan_represents_a_linked_storey() -> None:
    """
    A plan carries no BOT class of its own, but can be linked to the storey it
    stands for: it then `spot:represents` the storey's BOT referent, which
    stays owned by the storey. The link survives a helper rebuild and an
    import round trip.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    build_scene_with_plan()
    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]

    (plan_helper,) = [
        obj for obj in helper_collection.objects if obj.get(PROP_PLAN_KIND)
    ]
    assert PROP_BOT_CLASS not in plan_helper, plan_helper[PROP_BOT_CLASS]
    assert "::BOT=" not in plan_helper.name, plan_helper.name
    (room_helper,) = [
        obj for obj in helper_collection.objects if "room-1" in obj.name
    ]
    assert room_helper[PROP_BOT_CLASS] == "Space", room_helper.name
    (storey_helper,) = [
        obj
        for obj in helper_collection.objects
        if obj.get(PROP_BOT_CLASS) == "Storey"
    ]

    # Both are built from collections, so the link is held by the plan
    # collection and names the storey collection, not the helpers.
    bpy.context.view_layer.objects.active = plan_helper
    bpy.context.scene.spot_tools_settings.represents_candidate = storey_helper
    assert bpy.ops.spot.add_represents_link() == {"FINISHED"}
    plan_collection = bpy.data.collections["Floorplan_EG"]
    (link,) = getattr(plan_collection, PROP_REPRESENTS_LINKS)
    assert link.collection == bpy.data.collections[
        "IfcBuildingStorey/Level 1::SPOT=VolumeSpace"
    ], link.collection

    build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]

    with tempfile.TemporaryDirectory(prefix="spot-represents-test-") as directory:
        output_path = Path(directory) / "scene.ttl"
        export_helper_scene(
            helper_collection.name, output_path, local_prefix="ex"
        )
        turtle = output_path.read_text(encoding="utf8")

        blocks = turtle.split("\n\n")
        plan_block = next(
            block for block in blocks if "a spot:HorizontalAreaSpace" in block
        )
        plan_referents = [
            line.split("spot:represents ", 1)[1].rstrip(" ;.")
            for line in plan_block.splitlines()
            if "spot:represents " in line
        ]
        assert len(plan_referents) == 1, plan_block
        assert f"{plan_referents[0]} a bot:Storey" in turtle, plan_referents
        assert turtle.count("a bot:Space") == 1, turtle

        import_scene_graph_turtle(output_path)

    imported = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    (imported_plan,) = [
        obj for obj in imported if obj.get(PROP_SPOT_CLASS) == "HorizontalAreaSpace"
    ]
    (imported_storey,) = [
        obj for obj in imported if obj.get(PROP_BOT_CLASS) == "Storey"
    ]
    assert PROP_BOT_CLASS not in imported_plan, imported_plan[PROP_BOT_CLASS]
    links = [entry.object for entry in getattr(imported_plan, PROP_REPRESENTS_LINKS)]
    assert links == [imported_storey], links
    print("Plan represents a linked storey test passed")


def test_ifc_model_file_is_a_document_space() -> None:
    """
    The model file is a Document Space that owns the distribution, as a plan
    sheet does. The whole-model Volume Space represents no `dcat:Dataset`
    and owns no distribution of its own.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    collection = bpy.data.collections.new("Spaces")
    bpy.context.scene.collection.children.link(collection)

    helper = bpy.data.objects.new("volumespace-domhof-ifc-project", None)
    collection.objects.link(helper)
    helper[PROP_HELPER] = True
    helper[PROP_SPOT_CLASS] = "VolumeSpace"
    helper[PROP_LABEL] = "domhof-ifc-project"
    helper[PROP_SOURCE_IFC_CLASS] = "IfcProject"
    helper[PROP_SOURCE_ASSET_PATH] = "data/ifc/domhof-ifc-project.ifc"
    helper[PROP_LOCAL_BBOX] = [-1.0, -2.0, 0.0, 10.0, 20.0, 3.0]

    suffixes = {helper.name: "volumespace-domhof-ifc-project"}
    block = export_distribution_block(helper, suffixes, "/assets")
    assert (
        f"{LOCAL_TERM_MARKER}documentspace-domhof-ifc-project dcat:distribution "
        f"{LOCAL_TERM_MARKER}distribution_documentspace-domhof-ifc-project ." in block
    ), block
    assert "/assets/ifc/domhof-ifc-project.ifc" in block, block
    assert '"application/x-step"' in block, block
    assert "spot:represents" not in block, block
    assert "dcat:Dataset" not in block, block

    document_block = export_ifc_model_document_space_block(
        helper, suffixes, 6, include_bbox=True, document_backed_helper_names=set()
    )
    assert (
        f"{LOCAL_TERM_MARKER}documentspace-domhof-ifc-project a spot:DocumentSpace ;"
        in document_block
    ), document_block
    assert "qudt:hasUnit unit:M ;" in document_block, document_block
    assert '"[-1,-2,0,10,20,3]"^^spot-sg:boundingBoxArray' in document_block, (
        document_block
    )
    for axis_name in ("X", "Y", "Z"):
        assert (
            f"spot:has{axis_name}Axis {LOCAL_TERM_MARKER}"
            f"documentspace-domhof-ifc-project_{axis_name.lower()}_axis"
        ) in document_block, document_block
    # The image frame of a plan sheet is not the frame of a model file.
    assert "spot:hasDirection" not in document_block, document_block

    appearance_block = export_ifc_model_document_appearance_block(
        helper, suffixes, include_transform=True
    )
    app_id = (
        "appearance_volumespace-domhof-ifc-project_in_"
        "documentspace-domhof-ifc-project"
    )
    assert (
        f"{LOCAL_TERM_MARKER}volumespace-domhof-ifc-project spot:hasAppearance "
        f"{LOCAL_TERM_MARKER}{app_id} ." in appearance_block
    ), appearance_block
    assert "a spot:DocumentAppearance ;" in appearance_block, appearance_block
    assert (
        '"[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"^^gom:columnMajorArray'
        in appearance_block
    ), appearance_block
    print("IFC model file document space test passed")


def test_ifc_model_export_links_the_space_to_its_file() -> None:
    """
    In a full export the whole-model Volume Space keeps its Asset Appearance
    and gains one Document Appearance in the model file, which owns the
    distribution.
    """
    building_collection, _ifc_collection = build_asset_space_with_disconnected_ifc()
    bpy.ops.spot.create_ifc_bounding_box()
    build_helper_scene(visible_only=False)

    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "model.ttl"
        export_helper_scene(output_path=output_path, local_prefix="ex")
        turtle = output_path.read_text(encoding="utf8")

    assert "dcat:Dataset" not in turtle, turtle
    assert "model_" not in turtle, turtle
    assert (
        "ex:documentspace-my-project dcat:distribution "
        "ex:distribution_documentspace-my-project ." in turtle
    ), turtle
    assert "ex:volumespace-my-project dcat:distribution" not in turtle, turtle
    appearances = [
        line
        for line in turtle.splitlines()
        if line.startswith("ex:volumespace-my-project spot:hasAppearance ")
    ]
    assert len(appearances) == 2, appearances
    assert (
        "ex:volumespace-my-project spot:hasAppearance "
        "ex:appearance_volumespace-my-project_in_documentspace-my-project ."
        in appearances
    ), appearances
    print("IFC model export document appearance test passed")


def test_ifc_model_asset_path_is_inferred_for_the_project_box_only() -> None:
    """
    Only a box tagged `::IFC=IfcProject` names a model file. Every other
    IFC-backed helper is one element inside such a model and shares the file
    rather than owning a distribution of its own.
    """
    project = parse_name_semantics("VolumeSpace_domhof-ifc-project::IFC=IfcProject")
    assert project.asset_path == "data/ifc/domhof-ifc-project.ifc", project.asset_path

    element = parse_name_semantics("IfcWall/Wall 1")
    assert element.asset_path is None, element.asset_path
    print("IFC model asset path inference test passed")


def build_asset_space_with_disconnected_ifc() -> tuple[
    bpy.types.Collection, bpy.types.Collection
]:
    """
    An AssetSpace scene beside a disconnected Bonsai-style IFC import whose
    root sits off-site, as an IFC exported in survey coordinates does.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()

    building_collection = bpy.data.collections.new("AssetSpace_Building")
    bpy.context.scene.collection.children.link(building_collection)
    storey_collection = bpy.data.collections.new(
        "IfcBuildingStorey/Level 1::SPOT=VolumeSpace"
    )
    building_collection.children.link(storey_collection)
    box_object("VolumeSpace_Room::BOT=Space", storey_collection, (0.0, 0.0, 0.0))

    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)
    ifc_root = bpy.data.objects.new("IfcProject/My Project", None)
    ifc_collection.objects.link(ifc_root)
    ifc_root.location = (500.0, 500.0, 0.0)
    box_object("IfcWall/Wall 1", ifc_collection, (501.0, 500.0, 0.0))
    box_object("IfcWall/Wall 2", ifc_collection, (503.0, 500.0, 0.0))

    bpy.context.view_layer.update()
    return building_collection, ifc_collection


def test_create_ifc_bounding_box_sorts_into_the_asset_space() -> None:
    """
    The operator links the box straight into the AssetSpace collection, so no
    manual drag is needed, and selects it so it can be moved into place. A
    second run finds that box again instead of adding a duplicate that would
    export the model a second time at its original position.
    """
    building_collection, ifc_collection = build_asset_space_with_disconnected_ifc()

    result = bpy.ops.spot.create_ifc_bounding_box()
    assert result == {"FINISHED"}, result

    (bbox_object,) = [
        obj for obj in building_collection.objects if "my-project" in obj.name
    ]
    assert bbox_object.name not in ifc_collection.all_objects
    assert "IFC Bounding Boxes" not in bpy.data.collections
    assert bpy.context.view_layer.objects.active == bbox_object
    assert bbox_object.select_get()

    bbox_object.location = (10.0, 0.0, 0.0)
    result = bpy.ops.spot.create_ifc_bounding_box()
    assert result == {"FINISHED"}, result
    boxes = [obj for obj in bpy.data.objects if "my-project" in obj.name]
    assert boxes == [bbox_object], boxes
    assert tuple(bbox_object.location) == (10.0, 0.0, 0.0), bbox_object.location
    print("IFC bounding box sorts into asset space test passed")


def test_create_ifc_bounding_box_falls_back_without_an_asset_space() -> None:
    """Without an AssetSpace collection the box goes to the fallback collection."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    ifc_collection = bpy.data.collections.new("IfcProject/My Project")
    bpy.context.scene.collection.children.link(ifc_collection)
    box_object("IfcWall/Wall 1", ifc_collection, (0.0, 0.0, 0.0))
    bpy.context.view_layer.update()

    result = bpy.ops.spot.create_ifc_bounding_box()
    assert result == {"FINISHED"}, result
    fallback = bpy.data.collections["IFC Bounding Boxes"]
    assert [obj.name for obj in fallback.objects] == [
        "VolumeSpace_my-project::IFC=IfcProject"
    ], [obj.name for obj in fallback.objects]
    print("IFC bounding box fallback collection test passed")


def test_moved_ifc_bounding_box_exports_its_translation() -> None:
    """
    Moving the box by hand places the whole IFC model in the asset space. The
    build keeps the box origin on the moved IFC root, and the export states
    that placement relative to the asset space as the model's transformation
    matrix.
    """
    building_collection, _ifc_collection = build_asset_space_with_disconnected_ifc()
    bpy.ops.spot.create_ifc_bounding_box()
    (bbox_object,) = [
        obj for obj in building_collection.objects if "my-project" in obj.name
    ]
    local_bbox_before = [round(value, 6) for value in bbox_object.bound_box[0]]

    # The manual move: from the survey position next to the room.
    bbox_object.location = (3.0, 4.0, 0.0)
    bpy.context.view_layer.update()

    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    (box_helper,) = [
        obj for obj in helper_collection.objects if "my-project" in obj.name
    ]
    assert box_helper[PROP_SPOT_CLASS] == "VolumeSpace", box_helper[PROP_SPOT_CLASS]
    assert box_helper[PROP_SOURCE_IFC_CLASS] == "IfcProject"

    # The helper origin is the moved IFC root, and the box keeps its extent.
    assert tuple(round(value, 6) for value in box_helper.matrix_world.translation) == (
        3.0,
        4.0,
        0.0,
    ), box_helper.matrix_world.translation
    assert [round(value, 6) for value in box_helper[PROP_LOCAL_BBOX][:3]] == (
        local_bbox_before
    ), (list(box_helper[PROP_LOCAL_BBOX]), local_bbox_before)

    # The exported matrix is that placement relative to the asset space.
    asset_helper = box_helper.parent
    assert asset_helper[PROP_SPOT_CLASS] == "AssetSpace", asset_helper[PROP_SPOT_CLASS]
    expected = asset_helper.matrix_world.inverted() @ Matrix.Translation((3.0, 4.0, 0.0))
    literal = exported_matrix_literal(box_helper, asset_helper, 6, set())
    assert literal == matrix_to_column_major_literal(expected, 6), literal
    print("Moved IFC bounding box translation export test passed")


def test_ifc_file_name_drives_the_exported_distribution() -> None:
    """
    The `IfcProject` name is often an export default such as `Projekt-001`.
    The real file name stored on the box wins over the path guessed from that
    label, and a space in it is percent-encoded so the IRI stays valid.
    """
    building_collection, ifc_collection = build_asset_space_with_disconnected_ifc()
    bbox_object = build_ifc_bounding_box(
        ifc_bounding_box_source_objects([ifc_collection]),
        root_object=collection_object_named_like_collection(ifc_collection),
        label="Projekt-001",
        collection=building_collection,
        ifc_file="/somewhere/Domhof 6 Export.ifc",
    )
    assert bbox_object[PROP_IFC_FILE] == "Domhof 6 Export.ifc", bbox_object[PROP_IFC_FILE]

    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    (box_helper,) = [
        obj for obj in helper_collection.objects if "projekt-001" in obj.name
    ]
    assert box_helper[PROP_SOURCE_ASSET_PATH] == "data/ifc/Domhof 6 Export.ifc", (
        box_helper[PROP_SOURCE_ASSET_PATH]
    )
    block = export_distribution_block(box_helper, {box_helper.name: "model"}, "/assets")
    assert "</assets/ifc/Domhof%206%20Export.ifc>" in block, block
    print("IFC file name distribution test passed")


def test_set_ifc_file_updates_the_box_and_its_helper() -> None:
    """
    Choosing the file later updates the box, and a helper that was built
    already, so an export without a rebuild links the new file too.
    """
    building_collection, _ifc_collection = build_asset_space_with_disconnected_ifc()
    bpy.ops.spot.create_ifc_bounding_box()
    (bbox_object,) = [
        obj for obj in building_collection.objects if "my-project" in obj.name
    ]
    result = build_helper_scene(visible_only=False)
    helper_collection = bpy.data.collections[result["helper_collection_name"]]
    (box_helper,) = [
        obj for obj in helper_collection.objects if "my-project" in obj.name
    ]
    assert box_helper[PROP_SOURCE_ASSET_PATH] == "data/ifc/my-project.ifc"

    bpy.context.view_layer.objects.active = bbox_object
    result = bpy.ops.spot.set_ifc_file(filepath="/datasets/assets/ifc/Domhof 6.ifc")
    assert result == {"FINISHED"}, result
    assert bbox_object[PROP_IFC_FILE] == "Domhof 6.ifc", bbox_object[PROP_IFC_FILE]
    assert box_helper[PROP_SOURCE_ASSET_PATH] == "data/ifc/Domhof 6.ifc", (
        box_helper[PROP_SOURCE_ASSET_PATH]
    )

    # The helper is a valid selection too: it stands for the same box.
    bpy.context.view_layer.objects.active = box_helper
    result = bpy.ops.spot.set_ifc_file(filepath="/datasets/assets/ifc/Other.ifc")
    assert result == {"FINISHED"}, result
    assert bbox_object[PROP_IFC_FILE] == "Other.ifc", bbox_object[PROP_IFC_FILE]
    print("Set IFC file operator test passed")


def test_loaded_ifc_file_name_reads_bonsai() -> None:
    """The box takes its file name from the IFC that Bonsai loaded."""
    from spatial_semantic_addon import loaded_ifc_file_name

    bonsai_context = SimpleNamespace(
        scene=SimpleNamespace(
            BIMProperties=SimpleNamespace(ifc_file="/data/Domhof 6 Export.ifc")
        )
    )
    assert loaded_ifc_file_name(bonsai_context) == "Domhof 6 Export.ifc"
    assert loaded_ifc_file_name(SimpleNamespace(scene=SimpleNamespace())) is None
    print("Loaded IFC file name test passed")


def test_asset_space_without_storeys_builds() -> None:
    """An AssetSpace that holds only its root object and one element.

    A bridge has no storeys. The AssetSpace helper takes its extent from the
    root object, a scaled cube, and the element hangs directly off it.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    asset = bpy.data.collections.new("AssetSpace")
    bpy.context.scene.collection.children.link(asset)
    # A default cube stretched to the asset's size, as one is drawn by hand.
    root = box_object("AssetSpace_NBW", asset, (5.0, 2.0, 1.5), size=2.0)
    root.scale = (5.0, 2.0, 1.5)
    cube = box_object("Cube", asset, (1.5, 1.5, 1.5))
    bpy.context.view_layer.update()

    result = build_helper_scene(visible_only=False)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    asset_helper = next(
        helper for helper in helpers if helper[PROP_SPOT_CLASS] == "AssetSpace"
    )
    cube_helper = next(helper for helper in helpers if helper is not asset_helper)
    assert result["resolved_storey_count"] == 0
    assert len(helpers) == 2
    assert cube_helper.parent == asset_helper
    assert world_bbox_of_object(asset_helper) == ([0.0, 0.0, 0.0], [10.0, 4.0, 3.0])
    # The scale lands in the box, not the frame, so the box is in metres.
    assert tuple(round(v, 6) for v in asset_helper.matrix_world.to_scale()) == (
        1.0,
        1.0,
        1.0,
    )
    assert [round(v, 6) for v in asset_helper[PROP_LOCAL_BBOX]] == [
        -5.0,
        -2.0,
        -1.5,
        5.0,
        2.0,
        1.5,
    ]

    # A helper scaled by hand to fit is exported in metres: the scale goes
    # into its box, and its matrix stays a translation.
    cube_helper.scale = (4.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    with tempfile.TemporaryDirectory(prefix="spot-scaled-export-") as directory:
        output = Path(directory) / "scene.ttl"
        export_helper_scene(output_path=output, precision=3)
        text = output.read_text(encoding="utf8")
    assert '"[-5,-2,-1.5,5,2,1.5]"^^spot-sg:boundingBoxArray' in text, text
    assert '"[-2,-0.5,-0.5,2,0.5,0.5]"^^spot-sg:boundingBoxArray' in text, text
    assert '"[1,0,0,0,0,1,0,0,0,0,1,0,-3.5,-0.5,0,1]"^^gom:columnMajorArray' in text, text
    print("AssetSpace without storeys test passed")


if __name__ == "__main__":
    test_physical_plan_helper_keeps_the_drawing_scale()
    test_pixel_helper_exports_its_box_in_sheet_pixels()
    test_document_proxy_uses_the_same_local_frame_as_other_helpers()
    test_document_proxy_origin_already_centred_is_kept()
    test_document_space_axes_declare_the_sheet_frame()
    test_am_space_axes_carry_no_direction()
    test_build_mirrors_the_asset_space_structure()
    test_find_ifc_project_collections_is_name_only()
    test_ifc_bounding_box_naming_follows_the_scene_convention()
    test_ifc_tag_overrides_the_prefix_inference()
    test_ifc_bounding_box_spans_only_the_geometry()
    test_dragging_the_bounding_box_into_asset_space_converts_it_verbatim()
    test_ifc_model_space_keeps_an_asymmetric_box_around_the_ifc_origin()
    test_ifc_bounding_box_keeps_a_rotated_root_frame()
    test_damage_represents_no_bot_space()
    test_bot_space_is_never_assigned_by_position()
    test_plan_represents_a_linked_storey()
    test_ifc_model_file_is_a_document_space()
    test_ifc_model_export_links_the_space_to_its_file()
    test_ifc_model_asset_path_is_inferred_for_the_project_box_only()
    test_create_ifc_bounding_box_sorts_into_the_asset_space()
    test_create_ifc_bounding_box_falls_back_without_an_asset_space()
    test_moved_ifc_bounding_box_exports_its_translation()
    test_ifc_file_name_drives_the_exported_distribution()
    test_set_ifc_file_updates_the_box_and_its_helper()
    test_loaded_ifc_file_name_reads_bonsai()
    test_asset_space_without_storeys_builds()
