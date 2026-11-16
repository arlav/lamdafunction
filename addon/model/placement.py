# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigid placement on a cell envelope: where each solved variant goes, turned and scaled.

``docs/PLAN.md``, "Surfaces (phase 2)", "Placing geometry", rigid. Plain
NumPy, no ``bpy``; ``addon/bl/results.py`` writes what this returns.

A cell's frame (:func:`addon.model.cells.frame_of`) is its centre and three
axes from each -face centre to the +face centre. They are neither unit length
nor quite perpendicular on a curved shell. Rigid placement uses:

- **position:** the centre;
- **frame rotation:** the axes made orthonormal, keeping x exactly and z
  closest to ``x × y`` (Gram-Schmidt: ``z = x × y``, then ``y = z × x``), so
  +Z stays outward;
- **scale:** the length of each axis divided by the kit's cell size, so a
  module authored in one cell stretches to fill this one along its own axes;
- **world rotation:** the frame rotation times the variant's rotation in the
  slot (the slot's frame first carries the module's own axes into the cell's,
  as ``Compiled.world_rotation`` does with rotation indices on a box).

Scale is applied in the module's rotated frame, which is the cell's frame, so
the module's x, y and z extents stretch by the cell's. Instance on Points
scales before it rotates, so the variant's own turn must be folded into the
scale: the scale handed to Blender is the cell's scale read along the
module's axes after the variant rotation, ``|R_v^T| · s``.

Gaps between neighbours on a curved surface are expected here; the
trilinear deformation of S6 closes them.

Every float operation is element by element (no ``@``, no ``np.linalg``), so
the result does not depend on a platform's BLAS. The rotation matrices of
:data:`addon.compile.rotations.ROTATIONS` hold only 0 and ±1, so composing
with them is exact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..compile.rotations import ROTATIONS
from .cells import CORNER_OF_PORT, CellEnvelope


@dataclass(frozen=True)
class Placement:
    """Per slot: centre, frame and world rotations as quaternions ``(w, x, y, z)``, and scale."""

    position: np.ndarray
    frame: np.ndarray
    rotation: np.ndarray
    scale: np.ndarray


def _normalise(x: np.ndarray) -> np.ndarray:
    length = np.sqrt(x[..., 0] * x[..., 0] + x[..., 1] * x[..., 1] + x[..., 2] * x[..., 2])
    return x / np.where(length > 0.0, length, 1.0)[..., None]


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.stack(
        (
            a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1],
            a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
            a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0],
        ),
        axis=-1,
    )


def orthonormal_frames(frames: np.ndarray) -> np.ndarray:
    """``n x 3 x 3`` rotation matrices whose *columns* are the cell's x, y and z, made orthonormal."""
    x = _normalise(frames[:, 1])
    z = _normalise(_cross(x, frames[:, 2]))
    y = _cross(z, x)
    return np.stack((x, y, z), axis=-1)


