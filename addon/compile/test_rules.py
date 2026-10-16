# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule-expansion tests: symmetry on random kits, Indifference, twist flags, explicit rules."""

from __future__ import annotations

import random

from addon.compile import frames
from addon.compile.assemble import compile_assembly
from addon.compile.rotations import freedom_rotations
from addon.compile.rules import build_pairs, reference_rotation
from addon.compile.variants import expand_variants
from addon.model import Connector, ConnectorPair, Envelope, Kit, Module, Rule, SolveSettings


def random_kit(rng: random.Random) -> Kit:
    """A random kit: a few modules with random connectors, freedoms, pairs and rules."""
    names = ["a", "b", "c", "d"][: rng.randint(1, 4)]
    connectors = ["p", "q", "r", ""]
    modules = [
        Module(
            name,
            rng.choice(["NONE", "X", "Y", "Z", "FULL"]),
            rng.choice([1.0, 2.0]),
            (),
            tuple(Connector(c, rng.randrange(4)) if (c := rng.choice(connectors)) else Connector() for _ in range(6)),
            tuple(rng.random() < 0.2 for _ in range(6)),
        )
        for name in names
    ]
    pairs = [
        ConnectorPair(rng.choice("pqr"), rng.choice("pqr"), tuple(rng.random() < 0.6 for _ in range(4)))
        for _ in range(rng.randint(0, 4))
    ]
    rules = [
        Rule(rng.choice(["allowed", "disallowed", "exclusive"]), rng.choice(names), rng.randrange(6), rng.choice(names), rng.randrange(6))
        for _ in range(rng.randint(0, 3))
    ]
    return Kit(modules, pairs, rules, indifference=rng.random() < 0.7)


def test_random_kits_give_symmetric_tables() -> None:
    """Property: for 300 random kits, (v, w) at port p exactly when (w, v) at the opposite port, and compiling succeeds."""
    for seed in range(300):
        kit = random_kit(random.Random(seed))
        expansion = expand_variants(kit)
        pairs, _ = build_pairs(kit, expansion)
        for port in range(6):
            for v, w in pairs[port]:
                assert (w, v) in pairs[frames.opposite(port)], (seed, port, v, w)
        compile_assembly(kit, Envelope.box((2, 2, 1)), SolveSettings())


def test_indifference_only_between_uncovered_faces() -> None:
    """Uncovered meets uncovered when Indifference is on, never a named face; with it off, uncovered meets nothing."""
    bare = Module("bare")
    named = Module("named", faces=(Connector("p"),) * 6)
    kit_on = Kit([bare, named], [ConnectorPair("p", "p")], indifference=True)
    pairs, _ = build_pairs(kit_on, expand_variants(kit_on))
    assert pairs[0] == {(0, 0), (1, 1)}
    kit_off = Kit([bare, named], [ConnectorPair("p", "p")], indifference=False)
    pairs, _ = build_pairs(kit_off, expand_variants(kit_off))
    assert pairs[0] == {(1, 1)}


def test_self_pair_only_when_declared() -> None:
    """"p" meets "q" when declared either way; "p" meets "p" only with a ("p", "p") pair."""
    p = Module("p", faces=(Connector("p"),) * 6)
    q = Module("q", faces=(Connector("q"),) * 6)
    kit = Kit([p, q], [ConnectorPair("q", "p")])
    pairs, _ = build_pairs(kit, expand_variants(kit))
    assert pairs[0] == {(0, 1), (1, 0)}


def test_keyed_pin_and_slot_twist_flags() -> None:
    """A keyed pair (twist 0 only): offset 0 on +X meets offset 2 on -X, not offset 0; all flags admit both."""
    def kit(slot_offset: int, twists: tuple[bool, bool, bool, bool]) -> Kit:
        pin = Module("pin", faces=(Connector("key"),) + (Connector(),) * 5)
        slot = Module("slot", faces=(Connector(), Connector("key", slot_offset)) + (Connector(),) * 4)
        return Kit([pin, slot], [ConnectorPair("key", "key", twists)], indifference=False)

    keyed = (True, False, False, False)
    assert build_pairs(kit(2, keyed), expand_variants(kit(2, keyed)))[0][0] == {(0, 1)}
    assert build_pairs(kit(0, keyed), expand_variants(kit(0, keyed)))[0][0] == set()
    assert build_pairs(kit(0, (False, False, True, False)), expand_variants(kit(0, keyed)))[0][0] == {(0, 1)}
    square = (True, True, True, True)
    assert build_pairs(kit(1, square), expand_variants(kit(1, square)))[0][0] == {(0, 1)}


