from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import bpy
from mathutils import Vector

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import spatial_semantic_addon
from spatial_semantic_addon.import_spot_ttl import import_scene_graph_turtle
from spatial_semantic_addon.spot_blender_common import PROP_HELPER, PROP_LABEL
from spatial_semantic_addon.spot_transition import (
    commit_sg_placements,
    write_transition_dataset,
)
from test_import_spot_ttl_blender import bridge_fixture, world_x_range


def helper(label: str) -> bpy.types.Object:
    return next(
        obj
        for obj in bpy.data.objects
        if obj.get(PROP_HELPER) and obj[PROP_LABEL] == label
    )


def entry(log: str, header: str) -> str:
    """The body of one mutation entry, up to the next one."""
    body = log.split(header, 1)[1]
    return body.split("#!lbcf-entry", 1)[0]


def test_spans_move_from_spot_am_to_spot_sg() -> None:
    """Two spans placed by hand, then one corrected, then read back.

    SPOT-AM splits the 90 m bridge into three 30 m spans. The face between
    spans 1 and 2 really lies at 40 m, so both are committed as SPOT-SG, then
    span 2 is raised by 1 m and committed again. The written dataset keeps the
    input and logs both activities. Read back, spans 1 and 2 stand where they
    were placed, and span 3, still SPOT-AM, fills the rest of the bridge.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    spatial_semantic_addon.register()
    anchor_turtle, turtle = bridge_fixture()

    with tempfile.TemporaryDirectory(prefix="spot-transition-") as directory:
        anchor_path = Path(directory) / "model.ttl"
        anchor_path.write_text(anchor_turtle, encoding="utf8")
        input_path = Path(directory) / "bridge.ttl"
        input_path.write_text(turtle, encoding="utf8")
        import_scene_graph_turtle(anchor_path)
        anchor = next(obj for obj in bpy.data.objects if obj.get(PROP_HELPER))
        anchor.spot_asset_axis_x_direction = "Rear"
        anchor.spot_asset_axis_y_direction = "Left"
        anchor.spot_asset_axis_z_direction = "Top"
        import_scene_graph_turtle(input_path, anchor_helper=anchor)

        span_1, span_2 = helper("Span_1"), helper("Span_2")
        span_1.scale.x = 40.0 / 30.0
        span_1.location.x += 5.0
        span_2.scale.x = 20.0 / 30.0
        span_2.location.x += 5.0
        bpy.context.view_layer.update()
        first = commit_sg_placements(
            [span_1, span_2],
            agent="Surveyor",
            label="Spans placed from the plan",
            used=["http://example.org/plan"],
            ended_at=datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc),
        )
        assert [change["kind"] for change in first["changes"]] == [
            "am-to-sg",
            "am-to-sg",
        ]

        try:
            commit_sg_placements([span_2], agent="Surveyor", label="Nothing")
        except RuntimeError as error:
            assert "Nothing changed" in str(error)
        else:
            raise AssertionError("A commit without a change must be refused")

        span_2.location.z += 1.0
        bpy.context.view_layer.update()
        commit_sg_placements(
            [span_2],
            agent="Architect",
            label="Span 2 height corrected",
            ended_at=datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc),
        )

        output = Path(directory) / "transition-dataset"
        result = write_transition_dataset(output)
        # The bridge's frame is stated by an activity of its own, first.
        assert result["activity_count"] == 3
        assert (output / "rdf" / "scene.ttl").read_text(encoding="utf8") == turtle
        log = (output / "rdf" / "mutations.trig").read_text(encoding="utf8")

        frame_delta = entry(log, "#!lbcf-entry delta 0")
        assert "<http://example.org/Bridge> spot-sg:hasBoundingBox" in frame_delta
        assert "Frame of Bridge stated" in frame_delta
        assert "#!lbcf-entry retraction 0" not in log

        first_retraction = entry(log, "#!lbcf-entry retraction 1")
        assert (
            "<http://example.org/Span_1> spot:hasAppearance "
            "<http://example.org/Span_1_in_Bridge> ." in first_retraction
        )
        first_delta = entry(log, "#!lbcf-entry delta 1")
        assert "spot-sg:hasBoundingBox \"[0," not in first_delta
        assert "prov:used <http://example.org/plan>" in first_delta
        # Each SPOT-AM link is invalidated, and its SPOT-SG link revises it.
        assert first_delta.count("prov:wasInvalidatedBy") == 2
        assert first_delta.count("prov:wasRevisionOf") == 2

        # The correction revises the matrix only; the unchanged box keeps its
        # reifier and is neither retracted nor asserted again.
        second_retraction = entry(log, "#!lbcf-entry retraction 2")
        assert "gom:hasTransformationMatrix" in second_retraction
        assert "spot-sg:hasBoundingBox" not in second_retraction
        second_delta = entry(log, "#!lbcf-entry delta 2")
        assert second_delta.count("prov:wasInvalidatedBy") == 1
        assert second_delta.count("prov:wasRevisionOf") == 1
        assert "<http://example.org/agent/Architect>" in second_delta

        # Without a folder, the dataset becomes a snapshot of its project.
        exports = Path(directory) / "exports"
        previous_root = os.environ.get("SPATIAL_SEMANTIC_DATASETS_DIR")
        os.environ["SPATIAL_SEMANTIC_DATASETS_DIR"] = directory
        try:
            snapshot = write_transition_dataset(project_name="Nibelungen Bridge")
        finally:
            if previous_root is None:
                os.environ.pop("SPATIAL_SEMANTIC_DATASETS_DIR", None)
            else:
                os.environ["SPATIAL_SEMANTIC_DATASETS_DIR"] = previous_root
        snapshot_dir = Path(snapshot["output_dir"])
        assert snapshot_dir.parent == (exports / "nibelungen-bridge").resolve()
        assert (snapshot_dir / "rdf" / "mutations.trig").read_text(encoding="utf8") == log

        bpy.ops.wm.read_factory_settings(use_empty=True)
        spatial_semantic_addon.register()
        import_scene_graph_turtle(output)

    # The bridge now states its own box, so no anchor is needed to read it.
    assert world_x_range(helper("Span_1")) == (0.0, 40.0)
    assert world_x_range(helper("Span_2")) == (40.0, 60.0)
    assert world_x_range(helper("Span_3")) == (60.0, 90.0)
    assert world_x_range(helper("Section")) == (40.0, 40.0)
    span_2 = helper("Span_2")
    span_2_z = [(span_2.matrix_world @ Vector(corner)).z for corner in span_2.bound_box]
    assert (round(min(span_2_z), 3), round(max(span_2_z), 3)) == (1.0, 21.0)
    print("SPOT-AM to SPOT-SG transition test passed")


if __name__ == "__main__":
    test_spans_move_from_spot_am_to_spot_sg()
