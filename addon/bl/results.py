# SPDX-License-Identifier: GPL-3.0-or-later
"""The assembly object: one vertex per slot with the ``wfc_*`` result attributes.

Results are not written into the envelope because its evaluated Geometry
Nodes output cannot be written back; the assembly is a separate mesh object
the solver owns (``.claude/rules/blender-addon.md``, "Objects"). Every write
is one ``foreach_set`` from a contiguous NumPy array of the attribute's own
dtype, which spike 5 measured at about 2 ms for 200,000 points.

On a surface (phase 2) the slots are shell cells: points sit at the cell
centres, ``wfc_rotation`` is the cell's frame times the variant's rotation,
and the cell's own data goes with it (``SURFACE_ATTRIBUTES``): ``wfc_face``
and ``wfc_layer`` (where it came from), ``wfc_frame`` (the frame alone),
``wfc_scale`` (cell size along the module's axes, for rigid placement) and
``wfc_c0`` … ``wfc_c7`` (the corners, for the trilinear deformation of S6).
A surface assembly has no ``wfc_cell``.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

import bpy

from ..compile.rotations import quaternion
from ..model.cells import CellEnvelope
from ..model.placement import rigid
from .backend import ResultLike
from .envelope import Slots

ASSEMBLY_NAME = "LamdaFunction Assembly"

ATTRIBUTES = (
    ("wfc_module", "INT", "value"),
    ("wfc_variant", "INT", "value"),
    ("wfc_rotation", "QUATERNION", "value"),
    ("wfc_part", "INT", "value"),
    ("wfc_anchor", "BOOLEAN", "value"),
    ("wfc_state", "INT", "value"),
    ("wfc_options", "INT", "value"),
    ("wfc_step", "INT", "value"),
    ("wfc_cell", "FLOAT_VECTOR", "vector"),
)
"""Phase 1 attributes, in hash order; a surface assembly has all but ``wfc_cell``."""

SURFACE_ATTRIBUTES = (
    ("wfc_face", "INT", "value"),
    ("wfc_layer", "INT", "value"),
    ("wfc_frame", "QUATERNION", "value"),
    ("wfc_scale", "FLOAT_VECTOR", "vector"),
    *((f"wfc_c{i}", "FLOAT_VECTOR", "vector") for i in range(8)),
)
"""Written on a surface only, after the phase 1 attributes in hash order."""

ALL_ATTRIBUTES = ATTRIBUTES + SURFACE_ATTRIBUTES


def ensure_assembly_object(scene: bpy.types.Scene, name: str = ASSEMBLY_NAME) -> bpy.types.Object:
    """The assembly object, created and linked if needed."""
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
    if obj.name not in scene.collection.all_objects:
        scene.collection.objects.link(obj)
    return obj


def _attribute(mesh: bpy.types.Mesh, name: str, attr_type: str) -> bpy.types.Attribute:
    """An attribute of the right type on the point domain, replacing one of another type."""
    attr = mesh.attributes.get(name)
    if attr is not None and (attr.data_type != attr_type or attr.domain != "POINT"):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name, attr_type, "POINT")
    return attr


def _surface_arrays(cells: CellEnvelope, rotation_index: np.ndarray, cell_size: float) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Positions, world rotations and the surface attributes of a shell's slots."""
    placed = rigid(cells.frames, rotation_index, cell_size)
    face = np.array([int(s[1]) if s and s[0] == "face" else -1 for s in cells.sources], dtype=np.int32)
    layer = np.array([int(s[2]) if s and s[0] == "face" else 0 for s in cells.sources], dtype=np.int32)
    arrays: dict[str, np.ndarray] = {
        "wfc_face": face,
        "wfc_layer": layer,
        "wfc_frame": np.ascontiguousarray(placed.frame.astype(np.float32).ravel()),
        "wfc_scale": np.ascontiguousarray(placed.scale.astype(np.float32).ravel()),
    }
    for i in range(8):
        arrays[f"wfc_c{i}"] = np.ascontiguousarray(cells.corners[:, i].astype(np.float32).ravel())
    return placed.position, placed.rotation.astype(np.float32), arrays


