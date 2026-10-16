# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the example .blend files headless.

Usage, from the repository root::

    blender -b --factory-startup --python-exit-code 1 --python scripts/make_examples.py

Writes ``examples/pipes.blend``: the pipes kit, a closed 4 x 3 x 2 envelope,
compiled and solved with seed 1, materialised. Opening it in Blender with the
extension installed shows the result; the LamdaFunction tab re-solves it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "blender"))
import pipes_kit  # noqa: E402


def main() -> None:
    """Build, solve, materialise, save."""
    pipes_kit.setup_paths()
    pipes_kit.register_addon()
    pipes_kit.build_scene()
    bpy.ops.lamdafunction.compile()
    bpy.ops.lamdafunction.solve()
    bpy.ops.lamdafunction.materialise()
    out = ROOT / "examples" / "pipes.blend"
    out.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