def test_allowed_rule_both_rotatable_is_rigid() -> None:
    """a:+X -> a:-X with a FULL and no connectors: each variant meets only itself across its own +X."""
    a = Module("a", "FULL")
    kit = Kit([a], rules=[Rule.parse("allowed", "a:+X -> a:-X")], indifference=False)
    expansion = expand_variants(kit)
    assert len(expansion.variants) == 24  # no geometry hashes: no merging
    pairs, notes = build_pairs(kit, expansion)
    assert not notes.empty_rules
    for port in range(6):
        # Four rotations put +X on this port and four put -X there (their +X
        # is across, so the mirror of that touch lands here): eight self-pairs.
        assert len(pairs[port]) == 8
        assert all(v == w for v, w in pairs[port])
        for v, _ in pairs[port]:
            assert expansion.variants[v].authored_face[port] in (0, 1)


def test_allowed_rule_one_side_rotatable_permutes_that_side() -> None:
    """a NONE, b FULL, a:+X -> b:-X: a meets every b rotation that puts b's -X across, four of them."""
    a = Module("a")
    b = Module("b", "FULL")
    kit = Kit([a, b], rules=[Rule.parse("allowed", "a:+X -> b:-X")], indifference=False)
    expansion = expand_variants(kit)
    pairs, _ = build_pairs(kit, expansion)
    partners = {w for v, w in pairs[0] if v == 0}
    assert len(partners) == 4
    for w in partners:
        assert expansion.variants[w].world_face_of(1) == 1
    assert pairs[1] == {(w, 0) for w in partners}


def test_non_opposing_rule_uses_the_reference_rotation() -> None:
    """a:+X -> b:+Y rotates b a quarter turn so +Y faces -X; both NONE gives nothing, b rotatable gives the turned b."""
    assert reference_rotation(0, 1) == 0
    q = reference_rotation(0, 2)
    assert frames.FACE_TABLE[q][2][0] == 1
    assert frames.FACE_TABLE[reference_rotation(0, 0)][0][0] == 1
    a = Module("a")
    b_fixed = Module("b")
    kit = Kit([a, b_fixed], rules=[Rule.parse("allowed", "a:+X -> b:+Y")], indifference=False)
    pairs, notes = build_pairs(kit, expand_variants(kit))
    assert pairs[0] == set() and notes.empty_rules
    b_free = Module("b", "FULL")
    kit = Kit([a, b_free], rules=[Rule.parse("allowed", "a:+X -> b:+Y")], indifference=False)
    expansion = expand_variants(kit)
    pairs, _ = build_pairs(kit, expansion)
    assert all(expansion.variants[w].world_face_of(2) == 1 for _, w in pairs[0])
    assert len(pairs[0]) == 4


def test_disallowed_removes_through_every_rotation() -> None:
    """With "t" everywhere and FULL freedom, disallowing a:+X -> a:-X removes every a-a touch of those faces."""
    a = Module("a", "FULL", faces=(Connector("t"),) * 6)
    kit = Kit([a], [ConnectorPair("t", "t")])
    before, _ = build_pairs(kit, expand_variants(kit))
    kit.rules.append(Rule.parse("disallowed", "a:+X -> a:-X"))
    expansion = expand_variants(kit)
    after, _ = build_pairs(kit, expansion)
    assert len(before[0]) == 24 * 24
    for port in range(6):
        across = frames.opposite(port)
        # Four rotations put +X on this port, four put -X across it: 16 touches,
        # plus the 16 mirrors of the same rule seen from the other side.
        assert len(after[port]) == 24 * 24 - 32
        for v, w in after[port]:
            a_faces = (expansion.variants[v].authored_face[port], expansion.variants[w].authored_face[across])
            assert a_faces not in ((0, 1), (1, 0))


def test_exclusive_rule_keeps_only_its_partner() -> None:
    """a:+X -> b:-X exclusive among three modules sharing connector "t": a's +X meets only b's -X and vice versa."""
    a = Module("a", faces=(Connector("t"),) * 6)
    b = Module("b", faces=(Connector("t"),) * 6)
    c = Module("c", faces=(Connector("t"),) * 6)
    kit = Kit([a, b, c], [ConnectorPair("t", "t")], [Rule.parse("exclusive", "a:+X -> b:-X")])
    pairs, _ = build_pairs(kit, expand_variants(kit))
    assert {w for v, w in pairs[0] if v == 0} == {1}
    assert {v for v, w in pairs[1] if w == 0} == {1}
    assert {w for v, w in pairs[1] if v == 1} == {0}  # b's -X (port 1) meets only a
    assert {w for v, w in pairs[0] if v == 2} == {0, 1, 2} - {1} or {w for v, w in pairs[0] if v == 2} == {0, 2}
    assert (2, 2) in pairs[2]  # other faces untouched


def test_freedom_subsets_limit_variants() -> None:
    """A Z-free module with a connector on +X gets 4 variants; NONE gets 1; FULL with hashes merges nothing here."""
    m = Module("m", "Z", faces=(Connector("t"),) + (Connector(),) * 5)
    assert len(expand_variants(Kit([m])).variants) == 4
    assert len(expand_variants(Kit([Module("m")])).variants) == 1
    assert len(freedom_rotations("FULL")) == 24
