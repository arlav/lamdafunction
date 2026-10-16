# SPDX-License-Identifier: GPL-3.0-or-later
"""Exhaustive tests of the rotation group (spike 2, ``docs/PLAN.md`` "Testing and verification")."""

from __future__ import annotations

import itertools
import math

import pytest

from addon.compile import rotations as rot

ALL = range(len(rot.ROTATIONS))


def test_twenty_four_distinct_proper_rotations() -> None:
    """There are exactly 24 cube rotations, all distinct, all proper (det +1) and orthogonal."""
    assert len(rot.ROTATIONS) == 24
    assert len(set(rot.ROTATIONS)) == 24
    identity = rot.ROTATIONS[rot.IDENTITY]
    for m in rot.ROTATIONS:
        assert rot.determinant(m) == 1
        assert rot.matmul(m, rot.transpose(m)) == identity
        for row in m:
            assert all(entry in (-1, 0, 1) for entry in row)


def test_index_zero_is_identity() -> None:
    """Index 0 must be the identity: a module with rotational freedom None uses it."""
    assert rot.ROTATIONS[0] == ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    for axis in rot.AXES:
        assert rot.apply(0, axis) == axis


def test_numbering_is_stable() -> None:
    """The documented enumeration order: +X image outer, +Y image inner. Changing it breaks every compiled file."""
    images = [(rot.column(m, 0), rot.column(m, 1)) for m in rot.ROTATIONS]
    expected = [
        (x, y) for x in rot.AXES for y in rot.AXES if rot.dot(x, y) == 0
    ]
    assert images == expected


def test_closed_under_composition_with_inverses() -> None:
    """Group axioms: every product is in the group, every element has an inverse, composition is associative."""
    for a, b in itertools.product(ALL, ALL):
        assert 0 <= rot.compose(a, b) < 24
    for a in ALL:
        assert rot.compose(a, rot.inverse(a)) == rot.IDENTITY
        assert rot.compose(rot.inverse(a), a) == rot.IDENTITY
    for a, b, c in itertools.product(ALL, ALL, ALL):
        assert rot.compose(rot.compose(a, b), c) == rot.compose(a, rot.compose(b, c))


def test_compose_applies_right_operand_first() -> None:
    """compose(a, b) must mean "b then a", matching matrix product order, for every vector."""
    for a, b in itertools.product(ALL, ALL):
        ab = rot.compose(a, b)
        for v in rot.AXES:
            assert rot.apply(ab, v) == rot.apply(a, rot.apply(b, v))


def test_face_permutations_are_bijections_preserving_opposites() -> None:
    """Each rotation permutes the six face normals and keeps opposite faces opposite."""
    for r in ALL:
        images = [rot.apply(r, n) for n in rot.AXES]
        assert sorted(images) == sorted(rot.AXES)
        for n, image in zip(rot.AXES, images):
            assert rot.apply(r, rot.neg(n)) == rot.neg(image)


def test_four_quarter_turns_give_identity() -> None:
    """A quarter turn about any axis has order 4, and it turns counter-clockwise by the right-hand rule."""
    for axis in rot.AXES:
        q = rot.quarter_turn(axis)
        assert q != rot.IDENTITY
        twice = rot.compose(q, q)
        assert rot.compose(twice, twice) == rot.IDENTITY
        assert rot.compose(q, rot.compose(q, q)) == rot.inverse(q)
    # Right-hand rule about +Z: +X goes to +Y.
    assert rot.apply(rot.quarter_turn((0, 0, 1)), (1, 0, 0)) == (0, 1, 0)
    # About -Z the same turn is clockwise seen from +Z: +X goes to -Y.
    assert rot.apply(rot.quarter_turn((0, 0, -1)), (1, 0, 0)) == (0, -1, 0)


def test_rotations_about_an_axis_form_the_cyclic_subgroup() -> None:
    """Rotational freedom X, Y or Z gives four rotations: the powers of the quarter turn, including the identity."""
    for axis in rot.AXES[0::2]:
        subset = rot.rotations_about(axis)
        assert len(subset) == 4
        assert rot.IDENTITY in subset
        q = rot.quarter_turn(axis)
        powers = {rot.IDENTITY, q, rot.compose(q, q), rot.inverse(q)}
        assert set(subset) == powers
        assert rot.rotations_about(rot.neg(axis)) == subset


def test_freedom_rotations() -> None:
    """The freedom enum maps to the right subsets; unknown names are errors."""
    assert rot.freedom_rotations("none") == (0,)
    assert rot.freedom_rotations("FULL") == tuple(range(24))
    assert rot.freedom_rotations("Z") == rot.rotations_about((0, 0, 1))
    assert rot.freedom_rotations("x") == rot.rotations_about((1, 0, 0))
    with pytest.raises(ValueError):
        rot.freedom_rotations("W")


def test_index_of_rejects_reflections() -> None:
    """A mirror is not a rotation; the compiler must never produce one silently."""
    mirror = ((-1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert rot.determinant(mirror) == -1
    with pytest.raises(ValueError):
        rot.index_of(mirror)


def test_quaternions_rotate_like_the_matrices() -> None:
    """The quaternion of each rotation moves the basis vectors exactly where the matrix does, is unit length and has a fixed sign."""
    for r in ALL:
        q = rot.quaternion(r)
        assert math.isclose(sum(c * c for c in q), 1.0, abs_tol=1e-12)
        assert q[0] >= 0.0
        if q[0] == 0.0:
            first = next(c for c in q if c != 0.0)
            assert first > 0.0
        for v in rot.AXES:
            got = rot.rotate_by_quaternion(q, v)
            want = rot.apply(r, v)
            assert all(math.isclose(g, w, abs_tol=1e-12) for g, w in zip(got, want))
    assert rot.quaternion(rot.IDENTITY) == (1.0, 0.0, 0.0, 0.0)
    # 24 rotations give 24 distinct quaternions (up to the sign we fixed).
    assert len({tuple(round(c, 9) for c in rot.quaternion(r)) for r in ALL}) == 24
