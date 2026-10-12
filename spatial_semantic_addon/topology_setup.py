"""
Set the BOT or BROT class of a helper's referent by hand.

An imported graph often leaves referents unclassed, and a Blender scene
cannot say that a collection is a bridge superstructure. The SPOT Setup panel
calls `set_helper_topology_class` for each selected helper.
"""

from __future__ import annotations

import json

import bpy

from .spot_blender_common import (
    BOT_CLASS_BUILDING,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_SITE,
    BOT_CLASS_SPACE,
    BOT_CLASS_STOREY,
    BOT_CLASS_ZONE,
    BROT_CLASSES,
    NAME_TAG_BEO,
    NAME_TAG_BOT,
    NAME_TAG_BROT,
    NAME_TAG_ORDER,
    PROP_AM_REFERENCE_HELPER,
    PROP_BEO_CLASS,
    PROP_BOT_CLASS,
    PROP_BROT_CLASS,
    PROP_DOT_CLASS,
    PROP_HELPER,
    PROP_SOURCE_COLLECTION,
    PROP_SOURCE_COLLECTIONS,
    PROP_SOURCE_NAME,
    represents_link_owner,
    split_name_and_tags,
)

BOT_SETUP_CLASSES = (
    BOT_CLASS_SITE,
    BOT_CLASS_BUILDING,
    BOT_CLASS_STOREY,
    BOT_CLASS_SPACE,
    BOT_CLASS_ELEMENT,
    BOT_CLASS_ZONE,
)
BROT_SETUP_CLASSES = BROT_CLASSES

VOCABULARY_BOT = "BOT"
VOCABULARY_BROT = "BROT"
CLASS_PROP_BY_VOCABULARY = {
    VOCABULARY_BOT: PROP_BOT_CLASS,
    VOCABULARY_BROT: PROP_BROT_CLASS,
}
TAG_BY_PROP = {PROP_BOT_CLASS: NAME_TAG_BOT, PROP_BROT_CLASS: NAME_TAG_BROT}
TEST_SLOT_PROPS = ("test_left_helper", "test_right_helper", "test_reference_helper")


def helper_topology_label(helper: bpy.types.Object) -> str:
    classes = [
        f"{prefix}:{helper[prop]}"
        for prefix, prop in (("bot", PROP_BOT_CLASS), ("brot", PROP_BROT_CLASS))
        if helper.get(prop)
    ]
    return " + ".join(classes) or "no entity (SPOT only)"


def retag_name(name: str, tags: dict[str, str | None]) -> str:
    """
    `name` with each tag in `tags` set to its value, or dropped for None.

    Known tags are written in `NAME_TAG_ORDER`, so a `::BROT=` tag that
    replaces a `::BOT=` tag takes its place before `::SPOT=`.
    """
    base_name, current = split_name_and_tags(name)
    merged = dict(current)
    for key, value in tags.items():
        if value:
            merged[key] = value
        else:
            merged.pop(key, None)
    ordered = sorted(
        merged.items(),
        key=lambda item: NAME_TAG_ORDER.index(item[0]),
    )
    if not ordered:
        return base_name
    return "::".join([base_name, *(f"{key}={value}" for key, value in ordered)])


def rename_id(
    owner: bpy.types.Object | bpy.types.Collection,
    new_name: str,
) -> None:
    """
    Rename a helper or its source and keep every name reference to it.

    Helpers name their source in custom properties, SPOT-AM sources name
    their reference object, and the SPOT Test slots name helpers.
    """
    old_name = owner.name
    owner.name = new_name
    new_name = owner.name
    if new_name == old_name:
        return
    for obj in bpy.data.objects:
        if obj.get(PROP_AM_REFERENCE_HELPER) == old_name:
            obj[PROP_AM_REFERENCE_HELPER] = new_name
        if not obj.get(PROP_HELPER):
            continue
        for prop in (PROP_SOURCE_NAME, PROP_SOURCE_COLLECTION):
            if obj.get(prop) == old_name:
                obj[prop] = new_name
        collections = obj.get(PROP_SOURCE_COLLECTIONS)
        if collections:
            names = json.loads(str(collections))
            if old_name in names:
                obj[PROP_SOURCE_COLLECTIONS] = json.dumps(
                    [new_name if name == old_name else name for name in names]
                )
    for scene in bpy.data.scenes:
        settings = getattr(scene, "spot_tools_settings", None)
        if settings is None:
            continue
        for slot in TEST_SLOT_PROPS:
            if getattr(settings, slot, "") == old_name:
                setattr(settings, slot, new_name)


def set_helper_topology_class(
    helper: bpy.types.Object,
    vocabulary: str | None,
    class_name: str | None,
) -> None:
    """
    Set the one class of the referent a helper represents, or clear it.

    A referent is classed in one vocabulary: setting a BOT class drops the
    BROT class and the other way round, and neither implies the other. With
    no class, the space represents no entity. The tags on the helper's name
    follow, and so do the tags on its source object or collection, which a
    rebuild of the helper scene reads back. An imported helper has no source.
    """
    if class_name and vocabulary not in CLASS_PROP_BY_VOCABULARY:
        raise ValueError(f"Unknown vocabulary '{vocabulary}'")
    if helper.get(PROP_DOT_CLASS) and class_name:
        raise ValueError(
            f"'{helper.name}' stands for damage, which is not part of the topology"
        )
    values = {
        prop: class_name if name == vocabulary else None
        for name, prop in CLASS_PROP_BY_VOCABULARY.items()
    }
    for prop, value in values.items():
        if value:
            helper[prop] = value
        elif prop in helper:
            del helper[prop]
    if helper.get(PROP_BOT_CLASS) != BOT_CLASS_ELEMENT and PROP_BEO_CLASS in helper:
        del helper[PROP_BEO_CLASS]

    tags = {TAG_BY_PROP[prop]: value for prop, value in values.items()}
    if not helper.get(PROP_BEO_CLASS):
        tags[NAME_TAG_BEO] = None
    source = represents_link_owner(helper)
    for owner in (helper,) if source is helper else (source, helper):
        new_name = retag_name(owner.name, tags)
        if new_name != owner.name:
            rename_id(owner, new_name)

