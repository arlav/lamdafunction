# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2, S7: two layers, a boundary policy per side, painted face inputs and ``wfc_thickness``.

Through the add-on's operators on the 3 x 3 grid cube of ``test_surface.py``:

- **Two layers, closed differently.** Thickness 1 in two layers, the top
  closed by "0" and the bottom by "t": the compiled boundary lists both
  connectors, every inner cell's -Z and every outer cell's +Z is listed under
  its own class, and the solve validates (the validator checks boundary
  classes), so the pipe ends turn inward through the bottom.
- **Painted face inputs.** A face attribute ``wfc_w:cap`` of 0 on the +X side
  keeps caps off those faces in both layers; a face ``wfc_fix`` puts the cap
  on one face in both layers.
- **Thickness per vertex.** A point attribute ``wfc_thickness`` of 0.5 halves
  every cell's height.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipes_kit  # noqa: E402
import test_surface  # noqa: E402

N = 3


def paint_face(obj: bpy.types.Object, name: str, data_type: str, values: np.ndarray) -> None:
    """Write a face-domain attribute on the envelope's mesh."""
    attr = obj.data.attributes.get(name) or obj.data.attributes.new(name, data_type, "FACE")
    attr.data.foreach_set("value", values)
    obj.data.update()


def heights(assembly: bpy.types.Object) -> np.ndarray:
    """Per slot, the length of the cell's first vertical edge (corner 0 to corner 4)."""
    from addon.bl import results

    return np.linalg.norm(results.read_attribute(assembly, "wfc_c4") - results.read_attribute(assembly, "wfc_c0"), axis=1)


def main() -> None:
    """Run the layered scene through the add-on."""
    pipes_kit.setup_paths()
    pipes_kit.register_addon()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pipes_kit.build_scene()
    from addon.bl import results

    props = bpy.context.scene.lamdafunction
    cube = test_surface.grid_cube(N)
    props.envelope = cube
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 1.0
    props.surface_layers = 2
    props.closed = True
    props.closed_by = "0"
    props.boundary_bottom = "CLOSED"
    props.boundary_bottom_by = "t"

    # Painted inputs: no caps on the +X side; the cap fixed on the first face of the -X side.
    faces = len(cube.data.polygons)
    normal = np.empty(faces * 3, dtype=np.float32)
    cube.data.polygons.foreach_get("normal", normal)
    plus_x = normal.reshape(-1, 3)[:, 0] > 0.5
    paint_face(cube, "wfc_w:cap", "FLOAT", np.where(plus_x, 0.0, 1.0).astype(np.float32))
    names = list(pipes_kit.MODULES)
    fixed_face = int(np.nonzero(normal.reshape(-1, 3)[:, 0] < -0.5)[0][0])
    fix = np.full(faces, -1, dtype=np.int32)
    fix[fixed_face] = names.index("cap")
    paint_face(cube, "wfc_fix", "INT", fix)

    assert bpy.ops.lamdafunction.compile() == {"FINISHED"}
    doc = json.loads(bpy.data.texts["lamdafunction.assembly.json"].as_string())
    cells = 6 * N * N
    assert doc["slots"] == 2 * cells, doc["slots"]
    connector = {i: c["connector"] for i, c in enumerate(doc["boundary_classes"])}
    by_side = {(b["slot"] < cells, b["port"]): connector[b["class"]] for b in doc["boundary"]}
    assert set(by_side) == {(True, 5), (False, 4)}, set(by_side)  # inner -Z, outer +Z; no rim on a closed cube
    assert by_side[(True, 5)] == "t" and by_side[(False, 4)] == "0"
    assert sum(1 for b in doc["boundary"] if connector[b["class"]] == "t") == cells

    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    assembly = bpy.data.objects[props.assembly_object]
    assert json.loads(assembly["lamdafunction_violations"]) == []
    module = results.read_attribute(assembly, "wfc_module")
    face = results.read_attribute(assembly, "wfc_face")
    layer = results.read_attribute(assembly, "wfc_layer")
    assert sorted(layer.tolist()) == [0] * cells + [1] * cells
    cap = names.index("cap")
    assert not (module[plus_x[face]] == cap).any()
    assert (module[face == fixed_face] == cap).all() and (face == fixed_face).sum() == 2
    full = heights(assembly)
    print(f"surface layers: {len(module)} slots, bottom closed by t, {int((module == cap).sum())} caps")

    # Half the thickness at every vertex: every cell is half as tall.
    attr = cube.data.attributes.new("wfc_thickness", "FLOAT", "POINT")
    attr.data.foreach_set("value", np.full(len(cube.data.vertices), 0.5, dtype=np.float32))
    cube.data.update()
    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    assembly = bpy.data.objects[props.assembly_object]
    half = heights(assembly)
    order = np.argsort(results.read_attribute(assembly, "wfc_face") + cells * results.read_attribute(assembly, "wfc_layer"))
    assert np.allclose(half[order], full[np.argsort(face + cells * layer)] / 2.0, atol=1e-6)
    print("surface layers: wfc_thickness 0.5 halves every cell")

    # Free bottom: the "t" class disappears.
    props.boundary_bottom = "FREE"
    assert bpy.ops.lamdafunction.compile() == {"FINISHED"}
    doc = json.loads(bpy.data.texts["lamdafunction.assembly.json"].as_string())
    assert {c["connector"] for c in doc["boundary_classes"]} == {"0"}
    assert len(doc["boundary"]) == cells
    print("surface layers: pass")


if __name__ == "__main__":
    main()
