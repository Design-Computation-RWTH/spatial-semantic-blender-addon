from __future__ import annotations

import os
import re
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

import bpy
from mathutils import Matrix


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

import spatial_semantic_addon
from spatial_semantic_addon.bcf_authoring import (
    PROP_BCF_COMPONENTS,
    PROP_BCF_HAS_BITMAP,
    PROP_BCF_HAS_CLIPPING_PLANE,
    PROP_BCF_HAS_COLORING,
    PROP_BCF_HAS_COMMENT,
    PROP_BCF_HAS_COMPONENT_SELECTION,
    PROP_BCF_HAS_LINE,
    PROP_BCF_HAS_STATUS_HISTORY,
    PROP_BCF_ISSUE_ENABLED,
    PROP_OBSERVED_DAMAGES,
)
from spatial_semantic_addon.dataset_snapshot import export_dataset_snapshot
from spatial_semantic_addon.export_bcf_trig import export_bcf_fixture
from spatial_semantic_addon.export_spatial_history import (
    PROP_SIMULATE_PLACEMENT_CHANGE,
    export_spatial_history,
)
from spatial_semantic_addon.export_spot_ttl import EXPORT_PROFILE_SCENE_GRAPH, export_helper_scene
from spatial_semantic_addon.spot_blender_common import (
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SITE,
    BOT_CLASS_SPACE,
    HELPER_COLLECTION_MARKER,
    PROP_BOT_CLASS,
    PROP_DOT_CLASS,
    PROP_HELPER,
    PROP_LABEL,
    PROP_LENGTH_UNIT,
    PROP_LOCAL_BBOX,
    PROP_ROLE,
    PROP_SORT_KEY,
    PROP_SOURCE_ASSET_PATH,
    PROP_SOURCE_NAME,
    PROP_SPOT_CLASS,
    PROP_URI_SUFFIX,
    ROLE_SITE,
    SPOT_CLASS_ASSET,
)


