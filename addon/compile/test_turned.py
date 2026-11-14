# SPDX-License-Identifier: GPL-3.0-or-later
"""Turned frames (phase 2, S2): the tube kit on a box grid whose slots have random frames.

A turned frame changes only how each slot names its ports and rotations, not
the geometry, so every golden number of ``test_tube.py`` must hold, and every
solution must map back (each slot's variant turned by its frame) to a valid
solution of the plain grid.
"""

from __future__ import annotations

import json
import random

import pytest

from addon.compile.assemble import Compiled, compile_assembly
from addon.compile.rotations import compose, inverse
from addon.compile.test_tube import initial_domain, tube_envelope, tube_kit, variant_named
from addon.model import Envelope, SolveSettings
from reference import Solver, load_assembly, solve, validate

SETTINGS = SolveSettings(seed=1, attempts=4, closed_by="0")


def turned(seed: int) -> tuple[Compiled, Envelope]:
    """The tube envelope with a random frame per slot, compiled."""
    base = tube_envelope()
    rng = random.Random(seed)
    envelope = Envelope(base.cells, frames=[rng.randrange(24) for _ in base.cells])
    return compile_assembly(tube_kit(), envelope, SETTINGS), envelope


def plain() -> Compiled:
    """The tube envelope with no frames."""
    return compile_assembly(tube_kit(), tube_envelope(), SETTINGS)


def local_variant(result: Compiled, slot: int, world_variant: int) -> int:
    """The variant that, placed in ``slot``'s frame, is ``world_variant`` in the world."""
    info = result.expansion.variants[world_variant]
    rotation = compose(inverse(result.frames[slot]), info.rotation)
    local = result.expansion.variant_for(info.module, rotation)
    assert local is not None
    return local


def world_variant(result: Compiled, slot: int, variant: int) -> int:
    """The world variant of ``variant`` placed in ``slot``."""
    info = result.expansion.variants[variant]
    world = result.expansion.variant_for(info.module, result.world_rotation(slot, info.rotation))
    assert world is not None
    return world


def test_identity_frames_compile_byte_identically() -> None:
    """Explicit identity frames must give the phase 1 document exactly, so nothing on box grids changes."""
    cells = tube_envelope().cells
    with_frames = compile_assembly(tube_kit(), Envelope(cells, frames=[0] * len(cells)), SETTINGS)
    assert with_frames.text.replace(with_frames.document["compile_hash"], "") == plain().text.replace(
        plain().document["compile_hash"], ""
    )
    assert with_frames.document["schema_version"] == "1.0.0"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_turned_tube_golden_numbers(seed: int) -> None:
    """37 variants; initial domains 7, 13, 23; both propagation steps give the golden sizes."""
    result, envelope = turned(seed)
    doc = json.loads(result.text)
    assert doc["schema_version"] == "1.1.0"
    assert len(doc["variants"]) == 37
    assert not result.has_errors, [item.message for item in result.audit]
    cells = envelope.cells
    assert len(initial_domain(doc, cells.index((0, 0, 1)))) == 7
    assert len(initial_domain(doc, cells.index((1, 0, 1)))) == 13
    assert len(initial_domain(doc, cells.index((1, 1, 1)))) == 23

    reference_doc = plain()
    fixed = [-1] * len(cells)

    def fix(cell: tuple[int, int, int], module: str, opens: set[str]) -> None:
        slot = cells.index(cell)
        fixed[slot] = local_variant(result, slot, variant_named(reference_doc, module, opens))

    fix((0, 0, 1), "cap", {"+Y"})
    fix((0, 1, 1), "elbow", {"-Y", "-Z"})
    fix((1, 2, 1), "elbow", {"-Y", "-Z"})
    doc["fixed"] = fixed
    solver = Solver(load_assembly(doc))
    sizes = {c: solver.canonical[cells.index(c)].bit_count() for c in [(1, 1, 1), (1, 0, 1), (2, 1, 1), (0, 2, 1)]}
    assert sizes == {(1, 1, 1): 6, (1, 0, 1): 7, (2, 1, 1): 13, (0, 2, 1): 2}

    fix((1, 1, 1), "elbow", {"+X", "+Y"})
    doc["fixed"] = fixed
    solver = Solver(load_assembly(doc))
    assert solver.canonical[cells.index((1, 0, 1))].bit_count() == 4
    assert solver.canonical[cells.index((2, 1, 1))].bit_count() == 6


@pytest.mark.parametrize("seed", range(1, 9))
def test_turned_solutions_map_back(seed: int) -> None:
    """Solve on turned frames; turn every slot's variant into the world; the plain grid's validator accepts it."""
    result, envelope = turned(seed)
    assembly = load_assembly(result.text)
    outcome = solve(assembly)
    assert outcome.report["outcome"] == "solved"
    assert validate(assembly, outcome.variant, outcome.state) == []
    base = plain()
    mapped = outcome.variant.copy()
    for slot in range(len(envelope.cells)):
        mapped[slot] = world_variant(result, slot, int(outcome.variant[slot]))
    assert validate(load_assembly(base.text), mapped, outcome.state) == []


def test_turned_tables_agree_with_the_rust_core() -> None:
    """The Rust core reads the 1.1 document and solves it to the same arrays as the reference."""
    lamda_core = pytest.importorskip("lamda_core")
    result, _ = turned(4)
    rust = lamda_core.solve_json(result.text)
    ref = solve(load_assembly(result.text))
    assert list(rust.variant) == list(ref.variant)
    assert lamda_core.validate_json(result.text, rust.variant, rust.state) == []


@pytest.mark.parametrize("freedom", ["NONE", "Z", "X"])
def test_restricted_freedom_on_turned_frames_stays_symmetric(freedom: str) -> None:
    """Modules that cannot take every rotation: tables stay symmetric, and solutions still map back.

    Across a turned edge, w's rotation seen from the other slot may be outside its freedom;
    the compiler drops such pairs from both directions, so the reference loader's symmetry
    check passes and whatever solves is valid on the plain grid too.
    """
    from addon.model import Module

    kit = tube_kit()
    kit.modules = [
        Module(m.name, freedom if m.name in ("straight", "elbow") else m.freedom, m.weight, m.tags, m.faces,
               geometry_hash=m.geometry_hash)
        for m in kit.modules
    ]
    base = tube_envelope()
    for seed in range(1, 6):
        rng = random.Random(seed)
        envelope = Envelope(base.cells, frames=[rng.randrange(24) for _ in base.cells])
        result = compile_assembly(kit, envelope, SETTINGS)
        assembly = load_assembly(result.text)  # the reference checks symmetry while loading tables
        outcome = solve(assembly)
        if outcome.report["outcome"] != "solved":
            continue
        assert validate(assembly, outcome.variant, outcome.state) == []
        plain_result = compile_assembly(kit, base, SETTINGS)
        mapped = outcome.variant.copy()
        for slot in range(len(base.cells)):
            info = result.expansion.variants[int(outcome.variant[slot])]
            world = plain_result.expansion.variant_for(info.module, result.world_rotation(slot, info.rotation))
            assert world is not None
            mapped[slot] = world
        assert validate(load_assembly(plain_result.text), mapped, outcome.state) == []
