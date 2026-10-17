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
Without ``--blender`` the ``BLENDER`` environment variable is used, then
``blender`` on ``PATH``, then the standard install folders of each platform
(``/Applications/Blender*.app`` on macOS, ``Program Files\\Blender Foundation``
on Windows, ``/opt``, ``/usr/bin`` and snap on Linux). When several versions are
installed the newest wins, so a 5.2 LTS next to a 4.3 is picked automatically.
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


def installed_blenders() -> list[Path]:
    """Blender binaries in the usual install folders of this platform, any order."""
    patterns: list[str]
    if sys.platform == "darwin":
        patterns = ["/Applications/Blender*.app/Contents/MacOS/Blender", "~/Applications/Blender*.app/Contents/MacOS/Blender"]
    elif sys.platform.startswith("win"):
        patterns = [
            r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
            r"~\AppData\Local\Programs\Blender Foundation\Blender*\blender.exe",
        ]
    else:
        patterns = ["/usr/bin/blender", "/usr/local/bin/blender", "/opt/blender*/blender", "/snap/bin/blender", "~/blender*/blender"]
    found: list[Path] = []
    for pattern in patterns:
        expanded = Path(pattern).expanduser()
        # ``Path.glob`` needs an anchor and a relative pattern; split the first part off.
        anchor = Path(expanded.anchor)
        found.extend(p for p in anchor.glob(str(expanded.relative_to(anchor))) if p.is_file())
    return found


def blender_version(binary: Path) -> tuple[int, ...]:
    """The version ``blender --version`` reports, or ``()`` when it cannot be run."""
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=60, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"Blender (\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(g) for g in match.groups()) if match else ()


def find_blender(explicit: str | None) -> str:
    """The Blender binary to run: explicit, ``BLENDER``, ``PATH``, then the newest installed."""
    for candidate in (explicit, os.environ.get("BLENDER"), shutil.which("blender")):
        if candidate and Path(candidate).exists():
            return candidate
    installed = installed_blenders()
    if not installed:
        sys.exit("blender not found: pass --blender or set BLENDER")
    versions = {binary: blender_version(binary) for binary in installed}
    newest = max(installed, key=lambda b: (versions[b], str(b)))
    print(f"using Blender {'.'.join(map(str, versions[newest])) or '?'} at {newest}")
    return str(newest)


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
