from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import bpy


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

import spatial_semantic_addon
from spatial_semantic_addon.export_spot_ttl import (
    EXPORT_PROFILE_SCENE_GRAPH,
    export_helper_scene,
)
from spatial_semantic_addon.import_spot_ttl import import_scene_graph_turtle
from spatial_semantic_addon.spot_blender_common import (
    PROP_AM_REFERENCE_HELPER,
    PROP_BOT_CLASS,
    PROP_BROT_CLASS,
    PROP_HELPER,
    PROP_LABEL,
    PROP_SOURCE_NAME,
    parse_name_semantics,
)
from spatial_semantic_addon.topology_setup import (
    retag_name,
    set_helper_topology_class,
)


IDENTITY = '"[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]"^^gom:columnMajorArray'

BRIDGE_TURTLE = f"""\
@prefix ex: <https://example.org/bridge/> .
@prefix spot: <https://w3id.org/spot#> .
@prefix spot-sg: <https://w3id.org/spot/sg#> .
@prefix gom: <https://w3id.org/gom#> .
@prefix qudt: <http://qudt.org/schema/qudt/> .
@prefix unit: <http://qudt.org/vocab/unit/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:asset a spot:AssetSpace ;
    rdfs:label "Bridge Asset" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,0,40,10,10]"^^spot-sg:boundingBoxArray .

ex:bridge a spot:EntitySpace, spot:VolumeSpace ;
    rdfs:label "Bridge" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,0,40,10,10]"^^spot-sg:boundingBoxArray ;
    spot:hasAppearance ex:bridge_in_asset .

ex:bridge_in_asset a spot:EntityAppearance ;
    gom:fromCoordinateSystem ex:bridge ;
    gom:hasTransformationMatrix {IDENTITY} ;
    spot:appearsIn ex:asset .

ex:superstructure a spot:EntitySpace, spot:VolumeSpace ;
    rdfs:label "Superstructure" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,6,40,10,10]"^^spot-sg:boundingBoxArray ;
    spot:hasAppearance ex:superstructure_in_bridge .

ex:superstructure_in_bridge a spot:EntityAppearance ;
    gom:fromCoordinateSystem ex:superstructure ;
    gom:hasTransformationMatrix {IDENTITY} ;
    spot:appearsIn ex:bridge .

ex:deck a spot:EntitySpace, spot:VolumeSpace ;
    rdfs:label "Deck" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,8,40,10,10]"^^spot-sg:boundingBoxArray ;
    spot:hasAppearance ex:deck_in_superstructure .

ex:deck_in_superstructure a spot:EntityAppearance ;
    gom:fromCoordinateSystem ex:deck ;
    gom:hasTransformationMatrix {IDENTITY} ;
    spot:appearsIn ex:superstructure .

ex:joint a spot:EntitySpace, spot:VolumeSpace ;
    rdfs:label "Joint" ;
    qudt:hasUnit unit:M ;
    spot-sg:hasBoundingBox "[0,0,8,1,10,10]"^^spot-sg:boundingBoxArray ;
    spot:hasAppearance ex:joint_in_deck .

ex:joint_in_deck a spot:EntityAppearance ;
    gom:fromCoordinateSystem ex:joint ;
    gom:hasTransformationMatrix {IDENTITY} ;
    spot:appearsIn ex:deck .
"""


def helpers_by_label() -> dict[str, bpy.types.Object]:
    return {
        str(obj[PROP_LABEL]): obj
        for obj in bpy.data.objects
        if obj.get(PROP_HELPER)
    }


def test_untyped_import_sets_no_class() -> None:
    helpers = helpers_by_label()
    for label in ("Bridge", "Superstructure", "Deck", "Joint"):
        assert PROP_BOT_CLASS not in helpers[label], (
            f"'{label}' got a BOT class the graph does not state"
        )
    print("Untyped import sets no BOT class test passed")


def test_set_classes_and_export(directory: Path) -> Path:
    helpers = helpers_by_label()
    set_helper_topology_class(helpers["Bridge"], "BROT", "Bridge")
    set_helper_topology_class(helpers["Superstructure"], "BROT", "SuperStructure")
    set_helper_topology_class(helpers["Joint"], "BROT", "Joint")

    # The panel's operator works on the selection.
    bpy.ops.object.select_all(action="DESELECT")
    deck = helpers["Deck"]
    deck.select_set(True)
    bpy.context.view_layer.objects.active = deck
    assert bpy.ops.spot.set_topology_class(
        vocabulary="BROT", class_name="Component"
    ) == {"FINISHED"}
    assert deck[PROP_BROT_CLASS] == "Component"
    assert PROP_BOT_CLASS not in deck

    output_path = directory / "bridge.ttl"
    export_helper_scene(
        output_path=output_path,
        export_profile=EXPORT_PROFILE_SCENE_GRAPH,
        asset_base_url="/assets",
    )
    text = output_path.read_text(encoding="utf8")
    assert "@prefix brot: <https://w3id.org/brot#> ." in text
    assert "a brot:Bridge" in text and "a brot:SuperStructure" in text
    assert "a brot:Component" in text and "a brot:Joint" in text
    assert " brot:containsZone " in text
    assert " brot:containsComponent " in text
    assert " brot:hasJoint " in text
    # BROT implies nothing in BOT: without a BOT class there is no BOT.
    assert "bot:" not in text
    print("BROT class export test passed")
    return output_path


