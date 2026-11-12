# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike 6: can Geometry Nodes bend instanced modules into hexahedral cells, fast and without gaps?

Phase 2 places modules on a shell that follows a curved surface
(``docs/PLAN.md``, "Surfaces (phase 2)", "Placing geometry"). Each cell is a
hexahedron with eight corners; a module modelled in the unit cube
``[-0.5, 0.5]^3`` is rotated by its variant's quarter turns, then every
vertex moves to the trilinear blend of the cell's corners. Neighbours share
the four corners of their common face, and trilinear interpolation on a face
depends only on those four, so matching connector geometry should land on
the same points: watertight by construction. This script checks that in
Blender, with this recipe:

1. Assembly points carry ``wfc_c0`` … ``wfc_c7`` (corner ``x + 2y + 4z`` of
   the unit cube), ``wfc_module`` and ``wfc_rotation``.
2. Set Position moves every point to the origin, so instances are only rotated.
3. Instance on Points (Collection Info, Separate Children, Reset Children,
   Pick Instance, Instance Index from ``wfc_module``, Rotation from
   ``wfc_rotation``), then Realize Instances. The corner attributes travel
   from the points to the instances to the realised vertices.
4. Set Position to the trilinear blend, with ``(u, v, w) = position + 0.5``,
   as seven vector Mix nodes.

Cases: a quad sphere of about 10,000 faces, a coarse one (384 faces) and a
bumpy freeform sphere, one layer of thickness each. Pass: every connector
ring vertex on a shared face has a partner within 1e-5 x the mean cell size,
and the modifier evaluates in under 1 s for the 10,000-face case.

Runs headless: ``blender -b --factory-startup --python-exit-code 1 --python tests/blender/spike_06_trilinear.py``.
It is a benchmark, not a test, so ``run_all.py`` does not pick it up.
"""

from __future__ import annotations

import math
import time

import bpy
import mathutils
import numpy as np

RING = 16  # vertices per connector ring; a multiple of 4, so every ring is symmetric under mirror and quarter turn
RADIUS = 0.15  # tube radius in the unit cell
SEED = 6
GAP_LIMIT = 1e-5  # x mean cell size
TIME_LIMIT = 1.0  # seconds, for the ~10,000-face case

# Cube-face bases (outward normal, u, v) with u x v = normal, so quads come out counter-clockwise seen from outside.
CUBE_FACES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
)


# --- surfaces -------------------------------------------------------------


def quad_sphere(n: int, radius: float, bumps: bool) -> tuple[np.ndarray, np.ndarray]:
    """A cube subdivided ``n`` x ``n`` per face, welded and pushed onto a sphere (optionally bumpy).

    Lattice points are kept as integers (cube half-size ``n``, step 2) so welding
    is exact. Returns vertex positions (float64) and quads (corner indices in
    loop order, counter-clockwise seen from outside).
    """
    index: dict[tuple[int, int, int], int] = {}
    points: list[tuple[int, int, int]] = []
    quads: list[tuple[int, int, int, int]] = []

    def vertex(key: tuple[int, int, int]) -> int:
        found = index.get(key)
        if found is None:
            found = index[key] = len(points)
            points.append(key)
        return found

    for normal, u, v in CUBE_FACES:
        grid = [[0] * (n + 1) for _ in range(n + 1)]
        for i in range(n + 1):
            for j in range(n + 1):
                a, b = 2 * i - n, 2 * j - n
                key = tuple(normal[k] * n + u[k] * a + v[k] * b for k in range(3))
                grid[i][j] = vertex(key)  # type: ignore[arg-type]
        for i in range(n):
            for j in range(n):
                quads.append((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))

    lattice = np.array(points, dtype=np.float64)
    direction = lattice / np.linalg.norm(lattice, axis=1, keepdims=True)
    r = np.full(len(direction), radius)
    if bumps:
        dx, dy, dz = direction.T
        r = radius * (1.0 + 0.18 * np.sin(3.0 * dx + 1.0) * np.cos(2.0 * dy) + 0.10 * np.sin(5.0 * dz))
    return direction * r[:, None], np.array(quads, dtype=np.int64)


def vertex_normals(positions: np.ndarray, quads: np.ndarray) -> np.ndarray:
    """Smooth normals: the area-weighted sum of adjacent quad normals, normalised."""
    c0, c1, c2, c3 = (positions[quads[:, k]] for k in range(4))
    face = np.cross(c2 - c0, c3 - c1)  # twice the vector area of the quad
    normals = np.zeros_like(positions)
    for k in range(4):
        np.add.at(normals, quads[:, k], face)
    return normals / np.linalg.norm(normals, axis=1, keepdims=True)


def cell_corners(positions: np.ndarray, quads: np.ndarray, thickness: float) -> np.ndarray:
    """Eight corners per cell, shape ``(cells, 8, 3)``, in unit-cube order ``x + 2y + 4z``.

    ``x`` runs along c0 -> c1, ``y`` along c0 -> c3, ``z`` from the inner
    surface (the vertices) to the outer one (vertex + normal x thickness).
    """
    normals = vertex_normals(positions, quads)
    quad_of_xy = (0, 1, 3, 2)  # (x, y) = (0,0) c0, (1,0) c1, (0,1) c3, (1,1) c2
    corners = np.empty((len(quads), 8, 3))
    for z in range(2):
        for y in range(2):
            for x in range(2):
                vid = quads[:, quad_of_xy[x + 2 * y]]
                corners[:, x + 2 * y + 4 * z] = positions[vid] + normals[vid] * thickness * z
    return corners


def mean_edge(positions: np.ndarray, quads: np.ndarray) -> float:
    """Mean length of the quads' edges: the cell size the gaps are measured in."""
    lengths = [np.linalg.norm(positions[quads[:, (k + 1) % 4]] - positions[quads[:, k]], axis=1) for k in range(4)]
    return float(np.mean(np.concatenate(lengths)))


