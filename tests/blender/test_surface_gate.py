# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2, S8: the surface-tutorial gate — the three examples, pinned.

Mirrors ``docs/surface-tutorial.md``:

- **A. Quad sphere.** The pipes kit wrapped onto a cube with Subdivision
  Surface and Cast, one layer, seed 1: compiles to 96 slots with the cube's
  eight singularities in the audit, solves validly, the deformed pipes are
  watertight at every connector, and the discrete result hash is pinned.
- **B. Two-layer freeform.** The pipes kit on a sheared, flattened,
  subdivided box (exact binary cage coordinates), two layers, seed 3:
  192 slots, the same watertight check, its own pinned hash.
- **C. Rib kit on a barrel vault.** One rib module with rotation freedom
  None on a half cylinder, rim free: the direction field puts +X across the
  arches, so every placed rib follows its arch (its +X has no component
  along the vault's length).

The pinned hashes cover the discrete attributes only (module, variant, part,
anchor, state, options, step, face, layer). The float attributes (frames,
corners, scales) come from modifier-evaluated geometry, whose last bits may
differ between platforms while the result is the same (STATUS.md, "Open");
the discrete solve result depends only on the combinatorial document and the
seed. ``test_surface.py`` pins the full hash, floats included, on a mesh
built with exact coordinates.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

import bpy
from mathutils import Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipes_kit  # noqa: E402
import test_surface  # noqa: E402

GAP_LIMIT = test_surface.GAP_LIMIT
"""x cell size: the watertight bound from spike 6 that ``deformed_gaps`` must stay under."""

DISCRETE = (
    "wfc_module",
    "wfc_variant",
    "wfc_part",
    "wfc_anchor",
    "wfc_state",
    "wfc_options",
    "wfc_step",
    "wfc_face",
    "wfc_layer",
)

HASH_SPHERE_SEED_1 = "0c06b09ae345063f"  # 5.0.1, macOS arm64; 4.3.2 and both backends agree
HASH_FREEFORM_SEED_3 = "16ec231f389deaed"  # ditto


def discrete_hash(assembly: bpy.types.Object) -> str:
    """Hash only the integer and boolean result attributes, in a fixed order."""
    from addon.bl import results

    digest = hashlib.sha256()
    for name in DISCRETE:
        values = results.read_attribute(assembly, name)
        digest.update(name.encode("utf-8"))
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()[:16]


def subdivide(obj: bpy.types.Object, levels: int) -> None:
    """A Subdivision Surface modifier at the same viewport and render level."""
    modifier = obj.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = modifier.render_levels = levels


def quad_sphere(size: float = 8.0, levels: int = 2) -> bpy.types.Object:
    """Tutorial example A: a cube rounded to a sphere by Subdivision Surface and Cast."""
    bpy.ops.mesh.primitive_cube_add(size=size)
    sphere = bpy.context.active_object
    sphere.name = "Quad Sphere"
    subdivide(sphere, levels)
    cast = sphere.modifiers.new("Cast", "CAST")
    cast.factor = 1.0
    return sphere


def freeform(levels: int = 2) -> bpy.types.Object:
    """Tutorial example B: a sheared, flattened box cage, subdivided; exact binary coordinates."""
    vertices = [(3.0 * x + 0.5 * z, 2.0 * y, 1.5 * z) for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)]
    faces = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))
    mesh = bpy.data.meshes.new("Freeform")
    mesh.from_pydata(vertices, [], [tuple(f) for f in faces])
    mesh.update()
    obj = bpy.data.objects.new("Freeform", mesh)
    bpy.context.scene.collection.objects.link(obj)
    subdivide(obj, levels)
    return obj


def barrel_vault(n_arch: int = 12, n_length: int = 6, radius: float = 3.0, length: float = 6.0) -> bpy.types.Object:
    """Tutorial example C: half a cylinder, arches across YZ, straight generators along X."""
    vertices = [
        (length * i / n_length, radius * math.cos(math.pi * j / n_arch), radius * math.sin(math.pi * j / n_arch))
        for i in range(n_length + 1)
        for j in range(n_arch + 1)
    ]
    row = n_arch + 1
    faces = [(i * row + j, i * row + j + 1, (i + 1) * row + j + 1, (i + 1) * row + j) for i in range(n_length) for j in range(n_arch)]
    mesh = bpy.data.meshes.new("Barrel Vault")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Barrel Vault", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def checked_solve(props, expected_slots: int, expected_singularities: int | None = None) -> bpy.types.Object:
    """Compile and solve through the operators; assert the document, the audit and a valid result."""
    from addon.bl import results

    assert bpy.ops.lamdafunction.compile() == {"FINISHED"}
    doc = json.loads(bpy.data.texts["lamdafunction.assembly.json"].as_string())
    assert doc["schema_version"] in ("1.0.0", "1.1.0"), doc["schema_version"]
    # 1.1.0 exactly when some slot frame turns the ports (an all-identity frame stays 1.0.0).
    assert doc["slots"] == expected_slots, doc["slots"]
    audit = [(a.level, a.message, a.target_kind) for a in props.audit]
    assert not any(level == "error" for level, _, _ in audit), audit
    if expected_singularities is not None:
        assert sum(1 for _, _, kind in audit if kind == "mesh_vertex") == expected_singularities, audit
    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    assembly = bpy.data.objects[props.assembly_object]
    assert json.loads(assembly["lamdafunction_violations"]) == []
    assert (results.read_attribute(assembly, "wfc_state") == 0).all()
    return assembly


