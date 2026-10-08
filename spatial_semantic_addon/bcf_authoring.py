from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty

from .spot_blender_common import (
    BOT_CLASS_ELEMENT,
    HELPER_COLLECTION_MARKER,
    PROP_BOT_CLASS,
    PROP_DOT_CLASS,
    PROP_HELPER,
    PROP_SOURCE_ASSET_PATH,
    PROP_SOURCE_NAME,
    PROP_SPOT_CLASS,
    find_helper_for_source_object,
    find_source_object_for_helper,
    parse_name_semantics,
)


PROP_BCF_ISSUE_ENABLED = "spot_bcf_issue_enabled"
PROP_BCF_TOPIC_GUID = "spot_bcf_topic_guid"
PROP_BCF_VIEWPOINT_GUID = "spot_bcf_viewpoint_guid"
PROP_BCF_CREATED = "spot_bcf_created"
PROP_BCF_TITLE = "spot_bcf_title"
PROP_BCF_DESCRIPTION = "spot_bcf_description"
PROP_BCF_HAS_COMMENT = "spot_bcf_has_comment"
PROP_BCF_COMMENT_GUID = "spot_bcf_comment_guid"
PROP_BCF_COMMENT_CREATED = "spot_bcf_comment_created"
PROP_BCF_COMMENT_TEXT = "spot_bcf_comment_text"
PROP_BCF_HAS_STATUS_HISTORY = "spot_bcf_has_status_history"
PROP_BCF_HISTORY_CREATED = "spot_bcf_history_created"
PROP_BCF_HAS_COMPONENT_SELECTION = "spot_bcf_has_component_selection"
PROP_BCF_HAS_COLORING = "spot_bcf_has_coloring"
PROP_BCF_HAS_CLIPPING_PLANE = "spot_bcf_has_clipping_plane"
PROP_BCF_HAS_LINE = "spot_bcf_has_line"
PROP_BCF_HAS_BITMAP = "spot_bcf_has_bitmap"
PROP_OBSERVED_DAMAGES = "spot_observed_damages"
PROP_OBSERVED_DAMAGE_INDEX = "spot_observed_damage_index"
PROP_BCF_COMPONENTS = "spot_bcf_components"
PROP_BCF_COMPONENT_INDEX = "spot_bcf_component_index"


COMMENT_TEMPLATES = (
    "Please inspect the photographed condition and record the findings.",
    "Confirm the affected component and document the repair boundary.",
    "Take comparison measurements during the next site inspection.",
    "Review the visible damage and record its extent before remediation.",
)


