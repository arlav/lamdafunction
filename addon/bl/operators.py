# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: compile, solve, materialise, and the small editing helpers.

The solve follows ``.claude/rules/blender-addon.md``, "Threading and data
flow": compile on the main thread, solve on a worker thread with the GIL
released (``lamda_core``), poll progress from a modal timer, cancel on Esc,
write results on the main thread as one undo step. In background mode (tests,
``blender -b``) there is no event loop, so the same job runs to completion
synchronously.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

import bpy
from bpy.props import IntProperty, StringProperty

from ..compile.assemble import Compiled, compile_assembly
from ..model.envelope import SolveSettings
from . import backend as backend_module
from . import envelope as envelope_module
from . import library as library_module
from . import nodes, overlay, results
from .envelope import Slots
from .properties import SHELL_GROUPS, SceneProps

ASSEMBLY_TEXT = "lamdafunction.assembly.json"


@dataclass
class SolveJob:
    """One solve in flight: the compiled text, the backend, the worker thread and its result."""

    compiled: Compiled
    text: str
    backend: backend_module.Backend
    progress: Any
    slots: Slots
    seed: int
    cell_size: float
    started: float = field(default_factory=time.perf_counter)
    thread: threading.Thread | None = None
    result: Any = None
    error: str = ""

    def start(self) -> None:
        """Run the solve on a daemon thread."""

        def work() -> None:
            try:
                self.result = self.backend.solve(self.text, self.progress)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                self.error = str(exc)

        self.thread = threading.Thread(target=work, name="lamdafunction-solve", daemon=True)
        self.thread.start()

    def is_alive(self) -> bool:
        """Whether the worker is still solving."""
        return self.thread is not None and self.thread.is_alive()

    def status(self) -> str:
        """One line for the status bar."""
        snap = self.progress.snapshot()
        return (
            f"LamdaFunction: attempt {snap.get('attempt', 0)}, {snap.get('collapsed', 0)}/{self.slots.count} slots, "
            f"{snap.get('backtracks', 0)} backtracks, {time.perf_counter() - self.started:.1f} s (Esc cancels)"
        )


_JOB: SolveJob | None = None


def store_text(name: str, text: str) -> bpy.types.Text:
    """Keep the compiled assembly in a Text datablock, so it travels with the file."""
    block = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    block.clear()
    block.write(text)
    return block


def settings_from_props(props: SceneProps) -> SolveSettings:
    """Solve settings from the scene properties."""
    return SolveSettings(
        seed=props.seed,
        attempts=props.attempts,
        threads=props.threads,
        backtrack_budget=props.backtrack_budget,
        max_observations=props.max_observations,
        closed_by=props.closed_by.strip() if props.closed and props.closed_by.strip() else None,
        boundary_groups=boundary_groups(props),
    )


def boundary_groups(props: SceneProps) -> tuple[tuple[str, str | None], ...]:
    """Per-group boundary overrides of a shell (top, bottom, rim), sorted by group; none in points mode.

    "Closed by" with an empty connector counts as free.
    """
    if props.envelope_mode != "SURFACE":
        return ()
    out: list[tuple[str, str | None]] = []
    for group in SHELL_GROUPS:
        policy = getattr(props, f"boundary_{group}")
        if policy == "FREE":
            out.append((group, None))
        elif policy == "CLOSED":
            connector = getattr(props, f"boundary_{group}_by").strip()
            out.append((group, connector or None))
    return tuple(sorted(out))


def fill_audit(props: SceneProps, compiled: Compiled) -> None:
    """Copy audit items into the scene for the panel."""
    props.audit.clear()
    for item in compiled.audit:
        entry = props.audit.add()
        entry.level = item.level
        entry.message = item.message
        entry.target_kind = str(item.target[0]) if item.target else ""
        entry.target_name = str(item.target[1]) if len(item.target) > 1 else ""
        entry.target_index = int(item.target[2]) if len(item.target) > 2 else -1
    props.audit_index = 0


