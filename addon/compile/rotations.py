# SPDX-License-Identifier: GPL-3.0-or-later
"""The 24 proper rotations of the cube, numbered once for the whole project.

A solver variant is (module, rotation, part). The rotation index that
``lamda-core`` stores in ``variants[i].rotation`` and returns in the result
arrays is an index into :data:`ROTATIONS`, so the numbering here is part of the
compiled-assembly contract and must never change once files exist.

Everything is exact integer arithmetic: a rotation of the cube maps each axis
onto an axis, so its matrix holds only 0, 1 and -1. That makes equality
comparisons safe and the tests exhaustive. Floats appear only in
:func:`quaternion`, which exists for Blender's ``wfc_rotation`` attribute.

Conventions:

- Vectors are ``(x, y, z)`` tuples of ints. Matrices are tuples of three rows,
  so ``m[r][c]`` is row ``r``, column ``c``, and column ``c`` is the image of
  basis vector ``c``.
- A rotation acts on column vectors: ``R v``. Composition ``compose(a, b)``
  is "apply ``b`` first, then ``a``", the matrix product ``R_a R_b``.
- Rotations about an axis are counter-clockwise when looking down the axis
  towards the origin (the right-hand rule), the same sense as Blender.

Numbering: iterate the image of +X over the six signed axes in the order
+X, -X, +Y, -Y, +Z, -Z; for each, iterate the image of +Y over the four
signed axes perpendicular to it, in the same order; +Z follows from the
right-hand rule. Index 0 is the identity.
"""

from __future__ import annotations

import math
from typing import Final

Vec = tuple[int, int, int]
"""An integer 3-vector."""

Mat = tuple[Vec, Vec, Vec]
"""A 3x3 integer matrix stored as three rows."""

AXES: Final[tuple[Vec, ...]] = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)
"""The six signed axes in face order: +X, -X, +Y, -Y, +Z, -Z.

The order matches the face numbering in ``frames.py`` and the box-port
numbering in ``.claude/rules/solver-core.md``, so ``AXES[face]`` is that
face's outward normal.
"""

AXIS_NAMES: Final[tuple[str, ...]] = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
"""Display names of :data:`AXES`, same order."""


