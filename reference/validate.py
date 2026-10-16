# SPDX-License-Identifier: MIT OR Apache-2.0
"""Independent result checker, mirroring ``validate.rs`` and sharing nothing with the solver.

It reads the raw ``allowed`` pair lists and CSR arrays of the assembly
directly, so a bug in the solver's tables cannot hide here. Messages follow
the Rust ``Violation`` texts so test assertions can match either.
"""

from __future__ import annotations

from collections.abc import Sequence

from .assembly import Assembly

STATE_SOLVED = 0


def validate(assembly: Assembly, variant: Sequence[int], state: Sequence[int]) -> list[str]:
    """Return every violation as a message; an empty list means the result is valid.

    Checks: every solved neighbour pair is allowed across its port and twist,
    every solved slot holds a variant from its initial domain and its fixed
    value, every boundary port holds its connector class, and every
    multi-cell part has its sibling across the internal port.
    """
    slots = assembly.slots
    if len(variant) != slots or len(state) != slots:
        return [f"result has {len(variant)} entries, assembly has {slots} slots"]

    allowed: set[tuple[int, int, int, int]] = set()
    for table in assembly.allowed:
        for v, w in table.pairs:
            allowed.add((table.port, table.twist, v, w))

    def chosen(slot: int) -> int | None:
        if state[slot] == STATE_SOLVED and variant[slot] >= 0:
            return int(variant[slot])
        return None

    out: list[str] = []
    e = assembly.edges
    for slot in range(slots):
        v = chosen(slot)
        if v is None:
            continue
        domain = assembly.domain_classes[assembly.slot_domain[slot]]
        if v not in domain:
            out.append(f"slot {slot} holds {v}, which is not in its initial domain")
        if assembly.fixed is not None and assembly.fixed[slot] >= 0 and assembly.fixed[slot] != v:
            out.append(f"slot {slot} holds {v} but was fixed to {assembly.fixed[slot]}")
        for i in range(e.offsets[slot], e.offsets[slot + 1]):
            w = chosen(e.neighbour[i])
            if w is None:
                continue
            if (e.port[i], e.twist[i], v, w) not in allowed:
                out.append(
                    f"slot {slot} ({v}) and slot {e.neighbour[i]} ({w}) are not allowed across port {e.port[i]} twist {e.twist[i]}"
                )
        this = assembly.variants[v]
        for port, part in this.internal:
            across = next((e.neighbour[i] for i in range(e.offsets[slot], e.offsets[slot + 1]) if e.port[i] == port), None)
            sibling = chosen(across) if across is not None else None
            ok = False
            if sibling is not None:
                other = assembly.variants[sibling]
                ok = other.module == this.module and other.rotation == this.rotation and other.part == part
            if not ok:
                out.append(f"slot {slot} holds part {v} but port {port} does not hold sibling part {part}")
    for port in assembly.boundary:
        v = chosen(port.slot)
        if v is None:
            continue
        entry = assembly.boundary_classes[port.class_]
        if v not in entry.variants:
            out.append(f"slot {port.slot} port {port.port} needs connector {entry.connector!r} but holds {v}")
    return out
