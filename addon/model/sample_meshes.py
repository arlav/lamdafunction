# SPDX-License-Identifier: GPL-3.0-or-later
"""Quad meshes for tests and spikes, built with NumPy only.

Every mesh is oriented: corners run counter-clockwise seen from the side the
normal points to (outwards on closed surfaces, upwards on height fields, the
convex side on the vault), as Blender's loop order does.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .field import QuadMesh

Surface = Callable[[np.ndarray, np.ndarray], np.ndarray]
"""Maps parameter grids ``(s, t)`` to points, shape ``(..., 3)``."""


def grid(surface: Surface, ns: int, nt: int, *, closed_s: bool = False, closed_t: bool = False,
         s_range: tuple[float, float] = (0.0, 1.0), t_range: tuple[float, float] = (0.0, 1.0)) -> QuadMesh:
    """A quad grid over a parametric surface; closed directions wrap instead of repeating the seam."""
    cs, ct = ns if closed_s else ns + 1, nt if closed_t else nt + 1
    s = np.linspace(*s_range, ns + 1)[:cs]
    t = np.linspace(*t_range, nt + 1)[:ct]
    ss, tt = np.meshgrid(s, t, indexing="ij")
    vertices = surface(ss, tt).reshape(-1, 3)

    def vid(i: int, j: int) -> int:
        return (i % cs) * ct + (j % ct)

    faces = [(vid(i, j), vid(i + 1, j), vid(i + 1, j + 1), vid(i, j + 1)) for i in range(ns) for j in range(nt)]
    return QuadMesh(vertices, np.array(faces, dtype=np.int64))


def orient(mesh: QuadMesh, outward: Callable[[np.ndarray], np.ndarray]) -> QuadMesh:
    """Reverse every face whose normal points against ``outward(centroid)``."""
    corners = mesh.vertices[mesh.faces]
    n = np.cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 1])
    centroid = corners.mean(axis=1)
    bad = (n * outward(centroid)).sum(axis=1) < 0.0
    faces = mesh.faces.copy()
    faces[bad] = faces[bad][:, ::-1]
    return QuadMesh(mesh.vertices, faces)


def quad_sphere(n: int, radius: float = 1.0, bumps: float = 0.0) -> QuadMesh:
    """A cube with ``n x n`` quads per side projected onto a sphere; eight three-valent vertices.

    ``bumps`` adds a smooth radial ripple of that relative amplitude, for a freeform closed surface.
    """
    index: dict[tuple[int, int, int], int] = {}
    points: list[tuple[int, int, int]] = []

    def vid(p: tuple[int, int, int]) -> int:
        if p not in index:
            index[p] = len(points)
            points.append(p)
        return index[p]

    faces = []
    for axis in range(3):
        for side in (0, n):
            a, b = [x for x in range(3) if x != axis]
            for i in range(n):
                for j in range(n):
                    quad = []
                    for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        p = [0, 0, 0]
                        p[axis], p[a], p[b] = side, i + di, j + dj
                        quad.append(vid((p[0], p[1], p[2])))
                    faces.append(quad)
    lattice = np.array(points, dtype=np.float64) / n * 2.0 - 1.0
    direction = lattice / np.linalg.norm(lattice, axis=1, keepdims=True)
    r = radius * (1.0 + bumps * np.sin(3.0 * direction[:, 0]) * np.sin(2.0 * direction[:, 1] + 1.0) * np.cos(direction[:, 2] * 2.5))
    mesh = QuadMesh(direction * r[:, None], np.array(faces, dtype=np.int64))
    return orient(mesh, lambda c: c)


def barrel_vault(n_arch: int = 24, n_length: int = 32, radius: float = 3.0, length: float = 8.0, shear: float = 0.0) -> QuadMesh:
    """Half a cylinder: arches across ``s``, straight generators along ``t``.

    ``shear`` tilts the grid lines against the curvature lines (0 keeps them aligned).
    """

    def surface(s: np.ndarray, t: np.ndarray) -> np.ndarray:
        theta = s * np.pi + shear * t
        return np.stack((t * length, radius * np.cos(theta), radius * np.sin(theta)), axis=-1)

    mesh = grid(surface, n_arch, n_length)
    return orient(mesh, lambda c: np.stack((np.zeros(len(c)), c[:, 1], c[:, 2]), axis=1))


def saddle(n: int = 30, size: float = 3.0, depth: float = 0.25) -> QuadMesh:
    """``z = depth (x^2 - y^2)``: curvatures of opposite sign, equal and opposite at the centre."""

    def surface(s: np.ndarray, t: np.ndarray) -> np.ndarray:
        x, y = (s * 2.0 - 1.0) * size, (t * 2.0 - 1.0) * size
        return np.stack((x, y, depth * (x * x - y * y)), axis=-1)

    return orient(grid(surface, n, n), lambda c: np.tile([0.0, 0.0, 1.0], (len(c), 1)))


def torus(n_ring: int = 48, n_tube: int = 24, big: float = 3.0, small: float = 1.0) -> QuadMesh:
    """A torus: closed, every vertex four-valent, principal directions along the grid lines."""

    def surface(s: np.ndarray, t: np.ndarray) -> np.ndarray:
        a, b = s * 2.0 * np.pi, t * 2.0 * np.pi
        return np.stack(((big + small * np.cos(b)) * np.cos(a), (big + small * np.cos(b)) * np.sin(a), small * np.sin(b)), axis=-1)

    mesh = grid(surface, n_ring, n_tube, closed_s=True, closed_t=True)

    def outward(c: np.ndarray) -> np.ndarray:
        ring = c.copy()
        ring[:, 2] = 0.0
        ring = ring / np.linalg.norm(ring, axis=1, keepdims=True) * big
        return c - ring

    return orient(mesh, outward)


def bumpy_plane(n: int = 30, size: float = 4.0) -> QuadMesh:
    """A height field with three smooth bumps and a dip: a freeform open surface."""

    def surface(s: np.ndarray, t: np.ndarray) -> np.ndarray:
        x, y = (s * 2.0 - 1.0) * size, (t * 2.0 - 1.0) * size
        z = (1.2 * np.exp(-((x - 1.5) ** 2 + (y - 1.0) ** 2) / 2.0)
             + 0.8 * np.exp(-((x + 2.0) ** 2 + (y + 1.5) ** 2) / 1.5)
             - 0.9 * np.exp(-((x + 0.5) ** 2 + (y - 2.5) ** 2) / 1.0)
             + 0.6 * np.exp(-((x - 2.0) ** 2 + (y + 2.5) ** 2) / 3.0))
        return np.stack((x, y, z), axis=-1)

    return orient(grid(surface, n, n), lambda c: np.tile([0.0, 0.0, 1.0], (len(c), 1)))
