# SPDX-License-Identifier: GPL-3.0-or-later
"""Initial domains, weights, priorities and boundary classes per slot.

Build order step 11 (``docs/PLAN.md``, "Rule compiler"):

- A module is allowed in a slot when its weight multiplier there is above
  zero and the slot is not fixed to another module. The multiplier is the
  product of the envelope's ``wfc_w:`` inputs that name the module or one of
  its tags, taken in sorted key order so both languages multiply alike.
- A module's weight is split across its variants, so a module with 24
  rotations is not 24 times likelier than a fixed one.
- With a closed boundary, an exposed world face ``d`` admits the variants
  whose face ``d`` carries the boundary connector or is a terminator; that
  set is one boundary class per world face.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.envelope import Envelope, SolveSettings
from ..model.kit import Kit
from .graph import SlotGraph
from .variants import Expansion


@dataclass(frozen=True)
class Domains:
    """Everything per slot that the compiled assembly carries besides edges."""

    domain_classes: tuple[tuple[int, ...], ...]
    slot_domain: tuple[int, ...]
    variant_weight: tuple[float, ...]
    slot_module_weight: tuple[tuple[float, ...], ...] | None
    priority: tuple[int, ...] | None
    boundary_classes: tuple[tuple[str, tuple[int, ...]], ...]
    boundary: tuple[tuple[int, int, int], ...]
    empty_slots: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        """The matching fields of the compiled assembly."""
        data: dict[str, Any] = {
            "domain_classes": [list(c) for c in self.domain_classes],
            "slot_domain": list(self.slot_domain),
            "variant_weight": list(self.variant_weight),
            "boundary_classes": [{"connector": name, "variants": list(v)} for name, v in self.boundary_classes],
            "boundary": [{"slot": s, "port": p, "class": c} for s, p, c in self.boundary],
        }
        if self.slot_module_weight is not None:
            data["slot_module_weight"] = [list(row) for row in self.slot_module_weight]
        if self.priority is not None:
            data["priority"] = list(self.priority)
        return data


def module_multipliers(kit: Kit, envelope: Envelope) -> list[list[float]] | None:
    """Per slot, per module: the product of the matching ``wfc_w:`` inputs, or ``None`` when there are none."""
    if not envelope.weights:
        return None
    keys = sorted(envelope.weights)
    rows: list[list[float]] = []
    for slot in range(len(envelope.cells)):
        row: list[float] = []
        for module in kit.modules:
            factor = 1.0
            for key in keys:
                matches = key == module.name or (key.startswith("#") and key[1:] in module.tags)
                if matches:
                    factor = factor * envelope.weights[key][slot]
            row.append(factor)
        rows.append(row)
    return rows


def build_domains(kit: Kit, envelope: Envelope, settings: SolveSettings, expansion: Expansion, graph: SlotGraph) -> Domains:
    """Domain classes, weights, priorities and boundary classes for every slot."""
    multipliers = module_multipliers(kit, envelope)
    classes: dict[tuple[int, ...], int] = {}
    slot_domain: list[int] = []
    empty: list[int] = []
    for slot in range(len(envelope.cells)):
        allowed: list[int] = []
        for module_index, variants in enumerate(expansion.by_module):
            if multipliers is not None and multipliers[slot][module_index] <= 0.0:
                continue
            fixed = envelope.fixed[slot] if envelope.fixed is not None else -1
            if fixed >= 0 and fixed != module_index:
                continue
            allowed.extend(variants)
        domain = tuple(sorted(allowed))
        if not domain:
            empty.append(slot)
        slot_domain.append(classes.setdefault(domain, len(classes)))

    variant_weight = [
        kit.modules[v.module].weight / len(expansion.by_module[v.module]) for v in expansion.variants
    ]

    boundary_classes: dict[tuple[str, tuple[int, ...]], int] = {}
    boundary: list[tuple[int, int, int]] = []
    if settings.closed_by is not None:
        name = settings.closed_by
        per_face = [
            tuple(v.index for v in expansion.variants if v.faces[d].name == name or v.terminator[d])
            for d in range(6)
        ]
        for slot, port in graph.exposed:
            key = (name, per_face[port])
            boundary.append((slot, port, boundary_classes.setdefault(key, len(boundary_classes))))

    return Domains(
        domain_classes=tuple(classes),
        slot_domain=tuple(slot_domain),
        variant_weight=tuple(variant_weight),
        slot_module_weight=None if multipliers is None else tuple(tuple(r) for r in multipliers),
        priority=None if envelope.priority is None else tuple(envelope.priority),
        boundary_classes=tuple(boundary_classes),
        boundary=tuple(boundary),
        empty_slots=tuple(empty),
    )
