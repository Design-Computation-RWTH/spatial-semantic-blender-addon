from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import bpy
from .export_spot_ttl import (
    ExportResourceRegistry,
    appearance_subject_id,
    build_export_resource_registry,
    exported_matrix_literal,
    helper_spot_types,
    normalize_local_prefix,
    perspective_picture_camera,
)
from .spot_blender_common import (
    BOT_CLASS_BUILDING,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SPACE,
    BOT_CLASS_STOREY,
    PROP_BOT_CLASS,
    PROP_DOT_CLASS,
    PROP_LABEL,
    PROP_LOCAL_BBOX,
    PROP_PLAN_KIND,
    PROP_ROLE,
    PROP_SORT_KEY,
    PROP_SOURCE_COLLECTION,
    PROP_SPOT_CLASS,
    ROLE_BUILDING,
    ROLE_DOCUMENT,
    ROLE_SITE,
    find_source_object_for_helper,
    format_number_list,
    helper_root_objects,
    quote_turtle_string,
)

PROP_SIMULATE_PLACEMENT_CHANGE = "spot_simulate_placement_change"
DEFAULT_HISTORY_START = "2025-01-01T09:00:00Z"
REGISTRATION_PHASES = {
    0: ("assets-documents", "Asset and document spaces registered in the demo dataset."),
    1: ("buildings-storeys", "Building and storey spaces registered in the demo dataset."),
    2: ("rooms", "Room spaces registered in the demo dataset."),
    3: (
        "physical-elements-damage",
        "Physical, element, and damage spaces registered in the demo dataset.",
    ),
    4: ("areas", "Horizontal and vertical area spaces registered in the demo dataset."),
    5: ("points", "Camera and other point spaces registered in the demo dataset."),
}


