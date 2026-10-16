# SPDX-License-Identifier: MIT OR Apache-2.0
"""Differential tests: the Rust core and the Python reference must agree bit for bit.

Thousands of random small assemblies (irregular box grids, random symmetric
rules, random weights, priorities, fixed slots, boundary classes, budgets and
attempt counts) are solved by ``lamda_core`` and by ``reference``. Every
output array and every report field except ``elapsed`` must be identical,
and the reference validator must accept the Rust result.

Skipped when ``lamda_core`` is not built (``maturin develop``).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from reference import CanonicalError, Solver, load_assembly, solve, validate

lamda_core = pytest.importorskip("lamda_core")

SCRATCH = Path(__file__).resolve().parent / "_differential_failure.json"


def random_assembly(rng: random.Random, max_dim: int = 3, max_variants: int = 10) -> dict[str, Any]:
    """A random compiled assembly on an irregular box grid with symmetric random rules."""
    dims = [rng.randint(1, max_dim) for _ in range(3)]
    cells = [(x, y, z) for z in range(dims[2]) for y in range(dims[1]) for x in range(dims[0])]
    keep = [c for c in cells if rng.random() < 0.85] or [cells[0]]
    index = {c: i for i, c in enumerate(keep)}
    slots = len(keep)

    n = rng.randint(2, max_variants)
    modules = rng.randint(1, n)
    variants = [
        {"module": rng.randrange(modules), "rotation": rng.randrange(24), "part": 0, "anchor": rng.random() < 0.8}
        for _ in range(n)
    ]

    density = rng.uniform(0.3, 0.9)
    pairs: dict[int, set[tuple[int, int]]] = {p: set() for p in range(6)}
    for port in (0, 2, 4):
        for v in range(n):
            for w in range(n):
                if rng.random() < density:
                    pairs[port].add((v, w))
                    pairs[port ^ 1].add((w, v))
    allowed = [{"port": p, "twist": 0, "pairs": sorted(pairs[p])} for p in range(6)]

    offsets = [0]
    port: list[int] = []
    neighbour: list[int] = []
    neighbour_port: list[int] = []
    twist: list[int] = []
    deltas = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    exposed: list[tuple[int, int]] = []
    for cell in keep:
        for p, (dx, dy, dz) in enumerate(deltas):
            other = (cell[0] + dx, cell[1] + dy, cell[2] + dz)
            if other in index:
                port.append(p)
                neighbour.append(index[other])
                neighbour_port.append(p ^ 1)
                twist.append(0)
            else:
                exposed.append((index[cell], p))
        offsets.append(len(port))

    classes = [sorted(rng.sample(range(n), rng.randint(1, n))) for _ in range(rng.randint(1, 3))]
    slot_domain = [rng.randrange(len(classes)) for _ in range(slots)]

    weight_kind = rng.random()
    if weight_kind < 0.4:
        variant_weight = [float(rng.randint(1, 9)) for _ in range(n)]
    elif weight_kind < 0.8:
        variant_weight = [round(rng.uniform(0.05, 5.0), 3) for _ in range(n)]
    else:
        variant_weight = [rng.choice([0.0, 0.5, 1.0, 2.0, 7.25]) for _ in range(n)]

    doc: dict[str, Any] = {
        "schema_version": "1.0.0",
        "compile_hash": "differential",
        "modules": [{"name": f"m{i}", "library_index": i} for i in range(modules)],
        "variants": variants,
        "ports": 6,
        "allowed": allowed,
        "slots": slots,
        "edges": {"offsets": offsets, "port": port, "neighbour": neighbour, "neighbour_port": neighbour_port, "twist": twist},
        "domain_classes": classes,
        "slot_domain": slot_domain,
        "variant_weight": variant_weight,
        "settings": {
            "seed": rng.randrange(1_000_000),
            "attempts": rng.randint(1, 4),
            "threads": rng.randint(1, 2),
            "backtrack_budget": rng.choice([0, 1, 5, 50]),
            "max_observations": rng.choice([0, 0, 0, rng.randint(1, 6)]),
            "mode": "collapse",
        },
    }
    if rng.random() < 0.3:
        doc["slot_module_weight"] = [[rng.choice([0.0, 0.5, 1.0, 2.0]) for _ in range(modules)] for _ in range(slots)]
    if rng.random() < 0.3:
        doc["priority"] = [rng.randint(-2, 2) for _ in range(slots)]
    if rng.random() < 0.2:
        fixed = [-1] * slots
        for _ in range(rng.randint(1, 2)):
            s = rng.randrange(slots)
            fixed[s] = rng.choice(classes[slot_domain[s]])
        doc["fixed"] = fixed
    if rng.random() < 0.3 and exposed:
        doc["boundary_classes"] = [
            {"connector": f"c{i}", "variants": sorted(rng.sample(range(n), rng.randint(1, n)))} for i in range(rng.randint(1, 2))
        ]
        chosen = rng.sample(exposed, min(len(exposed), rng.randint(1, 4)))
        doc["boundary"] = [{"slot": s, "port": p, "class": rng.randrange(len(doc["boundary_classes"]))} for s, p in chosen]
    return doc


def compare(doc: dict[str, Any], label: str) -> str:
    """Solve in both implementations and return the outcome; assert agreement."""
    text = json.dumps(doc)
    assembly = load_assembly(text)
    rust_report = lamda_core.canonicalise_json(text)
    try:
        solver = Solver(assembly)
    except CanonicalError as error:
        assert rust_report == error.report.as_dict(), label
        with pytest.raises(ValueError):
            lamda_core.solve_json(text)
        return "setup-contradiction"
    assert rust_report == solver.report.as_dict(), label
    rust = lamda_core.solve_json(text)
    ref = solve(assembly)
    for name in ("variant", "module", "rotation", "part", "anchor", "state", "options_left", "collapse_step"):
        r = np.asarray(getattr(rust, name))
        p = getattr(ref, name)
        assert r.dtype == p.dtype, f"{label}: {name} dtype {r.dtype} vs {p.dtype}"
        assert np.array_equal(r, p), f"{label}: {name} differs\n rust {r.tolist()}\n ref  {p.tolist()}"
    rust_report = {k: v for k, v in rust.report.items() if k != "elapsed"}
    ref_report = {k: v for k, v in ref.report.items() if k != "elapsed"}
    assert rust_report == ref_report, f"{label}: report differs {rust_report} vs {ref_report}"
    if ref.report["outcome"] == "solved":
        assert validate(assembly, rust.variant, rust.state) == [], label
        assert lamda_core.validate_json(text, rust.variant, rust.state) == [], label
    return ref.report["outcome"]


def run_cases(first_seed: int, count: int, **kwargs: int) -> dict[str, int]:
    """Run ``count`` random cases; on failure write the case to ``SCRATCH`` for replay."""
    outcomes: dict[str, int] = {}
    for seed in range(first_seed, first_seed + count):
        doc = random_assembly(random.Random(seed), **kwargs)
        try:
            outcome = compare(doc, f"case seed {seed}")
        except AssertionError:
            SCRATCH.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            raise
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


def test_small_cases_agree() -> None:
    """2,000 tiny assemblies: every array and report field identical; all four outcomes must occur."""
    outcomes = run_cases(0, 2000)
    assert set(outcomes) >= {"solved", "contradiction", "partial", "setup-contradiction"}, outcomes
    assert outcomes["solved"] > 500, outcomes


def test_larger_cases_exercise_backtracking() -> None:
    """300 larger assemblies (up to 4 x 4 x 4 slots, 20 variants) so backtracking and multiple attempts run."""
    outcomes = run_cases(100_000, 300, max_dim=4, max_variants=20)
    assert outcomes.get("solved", 0) > 50, outcomes
