# SPDX-License-Identifier: GPL-3.0-or-later
"""Direction field: invariants, and ``k`` pinned on frozen meshes so every platform must agree.

The meshes in ``tests/fixtures/field/`` were written by ``tests/python/spike_07_field.py
--freeze`` with exact float64 coordinates: generating them here would call NumPy's ``sin``
and ``cos``, which may differ in the last bit between platforms. The pinned hashes were
computed on macOS arm64; CI checks them on Linux x64.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from . import field as F
from . import sample_meshes as sm

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "field"

PINNED = {
    ("quad_sphere_L3", "mesh"): "be9f6c7786f967b6",
    ("quad_sphere_L3", "field"): "8a50f86c3fe3728a",
    ("bumpy_sphere", "mesh"): "41739994a420a0d6",
    ("bumpy_sphere", "field"): "e858d11d28fa16af",
    ("vault", "mesh"): "fd9243e1ba57263e",
    ("vault_sheared", "mesh"): "fd9243e1ba57263e",
    ("saddle", "mesh"): "ff31eed352a6dc52",
    ("saddle", "field"): "fcebc2d58620c587",
}


GUIDE = (0.0, -0.6, -0.8)
"""A guide that is not along any mesh direction; on the bumpy sphere it turns the piece by a half."""

PINNED_GUIDED = {
    ("bumpy_sphere", "mesh"): "9b388d14ed8e4491",
    ("saddle", "field"): "d6be095de0b1c24d",
    ("vault", "mesh"): "fd9243e1ba57263e",
}


def load(name: str) -> F.QuadMesh:
    doc = json.loads((FIXTURES / f"{name}.json").read_text())
    return F.QuadMesh(np.array(doc["vertices"], dtype=np.float64), np.array(doc["faces"], dtype=np.int64))


def k_hash(k: np.ndarray) -> str:
    return hashlib.sha256(k.astype("<i8").tobytes()).hexdigest()[:16]


@pytest.mark.parametrize(("name", "follow"), sorted(PINNED))
def test_pinned_k(name: str, follow: str) -> None:
    """Same frozen mesh, same ``k`` on every platform."""
    assert k_hash(F.compute(load(name), follow=follow).k) == PINNED[(name, follow)]


@pytest.mark.parametrize(("name", "follow"), sorted(PINNED_GUIDED))
def test_pinned_k_with_guide(name: str, follow: str) -> None:
    """The guide's family and sign votes are as deterministic as the rest."""
    fld = F.compute(load(name), follow=follow, guide=GUIDE, guide_weight=1.0)
    assert k_hash(fld.k) == PINNED_GUIDED[(name, follow)]


def test_seam_turns_antisymmetric() -> None:
    """Across one edge, the turn seen from one side is minus the turn seen from the other."""
    mesh = sm.quad_sphere(6)
    topo = F.topology(mesh)
    turns = F.seam_turns(F.compute(mesh, topo=topo).k, topo)
    for f in range(len(mesh.faces)):
        for s in range(4):
            g, t = int(topo.neighbour[f, s]), int(topo.neighbour_side[f, s])
            assert (turns[f, s] + turns[g, t]) % 4 == 0


@pytest.mark.parametrize("build", [sm.barrel_vault, lambda: sm.barrel_vault(shear=0.6), sm.torus, sm.saddle, sm.bumpy_plane])
def test_mesh_mode_regular_meshes_have_no_seams(build) -> None:
    """With every vertex four-valent, combing along the edge flow leaves no seam at all."""
    mesh = build()
    topo = F.topology(mesh)
    turns = F.seam_turns(F.compute(mesh, topo=topo).k, topo)
    assert set(turns[turns >= 0].tolist()) == {0}


def test_vault_ribs_follow_the_arches() -> None:
    """+X lies in the arch plane (no component along the vault's length) on every face."""
    fld = F.compute(sm.barrel_vault())
    axis = np.where((fld.k % 2 == 0)[:, None], fld.u, fld.v)
    assert np.abs(axis[:, 0]).max() < 1e-9


def test_quad_sphere_seams_only_from_the_corners() -> None:
    """A quad sphere is regular except at its eight corners; seams stay a small share of the edges."""
    mesh = sm.quad_sphere(16)
    topo = F.topology(mesh)
    turns = F.seam_turns(F.compute(mesh, topo=topo).k, topo)
    edges = int((turns >= 0).sum()) // 2
    seams = int((turns > 0).sum()) // 2
    assert 0 < seams < 0.05 * edges


def test_topology_flags_non_manifold_and_flipped() -> None:
    """Three faces on one edge: non-manifold, left unconnected. Two faces running the edge the same way: flipped."""
    vertices = np.zeros((8, 3))
    fan = F.QuadMesh(vertices, np.array([[0, 1, 2, 3], [1, 0, 4, 5], [0, 1, 6, 7]]))
    assert F.topology(fan).non_manifold == ((0, 1),)
    flipped = F.QuadMesh(vertices, np.array([[0, 1, 2, 3], [0, 1, 4, 5]]))
    assert F.topology(flipped).flipped == ((0, 1),)


