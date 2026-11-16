# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2, S5 and S6: the tube kit on a subdivided cube in Surface mode, placed rigidly.

Through the add-on's operators: compile (one cell per quad, schema 1.1,
eight irregular vertices in the audit), solve (valid), the surface attributes
(no ``wfc_cell``; ``wfc_face``, ``wfc_layer``, ``wfc_frame``, ``wfc_scale``,
corners), the field overlay, and Materialise: one instance per slot, at the
cell centre, turned by ``wfc_rotation`` and scaled by ``wfc_scale``. Pins a
hash of the written attributes (floats rounded to 4 decimals) for seed 1,
the same with the wheel and the reference. Then a Subdivision Surface cube
with a UV guide checks that the evaluated mesh is what the shell follows,
and a switch back to Points mode checks that no surface attribute is left.

The cube is built by script with exact coordinates (halves and integers),
not with a modifier, so the pinned hash does not depend on how a platform
rounds subdivision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import bpy
from mathutils import Quaternion, kdtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipes_kit  # noqa: E402

GOLDEN_HASH_SEED_1 = "e5a45c300887cb80"  # seed 1, 3 x 3 quads per side, thickness 1, one layer; both backends
N = 3
GAP_LIMIT = 1e-5  # x cell size, between a pipe end and its partner after Deform to cells (spike 6)

SIDES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
)
"""Per cube side: outward normal d and axes u, v with u x v = d, so quads run counter-clockwise from outside."""


def grid_cube(n: int, name: str = "Surface Cube") -> bpy.types.Object:
    """A cube of side ``n`` centred at the origin with ``n x n`` quads per side, vertices shared, exact coordinates."""
    index: dict[tuple[int, int, int], int] = {}
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int, int]] = []

    def vid(p: tuple[int, int, int]) -> int:
        if p not in index:
            index[p] = len(vertices)
            vertices.append(tuple(c / 2.0 for c in p))  # type: ignore[arg-type]
        return index[p]

    for d, u, v in SIDES:
        # Doubled integer coordinates: the side's centre is d * n, corners step by 2 along u and v.
        def point(i: int, j: int, d=d, u=u, v=v) -> tuple[int, int, int]:
            return tuple(d[k] * n + u[k] * (2 * i - n) + v[k] * (2 * j - n) for k in range(3))  # type: ignore[return-value]

        for i in range(n):
            for j in range(n):
                faces.append((vid(point(i, j)), vid(point(i + 1, j)), vid(point(i + 1, j + 1)), vid(point(i, j + 1))))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def instances_of(assembly: bpy.types.Object) -> list[tuple[np.ndarray, Quaternion, np.ndarray, str]]:
    """Location, rotation, scale and module object name of every instance on the assembly."""
    out = []
    for inst in bpy.context.evaluated_depsgraph_get().object_instances:
        if inst.is_instance and inst.parent is not None and inst.parent.original == assembly:
            loc, rot, scale = inst.matrix_world.decompose()
            out.append((np.array(loc), rot.copy(), np.array(scale), inst.instance_object.original.name))
    return out


def mark_pipe_ends() -> int:
    """Give every module mesh a boolean ``end`` point attribute on the vertices of its pipe ends.

    A pipe end is a vertex on the cell's boundary (a coordinate at ±0.5 of the
    kit's 1-unit cell). Returns how many end vertices the kit has in all.
    """
    total = 0
    for name in pipes_kit.MODULES:
        mesh = bpy.data.objects[name].data
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        ends = (np.abs(co.reshape(-1, 3)) == 0.5).any(axis=1)
        mesh.attributes.new("end", "BOOLEAN", "POINT").data.foreach_set("value", ends)
        mesh.update()
        total += int(ends.sum())
    return total


def deformed_gaps(assembly: bpy.types.Object) -> tuple[int, int, float, set[str]]:
    """Vertices and pipe-end vertices of the deformed, realised mesh, the largest end gap, and its attribute names.

    Each pipe end must meet the end of the neighbouring cell's pipe: the gap of
    an end vertex is the distance to its nearest end vertex other than itself.
    """
    mesh = assembly.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    ends = np.empty(len(mesh.vertices), dtype=np.bool_)
    mesh.attributes["end"].data.foreach_get("value", ends)
    points = co[ends].astype(np.float64)
    tree = kdtree.KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(p, i)
    tree.balance()
    worst = max((tree.find_n(p, 2)[1][2] for p in points), default=0.0)
    return len(co), len(points), worst, set(mesh.attributes.keys())


