# SPDX-License-Identifier: GPL-3.0-or-later
"""Face frames, connector arrows, rotated faces and the twist between two faces.

This is the spec that spike 2 pins down (``docs/spikes/02-face-frames-twist.md``,
``docs/PLAN.md`` "Face frames and twist"). Everything downstream (rule
expansion, the overlay's arrows, the twist flags on connector pairs) calls
these functions rather than redoing the geometry.

Faces are numbered like the box ports in ``.claude/rules/solver-core.md``:
0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z, so ``opposite(face) == face ^ 1``.

Each face has a fixed right-handed in-plane frame ``(u, v, n)`` with
``u x v = n``, taken from the table in ``docs/PLAN.md``. Seen from outside the
face, ``u`` is "right" and ``v`` is "up" (with +Z up for the side faces). The
frames of opposite faces are mirror images: ``u`` flips, ``v`` stays.

A connector with offset ``k`` points along ``u`` turned ``k`` quarter turns
counter-clockwise about the outward normal (:func:`arrow`). Rotating a module
moves each face to another face and changes every offset on it by a constant
(:func:`rotate_face`, tabulated in :data:`FACE_TABLE`).

The twist of a touch is defined once, from world-space arrows: when face
``d`` of A (arrow ``a``) meets face ``opposite(d)`` of B (arrow ``b``), the
twist is the number of quarter turns counter-clockwise about A's outward
normal that turn ``a`` onto ``b`` (:func:`twist_from_arrows`). Twist 0 means
the two arrows coincide, so a keyed pin fits its slot. Measured from B's side
about B's own normal the number is the same, so one twist belongs to the touch,
not to a side, and the four twist flags on a connector pair have one meaning
whichever name is listed first.

In the face-frame convention the same twist is ``(2 - k_a - k_b) mod 4`` from
the two effective offsets (:func:`twist`); the tests prove the two definitions
agree for all 24 x 24 rotation pairs. The ``2`` is the mirror: an author who
wants twist 0 between offset ``k`` on +X and a connector on -X must give the
-X side offset ``(2 - k) mod 4``, which is what the overlay's world-space
arrows show.
"""

from __future__ import annotations

from typing import Final

from .rotations import (
    AXES,
    AXIS_NAMES,
    ROTATIONS,
    Vec,
    add,
    apply,
    cross,
    dot,
    scale,
)

FACE_COUNT: Final[int] = 6
"""Faces of a box slot."""

FACE_NAMES: Final[tuple[str, ...]] = AXIS_NAMES
"""Face names in face order: +X, -X, +Y, -Y, +Z, -Z."""

FACE_NORMAL: Final[tuple[Vec, ...]] = AXES
"""Outward normal of each face."""

FACE_U: Final[tuple[Vec, ...]] = (
    (0, 1, 0),  # +X: u = +Y
    (0, -1, 0),  # -X: u = -Y
    (0, 0, 1),  # +Y: u = +Z
    (0, 0, -1),  # -Y: u = -Z
    (1, 0, 0),  # +Z: u = +X
    (-1, 0, 0),  # -Z: u = -X
)
"""Reference axis ``u`` of each face: where a connector with offset 0 points."""

FACE_V: Final[tuple[Vec, ...]] = (
    (0, 0, 1),  # +X: v = +Z
    (0, 0, 1),  # -X: v = +Z
    (1, 0, 0),  # +Y: v = +X
    (1, 0, 0),  # -Y: v = +X
    (0, 1, 0),  # +Z: v = +Y
    (0, 1, 0),  # -Z: v = +Y
)
"""Second axis ``v`` of each face; ``u x v`` is the outward normal."""


def opposite(face: int) -> int:
    """The face across the slot: +X <-> -X, +Y <-> -Y, +Z <-> -Z."""
    return face ^ 1


def face_of_normal(normal: Vec) -> int:
    """The face whose outward normal is ``normal``.

    Raises ``ValueError`` if ``normal`` is not a signed axis.
    """
    try:
        return FACE_NORMAL.index(normal)
    except ValueError:
        raise ValueError(f"not a face normal: {normal!r}") from None


def face_from_name(name: str) -> int:
    """Face index from a name such as ``"+X"`` or ``"-z"``."""
    key = name.strip().upper()
    try:
        return FACE_NAMES.index(key)
    except ValueError:
        raise ValueError(f"unknown face name: {name!r}") from None


