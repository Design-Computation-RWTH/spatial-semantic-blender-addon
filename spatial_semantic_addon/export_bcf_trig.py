from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Vector

from .bcf_authoring import (
    PROP_BCF_COMMENT_CREATED,
    PROP_BCF_COMMENT_GUID,
    PROP_BCF_COMMENT_TEXT,
    PROP_BCF_COMPONENTS,
    PROP_BCF_CREATED,
    PROP_BCF_DESCRIPTION,
    PROP_BCF_HAS_BITMAP,
    PROP_BCF_HAS_CLIPPING_PLANE,
    PROP_BCF_HAS_COLORING,
    PROP_BCF_HAS_COMMENT,
    PROP_BCF_HAS_COMPONENT_SELECTION,
    PROP_BCF_HAS_LINE,
    PROP_BCF_HAS_STATUS_HISTORY,
    PROP_BCF_HISTORY_CREATED,
    PROP_BCF_ISSUE_ENABLED,
    PROP_BCF_TITLE,
    PROP_BCF_TOPIC_GUID,
    PROP_BCF_VIEWPOINT_GUID,
    helper_for_exported_reference,
    reference_objects,
)
from .export_spot_ttl import (
    DEFAULT_BASE_IRI,
    DEFAULT_LOCAL_PREFIX,
    LEGACY_DEFAULT_BASE_IRI,
    LEGACY_HASH_BASE_IRI,
    build_export_resource_registry,
    ensure_helper_collection,
    normalize_local_prefix,
    perspective_picture_camera,
    turtle_block,
)
from .spot_blender_common import (
    BOT_CLASS_ELEMENT,
    HELPER_COLLECTION_NAME,
    PROP_BOT_CLASS,
    PROP_SPOT_CLASS,
    SPOT_CLASS_ASSET,
    find_source_object_for_helper,
    format_number,
    helper_root_objects,
    quote_turtle_string,
    slugify,
)


def bcf_base_iri(base_iri: str) -> str:
    return base_iri.rstrip("/#") + "/bcf/"


def date_time_literal(value: str) -> str:
    if not value:
        raise RuntimeError("BCF test data contains an empty timestamp")
    return f"{quote_turtle_string(value)}^^xsd:dateTime"


def guid_suffix(value: str) -> str:
    if not value:
        raise RuntimeError("BCF test data contains an empty GUID")
    return value.lower()


def vector_literal(value: Vector, datatype: str, precision: int) -> str:
    parts = ",".join(format_number(float(component), precision) for component in value)
    return f'{quote_turtle_string(f"[{parts}]")}^^bcfOWL:{datatype}'


def double_literal(value: float, precision: int) -> str:
    return f'{quote_turtle_string(format_number(value, precision))}^^xsd:double'


def source_ifc_guid(source: bpy.types.Object | None) -> str | None:
    if source is None:
        return None
    for key in ("GlobalId", "global_id", "ifc_guid", "IfcGuid"):
        value = source.get(key)
        if value:
            return str(value)
    properties = source.get("BIMObjectProperties")
    if isinstance(properties, dict):
        for key in ("GlobalId", "global_id", "ifc_guid"):
            value = properties.get(key)
            if value:
                return str(value)
    return None


def issue_resources(camera: bpy.types.Object) -> tuple[str, str]:
    topic_guid = guid_suffix(getattr(camera, PROP_BCF_TOPIC_GUID))
    viewpoint_guid = guid_suffix(getattr(camera, PROP_BCF_VIEWPOINT_GUID))
    return f"bcf:topic_{topic_guid}", f"bcf:viewpoint_{viewpoint_guid}"


def component_references(
    camera: bpy.types.Object,
    registry,
) -> list[tuple[str, bpy.types.Object, bpy.types.Object]]:
    resolved: dict[str, tuple[str, bpy.types.Object, bpy.types.Object]] = {}
    for reference in reference_objects(camera, PROP_BCF_COMPONENTS):
        helper = helper_for_exported_reference(
            reference, set(registry.bot_export_suffixes)
        )
        if (
            helper is None
            or helper.name not in registry.bot_export_suffixes
            or str(helper.get(PROP_BOT_CLASS, "")) != BOT_CLASS_ELEMENT
        ):
            raise RuntimeError(
                f"Issue camera '{camera.name}' references non-element component '{reference.name}'"
            )
        bot_suffix = registry.bot_export_suffixes[helper.name]
        source = find_source_object_for_helper(helper) or reference
        resolved[bot_suffix] = (
            f"bcf:component_{slugify(bot_suffix)}",
            helper,
            source,
        )
    return [resolved[key] for key in sorted(resolved)]


