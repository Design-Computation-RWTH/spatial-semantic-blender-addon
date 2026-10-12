"""
SPOT-AM placements an imported graph states, kept to be written again.

An imported space is often located only with SPOT-AM: axis mappings and
`reloc:` relations of its boundary points, with no matrix.
The importer derives a region from them to draw the space, but a matrix
taken from that region would claim a precision the input never had. The
importer therefore keeps the description itself on the helper that carries
the placement, and the export writes it again in place of a matrix.

The description is JSON:

    {"axes": [{"source": "X", "target": "X", "target_direction": "Rear",
               "relation": "Codirectional", "accuracy": "Exact"}],
     "points": [{"axis": "X", "kind": "Max", "relation": "containedInRight",
                 "target": "6292_202e", "accuracy": "Approximate"}]}

`kind` is "Min", "Max" or null for a plain spot-am:BoundaryPoint, and an
accuracy is null when the input states none. A target is
the local name of the space related to, which the export resolves to the
helper that space was imported as. `target_direction` is the direction the
input gives the target axis, or null: the export maps it to the axis that
points that way in the space the export places the source in, so an
AssetSpace an import was placed in may name its axes otherwise.
"""

from __future__ import annotations

import json
from typing import Callable

import bpy

from .spot_blender_common import PROP_STATED_AM, quote_turtle_string


def stated_am(helper: bpy.types.Object) -> dict | None:
    raw = helper.get(PROP_STATED_AM)
    return json.loads(str(raw)) if raw else None


def point_id(source_suffix: str, axis: str, kind: str | None) -> str:
    if kind is None:
        return f"{source_suffix}_{axis.lower()}_boundary_point"
    return f"{source_suffix}_{axis.lower()}_{kind.lower()}_boundary_point"


def axis_id(space_suffix: str, axis: str) -> str:
    return f"{space_suffix}_{axis.lower()}_axis"


def accuracy_predicate(accuracy: str | None) -> list[str]:
    return [f"spot-am:hasAccuracy spot-am:{accuracy}"] if accuracy else []


def mapping_ids(app_id: str, description: dict) -> tuple[list[str], list[str]]:
    """The ids of the axis mappings and of the boundary point mappings."""
    axis_mapping_ids = [
        f"{app_id}_AM_{axis['source']}" for axis in description["axes"]
    ]
    point_mapping_ids: list[str] = []
    for point in description["points"]:
        base = f"{app_id}_BPM_{point['axis']}_{point['kind'] or 'Point'}"
        candidate, index = base, 2
        while candidate in point_mapping_ids:
            candidate, index = f"{base}_{index}", index + 1
        point_mapping_ids.append(candidate)
    return axis_mapping_ids, point_mapping_ids


def appearance_predicates(app_id: str, description: dict, marker: str) -> list[str]:
    """The appearance's links to its mappings."""
    axis_mapping_ids, point_mapping_ids = mapping_ids(app_id, description)
    predicates: list[str] = []
    if axis_mapping_ids:
        predicates.append(
            "spot-am:hasAxisMapping "
            + ",\n        ".join(f"{marker}{value}" for value in axis_mapping_ids)
        )
    if point_mapping_ids:
        predicates.append(
            "spot-am:hasBoundaryPointMapping "
            + ",\n        ".join(f"{marker}{value}" for value in point_mapping_ids)
        )
    return predicates


def mapping_blocks(
    app_id: str,
    description: dict,
    source_suffix: str,
    source_label: str,
    target_suffix: str,
    suffix_for_target: Callable[[str], str],
    marker: str,
    turtle_block: Callable[[str, list[str]], str],
    target_axis_for: Callable[[dict], tuple[str, bool]] = lambda axis: (axis["target"], False),
) -> str:
    """
    The mappings and the source's boundary points.

    `target_suffix` is the space the appearance places the source in, whose
    axes the axis mappings point at; `target_axis_for` gives the axis an
    axis mapping points at there, and whether it points the other way.
    `suffix_for_target` resolves the space a boundary point is related to.
    """
    axis_mapping_ids, point_mapping_ids = mapping_ids(app_id, description)
    blocks: list[str] = []
    for mapping_id, axis in zip(axis_mapping_ids, description["axes"]):
        target_axis, reversed_axis = target_axis_for(axis)
        relation = axis["relation"]
        if reversed_axis:
            relation = "Codirectional" if relation == "Antiparallel" else "Antiparallel"
        blocks.append(
            turtle_block(
                f"{marker}{mapping_id}",
                [
                    "a spot-am:AxisMapping",
                    *accuracy_predicate(axis["accuracy"]),
                    f"spot-am:hasAxisRelationType spot-am:{relation}",
                    f"spot-am:hasSourceAxis {marker}{axis_id(source_suffix, axis['source'])}",
                    f"spot-am:hasTargetAxis {marker}{axis_id(target_suffix, target_axis)}",
                ],
            )
        )

    emitted_points: set[str] = set()
    for mapping_id, point in zip(point_mapping_ids, description["points"]):
        source_point = point_id(source_suffix, point["axis"], point["kind"])
        blocks.append(
            turtle_block(
                f"{marker}{mapping_id}",
                [
                    "a spot-am:BoundaryPointMapping",
                    f"reloc:{point['relation']} {marker}{suffix_for_target(point['target'])}",
                    *accuracy_predicate(point["accuracy"]),
                    f"spot-am:hasSourcePoint {marker}{source_point}",
                ],
            )
        )
        if source_point in emitted_points:
            continue
        emitted_points.add(source_point)
        point_type = {"Min": "MinBoundaryPoint", "Max": "MaxBoundaryPoint"}.get(
            point["kind"], "BoundaryPoint"
        )
        bound = f"{point['axis']} {point['kind'].lower()}" if point["kind"] else point["axis"]
        blocks.append(
            f"{marker}{axis_id(source_suffix, point['axis'])} "
            f"spot-am:hasBoundaryPoint {marker}{source_point} ."
        )
        blocks.append(
            turtle_block(
                f"{marker}{source_point}",
                [
                    f"a spot-am:{point_type}",
                    "rdfs:label "
                    + quote_turtle_string(f"{bound} boundary point of {source_label}"),
                ],
            )
        )
    return "\n\n".join(blocks)
