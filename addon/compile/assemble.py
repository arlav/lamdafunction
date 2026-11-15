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

from ..model import cells as cells_module
from ..model.cells import CellEnvelope
from ..model.envelope import Envelope, SolveSettings
from ..model.io import envelope_to_dict, kit_to_dict, settings_to_dict
from ..model.kit import Kit
from . import audit as audit_module
from .audit import AuditItem
from .domains import build_domains
from .graph import envelope_cells, graph_from_cells
from .rotations import IDENTITY, compose
from .rules import build_pairs, edge_kind_pairs
from .variants import Expansion, expand_variants

SCHEMA_VERSION = "1.0.0"
"""Written when every edge is the box-grid kind, so phase 1 documents stay byte-identical."""

SCHEMA_VERSION_TURNED = "1.1.0"
"""Written when slot frames turn and tables name their ``neighbour_port``."""


@dataclass
class Compiled:
    """The compiled assembly document, its audit, and what is needed to map results back.

    ``frames`` is each slot's frame (rotation index, local to world) when the
    input was a box envelope; a solved variant's world rotation is then
    ``compose(frames[slot], rotation)`` (:meth:`world_rotation`). A cell
    envelope's frames are vectors (``cells.frames``), used for placement in
    Blender (S5), and ``frames`` is empty. ``cells`` is the cell envelope the
    compiler read.
    """

    document: dict[str, Any]
    audit: list[AuditItem]
    expansion: Expansion
    frames: tuple[int, ...] = ()
    cells: CellEnvelope | None = None

    def world_rotation(self, slot: int, rotation: int) -> int:
        """The world rotation of a variant rotation placed in ``slot``: the slot's frame, then the rotation within it."""
        if not self.frames:
            return rotation
        return compose(self.frames[slot], rotation)

    @property
    def text(self) -> str:
        """The document as compact JSON text."""
        return json.dumps(self.document, separators=(",", ":"))

    @property
    def has_errors(self) -> bool:
        """Whether the audit contains an error."""
        return audit_module.worst_level(self.audit) == "error"


def compile_hash(kit: Kit, envelope: Envelope | CellEnvelope, settings: SolveSettings) -> str:
    """A stable hash of the inputs, for caching and reports."""
    data = envelope_to_dict(envelope) if isinstance(envelope, Envelope) else cells_module.to_dict(envelope)
    payload = json.dumps(
        {"kit": kit_to_dict(kit), "envelope": data, "settings": settings_to_dict(settings)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compile_assembly(
    kit: Kit,
    envelope: Envelope | CellEnvelope,
    settings: SolveSettings,
    canonicalise: Callable[[str], dict[str, Any]] | None = None,
) -> Compiled:
    """Run the compiler.

    ``canonicalise`` optionally takes the document text and returns the
    core's canonicalisation report (``lamda_core.canonicalise_json`` or the
    reference's equivalent); its findings join the audit.

    ``envelope`` is a box envelope or a cell envelope (a shell, or a file from
    another host); a box envelope is converted first, so the compiler reads
    one structure.
    """
    kit.check()
    cells = envelope_cells(envelope) if isinstance(envelope, Envelope) else envelope
    expansion = expand_variants(kit)
    pairs, notes = build_pairs(kit, expansion)
    graph = graph_from_cells(cells)
    domains = build_domains(kit, cells, settings, expansion, graph)

    if graph.is_box_grid():
        # Phase 1 form: six tables keyed by port alone, schema 1.0.0, byte-identical.
        version = SCHEMA_VERSION
        allowed = [{"port": port, "twist": 0, "pairs": [list(p) for p in sorted(pairs[port])]} for port in range(6)]
    else:
        version = SCHEMA_VERSION_TURNED
        tables = edge_kind_pairs(pairs, expansion, graph.kinds())
        allowed = [
            {"port": p, "neighbour_port": q, "twist": t, "pairs": [list(x) for x in sorted(tables[(p, q, t)])]}
            for p, q, t in sorted(tables)
        ]
    document: dict[str, Any] = {
        "schema_version": version,
        "compile_hash": compile_hash(kit, envelope, settings),
        "modules": [{"name": m.name, "library_index": i, "parts": 1} for i, m in enumerate(kit.modules)],
        "variants": [
            {"module": v.module, "rotation": v.rotation, "part": 0, "anchor": True} for v in expansion.variants
        ],
        "ports": 6,
        "allowed": allowed,
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

    items = [AuditItem(n.level, n.message, n.target) for n in cells.notes]
    items += audit_module.audit_kit(kit, expansion, notes) + audit_module.audit_domains(kit, domains)
    items += audit_module.audit_seams(kit, expansion, graph)
    if canonicalise is not None and not domains.empty_slots:
        report = canonicalise(json.dumps(document, separators=(",", ":")))
        items.extend(audit_module.audit_canonical(kit, expansion, report))
    if isinstance(envelope, Envelope):
        frames = tuple(envelope.frames) if envelope.frames is not None else tuple([IDENTITY] * len(envelope.cells))
    else:
        frames = ()
    return Compiled(document, items, expansion, frames, cells)
