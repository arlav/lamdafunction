# SPDX-License-Identifier: GPL-3.0-or-later
"""The pipes example end to end inside Blender: compile, solve, materialise, inspect.

Checks the tube-kit golden numbers through the add-on (37 variants, 20
slots), that the solve is valid, that the Materialise node group places one
instance per solved anchor with the module ``wfc_module`` names, and pins a
hash of the written attributes for seed 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipes_kit  # noqa: E402

GOLDEN_HASH_SEED_1 = "ee1b017f2c553301"  # lamda_core wheel, seed 1, Blender 4.3.2; the reference must give the same


def main() -> None:
    """Run the pipes scene through the add-on."""
    solver = pipes_kit.setup_paths()
    pipes_kit.register_addon()
    bpy.ops.wm.read_factory_settings(use_empty=True)  # a clean file, whatever ran before in this process
    pipes_kit.build_scene()
    scene = bpy.context.scene
    props = scene.lamdafunction

    assert bpy.ops.lamdafunction.compile() == {"FINISHED"}
    text = bpy.data.texts["lamdafunction.assembly.json"].as_string()
    doc = json.loads(text)
    assert len(doc["variants"]) == 37, len(doc["variants"])
    assert doc["slots"] == 24  # the full 4 x 3 x 2 box
    messages = [(a.level, a.message) for a in props.audit]
    assert not any(level == "error" for level, _ in messages), messages
    print("audit:", messages)

    assert bpy.ops.lamdafunction.solve() == {"FINISHED"}
    assert props.status == "Solved", props.status
    print("report:", props.report)
    assembly = bpy.data.objects[props.assembly_object]
    from addon.bl import results

    state = results.read_attribute(assembly, "wfc_state")
    module = results.read_attribute(assembly, "wfc_module")
    anchor = results.read_attribute(assembly, "wfc_anchor")
    assert (state == 0).all()
    assert anchor.all()
    report = json.loads(assembly["lamdafunction_report"])
    assert report["outcome"] == "solved"
    assert json.loads(assembly["lamdafunction_violations"]) == []

    assert bpy.ops.lamdafunction.materialise() == {"FINISHED"}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    # DepsgraphObjectInstance handles are only valid inside the iteration, so copy what is needed.
    instances: list[tuple[tuple[float, float, float], str]] = []
    for inst in depsgraph.object_instances:
        if inst.is_instance and inst.parent is not None and inst.parent.original == assembly:
            instances.append((tuple(inst.matrix_world.translation), inst.instance_object.original.name))
    assert len(instances) == int(state.size), (len(instances), state.size)
    cells = results.read_attribute(assembly, "wfc_cell")
    names = list(pipes_kit.MODULES)
    for pos, instanced in instances:
        slot = min(range(len(cells)), key=lambda s: sum((cells[s][k] * props.cell_size - pos[k]) ** 2 for k in range(3)))
        expected = names[int(module[slot])]
        assert instanced == expected, (instanced, expected)

    assert bpy.ops.lamdafunction.inspect() == {"FINISHED"}
    bpy.context.evaluated_depsgraph_get()

    digest = results.attribute_hash(assembly)
    print(f"pipes: {solver} solver, {len(instances)} instances, attribute hash {digest}")
    # Both backends must produce the same result for the same seed (differential tests
    # cover the general case; this pins the pipes example).
    assert digest == GOLDEN_HASH_SEED_1, (solver, digest)


if __name__ == "__main__":
    main()
