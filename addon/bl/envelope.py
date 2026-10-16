# SPDX-License-Identifier: GPL-3.0-or-later
"""Read an envelope object into an :class:`Envelope`, and create box envelopes.

Slots are the evaluated vertices of the envelope mesh (so Geometry Nodes
can generate it). ``wfc_cell`` gives grid coordinates; without it they are
derived from position and the scene's cell size. ``wfc_w:*``,
``wfc_priority`` and ``wfc_fix`` are read when present
(``.claude/rules/blender-addon.md``, "Attribute contract").
"""

from __future__ import annotations

import numpy as np

import bpy

from ..model.envelope import Envelope

ASSEMBLY_NAME = "LamdaFunction Assembly"


def evaluated_mesh(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> bpy.types.Mesh:
    """The evaluated mesh of ``obj``: the Geometry Nodes result, not the original data."""
    return obj.evaluated_get(depsgraph).data


def read_envelope(obj: bpy.types.Object, cell_size: float, depsgraph: bpy.types.Depsgraph) -> tuple[Envelope, np.ndarray]:
    """The envelope and the world-space positions of its slots, one row per slot."""
    mesh = evaluated_mesh(obj, depsgraph)
    n = len(mesh.vertices)
    local = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    local = local.reshape(n, 3)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    positions = local.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]

    cell_attr = mesh.attributes.get("wfc_cell")
    if cell_attr is not None and cell_attr.domain == "POINT" and cell_attr.data_type == "FLOAT_VECTOR":
        raw = np.empty(n * 3, dtype=np.float32)
        cell_attr.data.foreach_get("vector", raw)
        cells_array = np.rint(raw.reshape(n, 3)).astype(np.int64)
    else:
        cells_array = np.rint(local / cell_size).astype(np.int64)
    cells = [(int(x), int(y), int(z)) for x, y, z in cells_array]

    weights: dict[str, list[float]] = {}
    priority: list[int] | None = None
    fixed: list[int] | None = None
    for attr in mesh.attributes:
        if attr.domain != "POINT":
            continue
        if attr.name.startswith("wfc_w:") and attr.data_type == "FLOAT":
            values = np.empty(n, dtype=np.float32)
            attr.data.foreach_get("value", values)
            weights[attr.name[len("wfc_w:"):]] = [float(v) for v in values]
        elif attr.name == "wfc_priority" and attr.data_type == "INT":
            values = np.empty(n, dtype=np.int32)
            attr.data.foreach_get("value", values)
            priority = [int(v) for v in values]
        elif attr.name == "wfc_fix" and attr.data_type == "INT":
            values = np.empty(n, dtype=np.int32)
            attr.data.foreach_get("value", values)
            fixed = [int(v) for v in values]
    envelope = Envelope(cells, weights, priority, fixed)
    envelope.check()
    return envelope, positions


def box_envelope(scene: bpy.types.Scene, size: tuple[int, int, int], cell_size: float, name: str = "LamdaFunction Envelope") -> bpy.types.Object:
    """A point-cloud mesh with one vertex per cell of a box, carrying ``wfc_cell``."""
    envelope = Envelope.box(size)
    mesh = bpy.data.meshes.new(name)
    n = len(envelope.cells)
    mesh.vertices.add(n)
    cells = np.array(envelope.cells, dtype=np.float32)
    mesh.vertices.foreach_set("co", (cells * cell_size).ravel())
    attr = mesh.attributes.new("wfc_cell", "FLOAT_VECTOR", "POINT")
    attr.data.foreach_set("vector", cells.ravel())
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    scene.lamdafunction.envelope = obj
    return obj
