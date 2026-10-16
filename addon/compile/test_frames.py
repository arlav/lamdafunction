# SPDX-License-Identifier: GPL-3.0-or-later
"""Exhaustive tests of face frames, rotated faces and twist (spike 2).

The twist test is the "physical keyed pin-and-slot example" from
``docs/PLAN.md``: a pin module with a key on its +X face and a slot module
with a keyway on its -X face, both rotated through all 24 rotations and all
four offsets, placed side by side. The key fits the keyway exactly when the
two world-space arrows coincide, and that must be exactly when the computed
twist is 0, from either side, by either definition.
"""

from __future__ import annotations

import itertools

import pytest

from addon.compile import frames
from addon.compile import rotations as rot

ALL = range(len(rot.ROTATIONS))
FACES = range(frames.FACE_COUNT)


def test_frames_are_right_handed_and_opposites_mirror() -> None:
    """u x v = n on every face; opposite faces flip u and keep v. The twist formula relies on this mirror."""
    for f in FACES:
        u, v, n = frames.FACE_U[f], frames.FACE_V[f], frames.FACE_NORMAL[f]
        assert rot.cross(u, v) == n
        assert rot.dot(u, n) == 0 and rot.dot(v, n) == 0 and rot.dot(u, v) == 0
        o = frames.opposite(f)
        assert frames.FACE_NORMAL[o] == rot.neg(n)
        assert frames.FACE_U[o] == rot.neg(u)
        assert frames.FACE_V[o] == v


def test_frame_table_matches_plan() -> None:
    """The exact table from docs/PLAN.md "Face frames and twist"; it is a contract with authored kits."""
    expected = {
        "+X": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        "-X": ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
        "+Y": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
        "-Y": ((0, -1, 0), (0, 0, -1), (1, 0, 0)),
        "+Z": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
        "-Z": ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    }
    for name, (n, u, v) in expected.items():
        f = frames.face_from_name(name)
        assert (frames.FACE_NORMAL[f], frames.FACE_U[f], frames.FACE_V[f]) == (n, u, v)
    assert frames.face_from_name(" -z ") == 5
    with pytest.raises(ValueError):
        frames.face_from_name("+W")


def test_turn_is_counter_clockwise_and_exact() -> None:
    """Quarter turns about a normal go u -> v -> -u -> -v and the normal component is untouched."""
    for f in FACES:
        u, v, n = frames.FACE_U[f], frames.FACE_V[f], frames.FACE_NORMAL[f]
        assert [frames.turn(u, n, k) for k in range(4)] == [u, v, rot.neg(u), rot.neg(v)]
        assert frames.turn(n, n, 1) == n
        assert frames.turn(u, n, 5) == frames.turn(u, n, 1)
        assert frames.turn(u, n, -1) == frames.turn(u, n, 3)
    with pytest.raises(ValueError):
        frames.turn((1, 0, 0), (1, 1, 0), 1)


def test_arrows_are_the_four_in_plane_axes() -> None:
    """A face has four distinct arrows, all perpendicular to its normal; offset_of_arrow inverts arrow."""
    for f in FACES:
        arrows = [frames.arrow(f, k) for k in range(4)]
        assert len(set(arrows)) == 4
        assert arrows[0] == frames.FACE_U[f] and arrows[1] == frames.FACE_V[f]
        for k, a in enumerate(arrows):
            assert rot.dot(a, frames.FACE_NORMAL[f]) == 0
            assert frames.offset_of_arrow(f, a) == k
        with pytest.raises(ValueError):
            frames.offset_of_arrow(f, frames.FACE_NORMAL[f])


def test_rotate_face_moves_every_arrow_consistently() -> None:
    """24 x 6 x 4 cases: a rotation sends face d to the face of R n, and every arrow on it by one offset change."""
    for r, f in itertools.product(ALL, FACES):
        new_face, delta = frames.rotate_face(r, f)
        assert frames.FACE_TABLE[r][f] == (new_face, delta)
        assert frames.FACE_NORMAL[new_face] == rot.apply(r, frames.FACE_NORMAL[f])
        for k in range(4):
            moved = rot.apply(r, frames.arrow(f, k))
            assert moved == frames.arrow(new_face, (k + delta) % 4)
            assert frames.variant_face(r, f, k) == (new_face, (k + delta) % 4)


