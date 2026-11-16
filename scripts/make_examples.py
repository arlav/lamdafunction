# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the example .blend files headless.

Usage, from the repository root::

    blender -b --factory-startup --python-exit-code 1 --python scripts/make_examples.py

Writes two files; opening either in Blender with the extension installed shows
the result, and the LamdaFunction tab re-solves it.

- ``examples/pipes.blend``: the pipes kit, a closed 4 x 3 x 2 envelope,
  compiled and solved with seed 1, materialised.
- ``examples/surface-pipes.blend``: the same kit wrapped onto a quad sphere in
  Surface mode (a cube with Subdivision Surface and Cast modifiers, so the
  envelope stays editable), one layer, deformed to the cells.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "blender"))
import pipes_kit  # noqa: E402


SURFACE_SEED = 2
SURFACE_LEVELS = 3  # Subdivision Surface levels: 8 x 8 quads per cube side, 384 cells
SURFACE_SIZE = 8.0  # cube size before Cast; the sphere's quads come out about 0.7 across


def pipes() -> None:
    """The box example: build, solve, materialise, save."""
    pipes_kit.build_scene()
    bpy.ops.lamdafunction.compile()
    bpy.ops.lamdafunction.solve()
    bpy.ops.lamdafunction.materialise()
    save("pipes.blend")


def surface_pipes() -> None:
    """The surface example: the pipes kit on a quad sphere, deformed to the cells."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pipes_kit.build_scene(seed=SURFACE_SEED)
    scene = bpy.context.scene
    props = scene.lamdafunction
    box = bpy.data.objects[props.envelope.name]
    box.hide_set(True)
    box.hide_render = True
    bpy.ops.mesh.primitive_cube_add(size=SURFACE_SIZE)
    sphere = bpy.context.active_object
    sphere.name = "Quad Sphere"
    subdivision = sphere.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = subdivision.render_levels = SURFACE_LEVELS
    cast = sphere.modifiers.new("Cast", "CAST")
    cast.factor = 1.0
    sphere.display_type = "WIRE"
    sphere.hide_render = True
    props.envelope = sphere
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 0.7
    props.surface_layers = 1
    props.deform_to_cells = True
    bpy.ops.lamdafunction.compile()
    bpy.ops.lamdafunction.solve()
    if props.status != "Solved":
        raise SystemExit(f"surface-pipes did not solve: {props.status}")
    bpy.ops.lamdafunction.materialise()
    save("surface-pipes.blend")


def save(name: str) -> None:
    """Save the current file into ``examples/``."""
    out = ROOT / "examples" / name
    out.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    print(f"wrote {out}")


def main() -> None:
    """Build both example files."""
    pipes_kit.setup_paths()
    pipes_kit.register_addon()
    pipes()
    surface_pipes()


if __name__ == "__main__":
    main()