def dot(a: Vec, b: Vec) -> int:
    """Dot product of two integer vectors."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    """Cross product ``a x b`` of two integer vectors (right-handed)."""
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def neg(a: Vec) -> Vec:
    """The vector ``-a``."""
    return (-a[0], -a[1], -a[2])


def add(a: Vec, b: Vec) -> Vec:
    """The vector ``a + b``."""
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(k: int, a: Vec) -> Vec:
    """The vector ``k * a`` for an integer ``k``."""
    return (k * a[0], k * a[1], k * a[2])


def matvec(m: Mat, v: Vec) -> Vec:
    """The product ``m v``: ``m`` acting on the column vector ``v``."""
    return (dot(m[0], v), dot(m[1], v), dot(m[2], v))


def matmul(a: Mat, b: Mat) -> Mat:
    """The matrix product ``a b`` (apply ``b`` first, then ``a``)."""
    columns = tuple(matvec(a, column(b, c)) for c in range(3))
    return from_columns(columns[0], columns[1], columns[2])


def transpose(m: Mat) -> Mat:
    """The transpose of ``m``, which for a rotation is also its inverse."""
    return from_columns(m[0], m[1], m[2])


def column(m: Mat, c: int) -> Vec:
    """Column ``c`` of ``m``: the image of basis vector ``c``."""
    return (m[0][c], m[1][c], m[2][c])


def from_columns(x: Vec, y: Vec, z: Vec) -> Mat:
    """Build a matrix from the images of the three basis vectors."""
    return (
        (x[0], y[0], z[0]),
        (x[1], y[1], z[1]),
        (x[2], y[2], z[2]),
    )


def determinant(m: Mat) -> int:
    """Determinant of ``m``: +1 for a rotation, -1 for a reflection."""
    return dot(m[0], cross(m[1], m[2]))


def _enumerate_rotations() -> tuple[Mat, ...]:
    """Build :data:`ROTATIONS` in the documented order.

    Choosing where +X goes (6 ways) and where +Y goes (4 ways perpendicular to
    that) fixes +Z by the right-hand rule, so exactly 24 matrices come out, all
    with determinant +1.
    """
    rotations: list[Mat] = []
    for x_image in AXES:
        for y_image in AXES:
            if dot(x_image, y_image) != 0:
                continue
            z_image = cross(x_image, y_image)
            rotations.append(from_columns(x_image, y_image, z_image))
    return tuple(rotations)


ROTATIONS: Final[tuple[Mat, ...]] = _enumerate_rotations()
"""The 24 proper rotations of the cube, index 0 the identity."""

IDENTITY: Final[int] = 0
"""Index of the identity rotation."""

_INDEX_OF: Final[dict[Mat, int]] = {m: i for i, m in enumerate(ROTATIONS)}


def index_of(m: Mat) -> int:
    """The index of rotation matrix ``m`` in :data:`ROTATIONS`.

    Raises ``ValueError`` if ``m`` is not one of the 24 cube rotations (for
    example a reflection or a non-integer matrix).
    """
    try:
        return _INDEX_OF[m]
    except KeyError:
        raise ValueError(f"not a cube rotation: {m!r}") from None


def apply(rotation: int, v: Vec) -> Vec:
    """Rotate vector ``v`` by rotation number ``rotation``."""
    return matvec(ROTATIONS[rotation], v)


def compose(a: int, b: int) -> int:
    """Index of the rotation "``b`` first, then ``a``" (``R_a R_b``)."""
    return index_of(matmul(ROTATIONS[a], ROTATIONS[b]))


def inverse(rotation: int) -> int:
    """Index of the rotation that undoes ``rotation``."""
    return index_of(transpose(ROTATIONS[rotation]))


def quarter_turn(axis: Vec) -> int:
    """Index of the +90 degree (counter-clockwise, right-hand rule) turn about ``axis``.

    ``axis`` must be one of :data:`AXES`. For the opposite axis the result is
    the inverse turn, so ``quarter_turn((0, 0, -1))`` turns clockwise about +Z.
    """
    if axis not in AXES:
        raise ValueError(f"not a signed axis: {axis!r}")
    # Rodrigues for 90 degrees about a unit axis n: v -> (n.v) n + n x v.
    columns = tuple(
        add(scale(dot(axis, e), axis), cross(axis, e)) for e in AXES[0::2]
    )
    return index_of(from_columns(columns[0], columns[1], columns[2]))


def rotations_about(axis: Vec) -> tuple[int, ...]:
    """The four rotations that keep ``axis`` fixed, in index order.

    This is the variant set for rotational freedom X, Y or Z. It always
    contains the identity.
    """
    if axis not in AXES:
        raise ValueError(f"not a signed axis: {axis!r}")
    return tuple(i for i in range(len(ROTATIONS)) if apply(i, axis) == axis)


def freedom_rotations(freedom: str) -> tuple[int, ...]:
    """The rotation indices a module may use under a rotational freedom.

    ``freedom`` is ``"NONE"`` (identity only), ``"X"``, ``"Y"`` or ``"Z"``
    (four turns about that axis) or ``"FULL"`` (all 24).
    """
    key = freedom.upper()
    if key == "NONE":
        return (IDENTITY,)
    if key == "FULL":
        return tuple(range(len(ROTATIONS)))
    axis = {"X": AXES[0], "Y": AXES[2], "Z": AXES[4]}.get(key)
    if axis is None:
        raise ValueError(f"unknown rotational freedom: {freedom!r}")
    return rotations_about(axis)


def quaternion(rotation: int) -> tuple[float, float, float, float]:
    """The unit quaternion ``(w, x, y, z)`` of a rotation, for ``wfc_rotation``.

    Blender's quaternion order is ``(w, x, y, z)``. The sign is fixed so that
    ``w >= 0``, and when ``w == 0`` the first non-zero component is positive,
    so the same rotation always gives the same floats.
    """
    m = ROTATIONS[rotation]
    trace = m[0][0] + m[1][1] + m[2][2]
    # Shepperd's method: pick the largest diagonal term so the divisor is
    # never small. Every branch is exact for these integer matrices.
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = (s / 4.0, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    elif m[0][0] >= m[1][1] and m[0][0] >= m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        q = ((m[2][1] - m[1][2]) / s, s / 4.0, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
    elif m[1][1] >= m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        q = ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4.0, (m[1][2] + m[2][1]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        q = ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4.0)
    for component in q:
        if component < 0:
            q = (-q[0], -q[1], -q[2], -q[3])
            break
        if component > 0:
            break
    return q


def rotate_by_quaternion(q: tuple[float, float, float, float], v: Vec) -> tuple[float, float, float]:
    """Rotate ``v`` by unit quaternion ``q = (w, x, y, z)``; used to test :func:`quaternion`.

    Uses ``v' = v + 2 w (u x v) + 2 u x (u x v)`` with ``u = (x, y, z)``.
    """
    w, ux, uy, uz = q
    u = (ux, uy, uz)
    t = _fcross(u, (float(v[0]), float(v[1]), float(v[2])))
    t2 = _fcross(u, t)
    return (
        v[0] + 2.0 * w * t[0] + 2.0 * t2[0],
        v[1] + 2.0 * w * t[1] + 2.0 * t2[1],
        v[2] + 2.0 * w * t[2] + 2.0 * t2[2],
    )


def _fcross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    """Cross product on float vectors."""
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
