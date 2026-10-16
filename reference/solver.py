# SPDX-License-Identifier: MIT OR Apache-2.0
"""The reference solver loop, mirroring ``solve.rs`` and the modules it uses.

Reading guide, with the Rust twin of each piece:

- :class:`Tables` (``adjacency.rs``): one row per variant per (port, twist),
  each row an int bitset of the variants allowed across that port.
- :class:`Graph` (``graph.rs``): the CSR edges, checked for self loops,
  duplicate ports and missing or mismatched reverse edges.
- :class:`Weights` and :class:`EntropySums` (``entropy.rs``): effective
  weights, the running ``W`` and ``S`` per slot and the integer entropy key.
- :class:`Propagator` (``propagate.rs``): FIFO AC-3 over int bitsets.
- :class:`Trail` (``trail.rs``): the undo log and backtracking.
- :class:`Solver` (``solve.rs``): canonicalisation and one attempt;
  :func:`solve` (``attempts.rs``): the attempt policy, sequential here.

Where Rust keeps a versioned heap for the next slot to observe, this file
scans all open slots for the smallest key. The spec allows it and the order
is identical: every open slot's key is its current key.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .assembly import Assembly
from .pcg32 import Pcg32

KEY_SCALE = 1e6
STATE_SOLVED = 0
STATE_OPEN = 1
STATE_CONTRADICTION = 2


def ones(bits: int):
    """Yield the set bit positions of ``bits`` in ascending order (``Bitset::ones``)."""
    while bits:
        low = bits & -bits
        yield low.bit_length() - 1
        bits ^= low


def single(bits: int) -> int | None:
    """The only set bit, or ``None`` if there are zero or several."""
    if bits and bits & (bits - 1) == 0:
        return bits.bit_length() - 1
    return None


def table_key(port: int, twist: int) -> int:
    """Index of the (port, twist) table, ``port * 4 + twist`` like ``ids::table_key``."""
    return port * 4 + twist


def opposite(port: int) -> int:
    """The port across the edge on a box slot: ``port ^ 1``."""
    return port ^ 1


class Tables:
    """Adjacency bitsets per (port, twist), built from ``allowed`` and checked for symmetry."""

    def __init__(self, assembly: Assembly) -> None:
        self.variant_count = assembly.variant_count
        self.rows: dict[int, list[int]] = {}
        for table in assembly.allowed:
            key = table_key(table.port, table.twist)
            if key in self.rows:
                raise ValueError(f"duplicate allowed table for port {table.port} twist {table.twist}")
            rows = [0] * self.variant_count
            for v, w in table.pairs:
                rows[v] |= 1 << w
            self.rows[key] = rows
        self._check_symmetry()

    def _check_symmetry(self) -> None:
        """``w in table(p, t)[v]`` exactly when ``v in table(opposite(p), t)[w]``."""
        for key, rows in self.rows.items():
            port, twist = divmod(key, 4)
            mirror = self.rows.get(table_key(opposite(port), twist))
            if mirror is None:
                raise ValueError(f"table for port {port} twist {twist} has no opposite table")
            for v, row in enumerate(rows):
                for w in ones(row):
                    if not (mirror[w] >> v) & 1:
                        raise ValueError(
                            f"asymmetric tables: {w} allowed across port {port} twist {twist} from {v}, but not the reverse"
                        )

    def support(self, port: int, twist: int, domain: int) -> int:
        """OR of the rows of every variant in ``domain``: what the neighbour across ``port`` may hold."""
        rows = self.rows[table_key(port, twist)]
        support = 0
        for v in ones(domain):
            support |= rows[v]
        return support

    def has(self, port: int, twist: int) -> bool:
        """Whether a table exists for (port, twist)."""
        return table_key(port, twist) in self.rows


@dataclass(frozen=True)
class Edge:
    """One directed edge of the slot graph."""

    port: int
    neighbour: int
    neighbour_port: int
    twist: int


class Graph:
    """The slot graph as per-slot edge lists in CSR order, checked like ``Graph::from_assembly``."""

    def __init__(self, assembly: Assembly) -> None:
        e = assembly.edges
        self.edges: list[list[Edge]] = []
        for slot in range(assembly.slots):
            seen_ports: set[int] = set()
            out: list[Edge] = []
            for i in range(e.offsets[slot], e.offsets[slot + 1]):
                edge = Edge(e.port[i], e.neighbour[i], e.neighbour_port[i], e.twist[i])
                if edge.neighbour == slot:
                    raise ValueError(f"slot {slot} has an edge to itself")
                if edge.port in seen_ports:
                    raise ValueError(f"slot {slot} uses port {edge.port} twice")
                seen_ports.add(edge.port)
                out.append(edge)
            self.edges.append(out)
        for slot, out in enumerate(self.edges):
            for edge in out:
                reverse = [
                    r
                    for r in self.edges[edge.neighbour]
                    if r.neighbour == slot and r.port == edge.neighbour_port and r.neighbour_port == edge.port
                ]
                if not reverse or reverse[0].twist != edge.twist:
                    raise ValueError(
                        f"edge {slot}:{edge.port} -> {edge.neighbour}:{edge.neighbour_port} has no matching reverse edge"
                    )


class Weights:
    """Effective weights: ``variant_weight[v] × slot_module_weight[s][module(v)]`` (``entropy.rs``)."""

    def __init__(self, assembly: Assembly) -> None:
        self.variant = assembly.variant_weight
        self.module_of = tuple(v.module for v in assembly.variants)
        self.slot_module = assembly.slot_module_weight

    def effective(self, slot: int, variant: int) -> float:
        """One multiplication in f64, or the base weight when there is no per-slot table."""
        base = self.variant[variant]
        if self.slot_module is None:
            return base
        return base * self.slot_module[slot][self.module_of[variant]]


def entropy_key(entropy: float) -> int:
    """``floor(H × 1e6 + 0.5)`` as an integer; never ``round()``."""
    return int(math.floor(entropy * KEY_SCALE + 0.5))


class EntropySums:
    """Per slot ``W = Σ w`` and ``S = Σ w·ln w`` kept incrementally (``EntropySums`` in Rust)."""

    def __init__(self, slots: int) -> None:
        self.weight_sum = [0.0] * slots
        self.wlogw_sum = [0.0] * slots

    def recompute(self, slot: int, domain: int, weights: Weights) -> None:
        """From scratch, ascending variant order, plain left-to-right sums."""
        weight_sum = 0.0
        wlogw_sum = 0.0
        for v in ones(domain):
            w = weights.effective(slot, v)
            weight_sum += w
            wlogw_sum += w * math.log(w)
        self.weight_sum[slot] = weight_sum
        self.wlogw_sum[slot] = wlogw_sum

    def remove(self, slot: int, removed: int, weights: Weights) -> None:
        """Subtract the removed variants in ascending order."""
        for v in ones(removed):
            w = weights.effective(slot, v)
            self.weight_sum[slot] -= w
            self.wlogw_sum[slot] -= w * math.log(w)

    def entropy(self, slot: int, count: int) -> float:
        """``ln W − S / W``; exactly 0 for one variant or none."""
        if count <= 1:
            return 0.0
        w = self.weight_sum[slot]
        return math.log(w) - self.wlogw_sum[slot] / w

    def key(self, slot: int, count: int) -> int:
        """The integer entropy key of a slot."""
        return entropy_key(self.entropy(slot, count))


class Propagator:
    """Bitset AC-3 with a FIFO queue and an in-queue flag per slot (``propagate.rs``)."""

    def __init__(self, slots: int) -> None:
        self.queue: deque[int] = deque()
        self.in_queue = [False] * slots

    def enqueue(self, slot: int) -> None:
        """Queue a slot unless it is already waiting."""
        if not self.in_queue[slot]:
            self.in_queue[slot] = True
            self.queue.append(slot)

    def enqueue_all(self) -> None:
        """Queue every slot in index order (the canonicalising pass)."""
        for slot in range(len(self.in_queue)):
            self.enqueue(slot)

    def clear(self) -> None:
        """Drop the queue (before backtracking)."""
        for slot in self.queue:
            self.in_queue[slot] = False
        self.queue.clear()

    def propagate(
        self,
        domains: list[int],
        graph: Graph,
        tables: Tables,
        changes: list[tuple[int, int]],
        on_change: Callable[[int, int, int], None],
    ) -> int | None:
        """Run to a fixpoint; returns the slot that emptied, or ``None`` if none did.

        For every popped slot and each of its edges in CSR order the
        neighbour keeps only the supported variants. A change is logged on
        ``changes`` as ``(slot, old domain)`` and reported to ``on_change(slot,
        removed, new)`` before a contradiction is returned.
        """
        while self.queue:
            slot = self.queue.popleft()
            self.in_queue[slot] = False
            for edge in graph.edges[slot]:
                support = tables.support(edge.port, edge.twist, domains[slot])
                neighbour = edge.neighbour
                current = domains[neighbour]
                if current & ~support == 0:
                    continue
                new = current & support
                domains[neighbour] = new
                on_change(neighbour, current & ~new, new)
                changes.append((neighbour, current))
                if new == 0:
                    return neighbour
                self.enqueue(neighbour)
        return None


@dataclass
class Decision:
    """A choice made by observation; ``trail_len`` is where its changes start on the trail."""

    trail_len: int
    slot: int
    variant: int


class Trail:
    """The undo log: domain changes and decision marks (``trail.rs``)."""

    def __init__(self) -> None:
        self.changes: list[tuple[int, int]] = []
        self.decisions: list[Decision] = []

    def push_decision(self, slot: int, variant: int) -> None:
        """Mark a decision before its changes are logged."""
        self.decisions.append(Decision(len(self.changes), slot, variant))

    def backtrack(self, domains: list[int]) -> tuple[Decision, list[int]] | None:
        """Undo the newest decision; forbid its variant; return it with the sorted, distinct touched slots."""
        if not self.decisions:
            return None
        decision = self.decisions.pop()
        touched: list[int] = []
        while len(self.changes) > decision.trail_len:
            slot, old = self.changes.pop()
            domains[slot] = old
            touched.append(slot)
        slot = decision.slot
        old = domains[slot]
        domains[slot] = old & ~(1 << decision.variant)
        self.changes.append((slot, old))
        touched.append(slot)
        return decision, sorted(set(touched))


@dataclass
class CanonicalReport:
    """What the canonicalising pass found: a contradiction slot, variants alive nowhere, collapsed slots."""

    contradiction: int | None = None
    dead_variants: list[int] = field(default_factory=list)
    collapsed: int = 0

    def as_dict(self) -> dict[str, Any]:
        """The dict shape ``lamda_core.canonicalise_json`` returns."""
        return {"contradiction": self.contradiction, "dead_variants": list(self.dead_variants), "collapsed": self.collapsed}


class CanonicalError(ValueError):
    """Raised when canonicalisation contradicts: a setup error, not bad luck."""

    def __init__(self, report: CanonicalReport) -> None:
        super().__init__(f"canonicalisation contradiction at slot {report.contradiction}")
        self.report = report


@dataclass
class SolveResult:
    """The output arrays and report (``SolveResult`` in Rust, dtypes as the bindings return them)."""

    variant: np.ndarray
    module: np.ndarray
    rotation: np.ndarray
    part: np.ndarray
    anchor: np.ndarray
    state: np.ndarray
    options_left: np.ndarray
    collapse_step: np.ndarray
    report: dict[str, Any]


@dataclass
class _Attempt:
    """Mutable state of one attempt (``Attempt`` in Rust)."""

    domains: list[int]
    sums: EntropySums
    trail: Trail
    propagator: Propagator
    rng: Pcg32
    noise: list[int]
    step: list[int]
    observations: int = 0
    backtracks: int = 0


class Solver:
    """Tables, graph, weights and the canonical domains of one assembly; runs attempts."""

    def __init__(self, assembly: Assembly) -> None:
        self.assembly = assembly
        self.graph = Graph(assembly)
        self.tables = Tables(assembly)
        self.weights = Weights(assembly)
        self.priority = list(assembly.priority) if assembly.priority is not None else [0] * assembly.slots
        for slot, edges in enumerate(self.graph.edges):
            for edge in edges:
                if not self.tables.has(edge.port, edge.twist):
                    raise ValueError(f"no adjacency table for port {edge.port} twist {edge.twist}, used by slot {slot}")
        self.canonical = self._initial_domains()
        self.report = CanonicalReport()
        propagator = Propagator(assembly.slots)
        propagator.enqueue_all()
        contradiction = propagator.propagate(self.canonical, self.graph, self.tables, [], lambda *_: None)
        self.report.contradiction = contradiction
        alive = 0
        for domain in self.canonical:
            alive |= domain
        full = (1 << assembly.variant_count) - 1
        self.report.dead_variants = list(ones(full & ~alive))
        self.report.collapsed = sum(1 for d in self.canonical if single(d) is not None)
        if contradiction is not None:
            raise CanonicalError(self.report)

    def _initial_domains(self) -> list[int]:
        """Domain class ∩ fixed ∩ boundary classes, minus variants with effective weight ≤ 0."""
        a = self.assembly
        classes = [sum(1 << v for v in c) for c in a.domain_classes]
        domains = [classes[c] for c in a.slot_domain]
        if a.fixed is not None:
            for slot, value in enumerate(a.fixed):
                if value >= 0:
                    domains[slot] &= 1 << value
        for port in a.boundary:
            mask = sum(1 << v for v in a.boundary_classes[port.class_].variants)
            domains[port.slot] &= mask
        for slot in range(a.slots):
            for v in list(ones(domains[slot])):
                if self.weights.effective(slot, v) <= 0.0:
                    domains[slot] &= ~(1 << v)
        return domains

    def run_attempt(self, attempt: int) -> SolveResult:
        """One attempt from the canonical domains with the attempt's own PCG32 stream."""
        started = time.perf_counter()
        state = self._start_attempt(attempt)
        outcome = self._run_loop(state)
        report = {
            "seed": self.assembly.settings.seed,
            "attempt": attempt,
            "observations": state.observations,
            "backtracks": state.backtracks,
            "elapsed": time.perf_counter() - started,
            "outcome": outcome,
        }
        return self._collect(state, report)

    def _start_attempt(self, attempt: int) -> _Attempt:
        """Draw the noise (one ``next_u32`` per slot), copy the canonical domains, seed the sums."""
        a = self.assembly
        rng = Pcg32.for_attempt(a.settings.seed, attempt)
        noise = [rng.next_u32() for _ in range(a.slots)]
        domains = list(self.canonical)
        sums = EntropySums(a.slots)
        step = [-1] * a.slots
        for slot in range(a.slots):
            sums.recompute(slot, domains[slot], self.weights)
            if single(domains[slot]) is not None:
                step[slot] = 0
        return _Attempt(domains, sums, Trail(), Propagator(a.slots), rng, noise, step)

    def _next_open(self, state: _Attempt) -> int | None:
        """The open slot with the smallest ``(−priority, entropy key, noise, slot)``."""
        best: tuple[int, int, int, int] | None = None
        for slot, domain in enumerate(state.domains):
            count = domain.bit_count()
            if count > 1:
                key = (-self.priority[slot], state.sums.key(slot, count), state.noise[slot], slot)
                if best is None or key < best:
                    best = key
        return None if best is None else best[3]

    def _choose_variant(self, state: _Attempt, slot: int) -> int:
        """Weighted choice: fresh ascending sum, ``r = unit × W'``, walk subtracting; last variant as fallback."""
        domain = state.domains[slot]
        total = 0.0
        for v in ones(domain):
            total += self.weights.effective(slot, v)
        r = state.rng.unit_f64() * total
        last = -1
        for v in ones(domain):
            r -= self.weights.effective(slot, v)
            if r < 0.0:
                return v
            last = v
        return last

    def _run_loop(self, state: _Attempt) -> str:
        """Observe, propagate, backtrack on contradiction, until solved, partial or out of budget."""
        a = self.assembly
        max_observations = a.settings.max_observations
        while True:
            if max_observations > 0 and state.observations >= max_observations:
                return "partial"
            slot = self._next_open(state)
            if slot is None:
                return "solved"
            variant = self._choose_variant(state, slot)
            state.observations += 1
            self._observe(state, slot, variant)
            contradiction = self._propagate(state)
            while contradiction is not None:
                state.backtracks += 1
                if state.backtracks > a.settings.backtrack_budget:
                    return "contradiction"
                decision_slot = self._backtrack(state)
                if decision_slot is None:
                    return "contradiction"
                if state.domains[decision_slot] == 0:
                    contradiction = decision_slot
                    continue
                state.propagator.enqueue(decision_slot)
                contradiction = self._propagate(state)

    def _observe(self, state: _Attempt, slot: int, variant: int) -> None:
        """Collapse ``slot`` to ``variant``: log the decision and the old domain, update sums and step, queue it."""
        state.trail.push_decision(slot, variant)
        old = state.domains[slot]
        chosen = 1 << variant
        state.domains[slot] = chosen
        state.sums.remove(slot, old & ~chosen, self.weights)
        state.step[slot] = state.observations
        state.trail.changes.append((slot, old))
        state.propagator.enqueue(slot)

    def _propagate(self, state: _Attempt) -> int | None:
        """Propagate with the bookkeeping callback: subtract sums, stamp the step of slots that collapse."""
        observation = state.observations
        sums = state.sums
        step = state.step
        weights = self.weights

        def on_change(slot: int, removed: int, new: int) -> None:
            sums.remove(slot, removed, weights)
            if single(new) is not None:
                step[slot] = observation

        return state.propagator.propagate(state.domains, self.graph, self.tables, state.trail.changes, on_change)

    def _backtrack(self, state: _Attempt) -> int | None:
        """Undo the last decision; recompute sums and steps of every touched slot; return the decision slot."""
        state.propagator.clear()
        undone = state.trail.backtrack(state.domains)
        if undone is None:
            return None
        decision, touched = undone
        for slot in touched:
            state.sums.recompute(slot, state.domains[slot], self.weights)
            state.step[slot] = state.observations if single(state.domains[slot]) is not None else -1
        return decision.slot

    def _collect(self, state: _Attempt, report: dict[str, Any]) -> SolveResult:
        """Turn the domains into the result arrays."""
        a = self.assembly
        n = a.slots
        variant = np.full(n, -1, dtype=np.int32)
        module = np.full(n, -1, dtype=np.int32)
        rotation = np.full(n, -1, dtype=np.int32)
        part = np.full(n, -1, dtype=np.int32)
        anchor = np.zeros(n, dtype=np.bool_)
        state_arr = np.full(n, STATE_OPEN, dtype=np.uint8)
        options = np.zeros(n, dtype=np.uint32)
        for slot, domain in enumerate(state.domains):
            options[slot] = domain.bit_count()
            if domain == 0:
                state_arr[slot] = STATE_CONTRADICTION
                continue
            v = single(domain)
            if v is not None:
                info = a.variants[v]
                variant[slot] = v
                module[slot] = info.module
                rotation[slot] = info.rotation
                part[slot] = info.part
                anchor[slot] = info.anchor
                state_arr[slot] = STATE_SOLVED
        return SolveResult(
            variant, module, rotation, part, anchor, state_arr, options, np.array(state.step, dtype=np.int64), report
        )


def is_final(outcome: str) -> bool:
    """Solved, partial and cancelled end the attempt sequence; contradiction does not."""
    return outcome in ("solved", "partial", "cancelled")


def solve(assembly: Assembly) -> SolveResult:
    """The attempt policy of ``attempts.rs``: attempt 0, then 1, 2, … until one is final; else the last.

    Rust runs attempts 1… in parallel batches and takes the lowest-numbered
    final one, which is what this sequential loop returns.
    """
    solver = Solver(assembly)
    attempts = max(1, assembly.settings.attempts)
    last = solver.run_attempt(0)
    if is_final(last.report["outcome"]):
        return last
    for attempt in range(1, attempts):
        last = solver.run_attempt(attempt)
        if is_final(last.report["outcome"]):
            return last
    return last
