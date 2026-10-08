from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parent


def find_blender() -> str:
    configured = os.environ.get("BLENDER_EXECUTABLE")
    if configured:
        executable = Path(configured).expanduser()
        if executable.is_file():
            return str(executable)
        raise SystemExit(f"BLENDER_EXECUTABLE does not exist: {executable}")

    discovered = shutil.which("blender")
    if discovered:
        return discovered

    macos_executable = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    if macos_executable.is_file():
        return str(macos_executable)

    raise SystemExit(
        "Blender was not found. Put blender on PATH or set BLENDER_EXECUTABLE."
    )


def main() -> None:
    blender = find_blender()
    tests = [
        TOOL_ROOT / "tests" / "test_import_spot_ttl_blender.py",
        TOOL_ROOT / "tests" / "test_bcf_fixture_blender.py",
        TOOL_ROOT / "tests" / "test_build_spot_scene_blender.py",
    ]

    for test_path in tests:
        subprocess.run(
            [
                blender,
                "--background",
                "--factory-startup",
                "--python",
                str(test_path),
            ],
            cwd=TOOL_ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
