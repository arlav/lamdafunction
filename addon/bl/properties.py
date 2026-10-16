# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``lamdafunction`` property groups.

- Every collection carries :class:`ModuleProps`; it matters only for the
  child collections of the library collection, which are the modules.
- The scene carries :class:`SceneProps`: the library, the envelope, connector
  pairs, rules, solve settings, the audit and the last report.

Face properties are flat (``connector_0`` … ``terminator_5``) rather than a
collection property, so a module needs no initialisation step and the UI can
draw them without writing.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from ..model.kit import FACE_NAMES

FREEDOM_ITEMS = [
    ("NONE", "None", "Only as modelled"),
    ("X", "X", "Four turns about X"),
    ("Y", "Y", "Four turns about Y"),
    ("Z", "Z", "Four turns about Z"),
    ("FULL", "Full", "All 24 rotations"),
]

RULE_KIND_ITEMS = [
    ("allowed", "Allowed", "These two faces may touch"),
    ("disallowed", "Disallowed", "These two faces may never touch"),
    ("exclusive", "Exclusive", "These two faces touch only each other"),
]

LEVEL_ICONS = {"error": "CANCEL", "warning": "ERROR", "info": "INFO"}


def _face_annotations() -> dict[str, object]:
    """Three properties per face: connector name, offset, terminator flag."""
    out: dict[str, object] = {}
    for i, name in enumerate(FACE_NAMES):
        out[f"connector_{i}"] = StringProperty(name=f"{name} connector", description="Connector name; empty leaves the face uncovered")
        out[f"offset_{i}"] = IntProperty(name=f"{name} offset", description="Quarter turns of the connector arrow", min=0, max=3, default=0)
        out[f"terminator_{i}"] = BoolProperty(name=f"{name} terminator", description="This face may sit at a closed boundary", default=False)
    return out


class ModuleProps(bpy.types.PropertyGroup):
    """Per-module settings on a library child collection."""

    freedom: EnumProperty(name="Rotation", items=FREEDOM_ITEMS, default="NONE")
    weight: FloatProperty(name="Weight", min=0.0, default=1.0, description="Relative likelihood; split across the module's rotations")
    tags: StringProperty(name="Tags", description="Comma-separated tags for wfc_w:#tag weights")


ModuleProps.__annotations__.update(_face_annotations())


class PairProps(bpy.types.PropertyGroup):
    """A connector pair with four twist flags."""

    a: StringProperty(name="A")
    b: StringProperty(name="B")
    t0: BoolProperty(name="0°", default=True)
    t1: BoolProperty(name="90°", default=True)
    t2: BoolProperty(name="180°", default=True)
    t3: BoolProperty(name="270°", default=True)


class RuleProps(bpy.types.PropertyGroup):
    """A rule as text, ``a:+X -> b:-X``."""

    kind: EnumProperty(name="Kind", items=RULE_KIND_ITEMS, default="allowed")
    text: StringProperty(name="Rule", default="a:+X -> b:-X")


class AuditProps(bpy.types.PropertyGroup):
    """One audit item, with a selectable target."""

    level: StringProperty()
    message: StringProperty()
    target_kind: StringProperty()
    target_name: StringProperty()
    target_index: IntProperty(default=-1)


def _is_mesh(_self: object, obj: bpy.types.Object) -> bool:
    """Envelope poll: mesh objects only."""
    return obj.type == "MESH"


class SceneProps(bpy.types.PropertyGroup):
    """Scene-level settings and state."""

    library: PointerProperty(name="Library", type=bpy.types.Collection, description="Collection whose child collections are the modules")
    envelope: PointerProperty(name="Envelope", type=bpy.types.Object, poll=_is_mesh, description="Mesh whose vertices are the slots")
    cell_size: FloatProperty(name="Cell size", min=1e-4, default=1.0, description="Size of one slot; used when the envelope has no wfc_cell attribute")
    pairs: CollectionProperty(type=PairProps)
    pair_index: IntProperty(default=0)
    rules: CollectionProperty(type=RuleProps)
    rule_index: IntProperty(default=0)
    indifference: BoolProperty(name="Indifference", default=True, description="Uncovered faces may meet uncovered faces")
    seed: IntProperty(name="Seed", default=1, min=0)
    attempts: IntProperty(name="Attempts", default=8, min=1)
    threads: IntProperty(name="Threads", default=0, min=0, description="0 uses every core")
    backtrack_budget: IntProperty(name="Backtracks", default=100, min=0)
    max_observations: IntProperty(name="Max observations", default=0, min=0, description="0 solves to the end")
    closed: BoolProperty(name="Closed boundary", default=True, description="Exposed faces must carry the boundary connector or be terminators")
    closed_by: StringProperty(name="Boundary connector", default="0")
    compile_hash: StringProperty()
    report: StringProperty()
    status: StringProperty()
    audit: CollectionProperty(type=AuditProps)
    audit_index: IntProperty(default=0)
    assembly_object: StringProperty(description="Name of the assembly object of the last solve")
    solving: BoolProperty(default=False)


CLASSES = (ModuleProps, PairProps, RuleProps, AuditProps, SceneProps)


def register() -> None:
    """Register the groups and attach them to Collection and Scene."""
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Collection.lamdafunction = PointerProperty(type=ModuleProps)
    bpy.types.Scene.lamdafunction = PointerProperty(type=SceneProps)


def unregister() -> None:
    """Detach and unregister."""
    del bpy.types.Scene.lamdafunction
    del bpy.types.Collection.lamdafunction
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
