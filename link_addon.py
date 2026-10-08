from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


REPO_ADDON_DIR = Path(__file__).resolve().parent / "spatial_semantic_addon"
ADDON_NAME = REPO_ADDON_DIR.name


def _version_key(path: Path) -> tuple[int, ...]:
    parts: list[int] = []
    for part in path.name.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


def _candidate_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Library" / "Application Support" / "Blender",
        home / ".config" / "blender",
    ]

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Blender Foundation" / "Blender")

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        candidates.append(Path(xdg_config_home) / "blender")

    candidates.append(
        home / ".var" / "app" / "org.blender.Blender" / "config" / "blender"
    )

    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


def _discover_addon_dirs() -> list[Path]:
    addon_dirs: list[Path] = []
    for root in _candidate_roots():
        if not root.is_dir():
            continue
        for version_dir in sorted(
            (path for path in root.iterdir() if path.is_dir()),
            key=_version_key,
            reverse=True,
        ):
            addon_dir = version_dir / "scripts" / "addons"
            if addon_dir.is_dir():
                addon_dirs.append(addon_dir)
    return addon_dirs


def _resolve_targets(version: str | None, all_versions: bool) -> list[Path]:
    addon_dirs = _discover_addon_dirs()
    if version:
        suffix = Path(version) / "scripts" / "addons"
        matches = [
            path for path in addon_dirs if path.as_posix().endswith(suffix.as_posix())
        ]
        if not matches:
            raise SystemExit(
                f"No Blender addon directory found for version {version!r}."
            )
        return matches

    if not addon_dirs:
        raise SystemExit(
            "No Blender addon directories found. Start Blender once so it creates a versioned scripts/addons directory, or pass --version after creating it."
        )

    if all_versions:
        return addon_dirs

    return [addon_dirs[0]]


def _create_symlink(target_dir: Path, force: bool, dry_run: bool) -> None:
    link_path = target_dir / ADDON_NAME
    source_path = REPO_ADDON_DIR

    if link_path.is_symlink():
        current_target = link_path.resolve(strict=False)
        if current_target == source_path:
            print(f"Already linked: {link_path} -> {source_path}")
            return
        if dry_run:
            print(
                f"Would relink: {link_path} -> {source_path} (currently {current_target})"
            )
            return
        link_path.unlink()
        link_path.symlink_to(source_path, target_is_directory=True)
        print(f"Relinked: {link_path} -> {source_path} (was {current_target})")
        return

    if link_path.exists() or link_path.is_symlink():
        if not force:
            raise SystemExit(
                f"Refusing to replace existing path {link_path}. Re-run with --force to replace it."
            )
        if dry_run:
            print(f"Would remove existing path: {link_path}")
        else:
            if link_path.is_dir() and not link_path.is_symlink():
                shutil.rmtree(link_path)
            else:
                link_path.unlink()

    if dry_run:
        print(f"Would link: {link_path} -> {source_path}")
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    link_path.symlink_to(source_path, target_is_directory=True)
    print(f"Linked: {link_path} -> {source_path}")


def _remove_symlink(target_dir: Path, dry_run: bool) -> None:
    link_path = target_dir / ADDON_NAME
    if not link_path.is_symlink():
        print(f"No managed symlink at: {link_path}")
        return

    try:
        resolved = link_path.resolve()
    except FileNotFoundError:
        resolved = None

    if resolved != REPO_ADDON_DIR:
        raise SystemExit(f"Refusing to remove unmanaged symlink: {link_path}")

    if dry_run:
        print(f"Would unlink: {link_path}")
        return

    link_path.unlink()
    print(f"Unlinked: {link_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Symlink the Spatial-Semantic Blender add-on into Blender's scripts/addons directory."
    )
    parser.add_argument(
        "--version", help="Specific Blender version to target, for example 4.2"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Link the addon into every discovered Blender version instead of only the newest one.",
    )
    parser.add_argument(
        "--unlink",
        action="store_true",
        help="Remove the managed addon symlink instead of creating it.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing addon path at the target location.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved action without modifying the filesystem.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not REPO_ADDON_DIR.is_dir():
        raise SystemExit(f"Repo addon directory is missing: {REPO_ADDON_DIR}")

    target_dirs = _resolve_targets(args.version, args.all)
    for target_dir in target_dirs:
        if args.unlink:
            _remove_symlink(target_dir, args.dry_run)
        else:
            _create_symlink(target_dir, args.force, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
