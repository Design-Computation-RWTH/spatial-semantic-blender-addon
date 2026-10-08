from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

import spatial_semantic_addon
from spatial_semantic_addon.import_spot_ttl import import_scene_graph_turtle
from spatial_semantic_addon.spot_blender_common import (
    PROP_HELPER,
    PROP_LABEL,
    PROP_LOCAL_BBOX,
    PROP_SPOT_CLASS,
)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    turtle = """\
@prefix ex: <https://example.org/scene/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix gom: <https://w3id.org/gom#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix sosa: <http://www.w3.org/ns/sosa/> .
@prefix prov: <http://www.w3.org/ns/prov#> .

ex:unlabelled-root a spot:AssetSpace ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,0,1,1,1]"^^spot-sg:boundingBoxArray .

ex:multi-valued-child qudt:hasUnit unit:M ;
    a spot:EntitySpace, spot:VolumeSpace ;
    rdfs:label "Primary label", "Secondary label" ;
    spot:hasAppearance ex:child-in-root ;
    spot-sg:hasBoundingBox "[0,0,0,1,1,1]"^^spot-sg:boundingBoxArray,
        "[-2,-3,-4,0.5,2,3]"^^spot-sg:boundingBoxArray .

ex:child-in-root a spot:EntityAppearance ;
    gom:fromCoordinateSystem ex:multi-valued-child ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"^^gom:columnMajorArray ;
    spot:appearsIn ex:unlabelled-root .

ex:unlabelled-root dcat:distribution ex:distribution_root .

ex:distribution_root a dcat:Distribution ;
    dcat:mediaType "image/png" .

ex:capture_root a sosa:Observation, prov:Activity ;
    sosa:hasResult ex:snapshot_root .
"""

    with tempfile.TemporaryDirectory(prefix="spot-import-test-") as directory:
        input_path = Path(directory) / "unlabelled-scene.ttl"
        input_path.write_text(turtle, encoding="utf8")
        result = import_scene_graph_turtle(input_path)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    assert result["helper_count"] == 2
    assert result["root_label"] == "unlabelled-root"
    assert result["warning_count"] == 3
    assert len(helpers) == 2
    root = next(helper for helper in helpers if helper[PROP_LABEL] == "unlabelled-root")
    child = next(helper for helper in helpers if helper[PROP_LABEL] == "Primary label")
    assert child.parent == root
    assert child[PROP_SPOT_CLASS] == "VolumeSpace"
    assert list(child[PROP_LOCAL_BBOX]) == [-2.0, -3.0, -4.0, 1.0, 2.0, 3.0]
    assert any("2 rdfs:label values" in warning for warning in result["warnings"])
    assert any("2 bounding boxes" in warning for warning in result["warnings"])
    assert any(
        "non-scene-graph statements" in warning for warning in result["warnings"]
    )
    print("Tolerant SPOT Turtle import test passed")


def test_document_appearances_do_not_win() -> None:
    """A space placed both in an asset space and on a drawing sheet.

    The document appearance is stated last on purpose: the asset appearance
    must still win, and the document space must not survive as a second root.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    turtle = """\
@prefix ex: <https://example.org/scene/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix gom: <https://w3id.org/gom#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:asset a spot:AssetSpace ;
    rdfs:label "Asset" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,0,10,10,10]"^^spot-sg:boundingBoxArray .

ex:document a spot:DocumentSpace ;
    rdfs:label "Sheet" ;
    qudt:hasUnit unit:PIXEL ;
    spot-sg:hasBoundingBox "[0,-6380,0,14382,0,0]"^^spot-sg:boundingBoxArray .

ex:area a spot:HorizontalAreaSpace ;
    rdfs:label "Storey" ;
    qudt:hasUnit unit:PIXEL ;
    spot-sg:hasBoundingBox "[-1500,-1000,0,1500,1000,0]"^^spot-sg:boundingBoxArray .

