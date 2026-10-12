from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import bpy

from .export_bcf_trig import export_bcf_fixture
from .export_spot_ttl import (
    DEFAULT_BASE_IRI,
    DEFAULT_LOCAL_PREFIX,
    EXPORT_PROFILE_SCENE_GRAPH,
    export_helper_scene,
)
from .export_spatial_history import export_spatial_history
from .spot_transition import write_snapshot_mutations
from .spot_blender_common import (
    HELPER_COLLECTION_NAME,
    PROP_HELPER,
    PROP_LABEL,
    PROP_SPOT_CLASS,
    SPOT_CLASS_ASSET,
    helper_root_objects,
    slugify,
)

DATASETS_ROOT_ENV = "SPATIAL_SEMANTIC_DATASETS_DIR"


def _configured_datasets_root() -> str:
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is not None and addon.preferences is not None:
        preferred_root = addon.preferences.datasets_dir.strip()
        if preferred_root:
            return bpy.path.abspath(preferred_root)
    return os.environ.get(DATASETS_ROOT_ENV, "")


def project_slug(
    project_name: str = "",
    helper_collection_name: str = HELPER_COLLECTION_NAME,
) -> str:
    """The folder name a project's exports are written under.

    Each project exports into `exports/<project>/`, so exporting one never
    replaces another's newest snapshot. The name is the explicit project name
    when one is set, else the label of the root AssetSpace, else the blend file.
    """
    if project_name.strip():
        return slugify(project_name)
    collection = bpy.data.collections.get(helper_collection_name)
    if collection is not None:
        for root in helper_root_objects(collection):
            if root.get(PROP_HELPER) and root.get(PROP_SPOT_CLASS) == SPOT_CLASS_ASSET:
                return slugify(str(root.get(PROP_LABEL, root.name)))
    return slugify(Path(bpy.data.filepath).stem or "untitled")


def project_snapshot_destination(
    project: str,
    exports_root: str | Path | None = None,
) -> tuple[Path, Path, str]:
    """A fresh snapshot folder for a project, a temporary folder beside it,
    and the snapshot's timestamp.

    The temporary folder shares the destination's parent, so moving it into
    place with os.replace is atomic.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (
        Path(exports_root).expanduser().resolve()
        if exports_root
        else _default_exports_root()
    )
    project_root = root / project
    project_root.mkdir(parents=True, exist_ok=True)
    destination = _unique_snapshot_path(project_root, timestamp)
    temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=project_root))
    return destination, temporary, timestamp


def _default_exports_root() -> Path:
    configured_root = _configured_datasets_root()
    if configured_root:
        return Path(configured_root).expanduser().resolve() / "exports"

    raise RuntimeError(
        "The datasets directory is not set. Set it in the add-on preferences "
        f"or in the {DATASETS_ROOT_ENV} environment variable."
    )


def _unique_snapshot_path(exports_root: Path, stem: str) -> Path:
    candidate = exports_root / stem
    suffix = 2
    while candidate.exists():
        candidate = exports_root / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def export_dataset_snapshot(
    helper_collection_name: str = HELPER_COLLECTION_NAME,
    exports_root: str | Path | None = None,
    base_iri: str = DEFAULT_BASE_IRI,
    precision: int = 6,
    local_prefix: str = DEFAULT_LOCAL_PREFIX,
    simulate_spatial_history: bool = False,
    spatial_history_start: str = "2025-01-01T09:00:00Z",
    project_name: str = "",
) -> dict[str, object]:
    """Atomically publish scene RDF, any explicitly authored BCF fixture and
    the committed SPOT-AM to SPOT-SG transitions into
    `exports/<project>/<timestamp>/`."""
    if bpy.data.collections.get(helper_collection_name) is None:
        raise RuntimeError(f"Helper collection '{helper_collection_name}' was not found")
    project = project_slug(project_name, helper_collection_name)
    destination, temporary, timestamp = project_snapshot_destination(
        project, exports_root
    )
    captured = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )

    try:
        rdf_dir = temporary / "rdf"
        rdf_dir.mkdir(parents=True)

        scene_path = rdf_dir / "scene.ttl"
        scene_result = export_helper_scene(
            helper_collection_name=helper_collection_name,
            output_path=scene_path,
            base_iri=base_iri,
            local_prefix=local_prefix,
            precision=precision,
            export_profile=EXPORT_PROFILE_SCENE_GRAPH,
            asset_base_url="/assets",
        )
        bcf_result = export_bcf_fixture(
            helper_collection_name=helper_collection_name,
            output_path=rdf_dir / "bcf.trig",
            base_iri=base_iri,
            local_prefix=local_prefix,
            precision=precision,
        )
        transition_result = write_snapshot_mutations(
            rdf_dir / "mutations.trig",
            helper_root_objects(bpy.data.collections[helper_collection_name]),
            str(scene_result["base_iri"]),
        )
        spatial_history_result = None
        if simulate_spatial_history:
            spatial_history_result = export_spatial_history(
                helper_collection_name=helper_collection_name,
                output_path=rdf_dir / "spatial-history.trig",
                base_iri=base_iri,
                local_prefix=local_prefix,
                precision=precision,
                start_time=spatial_history_start,
            )
        # os.replace is atomic because the temporary folder shares the
        # destination parent. Asset bytes remain in datasets/assets.
        os.replace(temporary, destination)
        return {
            "output_path": str(destination),
            "dataset_id": f"{project}/{destination.name}",
            "project": project,
            "timestamp": captured.isoformat(),
            "bcf_issue_count": bcf_result["issue_count"] if bcf_result else 0,
            "transition_activity_count": transition_result["activity_count"],
            "spatial_history_activity_count": (
                spatial_history_result["activity_count"]
                if spatial_history_result
                else 0
            ),
        }
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
