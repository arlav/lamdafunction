# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike 7: curvature, direction field and seams on quad meshes.

Run from the repository root: ``.venv/bin/python tests/python/spike_07_field.py [--freeze]``.
Not collected by pytest (no ``test_`` prefix). ``--freeze`` writes the meshes
to ``tests/fixtures/field/`` for ``addon/model/test_field.py``.

Per mesh it reports: faces, irregular interior vertices (valence not 4),
alignment of each face's +X with its direction of largest absolute curvature
(on faces whose curvature has a clear preference), seams by quarter turns, the
agreement between combinatorial and geometric seam turns, the field's change in
the last iteration, the time, and a hash of ``k``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from addon.model import field as fieldmod  # noqa: E402
from addon.model import sample_meshes as sm  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "field"

MESHES = {
    "quad_sphere_L1": lambda: sm.quad_sphere(2),
    "quad_sphere_L2": lambda: sm.quad_sphere(4),
    "quad_sphere_L3": lambda: sm.quad_sphere(8),
    "quad_sphere_L4": lambda: sm.quad_sphere(16),
    "quad_sphere_10k": lambda: sm.quad_sphere(41),
    "bumpy_sphere": lambda: sm.quad_sphere(16, bumps=0.15),
    "vault": lambda: sm.barrel_vault(),
    "vault_sheared": lambda: sm.barrel_vault(shear=0.6),
    "saddle": lambda: sm.saddle(),
    "torus": lambda: sm.torus(),
    "bumpy_plane": lambda: sm.bumpy_plane(),
}


def irregular_vertices(mesh: fieldmod.QuadMesh, topo: fieldmod.Topology) -> int:
    """Interior vertices whose valence is not 4 (boundary vertices are not counted)."""
    return int(irregular_vertex_mask(mesh, topo).sum())


def irregular_vertex_mask(mesh: fieldmod.QuadMesh, topo: fieldmod.Topology) -> np.ndarray:
    """Per vertex: interior and valence not 4."""
    valence = np.zeros(len(mesh.vertices), dtype=np.int64)
    boundary = np.zeros(len(mesh.vertices), dtype=bool)
    for f in range(len(mesh.faces)):
        for s in range(4):
            valence[mesh.faces[f, s]] += 1
            if topo.neighbour[f, s] < 0:
                boundary[mesh.faces[f, s]] = True
                boundary[mesh.faces[f, (s + 1) % 4]] = True
    return (valence != 4) & ~boundary


def alignment(fld: fieldmod.Field) -> tuple[int, float, float]:
    """Faces with a clear curvature preference; share whose +X is within 15 degrees of it; worst angle.

    The direction of largest |curvature| is ``line_target`` (a line, 2-theta space). +X for ``k``
    is arm ``k``; as a line that is (+1, 0) for even ``k`` and (-1, 0) for odd.
    """
    a, c = fld.shape[:, 0], fld.shape[:, 2]
    size = np.abs(a + c) * 0.5 + np.sqrt(fld.line_target.norm2() * 0 + (a - c) ** 2 + 4 * fld.shape[:, 1] ** 2) * 0.5
    # Clear preference: |k1| and |k2| differ by more than 20 % of the larger one.
    clear = fld.confidence > 0.2 * np.where(size > 0, size, np.inf)
    if not clear.any():
        return 0, float("nan"), float("nan")
    target = fld.line_target.unit()
    arm_line = np.where(fld.k % 2 == 0, 1.0, -1.0)
    cos2 = np.clip(target.re * arm_line, -1.0, 1.0)  # cos of twice the angle
    angle = np.degrees(np.arccos(cos2) / 2.0)[clear]
    return int(clear.sum()), float((angle <= 15.0).mean()), float(angle.max())


def run(name: str, mesh: fieldmod.QuadMesh, iterations: int = 200, follow: str = "mesh") -> dict:
    topo = fieldmod.topology(mesh)
    start = time.perf_counter()
    fld = fieldmod.compute(mesh, topo=topo, iterations=iterations, follow=follow)
    elapsed = time.perf_counter() - start
    before = fieldmod.compute(mesh, topo=topo, iterations=iterations - 1, follow=follow).line
    change = float(np.sqrt((fld.line.re - before.re) ** 2 + (fld.line.im - before.im) ** 2).max())
    turns = fieldmod.seam_turns(fld.k, topo)
    geometric = fieldmod.seam_turns_geometric(fld, topo)
    valid = turns >= 0
    agree = int((turns[valid] == geometric[valid]).sum())
    # Each edge appears from both sides: a quarter-turn seam as 1 on one side and 3 on the other,
    # a half-turn seam as 2 on both.
    seams = {"quarter": int((turns == 1).sum()), "half": int((turns == 2).sum() // 2)}
    # Where the two measures disagree: is every such edge next to an irregular vertex?
    irregular = irregular_vertex_mask(mesh, topo)
    off = np.argwhere(valid & (turns != geometric))
    near = sum(1 for f, s in off if irregular[mesh.faces[f, s]] or irregular[mesh.faces[f, (s + 1) % 4]])
    n_clear, share, worst = alignment(fld)
    again = fieldmod.compute(mesh, topo=topo, iterations=iterations, follow=follow).k
    return {
        "mesh": name,
        "follow": follow,
        "faces": len(mesh.faces),
        "irregular": irregular_vertices(mesh, topo),
        "clear": n_clear,
        "aligned_15": share,
        "worst_deg": worst,
        "seam_quarter": seams["quarter"],
        "seam_half": seams["half"],
        "agree": f"{agree}/{int(valid.sum())}",
        "off_near_irregular": f"{near}/{len(off)}",
        "last_change": change,
        "seconds": elapsed,
        "repeatable": bool((again == fld.k).all()),
        "k_hash": hashlib.sha256(fld.k.astype("<i8").tobytes()).hexdigest()[:16],
    }


def freeze(name: str, mesh: fieldmod.QuadMesh) -> None:
    """Exact coordinates (``repr`` round-trips a float64) so every platform starts from the same bits."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    doc = {"vertices": [[float(x) for x in v] for v in mesh.vertices], "faces": mesh.faces.tolist()}
    (FIXTURES / f"{name}.json").write_text(json.dumps(doc, separators=(",", ":")))


def main() -> int:
    rows = []
    for name, build in MESHES.items():
        mesh = build()
        if "--freeze" in sys.argv and name in ("quad_sphere_L3", "vault", "vault_sheared", "saddle", "bumpy_sphere"):
            freeze(name, mesh)
        rows.append(run(name, mesh, follow="mesh"))
        rows.append(run(name, mesh, follow="field"))
    keys = list(rows[0])
    print(" | ".join(keys))
    for row in rows:
        print(" | ".join(f"{row[k]:.3g}" if isinstance(row[k], float) else str(row[k]) for k in keys))
    ok = all(r["repeatable"] for r in rows) and all(r["agree"].split("/")[0] == r["agree"].split("/")[1] for r in rows)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
