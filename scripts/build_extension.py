# SPDX-License-Identifier: MIT OR Apache-2.0
"""Build the Blender extension zips.

Steps (``.claude/rules/blender-addon.md``, "Packaging"):

1. Copy ``reference/`` into ``addon/vendor/lamda_reference/`` (gitignored), so
   the shipped extension can solve without the native wheel.
2. Check that every wheel the manifest lists is present in ``addon/wheels/``
   (they come unmodified from ``maturin build --release``).
3. Run ``blender --command extension build --split-platforms --source-dir addon
   --output-dir dist``.

Usage: ``python scripts/build_extension.py [--blender /path/to/blender]``.
Without ``--blender`` the ``BLENDER`` environment variable or ``blender`` on
``PATH`` is used.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon"
VENDOR = ADDON / "vendor"


def vendor_reference() -> None:
    """Copy the reference solver into the add-on, without tests or caches."""
    target = VENDOR / "lamda_reference"
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    (VENDOR).mkdir(parents=True)
    (VENDOR / "__init__.py").write_text(
        "# SPDX-License-Identifier: MIT OR Apache-2.0\n"
        '"""Vendored copies made by scripts/build_extension.py; do not edit."""\n',
        encoding="utf-8",
    )
    shutil.copytree(
        ROOT / "reference",
        target,
        ignore=shutil.ignore_patterns("test_*.py", "__pycache__", "*.pyc"),
    )
    print(f"vendored reference/ into {target.relative_to(ROOT)}")


def listed_wheels() -> list[str]:
    """Wheel paths from the manifest."""
    manifest = (ADDON / "blender_manifest.toml").read_text(encoding="utf-8")
    block = re.search(r"wheels\s*=\s*\[(.*?)\]", manifest, re.S)
    if block is None:
        return []
    return re.findall(r'"([^"]+)"', block.group(1))


def check_wheels() -> None:
    """Every listed wheel must exist; the extension platform rejects a manifest that lies."""
    missing = [w for w in listed_wheels() if not (ADDON / w).exists()]
    if missing:
        sys.exit(f"missing wheels: {', '.join(missing)}; build them with maturin build --release --out addon/wheels")


def find_blender(explicit: str | None) -> str:
    """The Blender binary to run."""
    candidates = [explicit, os.environ.get("BLENDER"), shutil.which("blender")]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("blender not found: pass --blender or set BLENDER")


def main() -> None:
    """Vendor, check, build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", help="path to the Blender binary")
    parser.add_argument("--output", default=str(ROOT / "dist"), help="output directory for the zips")
    args = parser.parse_args()
    vendor_reference()
    check_wheels()
    blender = find_blender(args.blender)
    Path(args.output).mkdir(parents=True, exist_ok=True)
    command = [
        blender,
        "--command",
        "extension",
        "build",
        "--split-platforms",
        "--source-dir",
        str(ADDON),
        "--output-dir",
        args.output,
    ]
    print(" ".join(command))
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