# --- modules --------------------------------------------------------------


def arm(axis: int, sign: int) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]], list[bool]]:
    """A tube stub from the cell centre region to one side face: an end ring on the face and an inner ring."""
    verts: list[tuple[float, float, float]] = []
    ring_flags: list[bool] = []
    others = [k for k in range(3) if k != axis]
    for depth in (0.5, 0.15):
        for i in range(RING):
            a = 2.0 * math.pi * i / RING
            co = [0.0, 0.0, 0.0]
            co[axis] = sign * depth
            co[others[0]] = RADIUS * math.cos(a)
            co[others[1]] = RADIUS * math.sin(a)
            verts.append((co[0], co[1], co[2]))
            ring_flags.append(depth == 0.5)
    faces = [(i, (i + 1) % RING, RING + (i + 1) % RING, RING + i) for i in range(RING)]
    return verts, faces, ring_flags


def module_object(name: str, arms: list[tuple[int, int]]) -> bpy.types.Object:
    """A mesh made of tube stubs, with a boolean ``ring`` point attribute on the end-ring vertices."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    flags: list[bool] = []
    for axis, sign in arms:
        v, f, r = arm(axis, sign)
        base = len(verts)
        verts += v
        faces += [tuple(base + i for i in face) for face in f]
        flags += r
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    attr = mesh.attributes.new("ring", "BOOLEAN", "POINT")
    attr.data.foreach_set("value", np.array(flags, dtype=np.bool_))
    mesh.update()
    return bpy.data.objects.new(name, mesh)


def build_library(scene: bpy.types.Scene) -> tuple[bpy.types.Collection, dict[str, bpy.types.Object]]:
    """A visible library collection and a hidden instancer with ``0000``, ``0001`` children, as the add-on builds it."""
    library = bpy.data.collections.new("Spike6 Library")
    scene.collection.children.link(library)
    modules = {
        "cross": module_object("cross", [(0, 1), (0, -1), (1, 1), (1, -1)]),
        "elbow": module_object("elbow", [(0, 1), (1, 1)]),
    }
    instancer = bpy.data.collections.new("Spike6 Instancer")
    scene.collection.children.link(instancer)
    for index, obj in enumerate(modules.values()):
        library.objects.link(obj)
        child = bpy.data.collections.new(f"{index:04d}")
        child.objects.link(obj)
        instancer.children.link(child)
    for layer in scene.view_layers[0].layer_collection.children:
        if layer.collection == instancer:
            layer.exclude = True
    return instancer, modules


# --- node group -----------------------------------------------------------


def sock(node: bpy.types.Node, identifier: str, outputs: bool = False) -> bpy.types.NodeSocket:
    """A node socket by identifier (Mix and Store Named Attribute have one socket per type)."""
    for s in node.outputs if outputs else node.inputs:
        if s.identifier == identifier:
            return s
    raise KeyError(f"{node.bl_idname} has no socket {identifier!r}")


def build_group(instancer: bpy.types.Collection) -> bpy.types.GeometryNodeTree:
    """Points to origin, instance, realise, remember the local position, then trilinear Set Position."""
    tree = bpy.data.node_groups.new("Spike6 Deform", "GeometryNodeTree")
    tree.is_modifier = True
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    group_in = nodes.new("NodeGroupInput")
    group_out = nodes.new("NodeGroupOutput")

    def named(name: str, data_type: str) -> bpy.types.Node:
        node = nodes.new("GeometryNodeInputNamedAttribute")
        node.data_type = data_type
        node.inputs["Name"].default_value = name
        return node

    # An unlinked Position input on Set Position means "the point's own position" (an implicit field), not the
    # socket's value, so the origin has to come from a linked zero vector.
    zero = nodes.new("FunctionNodeInputVector")
    zero.vector = (0.0, 0.0, 0.0)
    to_origin = nodes.new("GeometryNodeSetPosition")
    links.new(group_in.outputs["Geometry"], to_origin.inputs["Geometry"])
    links.new(zero.outputs["Vector"], to_origin.inputs["Position"])

    info = nodes.new("GeometryNodeCollectionInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Collection"].default_value = instancer
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.inputs["Pick Instance"].default_value = True
    links.new(to_origin.outputs["Geometry"], instance.inputs["Points"])
    links.new(info.outputs["Instances"], instance.inputs["Instance"])
    module = named("wfc_module", "INT")
    links.new(module.outputs["Attribute"], instance.inputs["Instance Index"])
    rotation = named("wfc_rotation", "QUATERNION")
    links.new(rotation.outputs["Attribute"], instance.inputs["Rotation"])
    realize = nodes.new("GeometryNodeRealizeInstances")
    links.new(instance.outputs["Instances"], realize.inputs["Geometry"])

    position = nodes.new("GeometryNodeInputPosition")
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT_VECTOR"
    store.domain = "POINT"
    store.inputs["Name"].default_value = "wfc_local"
    links.new(realize.outputs["Geometry"], store.inputs["Geometry"])
    links.new(position.outputs["Position"], sock(store, "Value_Vector") if any(s.identifier == "Value_Vector" for s in store.inputs) else store.inputs["Value"])

    offset = nodes.new("ShaderNodeVectorMath")
    offset.operation = "ADD"
    offset.inputs[1].default_value = (0.5, 0.5, 0.5)
    links.new(position.outputs["Position"], offset.inputs[0])
    uvw = nodes.new("ShaderNodeSeparateXYZ")
    links.new(offset.outputs["Vector"], uvw.inputs["Vector"])

    def mix(a: bpy.types.NodeSocket, b: bpy.types.NodeSocket, factor: bpy.types.NodeSocket) -> bpy.types.NodeSocket:
        node = nodes.new("ShaderNodeMix")
        node.data_type = "VECTOR"
        node.factor_mode = "UNIFORM"
        node.clamp_factor = False
        links.new(factor, sock(node, "Factor_Float"))
        links.new(a, sock(node, "A_Vector"))
        links.new(b, sock(node, "B_Vector"))
        return sock(node, "Result_Vector", outputs=True)

    c = [named(f"wfc_c{i}", "FLOAT_VECTOR").outputs["Attribute"] for i in range(8)]
    u, v, w = uvw.outputs["X"], uvw.outputs["Y"], uvw.outputs["Z"]
    bottom = mix(mix(c[0], c[1], u), mix(c[2], c[3], u), v)
    top = mix(mix(c[4], c[5], u), mix(c[6], c[7], u), v)
    deformed = mix(bottom, top, w)

    deform = nodes.new("GeometryNodeSetPosition")
    links.new(store.outputs["Geometry"], deform.inputs["Geometry"])
    links.new(deformed, deform.inputs["Position"])
    links.new(deform.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


# --- assembly and measurement ----------------------------------------------


def assembly_object(scene: bpy.types.Scene, name: str, corners: np.ndarray, modules: np.ndarray, turns: np.ndarray) -> bpy.types.Object:
    """One point per cell at the cell centre, with the corner, module and rotation attributes."""
    n = len(corners)
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(n)
    mesh.vertices.foreach_set("co", corners.mean(axis=1).astype(np.float32).ravel())
    for i in range(8):
        attr = mesh.attributes.new(f"wfc_c{i}", "FLOAT_VECTOR", "POINT")
        attr.data.foreach_set("vector", np.ascontiguousarray(corners[:, i], dtype=np.float32).ravel())
    attr = mesh.attributes.new("wfc_module", "INT", "POINT")
    attr.data.foreach_set("value", modules.astype(np.int32))
    half = turns.astype(np.float64) * (math.pi / 4.0)  # a quarter turn about Z is a quaternion of half-angle pi/4
    quat = np.zeros((n, 4), dtype=np.float32)  # (w, x, y, z)
    quat[:, 0] = np.cos(half)
    quat[:, 3] = np.sin(half)
    attr = mesh.attributes.new("wfc_rotation", "QUATERNION", "POINT")
    attr.data.foreach_set("value", quat.ravel())
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def read_points(mesh: bpy.types.Mesh, name: str, components: int, prop: str, dtype: type = np.float32) -> np.ndarray:
    """A point attribute of an evaluated mesh as an array."""
    attr = mesh.attributes[name]
    out = np.empty(len(mesh.vertices) * components, dtype=dtype)
    attr.data.foreach_get(prop, out)
    return out.reshape(-1, components) if components > 1 else out


def evaluate(obj: bpy.types.Object) -> tuple[bpy.types.Mesh, float, float]:
    """Evaluate the modifier, then force a real re-evaluation and time both.

    ``update_tag()`` alone can leave the cached result in place, so the second
    run rewrites one input attribute with the same values, which invalidates
    the modifier's input and makes Geometry Nodes run again.
    """
    view_layer = bpy.context.view_layer
    start = time.perf_counter()
    view_layer.update()
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    _ = len(mesh.vertices)
    first = time.perf_counter() - start

    source = obj.data
    c0 = np.empty(len(source.vertices) * 3, dtype=np.float32)
    source.attributes["wfc_c0"].data.foreach_get("vector", c0)
    source.attributes["wfc_c0"].data.foreach_set("vector", c0)
    source.update()
    start = time.perf_counter()
    view_layer.update()
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    _ = len(mesh.vertices)
    return mesh, first, time.perf_counter() - start


def max_gap(mesh: bpy.types.Mesh) -> tuple[float, int]:
    """Largest distance from an end-ring vertex to its nearest partner from another cell, and the ring count."""
    co = read_points(mesh, "position", 3, "vector")
    ring = read_points(mesh, "ring", 1, "value", np.bool_)
    points = co[ring].astype(np.float64)
    tree = mathutils.kdtree.KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(p, i)
    tree.balance()
    worst = 0.0
    for p in points:
        hits = tree.find_n(p, 2)  # itself (distance 0) and its partner
        worst = max(worst, hits[1][2])
    return worst, len(points)


def run_case(scene, instancer, group, label: str, n: int, bumps: bool, all_cross: bool, rng: np.random.Generator) -> dict:
    """Build one surface, evaluate the deformation and measure it."""
    positions, quads = quad_sphere(n, 5.0, bumps)
    cell = mean_edge(positions, quads)
    corners = cell_corners(positions, quads, thickness=cell)
    cells = len(quads)
    turns = rng.integers(0, 4, size=cells)
    modules = np.zeros(cells, dtype=np.int64) if all_cross else np.ones(cells, dtype=np.int64)
    obj = assembly_object(scene, f"Spike6 {label}", corners, modules, turns)
    modifier = obj.modifiers.new("Deform", "NODES")
    modifier.node_group = group
    mesh, first, seconds = evaluate(obj)
    result = {"case": label, "cells": cells, "verts": len(mesh.vertices), "first": first, "seconds": seconds, "cell": cell}

    # Corner attributes reach the realised vertices: vertex j of cell i carries cell i's corners.
    per_cell = len(mesh.vertices) // cells
    c3 = read_points(mesh, "wfc_c3", 3, "vector")
    expected = np.repeat(corners[:, 3].astype(np.float32), per_cell, axis=0)
    result["propagated"] = bool(np.array_equal(c3, expected))

    # Before deformation each realised vertex is the module vertex rotated by the cell's quarter turns.
    local = read_points(mesh, "wfc_local", 3, "vector").reshape(cells, per_cell, 3).astype(np.float64)
    src = bpy.data.objects["cross" if all_cross else "elbow"].data
    base = np.empty(len(src.vertices) * 3, dtype=np.float32)
    src.vertices.foreach_get("co", base)
    base = base.reshape(-1, 3).astype(np.float64)
    angle = turns * (math.pi / 2.0)
    cos, sin = np.cos(angle)[:, None], np.sin(angle)[:, None]
    rotated = np.stack([cos * base[:, 0] - sin * base[:, 1], sin * base[:, 0] + cos * base[:, 1], np.broadcast_to(base[:, 2], (len(turns), len(base)))], axis=2)
    result["rotation_error"] = float(np.max(np.abs(local - rotated)))

    if all_cross:
        gap, rings = max_gap(mesh)
        result["gap"] = gap / cell
        result["ring_vertices"] = rings
    bpy.data.objects.remove(obj)
    return result


def main() -> None:
    """Run the three cases and the rotation check, print a table, and fail loudly."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    instancer, _ = build_library(scene)
    group = build_group(instancer)
    rng = np.random.default_rng(SEED)

    results = [
        run_case(scene, instancer, group, "sphere n=41", 41, False, True, rng),
        run_case(scene, instancer, group, "sphere n=8", 8, False, True, rng),
        run_case(scene, instancer, group, "bumpy n=24", 24, True, True, rng),
        run_case(scene, instancer, group, "elbows n=8", 8, False, False, rng),
    ]

    print(f"\nSpike 6 on Blender {bpy.app.version_string}")
    print(f"{'case':<14}{'cells':>8}{'verts':>10}{'rings':>9}{'1st s':>8}{'eval s':>9}{'gap/cell':>11}{'rot err':>10}  attrs")
    failures: list[str] = []
    for r in results:
        gap = r.get("gap")
        gap_text = f"{gap:.2e}" if gap is not None else "-"
        rings = r.get("ring_vertices", "-")
        print(f"{r['case']:<14}{r['cells']:>8}{r['verts']:>10}{rings:>9}{r['first']:>8.3f}{r['seconds']:>9.3f}{gap_text:>11}{r['rotation_error']:>10.1e}  {'ok' if r['propagated'] else 'MISSING'}")
        if not r["propagated"]:
            failures.append(f"{r['case']}: corner attributes did not reach the realised vertices")
        if r["rotation_error"] > 1e-6:
            failures.append(f"{r['case']}: local position is not the rotated module vertex ({r['rotation_error']:.2e})")
        if gap is not None and gap > GAP_LIMIT:
            failures.append(f"{r['case']}: gap {gap:.2e} x cell size")
    if results[0]["seconds"] > TIME_LIMIT:
        failures.append(f"10,000-face evaluation took {results[0]['seconds']:.3f} s")
    if failures:
        raise SystemExit("spike 6 failed:\n  " + "\n  ".join(failures))
    print("spike 6: pass")


main()
