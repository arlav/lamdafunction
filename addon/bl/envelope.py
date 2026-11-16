# SPDX-License-Identifier: GPL-3.0-or-later
"""Read an envelope object into slots, and create box envelopes.

Two modes (``SceneProps.envelope_mode``):

- **Points** (phase 1): slots are the evaluated vertices of the envelope mesh
  (so Geometry Nodes can generate it), read into an :class:`Envelope`.
- **Surface** (phase 2): the evaluated quads, in world space, grow into a
  shell (:func:`addon.model.shell.build_shell`) with frames from the
  direction field, read into a :class:`CellEnvelope`.

In points mode ``wfc_cell`` gives grid coordinates; without it they are
derived from position and the scene's cell size. ``wfc_w:*``,
``wfc_priority`` and ``wfc_fix`` are read when present
(``.claude/rules/blender-addon.md``, "Attribute contract").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

import bpy

from ..model.cells import CellEnvelope
from ..model.envelope import Envelope
from ..model.field import FieldSettings, uv_guide
from ..model.shell import build_shell


@dataclass
class Slots:
    """What a solve needs from the envelope: the envelope itself, slot positions, and grid cells in points mode."""

    envelope: Envelope | CellEnvelope
    positions: np.ndarray
    cells: list[tuple[int, int, int]] | None = None

    @property
    def count(self) -> int:
        """Number of slots."""
        return len(self.positions)

    @property
    def surface(self) -> bool:
        """Whether the slots are cells of a shell."""
        return isinstance(self.envelope, CellEnvelope)

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


@dataclass
class SurfaceMesh:
    """The evaluated mesh in world space: vertices, polygons in loop order, and UVs per polygon corner."""

    vertices: np.ndarray
    polygons: list[list[int]]
    uv: list[list[tuple[float, float]]] | None


def read_surface_mesh(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> SurfaceMesh:
    """Vertices (world space, float64), polygons and the active UV map of the evaluated mesh.

    The evaluated mesh is the modifier result (Subdivision Surface, Geometry
    Nodes), which is what the shell must follow. Its loops list each
    polygon's corners counter-clockwise seen from the polygon's normal.
    """
    mesh = evaluated_mesh(obj, depsgraph)
    n = len(mesh.vertices)
    local = np.empty(n * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    matrix = np.array(obj.matrix_world, dtype=np.float64)
    local64 = local.reshape(n, 3).astype(np.float64)
    # Element by element rather than ``@``, so the world positions do not depend on BLAS.
    vertices = np.stack(
        [local64[:, 0] * matrix[r, 0] + local64[:, 1] * matrix[r, 1] + local64[:, 2] * matrix[r, 2] + matrix[r, 3] for r in range(3)],
        axis=1,
    )
    count = len(mesh.polygons)
    starts = np.empty(count, dtype=np.int32)
    totals = np.empty(count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    corners = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corners)
    polygons = [[int(v) for v in corners[a : a + t]] for a, t in zip(starts, totals)]
    uv = None
    layer = mesh.uv_layers.active
    if layer is not None:
        flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        layer.data.foreach_get("uv", flat)
        pairs = flat.reshape(-1, 2).astype(np.float64)
        uv = [[(float(x), float(y)) for x, y in pairs[a : a + t]] for a, t in zip(starts, totals)]
    return SurfaceMesh(vertices, polygons, uv)


def field_settings(props: Any, surface: SurfaceMesh) -> FieldSettings:
    """The direction field's settings from the scene properties; a UV guide needs a UV map."""
    guide: tuple[float, float, float] | np.ndarray | None = None
    if props.field_guide == "AXIS":
        guide = tuple(float(x) for x in props.field_guide_axis)  # type: ignore[assignment]
    elif props.field_guide == "UV":
        if surface.uv is None:
            raise ValueError("the field guide is UV, but the envelope has no UV map")
        guide = uv_guide(surface.vertices, surface.polygons, surface.uv, axis=int(props.field_guide_uv))
    return FieldSettings(
        follow=props.field_follow,
        curvature_weight=props.field_curvature_weight,
        guide=guide,
        guide_weight=props.field_guide_weight,
    )


def read_surface(obj: bpy.types.Object, props: Any, depsgraph: bpy.types.Depsgraph) -> Slots:
    """Surface mode: the shell over the envelope's evaluated quads, slots at the cell centres."""
    surface = read_surface_mesh(obj, depsgraph)
    cells = build_shell(
        surface.vertices,
        surface.polygons,
        thickness=props.surface_thickness,
        layers=props.surface_layers,
        offset=props.surface_offset,
        field=field_settings(props, surface),
    )
    return Slots(cells, cells.frames[:, 0].copy())


def read_slots(obj: bpy.types.Object, props: Any, depsgraph: bpy.types.Depsgraph) -> Slots:
    """The slots of the envelope in the scene's mode."""
    if props.envelope_mode == "SURFACE":
        return read_surface(obj, props, depsgraph)
    envelope, positions = read_envelope(obj, props.cell_size, depsgraph)
    return Slots(envelope, positions, envelope.cells)