def mesh_object(name: str) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([(-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)], [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def helper_object(
    collection: bpy.types.Collection,
    name: str,
    spot_class: str,
    source: bpy.types.Object | None,
    parent: bpy.types.Object | None,
    sort_key: str,
) -> bpy.types.Object:
    helper = mesh_object(name)
    for owner in list(helper.users_collection):
        owner.objects.unlink(helper)
    collection.objects.link(helper)
    helper[PROP_HELPER] = True
    helper[PROP_ROLE] = ROLE_SITE if parent is None else "entity"
    helper[PROP_SPOT_CLASS] = spot_class
    helper[PROP_URI_SUFFIX] = name.lower()
    helper[PROP_LABEL] = name
    helper[PROP_LENGTH_UNIT] = "M"
    helper[PROP_SORT_KEY] = sort_key
    helper[PROP_LOCAL_BBOX] = [-0.5, -0.5, -0.5, 0.5, 0.5, 0.5]
    if source is not None:
        helper[PROP_SOURCE_NAME] = source.name
    if parent is not None:
        helper.parent = parent
        helper.matrix_parent_inverse = Matrix.Identity(4)
    return helper


def build_fixture_scene() -> tuple[bpy.types.Object, bpy.types.Object]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()

    collection = bpy.data.collections.new("SPOT Scene")
    collection[HELPER_COLLECTION_MARKER] = True
    bpy.context.scene.collection.children.link(collection)
    bpy.context.scene.spot_tools_settings.helper_collection = collection.name
    bpy.context.scene.spot_tools_settings.base_iri = "http://localhost:4000/scene/"

    root = helper_object(collection, "Asset", SPOT_CLASS_ASSET, None, None, "000")
    root[PROP_BOT_CLASS] = BOT_CLASS_SITE

    camera_data = bpy.data.cameras.new("Picture Camera Data")
    camera_data.type = "PERSP"
    camera = bpy.data.objects.new("Picture 1", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    picture = helper_object(collection, "Picture Point", "PointSpace", camera, root, "010")
    picture[PROP_SOURCE_ASSET_PATH] = "picture-1.jpg"

    for index in range(2):
        room_source = mesh_object(f"Room {index + 1}")
        room = helper_object(
            collection,
            f"Room Space {index + 1}",
            "PhysicalSpace",
            room_source,
            root,
            f"01{index + 1}",
        )
        room[PROP_BOT_CLASS] = BOT_CLASS_SPACE

    damage_source = mesh_object("Damage Moisture")
    damage = helper_object(collection, "Damage Space", "PhysicalSpace", damage_source, root, "020")
    damage[PROP_DOT_CLASS] = "Damage"

    element_source = mesh_object("IfcWall Test")
    element_source["GlobalId"] = "1ZwJH$85D3YQG5AK5ER1Wc"
    element = helper_object(collection, "Wall Element", "PhysicalSpace", element_source, root, "030")
    element[PROP_BOT_CLASS] = BOT_CLASS_ELEMENT

    damage_entry = getattr(camera, PROP_OBSERVED_DAMAGES).add()
    damage_entry.object = damage_source
    component_entry = getattr(camera, PROP_BCF_COMPONENTS).add()
    component_entry.object = element_source

    setattr(camera, PROP_BCF_ISSUE_ENABLED, True)
    setattr(camera, PROP_BCF_HAS_COMMENT, True)
    setattr(camera, PROP_BCF_HAS_STATUS_HISTORY, True)
    setattr(camera, PROP_BCF_HAS_COMPONENT_SELECTION, True)
    setattr(camera, PROP_BCF_HAS_COLORING, True)
    setattr(camera, PROP_BCF_HAS_CLIPPING_PLANE, True)
    setattr(camera, PROP_BCF_HAS_LINE, True)
    setattr(camera, PROP_BCF_HAS_BITMAP, True)
    return camera, picture


def main() -> None:
    camera, _picture = build_fixture_scene()
    retained_output = os.environ.get("SPOT_BCF_TEST_OUTPUT")
    output_context = (
        nullcontext(retained_output)
        if retained_output
        else tempfile.TemporaryDirectory(prefix="spot-bcf-test-")
    )
    with output_context as directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        first_scene = root / "scene-1.ttl"
        second_scene = root / "scene-2.ttl"
        first_bcf = root / "bcf-1.trig"
        second_bcf = root / "bcf-2.trig"

        for scene_path, bcf_path in (
            (first_scene, first_bcf),
            (second_scene, second_bcf),
        ):
            export_helper_scene(
                output_path=scene_path,
                export_profile=EXPORT_PROFILE_SCENE_GRAPH,
                asset_base_url="/assets",
            )
            result = export_bcf_fixture(output_path=bcf_path)
            assert result is not None and result["issue_count"] == 1

        scene_text = first_scene.read_text(encoding="utf8")
        bcf_text = first_bcf.read_text(encoding="utf8")
        assert "sosa:hasFeatureOfInterest" in scene_text
        assert "foaf:depicts" in scene_text
        assert "a bcfOWL:Topic" in bcf_text
        assert "a bcfOWL:Viewpoint" in bcf_text
        assert "a bcfOWL:Comment" in bcf_text
        assert "bcfOWL:referenceSpace ex:asset" in bcf_text
        assert "bcfOWL:hasPerspectiveCamera ex:capture_" in bcf_text
        assert "bcfOWL:hasSnapshot ex:snapshot_" in bcf_text
        assert "bcfOWL:hasIfcGuid \"1ZwJH$85D3YQG5AK5ER1Wc\"" in bcf_text
        assert "rdf:reifies <<(" in bcf_text
        assert first_scene.read_bytes() == second_scene.read_bytes()
        assert first_bcf.read_bytes() == second_bcf.read_bytes()

        previous_datasets_root = os.environ.get("SPATIAL_SEMANTIC_DATASETS_DIR")
        configured_datasets_root = root / "configured-datasets"
        os.environ["SPATIAL_SEMANTIC_DATASETS_DIR"] = str(configured_datasets_root)
        try:
            configured_result = export_dataset_snapshot()
        finally:
            if previous_datasets_root is None:
                os.environ.pop("SPATIAL_SEMANTIC_DATASETS_DIR", None)
            else:
                os.environ["SPATIAL_SEMANTIC_DATASETS_DIR"] = previous_datasets_root
        configured_path = Path(configured_result["output_path"])
        assert configured_path.parent == (configured_datasets_root / "exports").resolve()

        snapshot_result = export_dataset_snapshot(exports_root=root / "exports")
        snapshot_path = Path(snapshot_result["output_path"])
        assert snapshot_result["bcf_issue_count"] == 1
        assert (snapshot_path / "rdf" / "scene.ttl").is_file()
        assert (snapshot_path / "rdf" / "bcf.trig").is_file()
        assert not (snapshot_path / "rdf" / "spatial-history.trig").exists()

        assert not getattr(camera, PROP_SIMULATE_PLACEMENT_CHANGE)
        unflagged_history = root / "unflagged-spatial-history.trig"
        export_spatial_history(
            helper_collection_name="SPOT Scene",
            output_path=unflagged_history,
            base_iri="http://localhost:4000/scene/",
            local_prefix="ex",
            precision=6,
        )
        assert "gom:hasTransformationMatrix" not in unflagged_history.read_text(
            encoding="utf8"
        )

        setattr(camera, PROP_SIMULATE_PLACEMENT_CHANGE, True)
        first_history_result = export_dataset_snapshot(
            exports_root=root / "exports",
            simulate_spatial_history=True,
        )
        first_history = (
            Path(first_history_result["output_path"]) / "rdf" / "spatial-history.trig"
        )
        flagged_scene = Path(first_history_result["output_path"]) / "rdf" / "scene.ttl"
        second_history = root / "spatial-history-copy.trig"
        export_spatial_history(
            helper_collection_name="SPOT Scene",
            output_path=second_history,
            base_iri="http://localhost:4000/scene/",
            local_prefix="ex",
            precision=6,
        )
        history_text = first_history.read_text(encoding="utf8")
        matrix_literals = re.findall(
            r"appearance_picture-1_in_asset> gom:hasTransformationMatrix \"([^\"]+)\"",
            history_text,
        )
        assert first_history_result["spatial_history_activity_count"] == 5
        assert first_history.read_bytes() == second_history.read_bytes()
        assert len(matrix_literals) == 2 and matrix_literals[0] != matrix_literals[1]
        assert matrix_literals[1] in flagged_scene.read_text(encoding="utf8")
        assert history_text.count("rdf:reifies <<( <http://localhost:4000/scene/appearance_picture-1_in_asset>") == 2
        assert history_text.count("spatial-history/assertion/placement-initial-appearance_picture-1_in_asset>") == 2
        assert history_text.count("spatial-history/assertion/placement-final-appearance_picture-1_in_asset>") == 1
        assert "prov:wasRevisionOf" in history_text
        assert 'prov:endedAtTime "2025-01-01T09:00:00Z"^^xsd:dateTime' in history_text
        assert 'prov:endedAtTime "2025-01-01T09:03:00Z"^^xsd:dateTime' in history_text
        assert 'prov:endedAtTime "2025-01-01T09:04:00Z"^^xsd:dateTime' in history_text
        assert "registered in the demo dataset." in history_text
        assert history_text.count("activity/register-0002-rooms>") == 3
        assert history_text.index("activity/register-0001-assets-documents") < history_text.index(
            "activity/register-0002-rooms"
        ) < history_text.index(
            "activity/register-0004-points"
        ) < history_text.index("activity/correct-0001-picture-1")
        setattr(camera, PROP_SIMULATE_PLACEMENT_CHANGE, False)

        setattr(camera, PROP_BCF_ISSUE_ENABLED, False)
        plain_result = export_dataset_snapshot(exports_root=root / "exports")
        plain_path = Path(plain_result["output_path"])
        assert plain_result["bcf_issue_count"] == 0
        assert (plain_path / "rdf" / "scene.ttl").is_file()
        assert not (plain_path / "rdf" / "bcf.trig").exists()
        setattr(camera, PROP_BCF_ISSUE_ENABLED, True)

        blend_path = root / "persisted-fixture.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        camera = bpy.data.objects["Picture 1"]
        assert getattr(camera, PROP_BCF_ISSUE_ENABLED)
        assert getattr(camera, PROP_BCF_HAS_COMMENT)
        assert len(getattr(camera, PROP_OBSERVED_DAMAGES)) == 1
        assert getattr(camera, PROP_OBSERVED_DAMAGES)[0].object.name == "Damage Moisture"
        assert len(getattr(camera, PROP_BCF_COMPONENTS)) == 1
        assert getattr(camera, PROP_BCF_COMPONENTS)[0].object.name == "IfcWall Test"

        camera.data.type = "ORTHO"
        try:
            export_bcf_fixture(output_path=root / "invalid.trig")
        except RuntimeError as error:
            assert "perspective cameras" in str(error)
        else:
            raise AssertionError("Orthographic issue export was not rejected")

    print("BCF fixture Blender integration test passed")


if __name__ == "__main__":
    main()