def validate_issue(
    camera: bpy.types.Object,
    components: list[tuple[str, bpy.types.Object, bpy.types.Object]],
) -> None:
    required = (
        (PROP_BCF_TOPIC_GUID, "Topic GUID"),
        (PROP_BCF_VIEWPOINT_GUID, "Viewpoint GUID"),
        (PROP_BCF_CREATED, "creation timestamp"),
        (PROP_BCF_TITLE, "title"),
        (PROP_BCF_DESCRIPTION, "description"),
    )
    for property_name, label in required:
        if not getattr(camera, property_name):
            raise RuntimeError(f"Issue camera '{camera.name}' has no {label}")

    if getattr(camera, PROP_BCF_HAS_COMMENT):
        for property_name, label in (
            (PROP_BCF_COMMENT_GUID, "Comment GUID"),
            (PROP_BCF_COMMENT_CREATED, "Comment timestamp"),
            (PROP_BCF_COMMENT_TEXT, "Comment text"),
        ):
            if not getattr(camera, property_name):
                raise RuntimeError(f"Issue camera '{camera.name}' has no {label}")

    if getattr(camera, PROP_BCF_HAS_STATUS_HISTORY) and not getattr(
        camera, PROP_BCF_HISTORY_CREATED
    ):
        raise RuntimeError(f"Issue camera '{camera.name}' has no status-history timestamp")

    needs_components = getattr(camera, PROP_BCF_HAS_COMPONENT_SELECTION) or getattr(
        camera, PROP_BCF_HAS_COLORING
    )
    if needs_components and not components:
        raise RuntimeError(
            f"Issue camera '{camera.name}' needs at least one BOT element component"
        )


def project_blocks(settings, asset_space: str) -> list[str]:
    project_guid = guid_suffix(settings.bcf_project_guid)
    project = "bcf:project"
    return [
        turtle_block(
            project,
            [
                "a bcfOWL:Project",
                f"dcterms:identifier {quote_turtle_string(project_guid)}",
                f"dcterms:title {quote_turtle_string(settings.bcf_project_title)}",
                f"bcfOWL:referenceSpace {asset_space}",
                "bcfOWL:hasExtensionSchema bcf:extension_schema",
            ],
        ),
        turtle_block(
            "bcf:extension_schema",
            [
                "a bcfOWL:ExtensionSchema",
                "bcfOWL:hasStatusScheme bcf:status_scheme",
                "bcfOWL:hasTypeScheme bcf:type_scheme",
                "bcfOWL:hasLabelScheme bcf:label_scheme",
                "bcfOWL:hasPriorityScheme bcf:priority_scheme",
                "bcfOWL:hasStageScheme bcf:stage_scheme",
                "bcfOWL:hasUser bcf:test_author",
            ],
        ),
        turtle_block(
            "bcf:test_author",
            [
                "a prov:Person, prov:Agent",
                f"dcterms:identifier {quote_turtle_string(settings.bcf_author_identifier)}",
                f"foaf:name {quote_turtle_string(settings.bcf_author_name)}",
            ],
        ),
        "\n\n".join(
            [
                "bcf:status_scheme a skos:ConceptScheme ; skos:prefLabel \"Test issue statuses\"@en .",
                "bcf:status_open a bcfOWL:TopicStatus ; skos:inScheme bcf:status_scheme ; skos:prefLabel \"Open\"@en .",
                "bcf:status_in_progress a bcfOWL:TopicStatus ; skos:inScheme bcf:status_scheme ; skos:prefLabel \"In progress\"@en .",
                "bcf:status_closed a bcfOWL:TopicStatus ; skos:inScheme bcf:status_scheme ; skos:prefLabel \"Closed\"@en .",
                "bcf:type_scheme a skos:ConceptScheme ; skos:prefLabel \"Test issue types\"@en .",
                "bcf:type_defect a bcfOWL:TopicType ; skos:inScheme bcf:type_scheme ; skos:prefLabel \"Defect\"@en .",
                "bcf:type_inspection a bcfOWL:TopicType ; skos:inScheme bcf:type_scheme ; skos:prefLabel \"Inspection request\"@en .",
                "bcf:priority_scheme a skos:ConceptScheme ; skos:prefLabel \"Test priorities\"@en .",
                "bcf:priority_medium a bcfOWL:Priority ; skos:inScheme bcf:priority_scheme ; skos:prefLabel \"Medium\"@en .",
                "bcf:label_scheme a skos:ConceptScheme ; skos:prefLabel \"Test labels\"@en .",
                "bcf:label_photo a bcfOWL:Label ; skos:inScheme bcf:label_scheme ; skos:prefLabel \"Photo documentation\"@en .",
                "bcf:stage_scheme a skos:ConceptScheme ; skos:prefLabel \"Test stages\"@en .",
                "bcf:stage_existing a bcfOWL:Stage ; skos:inScheme bcf:stage_scheme ; skos:prefLabel \"Existing-condition survey\"@en .",
            ]
        ),
    ]