ex:point a spot:PointSpace ;
    rdfs:label "Damage" ;
    qudt:hasUnit unit:PIXEL .

ex:area spot:hasAppearance ex:area-in-asset .

ex:area-in-asset a spot:AssetAppearance ;
    spot:appearsIn ex:asset ;
    gom:hasTransformationMatrix "[0.006,0,0,0,0,0.006,0,0,0,0,0.006,0,8,2,7,1]"^^gom:columnMajorArray .

ex:point spot:hasAppearance ex:point-in-area .

ex:point-in-area a spot:EntityAppearance ;
    spot:appearsIn ex:area ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,10,20,0,1]"^^gom:columnMajorArray .

ex:area spot:hasAppearance ex:area-in-document .

ex:area-in-document a spot:DocumentAppearance ;
    spot:appearsIn ex:document ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,10471,-977,0,1]"^^gom:columnMajorArray .
"""

    with tempfile.TemporaryDirectory(prefix="spot-doc-test-") as directory:
        input_path = Path(directory) / "document-scene.ttl"
        input_path.write_text(turtle, encoding="utf8")
        result = import_scene_graph_turtle(input_path)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    labels = {str(helper[PROP_LABEL]) for helper in helpers}

    # The document space is dropped; asset, area and point survive.
    assert labels == {"Asset", "Storey", "Damage"}, labels
    assert result["root_label"] == "Asset"
    assert len(result["root_helper_names"]) == 1

    area = next(helper for helper in helpers if helper[PROP_LABEL] == "Storey")
    point = next(helper for helper in helpers if helper[PROP_LABEL] == "Damage")
    asset = next(helper for helper in helpers if helper[PROP_LABEL] == "Asset")

    # The asset appearance wins even though the document one was stated last.
    assert area.parent == asset
    assert tuple(round(value, 3) for value in area.location) == (8.0, 2.0, 7.0)

    # The point space rides along with its parent area rather than orphaning.
    assert point.parent == area
    print("Document appearance precedence test passed")


def test_spot_am_fuzzy_placement() -> None:
    """A damage located only by SPOT-AM boundary relations.

    The room is 6 x 6 x 4 and inherits its axis directions from the asset
    space (X=Right, Y=Rear). "containedInLeft" is the opposite of Right, so it
    picks the lower third of X; "containedInRear" matches Y, so it picks the
    upper third. Z has no relation and keeps the room's full height.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    turtle = """\
@prefix ex: <https://example.org/scene/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix spot-am: <https://w3id.org/spot/am#> .
@prefix reloc: <https://w3id.org/reloc#> .
@prefix gom: <https://w3id.org/gom#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:asset a spot:AssetSpace ;
    rdfs:label "Asset" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[-5,-5,-5,5,5,5]"^^spot-sg:boundingBoxArray ;
    spot:hasXAxis ex:asset_x ;
    spot:hasYAxis ex:asset_y ;
    spot:hasZAxis ex:asset_z .

ex:asset_x a spot:Axis ; spot:hasDirection spot:Right .
ex:asset_y a spot:Axis ; spot:hasDirection spot:Rear .
ex:asset_z a spot:Axis ; spot:hasDirection spot:Top .

ex:room a spot:VolumeSpace ;
    rdfs:label "Room" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[-3,-3,-2,3,3,2]"^^spot-sg:boundingBoxArray .

ex:damage a spot:PointSpace ;
    rdfs:label "Damage" ;
    qudt:hasUnit unit:M .

ex:damage_x a spot:Axis ; rdfs:label "X axis of Damage" .
ex:damage_y a spot:Axis ; rdfs:label "Y axis of Damage" .

ex:damage_x spot-am:hasBoundaryPoint ex:damage_x_min .
ex:damage_x_min a spot-am:MinBoundaryPoint ; rdfs:label "X min" .
ex:damage_y spot-am:hasBoundaryPoint ex:damage_y_min .
ex:damage_y_min a spot-am:MinBoundaryPoint ; rdfs:label "Y min" .

