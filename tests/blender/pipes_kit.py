# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the pipes example scene by script: six tube modules, a closed box envelope.

Used by ``test_pipes.py`` and by ``scripts/make_examples.py``. The modules
are the tube kit of the golden fixtures: straight, elbow, tee, cross, cap and
empty, with connector "t" on open faces and "0" on blank ones, full rotation,
and the boundary closed by "0".
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import zipfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]

FACE_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
MODULES = {
    "straight": (0, 1),
    "elbow": (0, 2),
    "tee": (0, 1, 2),
    "cross": (0, 1, 2, 3),
    "cap": (0,),
    "empty": (),
}


def pick_wheel(wheels: list[Path]) -> Path | None:
    """The wheel built for this platform, or None.

    CI puts all four platform wheels in ``addon/wheels``; the tag in the file
    name (``macosx_11_0_arm64``, ``win_amd64``, ``manylinux_2_28_x86_64``) says
    which one the running interpreter can import.
    """
    if sys.platform == "darwin":
        wanted = ("macosx", "arm64" if platform.machine() == "arm64" else "x86_64")
    elif sys.platform.startswith("win"):
        wanted = ("win_amd64",)
    else:
        wanted = ("manylinux", "x86_64")
    matching = [w for w in wheels if all(tag in w.name for tag in wanted)]
    return sorted(matching)[-1] if matching else None


def setup_paths() -> str:
    """Put the repository and the built wheel (if any) on ``sys.path``; returns which solver will load.

    Set ``LAMDA_NO_WHEEL=1`` to skip the wheel and exercise the Python reference fallback.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    wheel = pick_wheel(list((ROOT / "addon" / "wheels").glob("lamda_core-*.whl")))
    if wheel is not None and not os.environ.get("LAMDA_NO_WHEEL"):
        target = Path(tempfile.mkdtemp(prefix="lamda_core_wheel_"))
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)
        sys.path.insert(0, str(target))
        return "wheel"
    return "reference"


def register_addon() -> None:
    """Register the add-on package from the repository, once per process."""
    import addon

    if not hasattr(bpy.types.Scene, "lamdafunction"):
        addon.register()


def tube_mesh(name: str, open_faces: tuple[int, ...], cell: float, radius: float) -> bpy.types.Mesh:
    """A mesh of square-section pipe segments from the cell centre to each open face, plus a core cube."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []

    def box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> None:
        base = len(verts)
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    verts.append((x, y, z))
        for quad in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            faces.append(tuple(base + i for i in quad))  # type: ignore[arg-type]

    r = radius
    box((-r, -r, -r), (r, r, r))
    h = cell / 2.0
    for face in open_faces:
        d = FACE_DIRS[face]
        lo = [-r, -r, -r]
        hi = [r, r, r]
        for axis in range(3):
            if d[axis] > 0:
                lo[axis], hi[axis] = r, h
            elif d[axis] < 0:
                lo[axis], hi[axis] = -h, -r
        box((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2]))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return mesh


def build_scene(size: tuple[int, int, int] = (4, 3, 2), cell: float = 1.0, seed: int = 1) -> None:
    """Library, modules, pairs, envelope and settings in the current scene."""
    from addon.bl import library as library_module
    from addon.bl import envelope as envelope_module

    scene = bpy.context.scene
    props = scene.lamdafunction
    props.cell_size = cell
    library_module.ensure_library(scene)
    for index, (name, open_faces) in enumerate(MODULES.items()):
        collection = library_module.add_module(scene, name, cell, with_cube=False)
        mesh = tube_mesh(name, open_faces, cell, cell * 0.15)
        obj = bpy.data.objects.new(name, mesh)
        obj.location = (index * cell * 2.0, -cell * 3.0, 0.0)
        collection.objects.link(obj)
        collection.instance_offset = obj.location
        module = collection.lamdafunction
        module.freedom = "FULL"
        for face in range(6):
            setattr(module, f"connector_{face}", "t" if face in open_faces else "0")
    for a, b in (("t", "t"), ("0", "0")):
        pair = props.pairs.add()
        pair.a, pair.b = a, b
    props.indifference = False
    props.closed = True
    props.closed_by = "0"
    props.seed = seed
    props.attempts = 4
    props.backtrack_budget = 100
    envelope_module.box_envelope(scene, size, cell)
