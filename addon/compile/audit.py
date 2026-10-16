# SPDX-License-Identifier: GPL-3.0-or-later
"""The audit: everything an author should know before solving.

``docs/PLAN.md``, "Audit". Each item carries a level, a message and a
*target* the UI can select in the viewport: ``("module", name)``,
``("face", name, face_index)``, ``("rule", text)``, ``("slot", index)`` or
``("kit", "")``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.kit import FACE_NAMES, Kit, Rule
from .domains import Domains
from .rules import RuleNotes
from .variants import Expansion

VARIANT_WARN = 2_000
"""Above this many variants, warn: spike 4 measured about 3 s per attempt here."""

VARIANT_ERROR = 16_000
"""Above this many variants, refuse: spike 4 could not set up within a minute."""


@dataclass(frozen=True)
class AuditItem:
    """One audit finding."""

    level: str
    message: str
    target: tuple[Any, ...] = ("kit", "")


def audit_kit(kit: Kit, expansion: Expansion, notes: RuleNotes) -> list[AuditItem]:
    """Findings about the kit alone: uncovered faces, bad rules, unused connectors, variant budget."""
    items: list[AuditItem] = []
    if not kit.indifference:
        for module in kit.modules:
            for face, connector in enumerate(module.faces):
                if not connector.covered:
                    items.append(
                        AuditItem(
                            "warning",
                            f"{module.name}:{FACE_NAMES[face]} is uncovered and Indifference is off, so it can meet nothing",
                            ("face", module.name, face),
                        )
                    )
    for rule in notes.missing_modules:
        items.append(AuditItem("warning", f"rule {rule.text()} names a module that does not exist; ignored", ("rule", rule.text())))
    for rule in notes.empty_rules:
        items.append(AuditItem("warning", f"rule {rule.text()} matches no touch under the modules' rotational freedom", ("rule", rule.text())))
    for name in notes.unused_connectors:
        items.append(AuditItem("info", f"connector {name!r} is declared in a pair but no face carries it"))
    items.extend(contradicting_exclusives(kit))
    count = len(expansion.variants)
    if count > VARIANT_ERROR:
        items.append(AuditItem("error", f"{count} variants exceed the budget of {VARIANT_ERROR}; reduce rotational freedom or modules"))
    elif count > VARIANT_WARN:
        items.append(AuditItem("warning", f"{count} variants: solves will take seconds, not milliseconds (spike 4)"))
    else:
        items.append(AuditItem("info", f"{count} variants from {len(kit.modules)} modules"))
    return items


def contradicting_exclusives(kit: Kit) -> list[AuditItem]:
    """Two exclusive rules on one face with different partners cannot both hold."""
    partners: dict[tuple[str, int], set[tuple[str, int]]] = {}
    for rule in kit.rules:
        if rule.kind != "exclusive":
            continue
        partners.setdefault((rule.module_a, rule.face_a), set()).add((rule.module_b, rule.face_b))
        partners.setdefault((rule.module_b, rule.face_b), set()).add((rule.module_a, rule.face_a))
    items: list[AuditItem] = []
    for (module, face), others in sorted(partners.items()):
        if len(others) > 1:
            listed = ", ".join(f"{m}:{FACE_NAMES[f]}" for m, f in sorted(others))
            items.append(
                AuditItem("error", f"{module}:{FACE_NAMES[face]} is exclusive with more than one face: {listed}", ("face", module, face))
            )
    return items


def audit_domains(kit: Kit, domains: Domains) -> list[AuditItem]:
    """Slots that no module fits."""
    items: list[AuditItem] = []
    for slot in domains.empty_slots[:20]:
        items.append(AuditItem("error", f"slot {slot} allows no module (weights, fixes or tags exclude every one)", ("slot", slot)))
    if len(domains.empty_slots) > 20:
        items.append(AuditItem("error", f"{len(domains.empty_slots)} slots allow no module"))
    return items


def audit_canonical(kit: Kit, expansion: Expansion, report: dict[str, Any]) -> list[AuditItem]:
    """Findings from the core's canonicalisation report: a contradiction slot and modules left without a live variant."""
    items: list[AuditItem] = []
    if report.get("contradiction") is not None:
        slot = report["contradiction"]
        items.append(AuditItem("error", f"slot {slot} empties before any choice is made: the kit cannot fill this envelope", ("slot", slot)))
    dead = set(report.get("dead_variants", []))
    for module_index, variants in enumerate(expansion.by_module):
        if variants and all(v in dead for v in variants):
            name = kit.modules[module_index].name
            items.append(AuditItem("warning", f"module {name} has no live variant anywhere after canonicalisation", ("module", name)))
    collapsed = report.get("collapsed", 0)
    if collapsed:
        items.append(AuditItem("info", f"{collapsed} slots are decided by propagation alone"))
    return items


def worst_level(items: list[AuditItem]) -> str:
    """``"error"``, ``"warning"`` or ``"info"``, whichever is the most severe present."""
    for level in ("error", "warning"):
        if any(item.level == level for item in items):
            return level
    return "info"


def rule_text(rule: Rule) -> str:
    """Shortcut for messages."""
    return rule.text()