ex:room spot:hasAppearance ex:room-in-asset .
ex:room-in-asset a spot:EntityAppearance ;
    spot:appearsIn ex:asset ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"^^gom:columnMajorArray .

ex:damage spot:hasAppearance ex:damage-in-room .
ex:damage-in-room a spot:EntityAppearance ;
    spot:appearsIn ex:room ;
    spot-am:hasBoundaryPointMapping ex:bpm_x, ex:bpm_y .

ex:bpm_x a spot-am:BoundaryPointMapping ;
    reloc:containedInLeft ex:room ;
    spot-am:hasAccuracy spot-am:Approximate ;
    spot-am:hasSourcePoint ex:damage_x_min .

ex:bpm_y a spot-am:BoundaryPointMapping ;
    reloc:containedInRear ex:room ;
    spot-am:hasAccuracy spot-am:Approximate ;
    spot-am:hasSourcePoint ex:damage_y_min .
"""

    with tempfile.TemporaryDirectory(prefix="spot-am-test-") as directory:
        input_path = Path(directory) / "am-scene.ttl"
        input_path.write_text(turtle, encoding="utf8")
        result = import_scene_graph_turtle(input_path)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    damage = next(helper for helper in helpers if helper[PROP_LABEL] == "Damage")

    # X spans [-3,-1] and Y spans [1,3]; Z keeps the room's full [-2,2].
    assert tuple(round(value, 3) for value in damage.location) == (-2.0, 2.0, 0.0)
    assert tuple(round(value, 3) for value in damage.dimensions) == (2.0, 2.0, 4.0)
    assert any("SPOT-AM" in warning for warning in result["warnings"])
    print("SPOT-AM fuzzy placement test passed")


def test_spot_am_relation_to_other_space() -> None:
    """A relation that targets a space other than the damage's parent.

    The damage sits in room-a, but its relation names room-b, which is 10 m
    away on X. That extent has to be carried into room-a's frame before the
    thirds are taken, otherwise the damage lands in the wrong place entirely.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    turtle = """\
@prefix ex: <https://example.org/scene/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix spot-am: <https://w3id.org/spot/am#> .
@prefix reloc: <https://w3id.org/reloc#> .
@prefix gom: <https://w3id.org/gom#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:asset a spot:AssetSpace ;
    rdfs:label "Asset" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[-20,-20,-20,20,20,20]"^^spot-sg:boundingBoxArray ;
    spot:hasXAxis ex:asset_x ;
    spot:hasYAxis ex:asset_y ;
    spot:hasZAxis ex:asset_z .

ex:asset_x a spot:Axis ; spot:hasDirection spot:Right .
ex:asset_y a spot:Axis ; spot:hasDirection spot:Rear .
ex:asset_z a spot:Axis ; spot:hasDirection spot:Top .

ex:room-a a spot:VolumeSpace ;
    rdfs:label "RoomA" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[-3,-3,-2,3,3,2]"^^spot-sg:boundingBoxArray .

ex:room-b a spot:VolumeSpace ;
    rdfs:label "RoomB" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[-2,-2,-2,2,2,2]"^^spot-sg:boundingBoxArray .

ex:damage a spot:PointSpace ;
    rdfs:label "Damage" ;
    qudt:hasUnit unit:M .

ex:damage_x a spot:Axis ; rdfs:label "X axis of Damage" .
ex:damage_x spot-am:hasBoundaryPoint ex:damage_x_min .
ex:damage_x_min a spot-am:MinBoundaryPoint ; rdfs:label "X min" .

ex:room-a spot:hasAppearance ex:room-a-in-asset .
ex:room-a-in-asset a spot:EntityAppearance ;
    spot:appearsIn ex:asset ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"^^gom:columnMajorArray .

ex:room-b spot:hasAppearance ex:room-b-in-asset .
ex:room-b-in-asset a spot:EntityAppearance ;
    spot:appearsIn ex:asset ;
    gom:hasTransformationMatrix "[1,0,0,0,0,1,0,0,0,0,1,0,10,0,0,1]"^^gom:columnMajorArray .

ex:damage spot:hasAppearance ex:damage-in-room-a .
ex:damage-in-room-a a spot:EntityAppearance ;
    spot:appearsIn ex:room-a ;
    spot-am:hasBoundaryPointMapping ex:bpm_x .

ex:bpm_x a spot-am:BoundaryPointMapping ;
    reloc:containedInLeft ex:room-b ;
    spot-am:hasAccuracy spot-am:Approximate ;
    spot-am:hasSourcePoint ex:damage_x_min .
"""

    with tempfile.TemporaryDirectory(prefix="spot-am-cross-") as directory:
        input_path = Path(directory) / "cross-frame.ttl"
        input_path.write_text(turtle, encoding="utf8")
        import_scene_graph_turtle(input_path)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    damage = next(helper for helper in helpers if helper[PROP_LABEL] == "Damage")

    # Room B spans X 8..12 in room A's frame; its left third is 8..9.333.
    # Y and Z carry room A's own extent, so the centre stays at 0.
    assert round(damage.location.x, 3) == 8.667, damage.location.x
    assert round(damage.dimensions.x, 3) == 1.333, damage.dimensions.x
    print("SPOT-AM cross-frame relation test passed")