def parse_history_start(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("Spatial history start time must be an ISO 8601 date-time") from error
    if parsed.tzinfo is None:
        raise RuntimeError("Spatial history start time must include a time zone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def date_time_literal(value: datetime) -> str:
    lexical = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return f'{quote_turtle_string(lexical)}^^xsd:dateTime'


def placement_change_owners(helper: bpy.types.Object) -> list[object]:
    owners: list[object] = []
    source = find_source_object_for_helper(helper)
    if source is not None:
        owners.append(source)
    collection_name = helper.get(PROP_SOURCE_COLLECTION)
    if helper.get(PROP_PLAN_KIND) and collection_name:
        collection = bpy.data.collections.get(str(collection_name))
        if collection is not None:
            owners.append(collection)
    return owners


def placement_change_enabled(helper: bpy.types.Object) -> bool:
    return any(
        getattr(owner, PROP_SIMULATE_PLACEMENT_CHANGE, False)
        for owner in placement_change_owners(helper)
    )


def eligible_plan(helper: bpy.types.Object) -> bool:
    return bool(helper.get(PROP_PLAN_KIND))


def eligible_camera(helper: bpy.types.Object) -> bool:
    return perspective_picture_camera(helper) is not None


def history_phase(helper: bpy.types.Object) -> int:
    spot_class = str(helper.get(PROP_SPOT_CLASS, ""))
    role = str(helper.get(PROP_ROLE, ""))
    bot_class = str(helper.get(PROP_BOT_CLASS, ""))
    if role in {ROLE_SITE, ROLE_DOCUMENT}:
        return 0
    if role == ROLE_BUILDING or bot_class in {BOT_CLASS_BUILDING, BOT_CLASS_STOREY}:
        return 1
    if bot_class == BOT_CLASS_SPACE:
        return 2
    if spot_class in {"HorizontalAreaSpace", "VerticalAreaSpace"}:
        return 4
    if spot_class == "PointSpace":
        return 5
    if bot_class == BOT_CLASS_ELEMENT or helper.get(PROP_DOT_CLASS):
        return 3
    return 3


def registration_order(registry: ExportResourceRegistry) -> list[bpy.types.Object]:
    suffixes = registry.export_suffixes
    return sorted(
        registry.exported_helpers,
        key=lambda helper: (
            history_phase(helper),
            suffixes.get(helper.parent.name, "") if helper.parent else "",
            str(helper.get(PROP_SORT_KEY, "")),
            suffixes[helper.name],
        ),
    )


def registration_groups(
    registry: ExportResourceRegistry,
) -> list[tuple[int, list[bpy.types.Object]]]:
    grouped: dict[int, list[bpy.types.Object]] = {}
    for helper in registration_order(registry):
        grouped.setdefault(history_phase(helper), []).append(helper)
    return sorted(grouped.items())


def full_iri(base_iri: str, suffix: str) -> str:
    return f"<{base_iri}{suffix}>"


def assertion_block(
    assertion_iri: str,
    subject: str,
    predicate: str,
    value: str,
    activity: str,
    invalidated_by: str | None = None,
    revision_of: str | None = None,
) -> str:
    predicates = [
        "a prov:Entity",
        f"rdf:reifies <<( {subject} {predicate} {value} )>>",
        f"prov:wasGeneratedBy {activity}",
    ]
    if invalidated_by:
        predicates.append(f"prov:wasInvalidatedBy {invalidated_by}")
    if revision_of:
        predicates.append(f"prov:wasRevisionOf {revision_of}")
    return f"{assertion_iri}\n    " + " ;\n    ".join(predicates) + " ."


def activity_block(activity: str, label: str, time: datetime, agent: str) -> str:
    return "\n".join(
        [
            f"{activity}",
            "    a prov:Activity ;",
            f"    rdfs:label {quote_turtle_string(label)}@en ;",
            f"    prov:wasAssociatedWith {agent} ;",
            f"    prov:endedAtTime {date_time_literal(time)} .",
        ]
    )


def zero_translation(matrix_literal: str, precision: int) -> str:
    values = [float(value) for value in matrix_literal[1:-1].split(",")]
    values[12:15] = [0.0, 0.0, 0.0]
    return format_number_list(values, precision)


def offset_camera_translation(
    matrix_literal: str,
    parent: bpy.types.Object,
    precision: int,
) -> str:
    values = [float(value) for value in matrix_literal[1:-1].split(",")]
    raw_bounds = parent.get(PROP_LOCAL_BBOX)
    offset = [1.0, 0.0, 0.0]
    if raw_bounds is not None and len(raw_bounds) == 6:
        spans = [float(raw_bounds[index + 3]) - float(raw_bounds[index]) for index in range(3)]
        candidate = [span * 0.05 for span in spans]
        if any(abs(value) > 1e-12 for value in candidate):
            offset = candidate
    values[12:15] = [values[12 + index] + offset[index] for index in range(3)]
    return format_number_list(values, precision)


def correction_targets(
    registry: ExportResourceRegistry,
    precision: int,
) -> list[tuple[bpy.types.Object, str, str, str]]:
    targets: list[tuple[bpy.types.Object, str, str, str]] = []
    suffixes = registry.export_suffixes
    document_proxy_by_physical = {
        physical.name: document for physical, document in registry.proxy_pairings
    }
    for helper in registry.exported_helpers:
        if not placement_change_enabled(helper):
            continue
        if eligible_plan(helper) and helper.name in document_proxy_by_physical:
            document = document_proxy_by_physical[helper.name]
            parent = document.parent
            if parent is None:
                continue
            appearance = (
                f"appearance_{suffixes[helper.name]}_in_{suffixes[parent.name]}_via_"
                f"{suffixes[document.name]}"
            )
            final = exported_matrix_literal(
                document,
                parent,
                precision,
                registry.document_backed_helper_names,
            )
            targets.append((helper, appearance, zero_translation(final, precision), final))
        elif eligible_camera(helper) and helper.parent is not None:
            parent = helper.parent
            appearance = appearance_subject_id(helper, parent, suffixes)
            final = exported_matrix_literal(
                helper,
                parent,
                precision,
                registry.document_backed_helper_names,
            )
            targets.append(
                (helper, appearance, offset_camera_translation(final, parent, precision), final)
            )
    return sorted(targets, key=lambda item: registry.export_suffixes[item[0].name])


def export_spatial_history(
    helper_collection_name: str,
    output_path: str | Path,
    base_iri: str,
    local_prefix: str,
    precision: int,
    start_time: str = DEFAULT_HISTORY_START,
) -> dict[str, object]:
    collection = bpy.data.collections.get(helper_collection_name)
    if collection is None:
        raise RuntimeError(f"Helper collection '{helper_collection_name}' was not found")
    roots = helper_root_objects(collection)
    registry = build_export_resource_registry(roots)
    prefix = normalize_local_prefix(local_prefix)
    started = parse_history_start(start_time)
    groups = registration_groups(registry)
    suffixes = registry.export_suffixes
    corrections = correction_targets(registry, precision)
    correction_by_helper = {helper.name: index for index, (helper, *_rest) in enumerate(corrections)}
    agent = full_iri(base_iri, "spatial-history/agent/blender-exporter")
    graph = full_iri(base_iri, "spatial-history")
    blocks = [
        f"{agent} a prov:SoftwareAgent, prov:Agent ; rdfs:label \"Blender demo dataset exporter\"@en ."
    ]
    activity_by_helper: dict[str, str] = {}
    for index, (phase, helpers) in enumerate(groups):
        phase_token, phase_label = REGISTRATION_PHASES[phase]
        activity = full_iri(
            base_iri,
            f"spatial-history/activity/register-{index + 1:04d}-{phase_token}",
        )
        blocks.append(
            activity_block(
                activity,
                phase_label,
                started + timedelta(seconds=60 * index),
                agent,
            )
        )
        for helper in helpers:
            suffix = suffixes[helper.name]
            assertion = full_iri(base_iri, f"spatial-history/assertion/register-{suffix}")
            subject = full_iri(base_iri, suffix)
            activity_by_helper[helper.name] = activity
            blocks.append(
                assertion_block(
                    assertion,
                    subject,
                    "rdf:type",
                    f"spot:{helper_spot_types(helper)[0]}",
                    activity,
                )
            )

    for helper, appearance_suffix, initial, final in corrections:
        correction_index = correction_by_helper[helper.name]
        activity = full_iri(
            base_iri,
            f"spatial-history/activity/correct-{correction_index + 1:04d}-{suffixes[helper.name]}",
        )
        appearance = full_iri(base_iri, appearance_suffix)
        initial_assertion = full_iri(
            base_iri, f"spatial-history/assertion/placement-initial-{appearance_suffix}"
        )
        final_assertion = full_iri(
            base_iri, f"spatial-history/assertion/placement-final-{appearance_suffix}"
        )
        blocks.append(
            assertion_block(
                initial_assertion,
                appearance,
                "gom:hasTransformationMatrix",
                f"{quote_turtle_string(initial)}^^gom:columnMajorArray",
                activity_by_helper[helper.name],
                invalidated_by=activity,
            )
        )
        blocks.append(
            activity_block(
                activity,
                f"{helper.get(PROP_LABEL, helper.name)} placement corrected in the demo dataset.",
                started + timedelta(seconds=60 * (len(groups) + correction_index)),
                agent,
            )
        )
        blocks.append(
            assertion_block(
                final_assertion,
                appearance,
                "gom:hasTransformationMatrix",
                f"{quote_turtle_string(final)}^^gom:columnMajorArray",
                activity,
                revision_of=initial_assertion,
            )
        )

    source = "\n".join(
        [
            f"@prefix {prefix}: <{base_iri}> .",
            "@prefix gom: <https://w3id.org/gom#> .",
            "@prefix prov: <http://www.w3.org/ns/prov#> .",
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "@prefix spot: <https://w3id.org/spot#> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "",
            f"{graph} {{",
            "\n\n".join("  " + line.replace("\n", "\n  ") for line in blocks),
            "}",
            "",
        ]
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf8")
    return {
        "output_path": str(path),
        "activity_count": len(groups) + len(corrections),
        "registration_count": len(groups),
        "registered_space_count": sum(len(helpers) for _phase, helpers in groups),
        "correction_count": len(corrections),
    }