def component_blocks(
    components: list[tuple[str, bpy.types.Object, bpy.types.Object]],
    registry,
    local_prefix: str,
) -> list[str]:
    blocks: list[str] = []
    for component, helper, source in components:
        predicates = [
            "a bcfOWL:Component",
            f"bcfOWL:hasElement {local_prefix}:{registry.bot_export_suffixes[helper.name]}",
        ]
        guid = source_ifc_guid(source)
        if guid:
            predicates.append(f"bcfOWL:hasIfcGuid {quote_turtle_string(guid)}")
        blocks.append(turtle_block(component, predicates))
    return blocks


def visual_predicates(
    camera: bpy.types.Object,
    snapshot: str,
    component_terms: list[str],
    precision: int,
) -> list[str]:
    predicates: list[str] = []
    location = camera.matrix_world.translation.copy()
    rotation = camera.matrix_world.to_quaternion()
    forward = rotation @ Vector((0.0, 0.0, -1.0))
    up = rotation @ Vector((0.0, 1.0, 0.0))
    right = rotation @ Vector((1.0, 0.0, 0.0))
    focus = location + forward

    if getattr(camera, PROP_BCF_HAS_COMPONENT_SELECTION):
        predicates.append("bcfOWL:hasSelectedComponent " + ", ".join(component_terms))
    if getattr(camera, PROP_BCF_HAS_COLORING):
        predicates.append(
            "bcfOWL:hasColoring [\n"
            "        a bcfOWL:Coloring ;\n"
            "        bcfOWL:hasColor \"80FF0000\" ;\n"
            f"        bcfOWL:hasComponent {', '.join(component_terms)}\n"
            "    ]"
        )
    if getattr(camera, PROP_BCF_HAS_LINE):
        predicates.append(
            "bcfOWL:hasLine [\n"
            "        a bcfOWL:Line ;\n"
            f"        bcfOWL:hasStartPoint {vector_literal(focus, 'point3D', precision)} ;\n"
            f"        bcfOWL:hasEndPoint {vector_literal(focus + right, 'point3D', precision)}\n"
            "    ]"
        )
    if getattr(camera, PROP_BCF_HAS_CLIPPING_PLANE):
        predicates.append(
            "bcfOWL:hasClippingPlane [\n"
            "        a bcfOWL:ClippingPlane ;\n"
            f"        bcfOWL:hasLocation {vector_literal(focus, 'point3D', precision)} ;\n"
            f"        bcfOWL:hasDirection {vector_literal(forward, 'vector3D', precision)}\n"
            "    ]"
        )
    if getattr(camera, PROP_BCF_HAS_BITMAP):
        predicates.append(
            "bcfOWL:hasBitmap [\n"
            "        a bcfOWL:Bitmap ;\n"
            f"        bcfOWL:hasBitmapImage {snapshot} ;\n"
            f"        bcfOWL:hasLocation {vector_literal(focus, 'point3D', precision)} ;\n"
            f"        bcfOWL:hasNormal {vector_literal(forward, 'vector3D', precision)} ;\n"
            f"        bcfOWL:hasUpVector {vector_literal(up, 'vector3D', precision)} ;\n"
            f"        bcfOWL:hasHeight {double_literal(0.6, precision)}\n"
            "    ]"
        )
    return predicates