def main() -> None:
    """Run the surface scene through the add-on."""
    solver = pipes_kit.setup_paths()
    pipes_kit.register_addon()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pipes_kit.build_scene()
    from addon.bl import overlay, results

    scene = bpy.context.scene
    props = scene.lamdafunction
    props.envelope = grid_cube(N)
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 1.0
    props.surface_layers = 1

    # Compile: one slot per quad, turned frames, the cube's eight corners as singularities.
    assert bpy.ops.lamdafunction.compile() == {"FINISHED"}
    doc = json.loads(bpy.data.texts["lamdafunction.assembly.json"].as_string())
    assert doc["slots"] == 6 * N * N, doc["slots"]
    assert doc["schema_version"] == "1.1.0"
    audit = [(a.level, a.message, a.target_kind) for a in props.audit]
    assert not any(level == "error" for level, _, _ in audit), audit
    assert sum(1 for _, _, kind in audit if kind == "mesh_vertex") == 8, audit
    arrows_and_seams, colours = overlay.field_lines()
    assert len(arrows_and_seams) > 2 * 6 * N * N and len(colours) == len(arrows_and_seams)

    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    assembly = bpy.data.objects[props.assembly_object]
    assert json.loads(assembly["lamdafunction_violations"]) == []
    present = set(assembly.data.attributes.keys())
    assert "wfc_cell" not in present
    for name, _, _ in results.SURFACE_ATTRIBUTES:
        assert name in present, name
    state = results.read_attribute(assembly, "wfc_state")
    face = results.read_attribute(assembly, "wfc_face")
    assert (state == 0).all() and sorted(face.tolist()) == list(range(6 * N * N))
    assert (results.read_attribute(assembly, "wfc_layer") == 0).all()

    # Points sit at the cell centres: the mean of the eight corners.
    co = np.empty(len(assembly.data.vertices) * 3, dtype=np.float32)
    assembly.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    centre = np.mean([results.read_attribute(assembly, f"wfc_c{i}") for i in range(8)], axis=0)
    assert np.allclose(co, centre, atol=1e-5)

    # Materialise rigidly: every slot instanced at its centre, turned by wfc_rotation, scaled by wfc_scale.
    props.deform_to_cells = False
    assert bpy.ops.lamdafunction.materialise() == {"FINISHED"}
    rotation = results.read_attribute(assembly, "wfc_rotation")
    scale = results.read_attribute(assembly, "wfc_scale")
    module = results.read_attribute(assembly, "wfc_module")
    names = list(pipes_kit.MODULES)
    placed = instances_of(assembly)
    assert len(placed) == len(co), (len(placed), len(co))
    for loc, rot, size, obj_name in placed:
        # The module object sits at the collection's instance offset, so the instance lands on the slot centre.
        slot = int(np.argmin(((co - loc) ** 2).sum(axis=1)))
        assert np.allclose(loc, co[slot], atol=1e-4), (loc, co[slot])
        assert obj_name == names[int(module[slot])], (obj_name, names[int(module[slot])])
        # Compare matrices: q and -q are the same rotation, and decompose may return either.
        assert np.allclose(np.array(rot.to_matrix()), np.array(Quaternion(rotation[slot]).to_matrix()), atol=1e-4), (rot, rotation[slot])
        assert np.allclose(size, scale[slot], atol=1e-4), (size, scale[slot])

    # Deform to cells: modules bent into their cells, realised. Every pipe end meets its
    # neighbour's within float32 rounding, also across the cube's edges, where rigid
    # placement leaves wedge-shaped gaps.
    mark_pipe_ends()
    props.deform_to_cells = True
    assert bpy.ops.lamdafunction.materialise() == {"FINISHED"}
    counts = {name: len(bpy.data.objects[name].data.vertices) for name in names}
    expected = sum(counts[names[int(m)]] for m in module)
    vertices, end_vertices, gap, attributes = deformed_gaps(assembly)
    print(f"surface: deformed {vertices} vertices, {end_vertices} pipe-end vertices, max gap {gap:.2e}")
    assert vertices == expected, (vertices, expected)
    assert end_vertices > 0 and gap < GAP_LIMIT * props.cell_size, gap
    assert not any(name.startswith("wfc_c") for name in attributes), attributes
    assert "wfc_module" in attributes  # other slot attributes stay, for materials
    # Rigid again: the same modifier, back to instances.
    props.deform_to_cells = False
    assert bpy.ops.lamdafunction.materialise() == {"FINISHED"}
    assert len(instances_of(assembly)) == len(co)

    digest = results.attribute_hash(assembly, decimals=4)
    print(f"surface: {solver} solver, {len(placed)} instances, attribute hash {digest}")
    assert digest == GOLDEN_HASH_SEED_1, (solver, digest)

    # The evaluated mesh counts: a default cube with Subdivision Surface level 1 is 24 quads,
    # and a UV guide reads its UV map.
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    smooth = bpy.context.active_object
    modifier = smooth.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = 1
    props.envelope = smooth
    props.field_guide = "UV"
    props.surface_thickness = 0.4
    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    assembly = bpy.data.objects[props.assembly_object]
    assert len(assembly.data.vertices) == 24, len(assembly.data.vertices)

    # Back to Points mode on the same assembly object: the surface attributes are gone.
    props.field_guide = "NONE"
    pipes_kit_box = [o for o in bpy.data.objects if o.name.startswith("LamdaFunction Envelope")]
    props.envelope = pipes_kit_box[0]
    props.envelope_mode = "POINTS"
    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    present = set(bpy.data.objects[props.assembly_object].data.attributes.keys())
    assert "wfc_cell" in present and not any(name in present for name, _, _ in results.SURFACE_ATTRIBUTES)
    print("surface: subdivided cube and the switch back to points pass")


if __name__ == "__main__":
    main()
