# SPDX-License-Identifier: MIT OR Apache-2.0
"""Golden-fixture tests for the reference solver (``.claude/rules/solver-core.md``, "Golden fixtures")."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from reference import CanonicalError, Solver, load_assembly, solve, validate
from reference.pcg32 import Pcg32
from reference.solver import EntropySums, Weights, entropy_key

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
GOLDEN_SEED_1 = [0, 1, 2, 0, 1, 2, 0, 3, 4, 5, 3, 4, 5, 6]
GOLDEN_STEPS = [0, 2, 2, 2, 4, 4, 4, 1, 3, 3, 6, 5, 5, 7]


def office():
    """The office south strip fixture."""
    return load_assembly(FIXTURES / "office_south_strip.json")


def test_pcg32_test_vector() -> None:
    """The PCG32 vector from the fixture must match: it anchors every random choice in both languages."""
    fixture = json.loads((FIXTURES / "pcg32.json").read_text(encoding="utf-8"))
    rng = Pcg32(*fixture["new"])
    outputs = [rng.next_u32() for _ in fixture["outputs"]]
    assert [f"0x{x:08x}" for x in outputs] == fixture["outputs"]
    assert int(fixture["attempt_stream"], 16) == 0x4C414D44


def test_pcg32_attempt_stream_and_unit_float() -> None:
    """for_attempt(seed, k) is new(seed + k, STREAM), and unit_f64 is next_u32 / 2^32."""
    a = Pcg32.for_attempt(5, 2)
    b = Pcg32(7, 0x4C414D44)
    assert a.next_u32() == b.next_u32()
    u = Pcg32(1, 1)
    v = Pcg32(1, 1)
    assert u.unit_f64() == v.next_u32() / 4294967296.0
    assert 0.0 <= Pcg32(3, 3).unit_f64() < 1.0


def test_office_canonicalisation() -> None:
    """One propagation pass collapses exactly one slot (P1 = {S, WL} after the corner fix) and kills no variant."""
    solver = Solver(office())
    assert solver.report.as_dict() == {"contradiction": None, "dead_variants": [], "collapsed": 1}
    assert sorted(solver.canonical[1] >> v & 1 for v in range(7)) == [0, 0, 0, 0, 0, 1, 1]


def test_office_seed_1_is_golden() -> None:
    """Seed 1 must give the pinned result, observation count and collapse steps; this is the cross-language anchor."""
    result = solve(office())
    assert result.report["outcome"] == "solved"
    assert result.report["attempt"] == 0
    assert result.report["observations"] == 7
    assert result.report["backtracks"] == 0
    assert result.variant.tolist() == GOLDEN_SEED_1
    assert result.collapse_step.tolist() == GOLDEN_STEPS
    assert result.state.tolist() == [0] * 14
    assert result.options_left.tolist() == [1] * 14
    assert result.module.tolist() == GOLDEN_SEED_1  # one variant per module in this kit
    assert validate(office(), result.variant, result.state) == []


def test_office_every_seed_solves_and_validates() -> None:
    """Many seeds, every result valid and more than one distinct strip: the solver explores, not repeats."""
    assembly = office()
    doc = dict(assembly.raw)
    strips = set()
    for seed in range(60):
        doc["settings"] = dict(doc["settings"], seed=seed)
        result = solve(load_assembly(doc))
        assert result.report["outcome"] == "solved", seed
        assert validate(assembly, result.variant, result.state) == [], seed
        strips.add(tuple(result.variant.tolist()))
    assert len(strips) > 20


def test_office_entropies_match_the_plan() -> None:
    """The golden entropies: {S, WL} and {E, ST} 0.637, {E, DL} 0.500, a full floor cell {E, DL, DR, ST} 1.120."""
    assembly = office()
    weights = Weights(assembly)
    sums = EntropySums(assembly.slots)

    def entropy(slot: int, variants: list[int]) -> float:
        domain = sum(1 << v for v in variants)
        sums.recompute(slot, domain, weights)
        return sums.entropy(slot, len(variants))

    assert round(entropy(0, [0, 1]), 3) == 0.637
    assert round(entropy(7, [3, 6]), 3) == 0.637
    assert round(entropy(7, [3, 4]), 3) == 0.500
    assert round(entropy(7, [3, 4, 5, 6]), 3) == 1.120
    assert entropy(0, [0]) == 0.0
    assert entropy_key(0.6365141682948128) == 636514
    assert entropy_key(0.0000005) == 1 and entropy_key(0.0000004) == 0


def test_validate_reports_violations() -> None:
    """Swapping two neighbours breaks an allowed pair; the validator must say which."""
    assembly = office()
    result = solve(assembly)
    variant = result.variant.copy()
    variant[1], variant[2] = variant[2], variant[1]
    messages = validate(assembly, variant, result.state)
    assert messages and "not allowed" in messages[0]
    assert validate(assembly, variant[:3], result.state)[0].startswith("result has 3 entries")


def test_contradictory_fixed_slot_is_a_setup_error() -> None:
    """Fixing a window-left panel where its right half cannot follow contradicts at canonicalisation."""
    doc = dict(office().raw)
    fixed = [-1] * 14
    fixed[6] = 1  # WL in the last panel, which has no east neighbour for WR and is domain class {S, WR}
    doc["fixed"] = fixed
    with pytest.raises(CanonicalError) as info:
        Solver(load_assembly(doc))
    assert info.value.report.contradiction is not None


def test_backtracking_recovers_from_a_dead_end() -> None:
    """With a tiny budget the tight kit fails; with a larger one it backtracks and solves, counting the backtracks."""
    doc = json.loads((FIXTURES / "office_south_strip.json").read_text(encoding="utf-8"))
    # Make the kit tight: forbid S next to S so the façade has few solutions.
    for table in doc["allowed"]:
        table["pairs"] = [p for p in table["pairs"] if p != [0, 0]]
    doc["settings"].update(seed=3, attempts=1, backtrack_budget=0)
    budget0 = solve(load_assembly(doc))
    doc["settings"].update(backtrack_budget=50)
    budget50 = solve(load_assembly(doc))
    assert budget50.report["outcome"] == "solved"
    assert validate(load_assembly(doc), budget50.variant, budget50.state) == []
    assert budget0.report["backtracks"] <= 1 and budget50.report["backtracks"] >= budget0.report["backtracks"]
    assert math.isfinite(budget50.report["elapsed"])