def turn(v: Vec, normal: Vec, quarters: int) -> Vec:
    """Turn ``v`` by ``quarters`` quarter turns counter-clockwise about ``normal``.

    ``normal`` must be a signed axis. Counter-clockwise means the right-hand
    rule: with the thumb along ``normal``, the fingers give the direction.
    Rodrigues' formula with ``cos`` and ``sin`` in ``{-1, 0, 1}`` keeps it exact.
    """
    if normal not in AXES:
        raise ValueError(f"not a signed axis: {normal!r}")
    quarters %= 4
    cos = (1, 0, -1, 0)[quarters]
    sin = (0, 1, 0, -1)[quarters]
    along = scale(dot(normal, v), normal)
    across = add(v, scale(-1, along))
    return add(along, add(scale(cos, across), scale(sin, cross(normal, v))))


def arrow(face: int, offset: int) -> Vec:
    """World-space arrow of a connector with ``offset`` quarter turns on ``face`` of an unrotated module."""
    return turn(FACE_U[face], FACE_NORMAL[face], offset)


def offset_of_arrow(face: int, direction: Vec) -> int:
    """The offset ``k`` with ``arrow(face, k) == direction``.

    Raises ``ValueError`` if ``direction`` does not lie in the face's plane
    along one of its four axes.
    """
    for k in range(4):
        if arrow(face, k) == direction:
            return k
    raise ValueError(f"{direction!r} is not an arrow of face {FACE_NAMES[face]}")


def rotate_face(rotation: int, face: int) -> tuple[int, int]:
    """Where rotation number ``rotation`` sends ``face``, and how offsets change.

    Returns ``(new_face, delta)`` such that a connector with offset ``k`` on
    ``face`` of the unrotated module sits on ``new_face`` with offset
    ``(k + delta) % 4`` after the rotation. ``delta`` does not depend on
    ``k`` because rotating commutes with turning about the (rotated) normal.
    """
    new_face = face_of_normal(apply(rotation, FACE_NORMAL[face]))
    delta = offset_of_arrow(new_face, apply(rotation, FACE_U[face]))
    return new_face, delta


FACE_TABLE: Final[tuple[tuple[tuple[int, int], ...], ...]] = tuple(
    tuple(rotate_face(r, f) for f in range(FACE_COUNT)) for r in range(len(ROTATIONS))
)
"""``FACE_TABLE[rotation][face] == rotate_face(rotation, face)``, precomputed (24 x 6)."""


def variant_face(rotation: int, face: int, offset: int) -> tuple[int, int]:
    """The world face and effective offset of a connector after a rotation.

    This is what the compiler stores per variant: a connector authored on
    ``face`` with ``offset`` lands on ``new_face`` with ``new_offset``.
    """
    new_face, delta = FACE_TABLE[rotation][face]
    return new_face, (offset + delta) % 4


def twist(offset_a: int, offset_b: int) -> int:
    """Twist of a touch from the two effective offsets in the face-frame convention.

    ``offset_a`` is the effective offset of A's connector on A's world face
    ``d``; ``offset_b`` that of B's connector on ``opposite(d)``. The result is
    the same twist :func:`twist_from_arrows` computes from world arrows, and it
    is symmetric in A and B.
    """
    return (2 - offset_a - offset_b) % 4


def twist_from_arrows(normal: Vec, arrow_a: Vec, arrow_b: Vec) -> int:
    """Twist of a touch from world-space arrows: quarter turns about ``normal`` taking ``arrow_a`` onto ``arrow_b``.

    ``normal`` is A's outward normal across the touch; both arrows must lie in
    the plane perpendicular to it along signed axes, or ``ValueError`` is
    raised. The definition the whole project uses: see the module docstring.
    """
    for k in range(4):
        if turn(arrow_a, normal, k) == arrow_b:
            return k
    raise ValueError(f"{arrow_b!r} is not a quarter turn of {arrow_a!r} about {normal!r}")


def can_touch(face_a: int, face_b: int) -> bool:
    """Whether world face ``face_a`` of one slot can meet world face ``face_b`` of its neighbour on a box grid."""
    return face_b == opposite(face_a)