def compile_scene(context: bpy.types.Context) -> tuple[Compiled, Slots, backend_module.Backend]:
    """Read the library and the envelope, compile, audit with the backend's canonicalisation, store the text.

    In surface mode the shell's field (each cell's +X and the seams) goes to the overlay.
    """
    scene = context.scene
    props = scene.lamdafunction
    if props.envelope is None:
        raise ValueError("no envelope object is set")
    depsgraph = context.evaluated_depsgraph_get()
    kit = library_module.read_kit(props, depsgraph)
    slots = envelope_module.read_slots(props.envelope, props, depsgraph)
    overlay.set_field(slots.envelope if slots.surface else None)  # type: ignore[arg-type]
    chosen = backend_module.choose_backend()
    compiled = compile_assembly(kit, slots.envelope, settings_from_props(props), chosen.canonicalise)
    store_text(ASSEMBLY_TEXT, compiled.text)
    props.compile_hash = compiled.document["compile_hash"]
    fill_audit(props, compiled)
    nodes.ensure_instancer(scene, props.library)
    return compiled, slots, chosen


def start_job(context: bpy.types.Context) -> SolveJob:
    """Compile and start solving."""
    compiled, slots, chosen = compile_scene(context)
    if compiled.has_errors:
        raise ValueError("the audit reports errors; fix them before solving")
    props = context.scene.lamdafunction
    job = SolveJob(compiled, compiled.text, chosen, chosen.new_progress(), slots, props.seed, props.cell_size)
    props.solving = True
    props.status = f"Solving with {chosen.name}"
    job.start()
    return job


def finish_job(context: bpy.types.Context, job: SolveJob) -> None:
    """Write the result into the assembly object and the report into the scene."""
    scene = context.scene
    props = scene.lamdafunction
    props.solving = False
    if job.error:
        props.status = f"Solve failed: {job.error}"
        props.report = ""
        return
    obj = results.ensure_assembly_object(scene)
    results.write_result(obj, job.slots, job.result, job.compiled.document["compile_hash"], job.seed, job.cell_size)
    props.assembly_object = obj.name
    props.report = results.report_summary(job.result.report)
    violations = job.backend.validate(job.text, np.asarray(job.result.variant), np.asarray(job.result.state))
    props.status = "Solved" if not violations else f"Result has {len(violations)} violations: {violations[0]}"
    obj["lamdafunction_violations"] = json.dumps(violations[:50])


def solve_blocking(context: bpy.types.Context) -> SolveJob:
    """Compile, solve and write synchronously; used in background mode and by tests."""
    job = start_job(context)
    assert job.thread is not None
    job.thread.join()
    finish_job(context, job)
    return job


class LAMDAFUNCTION_OT_new_library(bpy.types.Operator):
    """Create the library collection and set it as the scene's library."""

    bl_idname = "lamdafunction.new_library"
    bl_label = "New Library"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        library_module.ensure_library(context.scene)
        return {"FINISHED"}


class LAMDAFUNCTION_OT_add_module(bpy.types.Operator):
    """Add a module collection (with a cube of one cell) to the library."""

    bl_idname = "lamdafunction.add_module"
    bl_label = "Add Module"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="module")

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[str]:
        library_module.add_module(context.scene, self.name, context.scene.lamdafunction.cell_size)
        return {"FINISHED"}


class LAMDAFUNCTION_OT_box_envelope(bpy.types.Operator):
    """Create a box envelope of the given size in cells."""

    bl_idname = "lamdafunction.box_envelope"
    bl_label = "Box Envelope"
    bl_options = {"REGISTER", "UNDO"}

    size_x: IntProperty(name="X", default=6, min=1)
    size_y: IntProperty(name="Y", default=6, min=1)
    size_z: IntProperty(name="Z", default=3, min=1)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context: bpy.types.Context) -> set[str]:
        envelope_module.box_envelope(context.scene, (self.size_x, self.size_y, self.size_z), context.scene.lamdafunction.cell_size)
        return {"FINISHED"}


class LAMDAFUNCTION_OT_add_pair(bpy.types.Operator):
    """Add a connector pair."""

    bl_idname = "lamdafunction.add_pair"
    bl_label = "Add Pair"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        pair = props.pairs.add()
        pair.a = "0"
        pair.b = "0"
        props.pair_index = len(props.pairs) - 1
        return {"FINISHED"}


