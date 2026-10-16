# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn connector pairs and rules into allowed variant pairs per port.

Build order steps 4 to 8 (``docs/PLAN.md``, "Rule compiler"), in this order:

1. **Connector pairs.** Variant A's world face ``d`` carries ``(name_a, k_a)``
   and B's face ``opposite(d)`` carries ``(name_b, k_b)``. The touch is
   allowed when a pair ``(name_a, name_b)`` is declared (in either order) and
   its flag for ``twist(k_a, k_b)`` is set (``frames.twist``).
2. **Indifference.** Two uncovered faces may meet at any twist. An uncovered
   face never meets a named one.
3. **Allowed rules** ``a:fa -> b:fb``. The rule describes a reference
   placement: ``a`` unrotated and ``b`` turned by the reference rotation
   ``Q`` that brings its face ``fb`` opposite ``fa`` (identity when the faces
   are opposite, the single quarter turn when they are perpendicular, a half
   turn when they are the same face). When both modules are rotatable the
   pair rotates rigidly: ``(a, R)`` meets ``(b, R·Q)`` for every ``R``. When
   at most one side is rotatable, that side may take any rotation that puts
   its face across the touch.
4. **Disallowed rules** remove every touch of the two named faces, in every
   rotation of both modules.
5. **Exclusive rules** are allowed rules after which face ``fa`` of ``a``
   meets nothing but face ``fb`` of ``b``, and vice versa, in every rotation.

Every pair is inserted and removed symmetrically, so the result satisfies the
symmetry invariant of ``.claude/rules/solver-core.md`` by construction; the
compiler still asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.kit import ConnectorPair, Kit, Rule
from . import frames
from .rotations import AXES, compose, cross, dot, freedom_rotations, neg, quarter_turn
from .variants import Expansion, VariantInfo

Pairs = list[set[tuple[int, int]]]
"""Allowed ``(v, w)`` per port: ``w`` may sit across that port from ``v``."""


@dataclass
class RuleNotes:
    """What rule processing wants the audit to know."""

    missing_modules: list[Rule] = field(default_factory=list)
    empty_rules: list[Rule] = field(default_factory=list)
    unused_connectors: list[str] = field(default_factory=list)


def new_pairs() -> Pairs:
    """Six empty pair sets."""
    return [set() for _ in range(6)]


def add_pair(pairs: Pairs, port: int, v: int, w: int) -> None:
    """Allow ``w`` across ``port`` from ``v``, and the mirror."""
    pairs[port].add((v, w))
    pairs[frames.opposite(port)].add((w, v))


def remove_pair(pairs: Pairs, port: int, v: int, w: int) -> None:
    """Forbid ``w`` across ``port`` from ``v``, and the mirror."""
    pairs[port].discard((v, w))
    pairs[frames.opposite(port)].discard((w, v))


def pair_table(kit: Kit) -> dict[tuple[str, str], tuple[bool, bool, bool, bool]]:
    """Declared connector pairs keyed both ways; repeated declarations OR their flags."""
    table: dict[tuple[str, str], tuple[bool, bool, bool, bool]] = {}
    for pair in kit.pairs:
        for key in ((pair.a, pair.b), (pair.b, pair.a)):
            old = table.get(key, (False, False, False, False))
            table[key] = tuple(o or n for o, n in zip(old, pair.twists))  # type: ignore[assignment]
    return table


def connector_pairs(kit: Kit, expansion: Expansion, pairs: Pairs) -> None:
    """Steps 1 and 2: connector pairs with twist flags, and Indifference."""
    table = pair_table(kit)
    variants = expansion.variants
    for port in range(6):
        across = frames.opposite(port)
        for a in variants:
            ca = a.faces[port]
            for b in variants:
                cb = b.faces[across]
                if ca.covered and cb.covered:
                    flags = table.get((ca.name, cb.name))
                    if flags is not None and flags[frames.twist(ca.offset, cb.offset)]:
                        pairs[port].add((a.index, b.index))
                elif not ca.covered and not cb.covered and kit.indifference:
                    pairs[port].add((a.index, b.index))


def reference_rotation(face_a: int, face_b: int) -> int:
    """The rotation ``Q`` of module b that brings its face ``face_b`` opposite ``face_a``."""
    normal_a = frames.FACE_NORMAL[face_a]
    normal_b = frames.FACE_NORMAL[face_b]
    target = neg(normal_a)
    if normal_b == target:
        return 0
    if normal_b == normal_a:
        # Same face: a half turn about the first axis perpendicular to it, preferring Z, then Y, then X.
        for axis in (AXES[4], AXES[2], AXES[0]):
            if dot(axis, normal_a) == 0:
                quarter = quarter_turn(axis)
                return compose(quarter, quarter)
    return quarter_turn(cross(normal_b, target))


def face_ports(kit: Kit, expansion: Expansion, module: int, face: int) -> set[tuple[int, int]]:
    """Every ``(world port, variant)`` where authored ``face`` of ``module`` lands, over the module's rotations.

    Rotations merged into one variant are all visited, so a face that lands
    on a world port under any merged rotation counts for that variant.
    """
    out: set[tuple[int, int]] = set()
    for rotation in freedom_rotations(kit.modules[module].freedom):
        variant = expansion.variant_for(module, rotation)
        if variant is not None:
            out.add((frames.FACE_TABLE[rotation][face][0], variant))
    return out


