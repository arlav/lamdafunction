# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON round-trip for kits and envelopes: the kit manifest format.

A kit manifest (``docs/PLAN.md``, "Where kits live") lists modules,
connectors, pairs, rules and Indifference. This is also what the tests use to
describe kits without Blender.
"""

from __future__ import annotations

from typing import Any

from .envelope import Envelope, SolveSettings
from .kit import FACE_NAMES, Connector, ConnectorPair, Kit, Module, Rule

KIT_FORMAT = "lamdafunction-kit-1"
"""Format tag written into every kit manifest."""


def module_to_dict(module: Module) -> dict[str, Any]:
    """A module as plain JSON data; uncovered faces are left out of ``faces``."""
    faces = {
        FACE_NAMES[i]: {"name": c.name, "offset": c.offset} if c.offset else {"name": c.name}
        for i, c in enumerate(module.faces)
        if c.covered
    }
    data: dict[str, Any] = {
        "name": module.name,
        "freedom": module.freedom,
        "weight": module.weight,
        "tags": list(module.tags),
        "faces": faces,
        "terminator": [FACE_NAMES[i] for i, flag in enumerate(module.terminator) if flag],
    }
    if module.geometry_hash is not None:
        data["geometry_hash"] = list(module.geometry_hash)
    return data


def module_from_dict(data: dict[str, Any]) -> Module:
    """Inverse of :func:`module_to_dict`."""
    faces = [Connector() for _ in range(6)]
    for face_name, entry in data.get("faces", {}).items():
        faces[FACE_NAMES.index(face_name.upper())] = Connector(str(entry["name"]), int(entry.get("offset", 0)))
    terminator = [False] * 6
    for face_name in data.get("terminator", []):
        terminator[FACE_NAMES.index(face_name.upper())] = True
    hashes = data.get("geometry_hash")
    return Module(
        name=str(data["name"]),
        freedom=str(data.get("freedom", "NONE")).upper(),
        weight=float(data.get("weight", 1.0)),
        tags=tuple(str(t) for t in data.get("tags", [])),
        faces=tuple(faces),
        terminator=tuple(terminator),
        geometry_hash=None if hashes is None else tuple(str(h) for h in hashes),
    )


def kit_to_dict(kit: Kit) -> dict[str, Any]:
    """The kit manifest."""
    return {
        "format": KIT_FORMAT,
        "modules": [module_to_dict(m) for m in kit.modules],
        "pairs": [{"a": p.a, "b": p.b, "twists": list(p.twists)} for p in kit.pairs],
        "rules": [{"kind": r.kind, "rule": r.text()} for r in kit.rules],
        "indifference": kit.indifference,
    }


def kit_from_dict(data: dict[str, Any]) -> Kit:
    """Inverse of :func:`kit_to_dict`; checks the format tag and the kit."""
    if data.get("format") != KIT_FORMAT:
        raise ValueError(f"not a kit manifest: format {data.get('format')!r}, expected {KIT_FORMAT!r}")
    kit = Kit(
        modules=[module_from_dict(m) for m in data.get("modules", [])],
        pairs=[
            ConnectorPair(str(p["a"]), str(p["b"]), tuple(bool(t) for t in p.get("twists", [True] * 4)))
            for p in data.get("pairs", [])
        ],
        rules=[Rule.parse(str(r["kind"]), str(r["rule"])) for r in data.get("rules", [])],
        indifference=bool(data.get("indifference", True)),
    )
    kit.check()
    return kit


def envelope_to_dict(envelope: Envelope) -> dict[str, Any]:
    """An envelope as JSON data."""
    data: dict[str, Any] = {"cells": [list(c) for c in envelope.cells], "weights": dict(envelope.weights)}
    if envelope.priority is not None:
        data["priority"] = list(envelope.priority)
    if envelope.fixed is not None:
        data["fixed"] = list(envelope.fixed)
    return data


def envelope_from_dict(data: dict[str, Any]) -> Envelope:
    """Inverse of :func:`envelope_to_dict`; checks the envelope."""
    envelope = Envelope(
        cells=[(int(c[0]), int(c[1]), int(c[2])) for c in data["cells"]],
        weights={str(k): [float(x) for x in v] for k, v in data.get("weights", {}).items()},
        priority=None if data.get("priority") is None else [int(p) for p in data["priority"]],
        fixed=None if data.get("fixed") is None else [int(f) for f in data["fixed"]],
    )
    envelope.check()
    return envelope


def settings_to_dict(settings: SolveSettings) -> dict[str, Any]:
    """Solve settings as JSON data."""
    return {
        "seed": settings.seed,
        "attempts": settings.attempts,
        "threads": settings.threads,
        "backtrack_budget": settings.backtrack_budget,
        "max_observations": settings.max_observations,
        "closed_by": settings.closed_by,
    }


def settings_from_dict(data: dict[str, Any]) -> SolveSettings:
    """Inverse of :func:`settings_to_dict`."""
    return SolveSettings(
        seed=int(data.get("seed", 1)),
        attempts=int(data.get("attempts", 8)),
        threads=int(data.get("threads", 0)),
        backtrack_budget=int(data.get("backtrack_budget", 100)),
        max_observations=int(data.get("max_observations", 0)),
        closed_by=None if data.get("closed_by") is None else str(data["closed_by"]),
    )
