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


def load(name: str) -> F.QuadMesh:
    doc = json.loads((FIXTURES / f"{name}.json").read_text())
    return F.QuadMesh(np.array(doc["vertices"], dtype=np.float64), np.array(doc["faces"], dtype=np.int64))


def k_hash(k: np.ndarray) -> str:
    return hashlib.sha256(k.astype("<i8").tobytes()).hexdigest()[:16]


@pytest.mark.parametrize(("name", "follow"), sorted(PINNED))
def test_pinned_k(name: str, follow: str) -> None:
    """Same frozen mesh, same ``k`` on every platform."""
    assert k_hash(F.compute(load(name), follow=follow).k) == PINNED[(name, follow)]


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