def plus_x(fld: F.Field) -> np.ndarray:
    """Each face's +X in world space: arm ``k`` of its basis (u, v, -u, -v)."""
    arms = np.stack((fld.u, fld.v, -fld.u, -fld.v), axis=1)
    return arms[np.arange(len(fld.k)), fld.k]


def flat(n: int = 6) -> F.QuadMesh:
    """An ``n x n`` grid in the XY plane, quads counter-clockwise from +Z."""
    vertices = np.array([(x, y, 0.0) for y in range(n + 1) for x in range(n + 1)], dtype=np.float64)
    faces = [(y * (n + 1) + x, y * (n + 1) + x + 1, (y + 1) * (n + 1) + x + 1, (y + 1) * (n + 1) + x) for y in range(n) for x in range(n)]
    return F.QuadMesh(vertices, np.array(faces, dtype=np.int64))


def test_cylinder_runs_around() -> None:
    """On a tube the largest curvature is around it, so +X has no component along its axis."""
    mesh = sm.cylinder()
    topo = F.topology(mesh)
    fld = F.compute(mesh, topo=topo)
    assert np.abs(plus_x(fld)[:, 2]).max() < 1e-9
    turns = F.seam_turns(fld.k, topo)
    assert set(turns[turns >= 0].tolist()) == {0}


def test_vault_guide_outvotes_curvature() -> None:
    """A guide along the vault's length with weight 1 beats the curvature's 1/r and turns +X along it."""
    mesh = sm.barrel_vault()
    fld = F.compute(mesh, guide=(1.0, 0.0, 0.0), guide_weight=1.0)
    assert np.all(plus_x(fld)[:, 0] > 1.0 - 1e-9)
    weak = F.compute(mesh, guide=(1.0, 0.0, 0.0), guide_weight=0.01)
    assert np.abs(plus_x(weak)[:, 0]).max() < 1e-9


@pytest.mark.parametrize("axis", [(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)])
@pytest.mark.parametrize("follow", ["mesh", "field"])
def test_flat_grid_follows_guide_with_no_seams(axis: tuple[float, float, float], follow: str) -> None:
    """A world-axis guide on a flat grid: no seams, and +X points along the guide, sign included."""
    mesh = flat()
    topo = F.topology(mesh)
    fld = F.compute(mesh, topo=topo, guide=axis, guide_weight=1.0, follow=follow)
    assert F.seam_edges(mesh, fld.k, topo) == []
    assert np.all(plus_x(fld) @ np.array(axis) > 1.0 - 1e-9)


def test_per_face_guide_equals_world_axis() -> None:
    """The same direction given per face or as one axis gives the same ``k``."""
    mesh = sm.quad_sphere(6)
    axis = (0.3, -0.2, 0.9)
    rows = np.tile(axis, (len(mesh.faces), 1))
    assert np.array_equal(F.compute(mesh, guide=axis, guide_weight=1.0).k, F.compute(mesh, guide=rows, guide_weight=1.0).k)


def test_uv_guide_is_the_uv_gradient() -> None:
    """UV = (y, x) on a flat grid: U grows along world Y, V along world X; a triangle gets no guide."""
    mesh = flat(3)
    polygons = [list(map(int, f)) for f in mesh.faces] + [[0, 1, 5]]
    uv = [[(float(mesh.vertices[v][1]), float(mesh.vertices[v][0])) for v in p] for p in polygons]
    u_dir = F.uv_guide(mesh.vertices, polygons, uv, axis=0)
    v_dir = F.uv_guide(mesh.vertices, polygons, uv, axis=1)
    assert np.allclose(u_dir[:-1], (0.0, 1.0, 0.0)) and np.allclose(v_dir[:-1], (1.0, 0.0, 0.0))
    assert np.array_equal(u_dir[-1], np.zeros(3))
    fld = F.compute(mesh, guide=u_dir[:-1], guide_weight=1.0)
    assert np.all(plus_x(fld)[:, 1] > 1.0 - 1e-9)


def test_quad_sphere_has_eight_singularities() -> None:
    """Eight three-valent corners, the field defined everywhere, and every seam listed once."""
    mesh = sm.quad_sphere(8)
    topo = F.topology(mesh)
    fld = F.compute(mesh, topo=topo)
    assert [n for _, n in F.irregular_vertices(mesh, topo)] == [3] * 8
    assert set(np.unique(fld.k).tolist()) <= {0, 1, 2, 3}
    seams = F.seam_edges(mesh, fld.k, topo)
    assert len(seams) == int((F.seam_turns(fld.k, topo) > 0).sum()) // 2
    assert len(set((a, b) for a, b, _ in seams)) == len(seams)
    assert {t for _, _, t in seams} <= {1, 2}


def test_open_grid_has_no_singularities() -> None:
    """Boundary vertices with fewer than four faces are not singularities."""
    mesh = flat(4)
    assert F.irregular_vertices(mesh, F.topology(mesh)) == []


def test_unknown_follow_is_rejected() -> None:
    with pytest.raises(ValueError):
        F.compute(flat(2), follow="curvature")
