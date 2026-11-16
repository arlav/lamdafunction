# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigid placement (phase 2, S5): quaternions, frames, scale."""

from __future__ import annotations

import numpy as np

from addon.compile.rotations import ROTATIONS, compose, quaternion
from addon.model.placement import orthonormal_frames, quaternions, rigid
from addon.model.sample_meshes import quad_sphere
from addon.model.shell import build_shell


def as_matrix(q: np.ndarray) -> np.ndarray:
    """Rotation matrix of a unit quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def test_cube_rotations_give_phase_1_quaternions_exactly() -> None:
    """All 24 cube rotations: the same floats as rotations.quaternion, which phase 1 writes."""
    got = quaternions(np.array(ROTATIONS, dtype=np.float64))
    want = np.array([quaternion(r) for r in range(24)])
    assert np.array_equal(got, want)


def test_quaternions_round_trip_random_rotations() -> None:
    rng = np.random.default_rng(3)
    q = rng.normal(size=(500, 4))
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    m = np.array([as_matrix(x) for x in q])
    back = np.array([as_matrix(x) for x in quaternions(m)])
    assert np.allclose(back, m, atol=1e-12)
    assert (quaternions(m)[:, 0] >= 0.0).all()


def test_box_frames_turned_by_a_cube_rotation_compose_like_indices() -> None:
    """A frame equal to rotation f, variant rotation r: world rotation is compose(f, r), exactly."""
    f_idx = np.repeat(np.arange(24), 24)
    r_idx = np.tile(np.arange(24), 24)
    m = np.array(ROTATIONS, dtype=np.float64)[f_idx]
    frames = np.concatenate([np.zeros((len(m), 1, 3)), np.transpose(m, (0, 2, 1))], axis=1)  # rows x, y, z = columns of m
    placed = rigid(frames, r_idx, 1.0)
    want = np.array([quaternion(compose(int(f), int(r))) for f, r in zip(f_idx, r_idx)])
    assert np.array_equal(placed.rotation, want)
    assert np.array_equal(placed.scale, np.ones((len(m), 3)))


def test_scale_follows_the_module_axes() -> None:
    """A 2 x 3 x 0.5 cell: unturned modules scale (2, 3, 0.5); a quarter turn about Z swaps x and y."""
    frames = np.array([[[0, 0, 0], [2.0, 0, 0], [0, 3.0, 0], [0, 0, 0.5]]] * 2)
    quarter_z = next(i for i, m in enumerate(ROTATIONS) if m == ((0, -1, 0), (1, 0, 0), (0, 0, 1)))
    placed = rigid(frames, np.array([0, quarter_z]), 1.0)
    assert np.allclose(placed.scale, [[2.0, 3.0, 0.5], [3.0, 2.0, 0.5]])
    # Open slots (rotation -1) place with no turn.
    assert np.allclose(rigid(frames, np.array([-1, -1]), 2.0).scale, [[1.0, 1.5, 0.25]] * 2)


def test_shell_frames_are_right_handed_and_outward() -> None:
    """On a quad sphere every orthonormal frame is a proper rotation whose z points away from the centre."""
    mesh = quad_sphere(4)
    shell = build_shell(mesh.vertices, [tuple(int(v) for v in f) for f in mesh.faces], thickness=0.2)
    m = orthonormal_frames(shell.frames)
    det = np.linalg.det(m)
    assert np.allclose(det, 1.0)
    outward = (m[:, :, 2] * shell.frames[:, 0]).sum(axis=1)
    assert (outward > 0.0).all()
    assert np.allclose(m[:, :, 0], shell.frames[:, 1] / np.linalg.norm(shell.frames[:, 1], axis=1, keepdims=True))


def test_field_marks_on_a_cube_and_a_flat_grid() -> None:
    """A one-layer cube (k = 0) has six seams and six arrows; two layers draw the inner layer only; a flat grid has no seams."""
    vertices = np.array([(x, y, z) for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)])
    polygons = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    from addon.model.placement import field_marks

    arrows, seams = field_marks(build_shell(vertices, polygons, thickness=0.1))
    assert arrows.shape == (6, 2, 3) and seams.shape == (6, 2, 3)
    arrows2, seams2 = field_marks(build_shell(vertices, polygons, thickness=0.1, layers=2))
    assert np.array_equal(arrows2[:, 0], arrows[:, 0]) and np.array_equal(seams2, seams)
    grid = np.array([(x, y, 0.0) for y in range(3) for x in range(3)])
    quads = [(y * 3 + x, y * 3 + x + 1, (y + 1) * 3 + x + 1, (y + 1) * 3 + x) for y in range(2) for x in range(2)]
    arrows3, seams3 = field_marks(build_shell(grid, quads))
    assert len(arrows3) == 4 and len(seams3) == 0
    assert np.allclose(arrows3[:, 1] - arrows3[:, 0], (0.35, 0.0, 0.0))