class LAMDAFUNCTION_OT_remove_pair(bpy.types.Operator):
    """Remove the selected connector pair."""

    bl_idname = "lamdafunction.remove_pair"
    bl_label = "Remove Pair"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        if 0 <= props.pair_index < len(props.pairs):
            props.pairs.remove(props.pair_index)
            props.pair_index = min(props.pair_index, len(props.pairs) - 1)
        return {"FINISHED"}


class LAMDAFUNCTION_OT_add_rule(bpy.types.Operator):
    """Add a rule."""

    bl_idname = "lamdafunction.add_rule"
    bl_label = "Add Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        props.rules.add()
        props.rule_index = len(props.rules) - 1
        return {"FINISHED"}


class LAMDAFUNCTION_OT_remove_rule(bpy.types.Operator):
    """Remove the selected rule."""

    bl_idname = "lamdafunction.remove_rule"
    bl_label = "Remove Rule"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        if 0 <= props.rule_index < len(props.rules):
            props.rules.remove(props.rule_index)
            props.rule_index = min(props.rule_index, len(props.rules) - 1)
        return {"FINISHED"}


class LAMDAFUNCTION_OT_compile(bpy.types.Operator):
    """Compile the kit and envelope and run the audit."""

    bl_idname = "lamdafunction.compile"
    bl_label = "Compile"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            compiled, slots, chosen = compile_scene(context)
        except (ValueError, ImportError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        props = context.scene.lamdafunction
        props.status = f"Compiled {slots.count} slots, {len(compiled.document['variants'])} variants with {chosen.name}"
        if compiled.has_errors:
            self.report({"WARNING"}, "the audit reports errors")
        return {"FINISHED"}


class LAMDAFUNCTION_OT_solve(bpy.types.Operator):
    """Compile and solve; runs on a worker thread, Esc cancels."""

    bl_idname = "lamdafunction.solve"
    bl_label = "Solve"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        props = context.scene.lamdafunction
        return props.envelope is not None and props.library is not None and not props.solving

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return self.execute(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _JOB
        try:
            job = start_job(context)
        except (ValueError, ImportError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        _JOB = job
        if bpy.app.background:
            assert job.thread is not None
            job.thread.join()
            finish_job(context, job)
            _JOB = None
            return {"FINISHED"}
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        global _JOB
        job = _JOB
        if job is None:
            return {"FINISHED"}
        if event.type == "ESC":
            job.progress.cancel()
        if event.type == "TIMER":
            if job.is_alive():
                context.workspace.status_text_set(job.status())
                context.scene.lamdafunction.status = job.status()
                return {"PASS_THROUGH"}
            context.window_manager.event_timer_remove(self._timer)
            context.workspace.status_text_set(None)
            finish_job(context, job)
            _JOB = None
            self.report({"INFO"}, context.scene.lamdafunction.report or context.scene.lamdafunction.status)
            return {"FINISHED"}
        return {"PASS_THROUGH"}


class LAMDAFUNCTION_OT_cancel(bpy.types.Operator):
    """Cancel the running solve."""

    bl_idname = "lamdafunction.cancel"
    bl_label = "Cancel"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _JOB is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        if _JOB is not None:
            _JOB.progress.cancel()
        return {"FINISHED"}


class LAMDAFUNCTION_OT_materialise(bpy.types.Operator):
    """Instance the library on the assembly with the Materialise node group (on a surface, optionally deformed to the cells)."""

    bl_idname = "lamdafunction.materialise"
    bl_label = "Materialise"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        props = context.scene.lamdafunction
        return bool(props.assembly_object) and props.assembly_object in bpy.data.objects and props.library is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        scene = context.scene
        props = scene.lamdafunction
        obj = bpy.data.objects[props.assembly_object]
        instancer = nodes.ensure_instancer(scene, props.library)
        tree = bpy.data.node_groups.get(nodes.MATERIALISE)
        if tree is None or not nodes.has_input(tree, "Deform"):
            # Missing, or built by an add-on version before Deform to cells existed.
            tree = nodes.ensure_materialise_group()
        modifier = nodes.attach_group(obj, tree, "LamdaFunction Materialise")
        nodes.set_group_input(modifier, "Library", instancer)
        # Only a surface assembly has corners to deform into; a box assembly stays rigid.
        surface = "wfc_c0" in obj.data.attributes
        nodes.set_group_input(modifier, "Deform", bool(props.deform_to_cells and surface))
        nodes.set_group_input(modifier, "Cell Size", float(props.cell_size))
        return {"FINISHED"}


class LAMDAFUNCTION_OT_inspect(bpy.types.Operator):
    """Show slot states as coloured cubes on the assembly."""

    bl_idname = "lamdafunction.inspect"
    bl_label = "Inspect"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        props = context.scene.lamdafunction
        return bool(props.assembly_object) and props.assembly_object in bpy.data.objects

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        obj = bpy.data.objects[props.assembly_object]
        tree = bpy.data.node_groups.get(nodes.INSPECT) or nodes.ensure_inspect_group(props.cell_size)
        nodes.attach_group(obj, tree, "LamdaFunction Inspect")
        return {"FINISHED"}


class LAMDAFUNCTION_OT_select_audit(bpy.types.Operator):
    """Select the culprit of the active audit item in the viewport."""

    bl_idname = "lamdafunction.select_audit"
    bl_label = "Select Culprit"

    def execute(self, context: bpy.types.Context) -> set[str]:
        props = context.scene.lamdafunction
        if not 0 <= props.audit_index < len(props.audit):
            return {"CANCELLED"}
        item = props.audit[props.audit_index]
        if item.target_kind in ("module", "face") and props.library is not None:
            collection = bpy.data.collections.get(item.target_name)
            if collection is None:
                return {"CANCELLED"}
            for obj in context.view_layer.objects:
                obj.select_set(False)
            for obj in collection.all_objects:
                obj.select_set(True)
                context.view_layer.objects.active = obj
            self.report({"INFO"}, item.message)
        elif item.target_kind == "slot" and props.envelope is not None:
            depsgraph = context.evaluated_depsgraph_get()
            positions = envelope_module.read_slots(props.envelope, props, depsgraph).positions
            slot = int(item.target_name) if item.target_name.isdigit() else item.target_index
            if 0 <= slot < len(positions):
                context.scene.cursor.location = positions[slot]
            self.report({"INFO"}, item.message)
        elif item.target_kind.startswith("mesh_") and props.envelope is not None:
            # Mesh targets index the envelope's evaluated mesh: put the cursor on the culprit.
            surface = envelope_module.read_surface_mesh(props.envelope, context.evaluated_depsgraph_get())
            point = _mesh_target(surface, item.target_kind, item.target_name, item.target_index)
            if point is not None:
                context.scene.cursor.location = point
            self.report({"INFO"}, item.message)
        else:
            self.report({"INFO"}, item.message)
        return {"FINISHED"}


def _mesh_target(surface: envelope_module.SurfaceMesh, kind: str, name: str, index: int) -> tuple[float, float, float] | None:
    """World position of a ``mesh_vertex``, ``mesh_edge`` (name, index = its two vertices) or ``mesh_face`` target."""
    if not name.isdigit():
        return None
    first = int(name)
    if kind == "mesh_vertex" and first < len(surface.vertices):
        chosen = [first]
    elif kind == "mesh_edge" and max(first, index) < len(surface.vertices) and index >= 0:
        chosen = [first, index]
    elif kind == "mesh_face" and first < len(surface.polygons):
        chosen = surface.polygons[first]
    else:
        return None
    point = surface.vertices[chosen].mean(axis=0)
    return (float(point[0]), float(point[1]), float(point[2]))


CLASSES = (
    LAMDAFUNCTION_OT_new_library,
    LAMDAFUNCTION_OT_add_module,
    LAMDAFUNCTION_OT_box_envelope,
    LAMDAFUNCTION_OT_add_pair,
    LAMDAFUNCTION_OT_remove_pair,
    LAMDAFUNCTION_OT_add_rule,
    LAMDAFUNCTION_OT_remove_rule,
    LAMDAFUNCTION_OT_compile,
    LAMDAFUNCTION_OT_solve,
    LAMDAFUNCTION_OT_cancel,
    LAMDAFUNCTION_OT_materialise,
    LAMDAFUNCTION_OT_inspect,
    LAMDAFUNCTION_OT_select_audit,
)


def register() -> None:
    """Register the operators."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister the operators."""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