def issue_blocks(
    helper: bpy.types.Object,
    camera: bpy.types.Object,
    registry,
    local_prefix: str,
    precision: int,
) -> list[str]:
    topic, viewpoint = issue_resources(camera)
    space_suffix = registry.export_suffixes[helper.name]
    capture = f"{local_prefix}:capture_{space_suffix}"
    snapshot = f"{local_prefix}:snapshot_{space_suffix}"
    pose = f"{local_prefix}:{space_suffix}"
    components = component_references(camera, registry)
    validate_issue(camera, components)
    component_terms = [component for component, _helper, _source in components]
    current_status = (
        "bcf:status_in_progress"
        if getattr(camera, PROP_BCF_HAS_STATUS_HISTORY)
        else "bcf:status_open"
    )

    topic_predicates = [
        "a bcfOWL:Topic",
        f"dcterms:identifier {quote_turtle_string(getattr(camera, PROP_BCF_TOPIC_GUID))}",
        f"dcterms:title {quote_turtle_string(getattr(camera, PROP_BCF_TITLE))}",
        f"dcterms:description {quote_turtle_string(getattr(camera, PROP_BCF_DESCRIPTION))}",
        "dcterms:creator bcf:test_author",
        f"dcterms:created {date_time_literal(getattr(camera, PROP_BCF_CREATED))}",
        "bcfOWL:hasProject bcf:project",
        f"bcfOWL:hasTopicStatus {current_status}",
        "bcfOWL:hasTopicType bcf:type_defect",
        "bcfOWL:hasPriority bcf:priority_medium",
        "bcfOWL:hasLabels bcf:label_photo",
        "bcfOWL:hasStage bcf:stage_existing",
        f"bcfOWL:hasReferenceLink {pose}",
        f"schema:about {pose}",
    ]
    viewpoint_predicates = [
        "a bcfOWL:Viewpoint",
        f"dcterms:identifier {quote_turtle_string(getattr(camera, PROP_BCF_VIEWPOINT_GUID))}",
        "dcterms:creator bcf:test_author",
        f"dcterms:created {date_time_literal(getattr(camera, PROP_BCF_CREATED))}",
        "bcfOWL:hasProject bcf:project",
        f"bcfOWL:hasTopic {topic}",
        f"bcfOWL:hasPerspectiveCamera {capture}",
        f"bcfOWL:hasSnapshot {snapshot}",
        "bcfOWL:hasDefaultVisibility true",
        "bcfOWL:spacesVisible true",
        "bcfOWL:spaceBoundariesVisible false",
        "bcfOWL:openingsVisible true",
    ]
    viewpoint_predicates.extend(
        visual_predicates(camera, snapshot, component_terms, precision)
    )

    blocks = [
        turtle_block(topic, topic_predicates),
        turtle_block(viewpoint, viewpoint_predicates),
    ]
    blocks.extend(component_blocks(components, registry, local_prefix))

    if getattr(camera, PROP_BCF_HAS_COMMENT):
        comment_guid = guid_suffix(getattr(camera, PROP_BCF_COMMENT_GUID))
        blocks.append(
            turtle_block(
                f"bcf:comment_{comment_guid}",
                [
                    "a bcfOWL:Comment",
                    f"dcterms:identifier {quote_turtle_string(getattr(camera, PROP_BCF_COMMENT_GUID))}",
                    "dcterms:creator bcf:test_author",
                    f"dcterms:created {date_time_literal(getattr(camera, PROP_BCF_COMMENT_CREATED))}",
                    f"bcfOWL:hasCommentText {quote_turtle_string(getattr(camera, PROP_BCF_COMMENT_TEXT))}",
                    "bcfOWL:hasProject bcf:project",
                    f"bcfOWL:hasTopic {topic}",
                    f"bcfOWL:hasViewpoint {viewpoint}",
                ],
            )
        )

    if getattr(camera, PROP_BCF_HAS_STATUS_HISTORY):
        seed = guid_suffix(getattr(camera, PROP_BCF_TOPIC_GUID))
        created = date_time_literal(getattr(camera, PROP_BCF_CREATED))
        changed = date_time_literal(getattr(camera, PROP_BCF_HISTORY_CREATED))
        # Numbered by the order the changes happened, never by which one is
        # current. A name like `status_assertion_current_<guid>` stops being
        # true the moment the field is edited again, and the stale name then
        # reads as authoritative to anyone scanning the file. Currentness is
        # carried by `prov:wasInvalidatedBy` alone: assertion 2 has none, so it
        # holds; assertion 1 has one, so it is history. That is the same rule
        # the mutation pipeline applies when it looks for the value to
        # supersede, so seeded and written provenance stay readable the same
        # way.
        blocks.extend(
            [
                turtle_block(
                    f"bcf:status_assertion_2_{seed}",
                    [
                        "a prov:Entity",
                        f"rdf:reifies <<( {topic} bcfOWL:hasTopicStatus bcf:status_in_progress )>>",
                        f"prov:wasGeneratedBy bcf:status_change_2_{seed}",
                        f"prov:wasRevisionOf bcf:status_assertion_1_{seed}",
                    ],
                ),
                turtle_block(
                    f"bcf:status_assertion_1_{seed}",
                    [
                        "a prov:Entity",
                        f"rdf:reifies <<( {topic} bcfOWL:hasTopicStatus bcf:status_open )>>",
                        f"prov:wasGeneratedBy bcf:status_change_1_{seed}",
                        f"prov:wasInvalidatedBy bcf:status_change_2_{seed}",
                    ],
                ),
                turtle_block(
                    f"bcf:status_change_1_{seed}",
                    [
                        "a prov:Activity",
                        "prov:wasAssociatedWith bcf:test_author",
                        f"prov:endedAtTime {created}",
                    ],
                ),
                turtle_block(
                    f"bcf:status_change_2_{seed}",
                    [
                        "a prov:Activity",
                        "prov:wasAssociatedWith bcf:test_author",
                        f"prov:endedAtTime {changed}",
                    ],
                ),
            ]
        )
    return blocks