def _times_rotation(m: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """``m[i] · R[rotation[i]]`` for each slot, exactly: R's entries are 0 and ±1."""
    table = np.array(ROTATIONS, dtype=np.float64)[rotation]  # n x 3 x 3
    out = np.zeros_like(m)
    for i in range(3):
        for j in range(3):
            # Fixed order of the three products, so the sum is the same everywhere.
            out[:, i, j] = m[:, i, 0] * table[:, 0, j] + m[:, i, 1] * table[:, 1, j] + m[:, i, 2] * table[:, 2, j]
    return out


def quaternions(m: np.ndarray) -> np.ndarray:
    """Unit quaternions ``(w, x, y, z)`` of rotation matrices, ``n x 4``.

    Shepperd's method: take the largest of ``w, x, y, z`` from the diagonal, then the
    others from the off-diagonal sums, so no square root is of a tiny number. The sign is
    then fixed as :func:`addon.compile.rotations.quaternion` fixes it (``w >= 0``, else the
    first non-zero component positive), so a frame equal to a cube rotation gives exactly
    the floats phase 1 writes for it.
    """
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    trace = (m00 + m11) + m22
    cand = np.stack((trace, m00, m11, m22), axis=1)
    pick = np.argmax(cand, axis=1)
    q = np.zeros((len(m), 4))
    # w largest.
    s = np.sqrt(np.maximum(1.0 + trace, 0.0)) * 2.0
    sel = pick == 0
    q[sel, 0] = 0.25 * s[sel]
    q[sel, 1] = (m[sel, 2, 1] - m[sel, 1, 2]) / s[sel]
    q[sel, 2] = (m[sel, 0, 2] - m[sel, 2, 0]) / s[sel]
    q[sel, 3] = (m[sel, 1, 0] - m[sel, 0, 1]) / s[sel]
    # x largest.
    s = np.sqrt(np.maximum(((1.0 + m00) - m11) - m22, 0.0)) * 2.0
    sel = pick == 1
    q[sel, 0] = (m[sel, 2, 1] - m[sel, 1, 2]) / s[sel]
    q[sel, 1] = 0.25 * s[sel]
    q[sel, 2] = (m[sel, 0, 1] + m[sel, 1, 0]) / s[sel]
    q[sel, 3] = (m[sel, 0, 2] + m[sel, 2, 0]) / s[sel]
    # y largest.
    s = np.sqrt(np.maximum(((1.0 + m11) - m00) - m22, 0.0)) * 2.0
    sel = pick == 2
    q[sel, 0] = (m[sel, 0, 2] - m[sel, 2, 0]) / s[sel]
    q[sel, 1] = (m[sel, 0, 1] + m[sel, 1, 0]) / s[sel]
    q[sel, 2] = 0.25 * s[sel]
    q[sel, 3] = (m[sel, 1, 2] + m[sel, 2, 1]) / s[sel]
    # z largest.
    s = np.sqrt(np.maximum(((1.0 + m22) - m00) - m11, 0.0)) * 2.0
    sel = pick == 3
    q[sel, 0] = (m[sel, 1, 0] - m[sel, 0, 1]) / s[sel]
    q[sel, 1] = (m[sel, 0, 2] + m[sel, 2, 0]) / s[sel]
    q[sel, 2] = (m[sel, 1, 2] + m[sel, 2, 1]) / s[sel]
    q[sel, 3] = 0.25 * s[sel]
    # Sign: w >= 0, and when w == 0 the first non-zero component positive.
    first = np.zeros(len(q))
    for c in (3, 2, 1, 0):
        first = np.where(q[:, c] != 0.0, q[:, c], first)
    q = np.where((first < 0.0)[:, None], -q, q)
    return q + 0.0  # turns -0.0 into 0.0, so equal rotations hash alike


def rigid(frames: np.ndarray, rotation: np.ndarray, cell_size: float) -> Placement:
    """Placement for each slot from its frame and its solved rotation index (-1 where open: no turn)."""
    rotation = np.where(np.asarray(rotation) >= 0, rotation, 0).astype(np.int64)
    frame = orthonormal_frames(frames)
    world = _times_rotation(frame, rotation)
    extent = np.sqrt(frames[:, 1:, 0] * frames[:, 1:, 0] + frames[:, 1:, 1] * frames[:, 1:, 1] + frames[:, 1:, 2] * frames[:, 1:, 2])
    cell_scale = extent / cell_size  # n x 3, along the cell's x, y, z
    # Module axis j lands on cell axis i where R_v[i, j] = ±1, so it takes the cell's scale i.
    table = np.abs(np.array(ROTATIONS, dtype=np.float64)[rotation])
    scale = (
        table[:, 0, :] * cell_scale[:, 0:1] + table[:, 1, :] * cell_scale[:, 1:2] + table[:, 2, :] * cell_scale[:, 2:3]
    )
    return Placement(frames[:, 0].copy(), quaternions(frame), quaternions(world), scale)


def field_marks(cells: CellEnvelope, length: float = 0.35) -> tuple[np.ndarray, np.ndarray]:
    """Line segments for the field overlay, on the innermost layer: each cell's +X, and the seams.

    Arrows run from the centre of each cell's -Z face along its x axis, ``length`` of the
    cell's size. A seam is a side touch whose neighbour port is not the opposite port
    (the frames turn across it); it is drawn along the shared face's two lower corners,
    once per touch. Both are ``k x 2 x 3``.
    """
    bottom = {int(s): True for s, z in _layers(cells) if z == 0}
    arrows = []
    for c in sorted(bottom):
        base = cells.corners[c, :4].mean(axis=0)
        arrows.append((base, base + cells.frames[c, 1] * length))
    seams = []
    for touch in cells.neighbours:
        if touch.port >= 4 or touch.cell > touch.other or touch.cell not in bottom:
            continue
        if touch.other_port == touch.port ^ 1:
            continue
        face = [i for i in CORNER_OF_PORT[touch.port] if i < 4]
        seams.append((cells.corners[touch.cell, face[0]], cells.corners[touch.cell, face[1]]))
    return np.array(arrows).reshape(-1, 2, 3), np.array(seams).reshape(-1, 2, 3)


def _layers(cells: CellEnvelope) -> list[tuple[int, int]]:
    """``(cell, layer)`` per cell: the layer from a shell's ``("face", f, layer)`` source, else 0."""
    return [(c, int(s[2]) if len(s) > 2 and s[0] == "face" else 0) for c, s in enumerate(cells.sources)]
