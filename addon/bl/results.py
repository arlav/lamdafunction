# SPDX-License-Identifier: GPL-3.0-or-later
"""The assembly object: one vertex per slot with the ``wfc_*`` result attributes.

Results are not written into the envelope because its evaluated Geometry
Nodes output cannot be written back; the assembly is a separate mesh object
the solver owns (``.claude/rules/blender-addon.md``, "Objects"). Every write
is one ``foreach_set`` from a contiguous NumPy array of the attribute's own
dtype, which spike 5 measured at about 2 ms for 200,000 points.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

import bpy

from ..compile.rotations import quaternion
from .backend import ResultLike

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


def write_result(
    obj: bpy.types.Object,
    positions: np.ndarray,
    cells: list[tuple[int, int, int]],
    result: ResultLike,
    compile_hash: str,
    seed: int,
) -> None:
    """Rebuild the assembly mesh and write every result attribute."""
    n = len(cells)
    mesh = obj.data
    mesh.clear_geometry()
    mesh.vertices.add(n)
    mesh.vertices.foreach_set("co", np.ascontiguousarray(positions, dtype=np.float32).ravel())

    rotation_index = np.asarray(result.rotation, dtype=np.int64)
    quats = np.zeros((n, 4), dtype=np.float32)
    quats[:, 0] = 1.0
    table = np.array([quaternion(r) for r in range(24)], dtype=np.float32)
    placed = rotation_index >= 0
    quats[placed] = table[rotation_index[placed]]

    arrays: dict[str, np.ndarray] = {
        "wfc_module": np.ascontiguousarray(result.module, dtype=np.int32),
        "wfc_variant": np.ascontiguousarray(result.variant, dtype=np.int32),
        "wfc_rotation": np.ascontiguousarray(quats.ravel()),
        "wfc_part": np.ascontiguousarray(result.part, dtype=np.int32),
        "wfc_anchor": np.ascontiguousarray(result.anchor, dtype=np.bool_),
        "wfc_state": np.ascontiguousarray(result.state, dtype=np.int32),
        "wfc_options": np.ascontiguousarray(result.options_left, dtype=np.int32),
        "wfc_step": np.ascontiguousarray(np.asarray(result.collapse_step, dtype=np.int64).clip(-1, 2**31 - 1), dtype=np.int32),
        "wfc_cell": np.ascontiguousarray(np.array(cells, dtype=np.float32).ravel()),
    }
    for name, attr_type, prop in ATTRIBUTES:
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


def attribute_hash(obj: bpy.types.Object) -> str:
    """A hash of the written attributes, for golden tests."""
    import hashlib

    digest = hashlib.sha256()
    for name, _, _ in ATTRIBUTES:
        digest.update(name.encode("utf-8"))
        digest.update(np.ascontiguousarray(read_attribute(obj, name)).tobytes())
    return digest.hexdigest()[:16]


def report_summary(report: dict[str, Any]) -> str:
    """One line for the panel."""
    return (
        f"{report.get('outcome', '?')}: attempt {report.get('attempt', 0)}, "
        f"{report.get('observations', 0)} observations, {report.get('backtracks', 0)} backtracks, "
        f"{float(report.get('elapsed', 0.0)):.2f} s"
    )