def watertight(props, assembly: bpy.types.Object, label: str) -> None:
    """Deform to cells and check every pipe end meets its partner within the spike-6 bound."""
    test_surface.mark_pipe_ends()
    props.deform_to_cells = True
    assert bpy.ops.lamdafunction.materialise() == {"FINISHED"}
    vertices, ends, gap, _ = test_surface.deformed_gaps(assembly)
    print(f"{label}: deformed {vertices} vertices, {ends} pipe-end vertices, max gap {gap:.2e}")
    assert ends > 0 and gap < GAP_LIMIT * props.cell_size, gap


def part_a_sphere() -> None:
    """The pipes kit on a quad sphere, one layer, seed 1."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pipes_kit.build_scene(seed=1)
    props = bpy.context.scene.lamdafunction
    props.envelope = quad_sphere()
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 0.7
    props.surface_layers = 1

    assembly = checked_solve(props, expected_slots=96, expected_singularities=8)
    digest = discrete_hash(assembly)
    print(f"gate A: quad sphere, seed 1, discrete hash {digest}")
    assert digest == HASH_SPHERE_SEED_1, digest
    watertight(props, assembly, "gate A")


def part_b_freeform() -> None:
    """The pipes kit on a two-layer freeform subdivision surface, seed 3."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    pipes_kit.build_scene(seed=3)
    props = bpy.context.scene.lamdafunction
    props.envelope = freeform()
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 0.5
    props.surface_layers = 2

    assembly = checked_solve(props, expected_slots=192)
    from addon.bl import results

    layer = results.read_attribute(assembly, "wfc_layer")
    assert sorted(set(layer.tolist())) == [0, 1], set(layer.tolist())
    digest = discrete_hash(assembly)
    print(f"gate B: two-layer freeform, seed 3, discrete hash {digest}")
    assert digest == HASH_FREEFORM_SEED_3, digest
    watertight(props, assembly, "gate B")


def part_c_vault() -> None:
    """A rib kit with no rotation freedom on a barrel vault: the ribs follow the arches."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    props = scene.lamdafunction
    from addon.bl import library as library_module
    from addon.bl import results

    library_module.ensure_library(scene)
    collection = library_module.add_module(scene, "rib", 1.0, with_cube=False)
    mesh = pipes_kit.tube_mesh("rib", (0, 1), 1.0, 0.15)
    obj = bpy.data.objects.new("rib", mesh)
    obj.location = (0.0, -3.0, 0.0)
    collection.objects.link(obj)
    collection.instance_offset = obj.location
    module = collection.lamdafunction
    module.freedom = "NONE"
    for face in range(6):
        setattr(module, f"connector_{face}", "t" if face in (0, 1) else "0")
    for a, b in (("t", "t"), ("0", "0")):
        pair = props.pairs.add()
        pair.a, pair.b = a, b
    props.indifference = False
    props.closed = True
    props.closed_by = "0"
    props.boundary_rim = "FREE"  # the arches end open at the vault's springing line
    props.seed = 5
    props.attempts = 4
    props.backtrack_budget = 100

    props.envelope = barrel_vault()
    props.envelope_mode = "SURFACE"
    props.surface_thickness = 0.4
    props.surface_layers = 1

    assembly = checked_solve(props, expected_slots=72)
    module_index = results.read_attribute(assembly, "wfc_module")
    assert set(module_index.tolist()) == {0}, set(module_index.tolist())
    # Freedom None means "follow the field": the placed +X (the rib's long axis)
    # lies in the arch plane, with no component along the vault's length (world X).
    rotation = results.read_attribute(assembly, "wfc_rotation")
    worst = max(abs((Quaternion(tuple(q)) @ Vector((1.0, 0.0, 0.0))).x) for q in rotation)
    print(f"gate C: rib kit on the vault, {len(rotation)} ribs, max |+X . length| {worst:.2e}")
    assert worst < 1e-3, worst


def main() -> None:
    """Run the three gate examples."""
    pipes_kit.setup_paths()
    pipes_kit.register_addon()
    part_a_sphere()
    part_b_freeform()
    part_c_vault()
    print("gate: surface-tutorial examples pass")


if __name__ == "__main__":
    main()
