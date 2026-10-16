# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike 5: how long does writing 200,000 points x 8 attributes with ``foreach_set`` take?

The solve operator writes the result arrays into the assembly mesh on the
main thread (``.claude/rules/blender-addon.md``, "Threading and data flow").
This script times every part of that write for the attribute contract:
``wfc_cell`` (vector), ``wfc_module``, ``wfc_rotation`` (quaternion),
``wfc_part``, ``wfc_anchor``, ``wfc_state``, ``wfc_options``, ``wfc_step``.
Pass: the attribute writes total under 0.5 s.

Runs headless: ``blender -b --factory-startup --python tests/blender/spike_05_foreach_set.py``.
It is a benchmark, not a test, so ``run_all.py`` does not pick it up.
"""

from __future__ import annotations

import time

import bpy
import numpy as np

POINTS = 200_000

# (name, attribute type, foreach_set property, components, numpy dtype)
ATTRIBUTES = (
    ("wfc_cell", "FLOAT_VECTOR", "vector", 3, np.float32),
    ("wfc_module", "INT", "value", 1, np.int32),
    ("wfc_rotation", "QUATERNION", "value", 4, np.float32),
    ("wfc_part", "INT", "value", 1, np.int32),
    ("wfc_anchor", "BOOLEAN", "value", 1, np.bool_),
    ("wfc_state", "INT", "value", 1, np.int32),
    ("wfc_options", "INT", "value", 1, np.int32),
    ("wfc_step", "INT", "value", 1, np.int32),
)


def sample_arrays(rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Plausible result arrays, flat and in the dtype foreach_set expects."""
    arrays: dict[str, np.ndarray] = {}
    for name, _, _, components, dtype in ATTRIBUTES:
        if dtype is np.bool_:
            data = rng.integers(0, 2, POINTS * components).astype(dtype)
        elif dtype is np.float32:
            data = rng.random(POINTS * components, dtype=np.float32)
        else:
            data = rng.integers(0, 400, POINTS * components).astype(dtype)
        arrays[name] = np.ascontiguousarray(data)
    return arrays


def build_mesh(positions: np.ndarray) -> bpy.types.Mesh:
    """A point cloud mesh with ``POINTS`` vertices placed by one foreach_set."""
    mesh = bpy.data.meshes.new("spike5")
    mesh.vertices.add(POINTS)
    mesh.vertices.foreach_set("co", positions)
    return mesh


def write_attributes(mesh: bpy.types.Mesh, arrays: dict[str, np.ndarray], create: bool) -> dict[str, float]:
    """Write every attribute; returns seconds per attribute. ``create`` adds the attributes first."""
    timings: dict[str, float] = {}
    for name, attr_type, prop, _, _ in ATTRIBUTES:
        start = time.perf_counter()
        attribute = mesh.attributes.new(name, attr_type, "POINT") if create else mesh.attributes[name]
        attribute.data.foreach_set(prop, arrays[name])
        timings[name] = time.perf_counter() - start
    return timings


def read_attributes(mesh: bpy.types.Mesh, arrays: dict[str, np.ndarray]) -> float:
    """Read every attribute back with foreach_get (what the compiler does for the envelope) and check it round-trips; returns seconds."""
    start = time.perf_counter()
    read: dict[str, np.ndarray] = {}
    for name, _, prop, components, dtype in ATTRIBUTES:
        out = np.empty(POINTS * components, dtype=dtype)
        mesh.attributes[name].data.foreach_get(prop, out)
        read[name] = out
    elapsed = time.perf_counter() - start
    for name, data in read.items():
        # The timing above is only meaningful if the bytes really landed.
        assert np.array_equal(data, arrays[name]), f"{name} did not round-trip"
    return elapsed


def main() -> None:
    """Time the build, the first write, a rewrite, a read-back and the mesh update."""
    rng = np.random.default_rng(1)
    positions = np.ascontiguousarray(rng.random(POINTS * 3, dtype=np.float32))
    arrays = sample_arrays(rng)

    start = time.perf_counter()
    mesh = build_mesh(positions)
    build = time.perf_counter() - start

    first = write_attributes(mesh, arrays, create=True)
    second = write_attributes(mesh, arrays, create=False)
    read = read_attributes(mesh, arrays)

    start = time.perf_counter()
    mesh.update()
    update = time.perf_counter() - start

    start = time.perf_counter()
    obj = bpy.data.objects.new("spike5", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    link = time.perf_counter() - start

    print(f"blender {bpy.app.version_string}, numpy {np.__version__}, {POINTS:,} points")
    print(f"{'attribute':14} {'create+write':>13} {'rewrite':>9}")
    for name, *_ in ATTRIBUTES:
        print(f"{name:14} {first[name] * 1000:10.1f} ms {second[name] * 1000:6.1f} ms")
    total_first = sum(first.values())
    total_second = sum(second.values())
    print(f"{'total':14} {total_first * 1000:10.1f} ms {total_second * 1000:6.1f} ms")
    print(f"mesh build (vertices.add + co): {build * 1000:.1f} ms")
    print(f"read back all 8 with foreach_get: {read * 1000:.1f} ms")
    print(f"mesh.update(): {update * 1000:.1f} ms; object link + view layer update: {link * 1000:.1f} ms")
    verdict = "pass" if total_first < 0.5 else "FAIL"
    print(f"spike 5: {verdict} ({total_first * 1000:.0f} ms for 8 attributes, target under 500 ms)")


if __name__ == "__main__":
    main()