def write_result(
    obj: bpy.types.Object,
    slots: Slots,
    result: ResultLike,
    compile_hash: str,
    seed: int,
    cell_size: float = 1.0,
) -> None:
    """Rebuild the assembly mesh and write every result attribute.

    ``cell_size`` is the kit's cell, which a surface cell's size is measured against.
    """
    n = slots.count
    rotation_index = np.asarray(result.rotation, dtype=np.int64)
    extra: dict[str, np.ndarray] = {}
    if isinstance(slots.envelope, CellEnvelope):
        positions, quats, extra = _surface_arrays(slots.envelope, rotation_index, cell_size)
    else:
        positions = slots.positions
        quats = np.zeros((n, 4), dtype=np.float32)
        quats[:, 0] = 1.0
        table = np.array([quaternion(r) for r in range(24)], dtype=np.float32)
        placed = rotation_index >= 0
        quats[placed] = table[rotation_index[placed]]
        extra["wfc_cell"] = np.ascontiguousarray(np.array(slots.cells, dtype=np.float32).ravel())

    mesh = obj.data
    # Attributes left from a solve in the other mode would be stale (and would change the hash).
    for name in [a.name for a in mesh.attributes if a.name.startswith("wfc_")]:
        mesh.attributes.remove(mesh.attributes[name])
    mesh.clear_geometry()
    mesh.vertices.add(n)
    mesh.vertices.foreach_set("co", np.ascontiguousarray(positions, dtype=np.float32).ravel())

    arrays: dict[str, np.ndarray] = {
        "wfc_module": np.ascontiguousarray(result.module, dtype=np.int32),
        "wfc_variant": np.ascontiguousarray(result.variant, dtype=np.int32),
        "wfc_rotation": np.ascontiguousarray(quats.ravel()),
        "wfc_part": np.ascontiguousarray(result.part, dtype=np.int32),
        "wfc_anchor": np.ascontiguousarray(result.anchor, dtype=np.bool_),
        "wfc_state": np.ascontiguousarray(result.state, dtype=np.int32),
        "wfc_options": np.ascontiguousarray(result.options_left, dtype=np.int32),
        "wfc_step": np.ascontiguousarray(np.asarray(result.collapse_step, dtype=np.int64).clip(-1, 2**31 - 1), dtype=np.int32),
        **extra,
    }
    for name, attr_type, prop in ALL_ATTRIBUTES:
        if name in arrays:
            _attribute(mesh, name, attr_type).data.foreach_set(prop, arrays[name])
    mesh.update()

    obj["lamdafunction_seed"] = seed
    obj["lamdafunction_compile_hash"] = compile_hash
    obj["lamdafunction_report"] = json.dumps(result.report)


def read_attribute(obj: bpy.types.Object, name: str) -> np.ndarray:
    """A point attribute of the assembly as a NumPy array (ints, bools or floats)."""
    attr = obj.data.attributes[name]
    n = len(obj.data.vertices)
    if attr.data_type == "INT":
        out = np.empty(n, dtype=np.int32)
        attr.data.foreach_get("value", out)
    elif attr.data_type == "BOOLEAN":
        out = np.empty(n, dtype=np.bool_)
        attr.data.foreach_get("value", out)
    elif attr.data_type == "QUATERNION":
        out = np.empty(n * 4, dtype=np.float32)
        attr.data.foreach_get("value", out)
        out = out.reshape(n, 4)
    elif attr.data_type == "FLOAT_VECTOR":
        out = np.empty(n * 3, dtype=np.float32)
        attr.data.foreach_get("vector", out)
        out = out.reshape(n, 3)
    else:
        out = np.empty(n, dtype=np.float32)
        attr.data.foreach_get("value", out)
    return out


def attribute_hash(obj: bpy.types.Object, decimals: int | None = None) -> str:
    """A hash of the written attributes present on ``obj``, in a fixed order, for golden tests.

    ``decimals`` rounds float attributes first. A surface's corners and frames come
    from float geometry (vertex normals, the evaluated mesh), whose last bits could
    differ between platforms while the result is the same; phase 1's floats are exact,
    so its hash keeps ``None``.
    """
    import hashlib

    digest = hashlib.sha256()
    present = set(obj.data.attributes.keys())
    for name, _, _ in ALL_ATTRIBUTES:
        if name not in present:
            continue
        values = read_attribute(obj, name)
        if decimals is not None and values.dtype.kind == "f":
            values = np.round(values.astype(np.float64), decimals) + 0.0
        digest.update(name.encode("utf-8"))
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()[:16]


def report_summary(report: dict[str, Any]) -> str:
    """One line for the panel."""
    return (
        f"{report.get('outcome', '?')}: attempt {report.get('attempt', 0)}, "
        f"{report.get('observations', 0)} observations, {report.get('backtracks', 0)} backtracks, "
        f"{float(report.get('elapsed', 0.0)):.2f} s"
    )