def rule_touches(rule: Rule, kit: Kit, expansion: Expansion, rigid: bool) -> set[tuple[int, int, int]]:
    """Every ``(port, v, w)`` the rule speaks about: v of module a with face_a on ``port``, w of b with face_b across.

    With ``rigid`` only pairs where ``w`` is ``(b, R·Q)`` for ``v = (a, R)``,
    ``Q`` the reference rotation. Rotations are enumerated from the modules'
    freedoms, not from the deduplicated variants, so merged rotations
    contribute their partners too.
    """
    a = kit.module_index(rule.module_a)
    b = kit.module_index(rule.module_b)
    touches: set[tuple[int, int, int]] = set()
    if rigid:
        q = reference_rotation(rule.face_a, rule.face_b)
        for rotation in freedom_rotations(kit.modules[a].freedom):
            v = expansion.variant_for(a, rotation)
            partner = compose(rotation, q)
            w = expansion.variant_for(b, partner)
            if v is None or w is None:
                continue
            port = frames.FACE_TABLE[rotation][rule.face_a][0]
            if frames.FACE_TABLE[partner][rule.face_b][0] == frames.opposite(port):
                touches.add((port, v, w))
        return touches
    sides_b = face_ports(kit, expansion, b, rule.face_b)
    for port, v in face_ports(kit, expansion, a, rule.face_a):
        across = frames.opposite(port)
        for port_b, w in sides_b:
            if port_b == across:
                touches.add((port, v, w))
    return touches


def is_rotatable(kit: Kit, name: str) -> bool:
    """Whether a module's freedom allows more than the identity."""
    return kit.modules[kit.module_index(name)].freedom != "NONE"


def apply_rules(kit: Kit, expansion: Expansion, pairs: Pairs, notes: RuleNotes) -> None:
    """Steps 3 to 5: allowed, then disallowed, then exclusive rules."""
    known = {m.name for m in kit.modules}
    rules = [r for r in kit.rules if r.module_a in known and r.module_b in known]
    notes.missing_modules.extend(r for r in kit.rules if r.module_a not in known or r.module_b not in known)

    for rule in rules:
        if rule.kind in ("allowed", "exclusive"):
            rigid = is_rotatable(kit, rule.module_a) and is_rotatable(kit, rule.module_b)
            touches = rule_touches(rule, kit, expansion, rigid)
            if not touches:
                notes.empty_rules.append(rule)
            for port, v, w in sorted(touches):
                add_pair(pairs, port, v, w)
    for rule in rules:
        if rule.kind == "disallowed":
            touches = rule_touches(rule, kit, expansion, rigid=False)
            if not touches:
                notes.empty_rules.append(rule)
            for port, v, w in sorted(touches):
                remove_pair(pairs, port, v, w)
    for rule in rules:
        if rule.kind == "exclusive":
            restrict_to(kit, expansion, pairs, rule)


def restrict_to(kit: Kit, expansion: Expansion, pairs: Pairs, rule: Rule) -> None:
    """Keep, on face_a of a and face_b of b, only touches between those two faces (in any rotation)."""
    a = kit.module_index(rule.module_a)
    b = kit.module_index(rule.module_b)
    touches = rule_touches(rule, kit, expansion, rigid=False)
    for port, v in face_ports(kit, expansion, a, rule.face_a):
        for w in [w for (vv, w) in pairs[port] if vv == v]:
            if (port, v, w) not in touches:
                remove_pair(pairs, port, v, w)
    for port, w in face_ports(kit, expansion, b, rule.face_b):
        for v in [v for (ww, v) in pairs[port] if ww == w]:
            if (frames.opposite(port), v, w) not in touches:
                remove_pair(pairs, port, w, v)


def unused_connectors(kit: Kit) -> list[str]:
    """Connector names declared in pairs that no module face carries."""
    used = {c.name for m in kit.modules for c in m.faces if c.covered}
    declared = {name for p in kit.pairs for name in (p.a, p.b)}
    return sorted(declared - used)


def check_symmetry(pairs: Pairs) -> None:
    """The invariant the solver relies on: ``(v, w)`` at ``p`` iff ``(w, v)`` at ``opposite(p)``."""
    for port in range(6):
        mirror = pairs[frames.opposite(port)]
        for v, w in pairs[port]:
            if (w, v) not in mirror:
                raise AssertionError(f"asymmetric pair ({v}, {w}) at port {port}")


def build_pairs(kit: Kit, expansion: Expansion) -> tuple[Pairs, RuleNotes]:
    """Run every step and return the symmetric pair sets plus notes for the audit."""
    pairs = new_pairs()
    notes = RuleNotes()
    connector_pairs(kit, expansion, pairs)
    apply_rules(kit, expansion, pairs, notes)
    notes.unused_connectors = unused_connectors(kit)
    check_symmetry(pairs)
    return pairs, notes


def variant_description(kit: Kit, variant: VariantInfo) -> str:
    """``name@rotation`` with its covered world faces, for audit messages."""
    faces = ", ".join(f"{frames.FACE_NAMES[d]}={c.name}" + (f"/{c.offset}" if c.offset else "") for d, c in enumerate(variant.faces) if c.covered)
    return f"{kit.modules[variant.module].name}@{variant.rotation} [{faces}]"