def test_dataset_folder_imports_live_spatial_mutations() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = """\
@prefix ex: <http://localhost:4000/scene/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:asset a spot:AssetSpace ;
    rdfs:label "Retracted asset label" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,0,10,10,10]"^^spot-sg:boundingBoxArray .
"""
    mutations = """\
#!lbcf-entry delta id=live-pose
<http://localhost:4000/id/pose-live> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <https://w3id.org/spot#PointSpace> .
<http://localhost:4000/id/pose-live> <https://w3id.org/spot#hasAppearance> <http://localhost:4000/id/appearance-live> .
<http://localhost:4000/id/appearance-live> <https://w3id.org/spot#appearsIn> <http://localhost:4000/scene/asset> .
<http://localhost:4000/id/appearance-live> <https://w3id.org/gom#hasTransformationMatrix> "[1,0,0,0,0,1,0,0,0,0,1,0,4,5,6,1]"^^<https://w3id.org/gom#columnMajorArray> .

#!lbcf-entry retraction id=remove-old-label
<http://localhost:4000/scene/asset> <http://www.w3.org/2000/01/rdf-schema#label> "Retracted asset label" .
"""

    with tempfile.TemporaryDirectory(prefix="spot-dataset-test-") as directory:
        dataset_dir = Path(directory)
        rdf_dir = dataset_dir / "rdf"
        rdf_dir.mkdir()
        (rdf_dir / "scene.ttl").write_text(scene, encoding="utf8")
        (rdf_dir / "mutations.trig").write_text(mutations, encoding="utf8")
        result = import_scene_graph_turtle(dataset_dir)

    helpers = [obj for obj in bpy.data.objects if obj.get(PROP_HELPER)]
    root = next(helper for helper in helpers if helper.parent is None)
    live_pose = next(helper for helper in helpers if helper[PROP_LABEL] == "pose-live")

    assert result["helper_count"] == 2
    assert result["input_files"] == [
        "rdf/mutations.trig",
        "rdf/scene.ttl",
    ]
    assert root[PROP_LABEL] == "asset"
    assert live_pose.parent == root
    assert tuple(round(value, 3) for value in live_pose.location) == (
        4.0,
        5.0,
        6.0,
    )
    print("Dataset folder live spatial mutation import test passed")


if __name__ == "__main__":
    main()
    test_document_appearances_do_not_win()
    test_spot_am_fuzzy_placement()
    test_spot_am_relation_to_other_space()
    test_dataset_folder_imports_live_spatial_mutations()
