# SPDX-License-Identifier: GPL-3.0-or-later
"""The LamdaFunction sidebar tab: Library, Faces and Connectors, Rules, Envelope, Solve, Results and Audit."""

from __future__ import annotations

import bpy

from ..model.kit import FACE_NAMES
from .properties import LEVEL_ICONS


def active_module(context: bpy.types.Context) -> bpy.types.Collection | None:
    """The module collection being edited: the active collection if it is a library child, else the active object's."""
    props = context.scene.lamdafunction
    library = props.library
    if library is None:
        return None
    children = set(library.children)
    collection = context.collection
    if collection is not None and collection in children:
        return collection
    obj = context.active_object
    if obj is not None:
        for collection in obj.users_collection:
            if collection in children:
                return collection
    return None


class LamdaPanel:
    """Common settings of every panel."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LamdaFunction"


class LAMDAFUNCTION_PT_library(LamdaPanel, bpy.types.Panel):
    """Library: the modules."""

    bl_label = "Library"
    bl_idname = "LAMDAFUNCTION_PT_library"

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.lamdafunction
        layout = self.layout
        layout.prop(props, "library")
        row = layout.row(align=True)
        row.operator("lamdafunction.new_library", icon="COLLECTION_NEW")
        row.operator("lamdafunction.add_module", icon="ADD")
        layout.prop(props, "cell_size")
        if props.library is None:
            return
        box = layout.box()
        for index, module in enumerate(props.library.children):
            row = box.row(align=True)
            row.label(text=f"{index:02d}  {module.name}")
            row.prop(module.lamdafunction, "freedom", text="")
            row.prop(module.lamdafunction, "weight", text="")


class LAMDAFUNCTION_PT_faces(LamdaPanel, bpy.types.Panel):
    """Faces and connectors of the active module."""

    bl_label = "Faces and Connectors"
    bl_idname = "LAMDAFUNCTION_PT_faces"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        module = active_module(context)
        if module is None:
            layout.label(text="Select an object of a module", icon="INFO")
            return
        props = module.lamdafunction
        layout.label(text=module.name, icon="OUTLINER_COLLECTION")
        layout.prop(props, "tags")
        for i, name in enumerate(FACE_NAMES):
            row = layout.row(align=True)
            row.label(text=name)
            row.prop(props, f"connector_{i}", text="")
            row.prop(props, f"offset_{i}", text="")
            row.prop(props, f"terminator_{i}", text="", icon="PINNED", toggle=True)


class LAMDAFUNCTION_UL_pairs(bpy.types.UIList):
    """Connector pair rows."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):  # noqa: D102, PLR0913
        row = layout.row(align=True)
        row.prop(item, "a", text="")
        row.prop(item, "b", text="")
        for flag in ("t0", "t1", "t2", "t3"):
            row.prop(item, flag, toggle=True)


class LAMDAFUNCTION_UL_rules(bpy.types.UIList):
    """Rule rows."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):  # noqa: D102, PLR0913
        row = layout.row(align=True)
        row.prop(item, "kind", text="")
        row.prop(item, "text", text="")


class LAMDAFUNCTION_UL_audit(bpy.types.UIList):
    """Audit rows."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):  # noqa: D102, PLR0913
        layout.label(text=item.message, icon=LEVEL_ICONS.get(item.level, "INFO"))


class LAMDAFUNCTION_PT_rules(LamdaPanel, bpy.types.Panel):
    """Connector pairs and rules."""

    bl_label = "Rules"
    bl_idname = "LAMDAFUNCTION_PT_rules"

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.lamdafunction
        layout = self.layout
        layout.prop(props, "indifference")
        layout.label(text="Connector pairs (twists 0, 90, 180, 270)")
        row = layout.row()
        row.template_list("LAMDAFUNCTION_UL_pairs", "", props, "pairs", props, "pair_index", rows=3)
        col = row.column(align=True)
        col.operator("lamdafunction.add_pair", text="", icon="ADD")
        col.operator("lamdafunction.remove_pair", text="", icon="REMOVE")
        layout.label(text="Rules, as a:+X -> b:-X")
        row = layout.row()
        row.template_list("LAMDAFUNCTION_UL_rules", "", props, "rules", props, "rule_index", rows=3)
        col = row.column(align=True)
        col.operator("lamdafunction.add_rule", text="", icon="ADD")
        col.operator("lamdafunction.remove_rule", text="", icon="REMOVE")


class LAMDAFUNCTION_PT_envelope(LamdaPanel, bpy.types.Panel):
    """Envelope."""

    bl_label = "Envelope"
    bl_idname = "LAMDAFUNCTION_PT_envelope"

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.lamdafunction
        layout = self.layout
        layout.prop(props, "envelope")
        layout.operator("lamdafunction.box_envelope", icon="MESH_GRID")
        row = layout.row(align=True)
        row.prop(props, "closed")
        sub = row.row()
        sub.active = props.closed
        sub.prop(props, "closed_by", text="")


class LAMDAFUNCTION_PT_solve(LamdaPanel, bpy.types.Panel):
    """Solve settings and buttons."""

    bl_label = "Solve"
    bl_idname = "LAMDAFUNCTION_PT_solve"

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.lamdafunction
        layout = self.layout
        col = layout.column(align=True)
        col.prop(props, "seed")
        col.prop(props, "attempts")
        col.prop(props, "backtrack_budget")
        col.prop(props, "max_observations")
        col.prop(props, "threads")
        row = layout.row(align=True)
        row.operator("lamdafunction.compile", icon="FILE_REFRESH")
        row.operator("lamdafunction.solve", icon="PLAY")
        row.operator("lamdafunction.cancel", icon="X")
        if props.status:
            layout.label(text=props.status)


class LAMDAFUNCTION_PT_results(LamdaPanel, bpy.types.Panel):
    """Results and audit."""

    bl_label = "Results and Audit"
    bl_idname = "LAMDAFUNCTION_PT_results"

    def draw(self, context: bpy.types.Context) -> None:
        props = context.scene.lamdafunction
        layout = self.layout
        if props.report:
            layout.label(text=props.report, icon="CHECKMARK")
        row = layout.row(align=True)
        row.operator("lamdafunction.materialise", icon="OUTLINER_OB_GROUP_INSTANCE")
        row.operator("lamdafunction.inspect", icon="VIEWZOOM")
        if props.compile_hash:
            layout.label(text=f"Compile hash {props.compile_hash}")
        layout.template_list("LAMDAFUNCTION_UL_audit", "", props, "audit", props, "audit_index", rows=4)
        layout.operator("lamdafunction.select_audit", icon="RESTRICT_SELECT_OFF")


CLASSES = (
    LAMDAFUNCTION_UL_pairs,
    LAMDAFUNCTION_UL_rules,
    LAMDAFUNCTION_UL_audit,
    LAMDAFUNCTION_PT_library,
    LAMDAFUNCTION_PT_faces,
    LAMDAFUNCTION_PT_rules,
    LAMDAFUNCTION_PT_envelope,
    LAMDAFUNCTION_PT_solve,
    LAMDAFUNCTION_PT_results,
)


def register() -> None:
    """Register lists and panels."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister lists and panels."""
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