def utc_now_literal() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def stable_template(values: tuple[str, ...], seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf8")).digest()
    return values[int.from_bytes(digest[:4], "big") % len(values)]


def normalize_reference_object(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    if obj is None:
        return None
    if obj.get(PROP_HELPER):
        return find_source_object_for_helper(obj) or obj
    return obj


def helper_for_reference(
    obj: bpy.types.Object | None,
    helper_collection: bpy.types.Collection | None = None,
) -> bpy.types.Object | None:
    if obj is None:
        return None
    if obj.get(PROP_HELPER):
        if helper_collection is None or helper_collection.objects.get(obj.name) is not None:
            return obj
        return None
    return find_helper_for_source_object(obj, helper_collection)


def helper_for_exported_reference(
    obj: bpy.types.Object | None,
    exported_helper_names: set[str],
) -> bpy.types.Object | None:
    if obj is None:
        return None
    if obj.get(PROP_HELPER):
        return obj if obj.name in exported_helper_names else None
    for helper_name in sorted(exported_helper_names):
        helper = bpy.data.objects.get(helper_name)
        if helper is not None and find_source_object_for_helper(helper) == obj:
            return helper
    return None


def object_dot_class(obj: bpy.types.Object | None) -> str | None:
    if obj is None:
        return None
    explicit = obj.get(PROP_DOT_CLASS)
    if explicit:
        return str(explicit)
    helper = helper_for_reference(obj)
    if helper is not None:
        explicit = helper.get(PROP_DOT_CLASS)
        if explicit:
            return str(explicit)
        semantic_name = str(helper.get(PROP_SOURCE_NAME, helper.name))
    else:
        semantic_name = obj.name
    return parse_name_semantics(semantic_name).dot_class


def is_damage_object(_owner: object, obj: bpy.types.Object) -> bool:
    helper = helper_for_reference(obj)
    return helper is not None and object_dot_class(helper) == "Damage"


def is_component_object(_owner: object, obj: bpy.types.Object) -> bool:
    helper = helper_for_reference(obj)
    return helper is not None and str(helper.get(PROP_BOT_CLASS, "")) == BOT_CLASS_ELEMENT


def resolve_picture_owner_and_helper(
    context: bpy.types.Context,
    helper_collection_name: str,
) -> tuple[bpy.types.Object | None, bpy.types.Object | None, str | None]:
    active = context.active_object
    if active is None:
        return None, None, "Select an image-backed perspective camera or its PointSpace helper"

    collection = bpy.data.collections.get(helper_collection_name)
    if collection is None or not collection.get(HELPER_COLLECTION_MARKER):
        return None, None, f"SPOT helper collection '{helper_collection_name}' was not found"

    helper = helper_for_reference(active, collection)
    owner = normalize_reference_object(active)
    if helper is None or owner is None:
        return None, None, "The selected object has no generated SPOT helper"
    if helper.get(PROP_SPOT_CLASS) != "PointSpace":
        return None, None, "The selected object is not a picture PointSpace"
    if not helper.get(PROP_SOURCE_ASSET_PATH):
        return None, None, "The selected picture has no image asset"
    if owner.type != "CAMERA" or getattr(owner, "data", None) is None:
        return None, None, "The selected picture is not backed by a Blender camera"
    if owner.data.type != "PERSP":
        return None, None, "Only perspective picture cameras can become BCF issues"
    return owner, helper, None


def initialize_project(scene: bpy.types.Scene) -> None:
    settings = scene.spot_tools_settings
    if not settings.bcf_project_guid:
        settings.bcf_project_guid = str(uuid.uuid4())
    if not settings.bcf_project_title:
        blend_name = Path(bpy.data.filepath).stem or "Untitled scene"
        settings.bcf_project_title = f"{blend_name} photographic test project"
    if not settings.bcf_author_name:
        settings.bcf_author_name = "Blender Test Author"
    if not settings.bcf_author_identifier:
        settings.bcf_author_identifier = "blender-test-author@example.invalid"


def initialize_issue(owner: bpy.types.Object, scene: bpy.types.Scene) -> None:
    initialize_project(scene)
    if not getattr(owner, PROP_BCF_TOPIC_GUID):
        setattr(owner, PROP_BCF_TOPIC_GUID, str(uuid.uuid4()))
    if not getattr(owner, PROP_BCF_VIEWPOINT_GUID):
        setattr(owner, PROP_BCF_VIEWPOINT_GUID, str(uuid.uuid4()))
    if not getattr(owner, PROP_BCF_CREATED):
        setattr(owner, PROP_BCF_CREATED, utc_now_literal())
    if not getattr(owner, PROP_BCF_TITLE):
        setattr(owner, PROP_BCF_TITLE, f"Inspect {owner.name}")
    if not getattr(owner, PROP_BCF_DESCRIPTION):
        setattr(
            owner,
            PROP_BCF_DESCRIPTION,
            f"Synthetic BCF test issue generated for the photograph {owner.name}.",
        )


def initialize_comment(owner: bpy.types.Object) -> None:
    if not getattr(owner, PROP_BCF_COMMENT_GUID):
        setattr(owner, PROP_BCF_COMMENT_GUID, str(uuid.uuid4()))
    if not getattr(owner, PROP_BCF_COMMENT_CREATED):
        setattr(owner, PROP_BCF_COMMENT_CREATED, utc_now_literal())
    if not getattr(owner, PROP_BCF_COMMENT_TEXT):
        seed = getattr(owner, PROP_BCF_TOPIC_GUID) or owner.name
        setattr(owner, PROP_BCF_COMMENT_TEXT, stable_template(COMMENT_TEMPLATES, seed))


def issue_enabled_update(owner: bpy.types.Object, context: bpy.types.Context) -> None:
    if getattr(owner, PROP_BCF_ISSUE_ENABLED, False):
        initialize_issue(owner, context.scene)


def comment_enabled_update(owner: bpy.types.Object, _context: bpy.types.Context) -> None:
    if getattr(owner, PROP_BCF_HAS_COMMENT, False):
        initialize_comment(owner)


def history_enabled_update(owner: bpy.types.Object, _context: bpy.types.Context) -> None:
    if getattr(owner, PROP_BCF_HAS_STATUS_HISTORY, False) and not getattr(
        owner, PROP_BCF_HISTORY_CREATED
    ):
        setattr(owner, PROP_BCF_HISTORY_CREATED, utc_now_literal())


class SPOT_PG_object_reference(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)


def reference_objects(owner: bpy.types.Object, property_name: str) -> list[bpy.types.Object]:
    return [entry.object for entry in getattr(owner, property_name) if entry.object is not None]


OBJECT_PROPERTY_NAMES = (
    PROP_BCF_ISSUE_ENABLED,
    PROP_BCF_TOPIC_GUID,
    PROP_BCF_VIEWPOINT_GUID,
    PROP_BCF_CREATED,
    PROP_BCF_TITLE,
    PROP_BCF_DESCRIPTION,
    PROP_BCF_HAS_COMMENT,
    PROP_BCF_COMMENT_GUID,
    PROP_BCF_COMMENT_CREATED,
    PROP_BCF_COMMENT_TEXT,
    PROP_BCF_HAS_STATUS_HISTORY,
    PROP_BCF_HISTORY_CREATED,
    PROP_BCF_HAS_COMPONENT_SELECTION,
    PROP_BCF_HAS_COLORING,
    PROP_BCF_HAS_CLIPPING_PLANE,
    PROP_BCF_HAS_LINE,
    PROP_BCF_HAS_BITMAP,
    PROP_OBSERVED_DAMAGES,
    PROP_OBSERVED_DAMAGE_INDEX,
    PROP_BCF_COMPONENTS,
    PROP_BCF_COMPONENT_INDEX,
)


def register_object_properties() -> None:
    unregister_object_properties()
    setattr(
        bpy.types.Object,
        PROP_BCF_ISSUE_ENABLED,
        BoolProperty(name="Make it an issue", default=False, update=issue_enabled_update),
    )
    for name, label in (
        (PROP_BCF_TOPIC_GUID, "BCF Topic GUID"),
        (PROP_BCF_VIEWPOINT_GUID, "BCF Viewpoint GUID"),
        (PROP_BCF_CREATED, "BCF Created"),
        (PROP_BCF_COMMENT_GUID, "BCF Comment GUID"),
        (PROP_BCF_COMMENT_CREATED, "Comment Created"),
        (PROP_BCF_HISTORY_CREATED, "Status Change Created"),
    ):
        setattr(bpy.types.Object, name, StringProperty(name=label, default=""))
    setattr(bpy.types.Object, PROP_BCF_TITLE, StringProperty(name="Title", default=""))
    setattr(
        bpy.types.Object,
        PROP_BCF_DESCRIPTION,
        StringProperty(name="Description", default=""),
    )
    setattr(
        bpy.types.Object,
        PROP_BCF_HAS_COMMENT,
        BoolProperty(name="Has Comment", default=False, update=comment_enabled_update),
    )
    setattr(
        bpy.types.Object,
        PROP_BCF_COMMENT_TEXT,
        StringProperty(name="Comment", default=""),
    )
    setattr(
        bpy.types.Object,
        PROP_BCF_HAS_STATUS_HISTORY,
        BoolProperty(name="Has Status History", default=False, update=history_enabled_update),
    )
    for name, label in (
        (PROP_BCF_HAS_COMPONENT_SELECTION, "Has Component Selection"),
        (PROP_BCF_HAS_COLORING, "Has Coloring"),
        (PROP_BCF_HAS_CLIPPING_PLANE, "Has Clipping Plane"),
        (PROP_BCF_HAS_LINE, "Has Line"),
        (PROP_BCF_HAS_BITMAP, "Has Bitmap"),
    ):
        setattr(bpy.types.Object, name, BoolProperty(name=label, default=False))
    setattr(
        bpy.types.Object,
        PROP_OBSERVED_DAMAGES,
        CollectionProperty(type=SPOT_PG_object_reference),
    )
    setattr(
        bpy.types.Object,
        PROP_OBSERVED_DAMAGE_INDEX,
        IntProperty(default=0, min=0),
    )
    setattr(
        bpy.types.Object,
        PROP_BCF_COMPONENTS,
        CollectionProperty(type=SPOT_PG_object_reference),
    )
    setattr(
        bpy.types.Object,
        PROP_BCF_COMPONENT_INDEX,
        IntProperty(default=0, min=0),
    )


def unregister_object_properties() -> None:
    for name in OBJECT_PROPERTY_NAMES:
        if hasattr(bpy.types.Object, name):
            delattr(bpy.types.Object, name)