def export_bcf_fixture(
    helper_collection_name: str = HELPER_COLLECTION_NAME,
    output_path: str | Path | None = None,
    base_iri: str = DEFAULT_BASE_IRI,
    precision: int = 6,
    local_prefix: str = DEFAULT_LOCAL_PREFIX,
) -> dict[str, object] | None:
    if base_iri in {LEGACY_DEFAULT_BASE_IRI, LEGACY_HASH_BASE_IRI}:
        base_iri = DEFAULT_BASE_IRI
    local_prefix = normalize_local_prefix(local_prefix)
    collection = ensure_helper_collection(helper_collection_name)
    roots = helper_root_objects(collection)
    registry = build_export_resource_registry(roots)

    issues: list[tuple[bpy.types.Object, bpy.types.Object]] = []
    for helper in registry.exported_helpers:
        camera = perspective_picture_camera(helper)
        if camera is not None and getattr(camera, PROP_BCF_ISSUE_ENABLED, False):
            issues.append((helper, camera))

    enabled_cameras = {
        obj.name for obj in bpy.data.objects if getattr(obj, PROP_BCF_ISSUE_ENABLED, False)
    }
    exported_cameras = {camera.name for _helper, camera in issues}
    missing = sorted(enabled_cameras - exported_cameras)
    if missing:
        raise RuntimeError(
            "BCF issues require exported image-backed perspective cameras: "
            + ", ".join(missing)
        )
    if not issues:
        return None

    topic_guids = [getattr(camera, PROP_BCF_TOPIC_GUID) for _helper, camera in issues]
    viewpoint_guids = [
        getattr(camera, PROP_BCF_VIEWPOINT_GUID) for _helper, camera in issues
    ]
    if len(set(topic_guids)) != len(topic_guids):
        raise RuntimeError("BCF Topic GUIDs must be unique")
    if len(set(viewpoint_guids)) != len(viewpoint_guids):
        raise RuntimeError("BCF Viewpoint GUIDs must be unique")

    settings = bpy.context.scene.spot_tools_settings
    if not settings.bcf_project_guid or not settings.bcf_project_title:
        raise RuntimeError("BCF project defaults have not been initialized")

    lines = [
        'VERSION "1.2"',
        "",
        f"@prefix {local_prefix}: <{base_iri}> .",
        f"@prefix bcf: <{bcf_base_iri(base_iri)}> .",
        "@prefix bcfOWL: <https://w3id.org/bcfOWL#> .",
        "@prefix dcat: <http://www.w3.org/ns/dcat#> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix foaf: <http://xmlns.com/foaf/0.1/> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix schema: <http://schema.org/> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]
    asset_roots = [
        helper for helper in registry.exported_helpers
        if helper.parent is None
        and helper.name in registry.export_suffixes
        and helper.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET
    ]
    if len(asset_roots) != 1:
        raise RuntimeError("BCF export requires exactly one exported AssetSpace root")
    asset_space = f"{local_prefix}:{registry.export_suffixes[asset_roots[0].name]}"
    lines.extend(project_blocks(settings, asset_space))
    for helper, camera in sorted(issues, key=lambda item: item[0].name):
        lines.extend(issue_blocks(helper, camera, registry, local_prefix, precision))

    destination = Path(output_path) if output_path else Path(bpy.data.filepath).with_suffix(".bcf.trig")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n\n".join(lines).rstrip() + "\n", encoding="utf8")
    return {"output_path": str(destination), "issue_count": len(issues)}