def test_reimport_keeps_brot(output_path: Path) -> None:
    import_scene_graph_turtle(output_path)
    helpers = helpers_by_label()
    assert helpers["Bridge"][PROP_BROT_CLASS] == "Bridge"
    assert helpers["Deck"][PROP_BROT_CLASS] == "Component"
    assert helpers["Joint"][PROP_BROT_CLASS] == "Joint"
    assert not any(PROP_BOT_CLASS in helper for helper in helpers.values())
    print("BROT round trip test passed")


def test_bot_relations_need_bot_on_both_ends(directory: Path) -> None:
    helpers = helpers_by_label()
    # Deck and Joint: BOT elements inside a parent without a BOT class.
    set_helper_topology_class(helpers["Deck"], "BOT", "Element")
    set_helper_topology_class(helpers["Joint"], "BOT", "Element")
    assert PROP_BROT_CLASS not in helpers["Deck"], "a BOT class replaces BROT"

    output_path = directory / "bridge-bot.ttl"
    export_helper_scene(
        output_path=output_path,
        export_profile=EXPORT_PROFILE_SCENE_GRAPH,
        asset_base_url="/assets",
    )
    text = output_path.read_text(encoding="utf8")
    assert "@prefix bot: <https://w3id.org/bot#> ." in text
    assert "ex:ref_deck bot:hasSubElement ex:ref_joint ." in text
    assert "bot:hasElement" not in text
    assert "a bot:Element" in text and "a bot:Zone" not in text
    print("BOT relations need BOT on both ends test passed")


def test_clear_class() -> None:
    helper = helpers_by_label()["Deck"]
    set_helper_topology_class(helper, None, None)
    assert PROP_BOT_CLASS not in helper and PROP_BROT_CLASS not in helper
    assert "::BOT=" not in helper.name and "::BROT=" not in helper.name
    print("Clear class test passed")


def test_set_as_renames_helper_and_source() -> None:
    settings = bpy.context.scene.spot_tools_settings
    collection = bpy.data.collections.new("Rename Test")
    bpy.context.scene.collection.children.link(collection)
    source = bpy.data.objects.new("SubStructureZone::BOT=Element::SPOT=VolumeSpace", None)
    am_user = bpy.data.objects.new("AM user", None)
    helper = bpy.data.objects.new(
        "SPOT::Imported::Element::SubStructureZone::BOT=Element::SPOT=VolumeSpace", None
    )
    for obj in (source, am_user, helper):
        collection.objects.link(obj)
    helper[PROP_HELPER] = True
    helper[PROP_BOT_CLASS] = "Element"
    helper[PROP_SOURCE_NAME] = source.name
    am_user[PROP_AM_REFERENCE_HELPER] = source.name
    settings.test_left_helper = helper.name

    set_helper_topology_class(helper, "BROT", "SubStructure")

    assert helper.name == (
        "SPOT::Imported::Element::SubStructureZone::BROT=SubStructure::SPOT=VolumeSpace"
    ), helper.name
    assert source.name == "SubStructureZone::BROT=SubStructure::SPOT=VolumeSpace"
    assert helper[PROP_SOURCE_NAME] == source.name
    assert am_user[PROP_AM_REFERENCE_HELPER] == source.name
    assert settings.test_left_helper == helper.name
    assert PROP_BOT_CLASS not in helper and helper[PROP_BROT_CLASS] == "SubStructure"
    semantics = parse_name_semantics(source.name)
    assert semantics.bot_class is None and semantics.brot_class == "SubStructure"
    print("Set as renames helper and source test passed")


def test_source_tags() -> None:
    assert (
        retag_name("Deck::SPOT=VolumeSpace::BOT=Element", {"BOT": None, "BROT": "Bridge"})
        == "Deck::BROT=Bridge::SPOT=VolumeSpace"
    )
    assert retag_name("Deck::BOT=Element::BEO=Slab", {"BOT": None, "BEO": None}) == "Deck"
    semantics = parse_name_semantics("Pier 1::BROT=SubStructure")
    assert semantics.bot_class is None and semantics.brot_class == "SubStructure"
    print("BROT name tag test passed")


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    with tempfile.TemporaryDirectory(prefix="spot-brot-test-") as name:
        directory = Path(name)
        input_path = directory / "bridge-input.ttl"
        input_path.write_text(BRIDGE_TURTLE, encoding="utf8")
        import_scene_graph_turtle(input_path)
        test_untyped_import_sets_no_class()
        output_path = test_set_classes_and_export(directory)
        test_reimport_keeps_brot(output_path)
        test_bot_relations_need_bot_on_both_ends(directory)
    test_clear_class()
    test_set_as_renames_helper_and_source()
    test_source_tags()


main()