def test_face_table_rows_are_permutations_and_compose() -> None:
    """Each table row permutes the faces; the identity row is trivial; rows compose like the rotations."""
    assert frames.FACE_TABLE[rot.IDENTITY] == tuple((f, 0) for f in FACES)
    for r in ALL:
        assert sorted(new for new, _ in frames.FACE_TABLE[r]) == list(FACES)
    for a, b in itertools.product(ALL, ALL):
        ab = rot.compose(a, b)
        for f in FACES:
            mid, delta_b = frames.FACE_TABLE[b][f]
            end, delta_a = frames.FACE_TABLE[a][mid]
            assert frames.FACE_TABLE[ab][f] == (end, (delta_a + delta_b) % 4)


def test_twist_symmetric_and_total() -> None:
    """twist(a, b) == twist(b, a), and each of the four values is reachable."""
    seen = set()
    for a, b in itertools.product(range(4), range(4)):
        assert frames.twist(a, b) == frames.twist(b, a)
        seen.add(frames.twist(a, b))
    assert seen == {0, 1, 2, 3}
    # Unrotated, offsets 0 on +X and 2 on -X: both arrows are +Y, twist 0.
    assert frames.arrow(0, 0) == frames.arrow(1, 2) == (0, 1, 0)
    assert frames.twist(0, 2) == 0
    # Offsets 0 and 0: +Y meets -Y, a half turn apart.
    assert frames.twist(0, 0) == 2


def _pin_and_slot_cases() -> list[tuple[int, int, int, int]]:
    """All (rotation of pin, rotation of slot, key offset, keyway offset) where the pin's +X face can meet the slot's -X face."""
    cases = []
    for ra, rb in itertools.product(ALL, ALL):
        face_a, _ = frames.FACE_TABLE[ra][0]
        face_b, _ = frames.FACE_TABLE[rb][1]
        if frames.can_touch(face_a, face_b):
            for ka, kb in itertools.product(range(4), range(4)):
                cases.append((ra, rb, ka, kb))
    return cases


def test_keyed_pin_and_slot_through_all_rotation_pairs() -> None:
    """The physical check: for 24 x 24 rotation pairs and 4 x 4 offsets, the key lands in the keyway exactly when twist is 0,
    the frame formula agrees with the world-arrow definition, and both sides measure the same twist."""
    cases = _pin_and_slot_cases()
    # For each pin rotation its +X face lands somewhere; 4 of the 24 slot rotations bring -X opposite it.
    assert len(cases) == 24 * 4 * 16
    twists_seen = set()
    for ra, rb, ka, kb in cases:
        face_a, offset_a = frames.variant_face(ra, 0, ka)
        face_b, offset_b = frames.variant_face(rb, 1, kb)
        normal = frames.FACE_NORMAL[face_a]
        # World-space arrows: rotate the authored arrows with the modules.
        arrow_a = rot.apply(ra, frames.arrow(0, ka))
        arrow_b = rot.apply(rb, frames.arrow(1, kb))
        assert arrow_a == frames.arrow(face_a, offset_a)
        assert arrow_b == frames.arrow(face_b, offset_b)
        # Physical positions, unit cubes: pin centred at the origin, slot one cell along the normal.
        # The key sits 0.4 from the shared face centre along the pin's arrow, the keyway likewise along the slot's.
        key = rot.add(rot.scale(5, normal), rot.scale(4, arrow_a))  # tenths of a cell
        keyway = rot.add(rot.add(rot.scale(10, normal), rot.scale(-5, normal)), rot.scale(4, arrow_b))
        fits = key == keyway
        t_frames = frames.twist(offset_a, offset_b)
        t_arrows = frames.twist_from_arrows(normal, arrow_a, arrow_b)
        t_from_b = frames.twist_from_arrows(frames.FACE_NORMAL[face_b], arrow_b, arrow_a)
        assert t_frames == t_arrows == t_from_b
        assert fits == (t_frames == 0)
        twists_seen.add(t_frames)
    assert twists_seen == {0, 1, 2, 3}


def test_twist_from_arrows_rejects_out_of_plane() -> None:
    """An arrow along the normal is not a connector arrow; the function must say so rather than guess."""
    with pytest.raises(ValueError):
        frames.twist_from_arrows((1, 0, 0), (0, 1, 0), (1, 0, 0))
