# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile a kit and an envelope into a compiled assembly (schema v1).

This is the entry point of the compiler. It runs the build order of
``docs/PLAN.md`` ("Rule compiler") and returns the JSON document described in
``.claude/rules/solver-core.md`` together with the audit items. The caller
(the Blender operator, the CLI or a test) hands the document to
``lamda_core.solve_json`` or to ``reference.solve``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..model.envelope import Envelope, SolveSettings
from ..model.io import envelope_to_dict, kit_to_dict, settings_to_dict
from ..model.kit import Kit
from . import audit as audit_module
from .audit import AuditItem
from .domains import build_domains
from .graph import build_graph
from .rules import build_pairs
from .variants import Expansion, expand_variants

SCHEMA_VERSION = "1.0.0"


@dataclass
class Compiled:
    """The compiled assembly document, its audit, and the expansion for mapping results back."""

    document: dict[str, Any]
    audit: list[AuditItem]
    expansion: Expansion

    @property
    def text(self) -> str:
        """The document as compact JSON text."""
        return json.dumps(self.document, separators=(",", ":"))

    @property
    def has_errors(self) -> bool:
        """Whether the audit contains an error."""
        return audit_module.worst_level(self.audit) == "error"


def compile_hash(kit: Kit, envelope: Envelope, settings: SolveSettings) -> str:
    """A stable hash of the inputs, for caching and reports."""
    payload = json.dumps(
        {"kit": kit_to_dict(kit), "envelope": envelope_to_dict(envelope), "settings": settings_to_dict(settings)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compile_assembly(
    kit: Kit,
    envelope: Envelope,
    settings: SolveSettings,
    canonicalise: Callable[[str], dict[str, Any]] | None = None,
) -> Compiled:
    """Run the compiler.

    ``canonicalise`` optionally takes the document text and returns the
    core's canonicalisation report (``lamda_core.canonicalise_json`` or the
    reference's equivalent); its findings join the audit.
    """
    kit.check()
    envelope.check()
    expansion = expand_variants(kit)
    pairs, notes = build_pairs(kit, expansion)
    graph = build_graph(envelope.cells)
    domains = build_domains(kit, envelope, settings, expansion, graph)

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "compile_hash": compile_hash(kit, envelope, settings),
        "modules": [{"name": m.name, "library_index": i, "parts": 1} for i, m in enumerate(kit.modules)],
        "variants": [
            {"module": v.module, "rotation": v.rotation, "part": 0, "anchor": True} for v in expansion.variants
        ],
        "ports": 6,
        "allowed": [{"port": port, "twist": 0, "pairs": [list(p) for p in sorted(pairs[port])]} for port in range(6)],
        "slots": graph.slot_count,
        "edges": graph.as_dict(),
        **domains.as_dict(),
        "settings": {
            "seed": settings.seed,
            "attempts": settings.attempts,
            "threads": settings.threads,
            "backtrack_budget": settings.backtrack_budget,
            "max_observations": settings.max_observations,
            "mode": "collapse",
        },
    }

    items = audit_module.audit_kit(kit, expansion, notes) + audit_module.audit_domains(kit, domains)
    if canonicalise is not None and not domains.empty_slots:
        report = canonicalise(json.dumps(document, separators=(",", ":")))
        items.extend(audit_module.audit_canonical(kit, expansion, report))
    return Compiled(document, items, expansion)
