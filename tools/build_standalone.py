from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _require_module(module_name: str, help_text: str) -> None:
    try:
        __import__(module_name)
    except Exception as exc:
        raise SystemExit(f"{help_text}\nOriginal error: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the standalone market2gnucash desktop app with PyInstaller."
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete local build/dist directories before running PyInstaller.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    spec_path = repo_root / "market2gnucash.spec"
    build_dir = repo_root / "build"
    dist_dir = repo_root / "dist"

    _require_module(
        "PyInstaller",
        "PyInstaller is not installed. Install build deps with: pip install -e '.[build]'",
    )
    _require_module(
        "gnucash",
        (
            "The GnuCash Python bindings are not importable in this build environment. "
            "Build with the same interpreter that can already run 'import gnucash'."
        ),
    )

    if args.clean:
        shutil.rmtree(build_dir, ignore_errors=True)
        shutil.rmtree(dist_dir, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec_path),
    ]
    subprocess.run(command, cwd=repo_root, check=True)

    artifact_dir = dist_dir / "market2gnucash"
    print(f"Standalone build created at: {artifact_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
