# SPDX-License-Identifier: MIT OR Apache-2.0
"""Load a compiled assembly (schema v1) and check it, mirroring ``assembly.rs``.

The field list is the table in ``.claude/rules/solver-core.md``, "Compiled
assembly, schema v1". Every check here has a twin in ``Assembly::check`` in
Rust, raising ``ValueError`` with the same meaning as the ``LoadError`` variant.
Python's ``json`` reads floats exactly (round-trip safe), like
``serde_json`` with ``float_roundtrip``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class Module:
    """An authored module: its name, index in the Blender library and part count."""

    name: str
    library_index: int
    parts: int = 1


@dataclass(frozen=True)
class Variant:
    """What the solver places: module × rotation × part, plus the sibling parts it needs across ports."""

    module: int
    rotation: int
    part: int
    anchor: bool
    internal: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class AllowedTable:
    """Allowed ``(v, w)`` pairs across ``port`` at ``twist``: ``w`` may sit across that port from ``v``."""

    port: int
    twist: int
    pairs: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Edges:
    """The slot graph in CSR form; the edges of slot ``s`` are entries ``offsets[s]`` to ``offsets[s + 1]``."""

    offsets: tuple[int, ...]
    port: tuple[int, ...]
    neighbour: tuple[int, ...]
    neighbour_port: tuple[int, ...]
    twist: tuple[int, ...]


@dataclass(frozen=True)
class BoundaryClass:
    """A connector name and the sorted variants whose face carries it."""

    connector: str
    variants: tuple[int, ...]


@dataclass(frozen=True)
class BoundaryPort:
    """An exposed port that must carry a connector class."""

    slot: int
    port: int
    class_: int


@dataclass(frozen=True)
class Settings:
    """Solver settings from the compiled assembly."""

    seed: int
    attempts: int
    threads: int
    backtrack_budget: int
    max_observations: int
    mode: str
    supply_caps: tuple[int, ...] | None = None


@dataclass(frozen=True)
class Assembly:
    """A loaded and checked compiled assembly."""

    schema_version: str
    compile_hash: str
    modules: tuple[Module, ...]
    variants: tuple[Variant, ...]
    ports: int
    allowed: tuple[AllowedTable, ...]
    slots: int
    edges: Edges
    boundary_classes: tuple[BoundaryClass, ...]
    boundary: tuple[BoundaryPort, ...]
    domain_classes: tuple[tuple[int, ...], ...]
    slot_domain: tuple[int, ...]
    variant_weight: tuple[float, ...]
    slot_module_weight: tuple[tuple[float, ...], ...] | None
    priority: tuple[int, ...] | None
    fixed: tuple[int, ...] | None
    settings: Settings
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    @property
    def variant_count(self) -> int:
        """Number of variants."""
        return len(self.variants)

    @property
    def slot_count(self) -> int:
        """Number of slots."""
        return self.slots


def load_assembly(source: str | Path | dict[str, Any]) -> Assembly:
    """Load from JSON text, a path, or an already parsed document, and check it.

    Raises ``ValueError`` for a schema or consistency problem, like
    ``LoadError`` in Rust.
    """
    if isinstance(source, dict):
        doc = source
    elif isinstance(source, Path):
        doc = json.loads(source.read_text(encoding="utf-8"))
    else:
        doc = json.loads(source)
    assembly = _from_doc(doc)
    _check(assembly)
    return assembly


def _from_doc(doc: dict[str, Any]) -> Assembly:
    """Build the dataclasses from the parsed JSON; missing optional fields get their defaults."""
    edges = doc["edges"]
    settings = doc["settings"]
    return Assembly(
        schema_version=str(doc["schema_version"]),
        compile_hash=str(doc.get("compile_hash", "")),
        modules=tuple(
            Module(str(m["name"]), int(m["library_index"]), int(m.get("parts", 1))) for m in doc["modules"]
        ),
        variants=tuple(
            Variant(
                int(v["module"]),
                int(v["rotation"]),
                int(v["part"]),
                bool(v["anchor"]),
                tuple((int(p), int(part)) for p, part in v.get("internal", [])),
            )
            for v in doc["variants"]
        ),
        ports=int(doc["ports"]),
        allowed=tuple(
            AllowedTable(int(t["port"]), int(t["twist"]), tuple((int(v), int(w)) for v, w in t["pairs"]))
            for t in doc["allowed"]
        ),
        slots=int(doc["slots"]),
        edges=Edges(
            tuple(int(x) for x in edges["offsets"]),
            tuple(int(x) for x in edges["port"]),
            tuple(int(x) for x in edges["neighbour"]),
            tuple(int(x) for x in edges["neighbour_port"]),
            tuple(int(x) for x in edges["twist"]),
        ),
        boundary_classes=tuple(
            BoundaryClass(str(c["connector"]), tuple(int(v) for v in c["variants"]))
            for c in doc.get("boundary_classes", [])
        ),
        boundary=tuple(
            BoundaryPort(int(b["slot"]), int(b["port"]), int(b["class"])) for b in doc.get("boundary", [])
        ),
        domain_classes=tuple(tuple(int(v) for v in c) for c in doc["domain_classes"]),
        slot_domain=tuple(int(c) for c in doc["slot_domain"]),
        variant_weight=tuple(float(w) for w in doc["variant_weight"]),
        slot_module_weight=(
            None
            if doc.get("slot_module_weight") is None
            else tuple(tuple(float(w) for w in row) for row in doc["slot_module_weight"])
        ),
        priority=None if doc.get("priority") is None else tuple(int(p) for p in doc["priority"]),
        fixed=None if doc.get("fixed") is None else tuple(int(f) for f in doc["fixed"]),
        settings=Settings(
            seed=int(settings["seed"]),
            attempts=int(settings["attempts"]),
            threads=int(settings["threads"]),
            backtrack_budget=int(settings["backtrack_budget"]),
            max_observations=int(settings["max_observations"]),
            mode=str(settings["mode"]),
            supply_caps=None if settings.get("supply_caps") is None else tuple(int(c) for c in settings["supply_caps"]),
        ),
        raw=doc,
    )


def _index(field_name: str, index: int, limit: int) -> None:
    """``index`` must be in ``0..limit``."""
    if not 0 <= index < limit:
        raise ValueError(f"{field_name}: index {index} is out of range (limit {limit})")


def _length(field_name: str, found: int, expected: int) -> None:
    """A per-slot or per-variant array must have the right length."""
    if found != expected:
        raise ValueError(f"{field_name} has {found} entries, expected {expected}")


def _twist(field_name: str, twist: int) -> None:
    """Twists are quarter turns, 0 to 3."""
    if not 0 <= twist <= 3:
        raise ValueError(f"{field_name}: twist {twist} is not in 0..=3")


def _sorted_strict(values: tuple[int, ...]) -> bool:
    """Ascending without duplicates, like ``is_sorted_by(|a, b| a < b)``."""
    return all(a < b for a, b in zip(values, values[1:]))


def _check(a: Assembly) -> None:
    """All of ``Assembly::check`` from Rust, in the same order."""
    if a.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version {a.schema_version}, expected {SCHEMA_VERSION}")
    modules = len(a.modules)
    variants = len(a.variants)
    for v in a.variants:
        _index("variants.module", v.module, modules)
        _index("variants.rotation", v.rotation, 24)
        for port, _part in v.internal:
            _index("variants.internal.port", port, a.ports)
    if a.settings.supply_caps is not None:
        _length("settings.supply_caps", len(a.settings.supply_caps), modules)
    for table in a.allowed:
        _index("allowed.port", table.port, a.ports)
        _twist("allowed.twist", table.twist)
        for v, w in table.pairs:
            _index("allowed.pairs", v, variants)
            _index("allowed.pairs", w, variants)
    e = a.edges
    _length("edges.offsets", len(e.offsets), a.slots + 1)
    for lo, hi in zip(e.offsets, e.offsets[1:]):
        if lo > hi:
            raise ValueError(f"edges.offsets: index {hi} is out of range (limit {lo})")
    count = e.offsets[-1] if e.offsets else 0
    for name, values in (("port", e.port), ("neighbour", e.neighbour), ("neighbour_port", e.neighbour_port), ("twist", e.twist)):
        _length(f"edges.{name}", len(values), count)
    for i in range(count):
        _index("edges.port", e.port[i], a.ports)
        _index("edges.neighbour", e.neighbour[i], a.slots)
        _index("edges.neighbour_port", e.neighbour_port[i], a.ports)
        _twist("edges.twist", e.twist[i])
    for class_index, domain in enumerate(a.domain_classes):
        for v in domain:
            _index("domain_classes", v, variants)
        if not _sorted_strict(domain):
            raise ValueError(f"domain_classes[{class_index}] is not sorted ascending without duplicates")
    _length("slot_domain", len(a.slot_domain), a.slots)
    for c in a.slot_domain:
        _index("slot_domain", c, len(a.domain_classes))
    _length("variant_weight", len(a.variant_weight), variants)
    if a.slot_module_weight is not None:
        _length("slot_module_weight", len(a.slot_module_weight), a.slots)
        for row in a.slot_module_weight:
            _length("slot_module_weight[s]", len(row), modules)
    if a.priority is not None:
        _length("priority", len(a.priority), a.slots)
    if a.fixed is not None:
        _length("fixed", len(a.fixed), a.slots)
        for value in a.fixed:
            if value < -1:
                raise ValueError(f"fixed: {value} is not a variant index or -1")
            if value >= 0:
                _index("fixed", value, variants)
    for class_index, entry in enumerate(a.boundary_classes):
        for v in entry.variants:
            _index("boundary_classes.variants", v, variants)
        if not _sorted_strict(entry.variants):
            raise ValueError(f"boundary_classes[{class_index}] is not sorted ascending without duplicates")
    for port in a.boundary:
        _index("boundary.slot", port.slot, a.slots)
        _index("boundary.port", port.port, a.ports)
        _index("boundary.class", port.class_, len(a.boundary_classes))
