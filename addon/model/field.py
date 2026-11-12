# SPDX-License-Identifier: GPL-3.0-or-later
"""Direction field on a quad mesh: which side of each quad is the slot's +X.

Spike 7 (``docs/spikes/07-direction-field.md``) wrote this; iteration S4 of
phase 2 finishes it. Plain NumPy, no ``bpy``.

A slot's frame on a surface is the quad's own corner labelling (corner ``c0``
at unit ``(0, 0)``, ``c1`` at ``(1, 0)``, ``c2`` at ``(1, 1)``, ``c3`` at
``(0, 1)``) turned by ``k`` quarter turns. This module chooses ``k`` per face:

1. **Curvature.** A shape operator per face from the corner normals along its
   four sides, least squares, in the face's tangent basis ``(u, v)``.
2. **Line field.** A smooth field of lines (directions up to a half turn),
   pulled towards the direction of largest absolute curvature and optionally
   towards a guide axis, smoothed across edges after carrying each
   neighbour's basis over the fold (this is where neighbouring normals enter).
3. **Family.** Each face takes the pair of opposite quad sides closest to its
   line: ``k`` mod 2.
4. **Combing.** Breadth-first from a seed, each face takes the sign
   (``k`` or ``k + 2``) closest to its parent's +X carried across the edge.

Angles are never computed: a direction at angle ``a`` in a face's basis is the
complex number ``e^{ia}``, a line is ``e^{2ia}`` (``a`` and ``a + pi`` agree),
and turning by the angle between two bases is a complex product. Complex
numbers are kept as pairs of real arrays (:class:`C`) and multiplied by hand,
one NumPy operation per step: NumPy's own complex multiply is compiled C that
a compiler may fuse into multiply-adds on one platform and not another. Only
``+``, ``-``, ``*``, ``/`` and ``sqrt`` touch floats, element by element, sums
run in a fixed order, and nothing goes through BLAS or LAPACK, so the result
is the same on every platform (``.claude/rules/solver-core.md``, "Floating
point").

Quad side ``s`` runs from corner ``s`` to corner ``s + 1``. With ``k = 0`` the
sides are ports -Y (side 0), +X (side 1), +Y (side 2) and -X (side 3); turning
by ``k`` moves +X to side ``1 + k``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

SIDE_PORT: tuple[int, ...] = (3, 0, 2, 1)
"""Port of quad side ``s`` at ``k = 0``: -Y, +X, +Y, -X (box port numbering)."""

TIE: float = 1e-9
"""Two candidate choices closer than this (relative) count as a tie; the lower ``k`` wins."""


@dataclass(frozen=True)
class QuadMesh:
    """Vertices (``n x 3``) and quads (``m x 4``, corners in loop order: counter-clockwise seen from the normal)."""

    vertices: np.ndarray
    faces: np.ndarray


@dataclass(frozen=True)
class Topology:
    """Face neighbours across each side: ``neighbour[f, s]`` (or -1) and the neighbour's side ``neighbour_side[f, s]``."""

    neighbour: np.ndarray
    neighbour_side: np.ndarray
    non_manifold: tuple[tuple[int, int], ...]
    flipped: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Field:
    """Everything the field computes, per face."""

    normal: np.ndarray
    u: np.ndarray
    v: np.ndarray
    shape: np.ndarray
    confidence: np.ndarray
    line_target: C
    line: C
    k: np.ndarray
    transport: tuple[C, C, C, C]


def _normalise(x: np.ndarray) -> np.ndarray:
    length = np.sqrt(x[..., 0] * x[..., 0] + x[..., 1] * x[..., 1] + x[..., 2] * x[..., 2])
    return x / np.where(length > 0.0, length, 1.0)[..., None]


def _dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a[..., 0] * b[..., 0] + a[..., 1] * b[..., 1] + a[..., 2] * b[..., 2]


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.stack(
        (
            a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1],
            a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
            a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0],
        ),
        axis=-1,
    )


@dataclass(frozen=True)
class C:
    """Complex numbers as two real arrays, multiplied one rounding at a time."""

    re: np.ndarray
    im: np.ndarray

    def __add__(self, o: C) -> C:
        return C(self.re + o.re, self.im + o.im)

    def __mul__(self, o: C) -> C:
        rr = self.re * o.re
        ii = self.im * o.im
        ri = self.re * o.im
        ir = self.im * o.re
        return C(rr - ii, ri + ir)

    def scale(self, x: np.ndarray | float) -> C:
        return C(self.re * x, self.im * x)

    def conj(self) -> C:
        return C(self.re, -self.im)

    def norm2(self) -> np.ndarray:
        return self.re * self.re + self.im * self.im

    def unit(self) -> C:
        """``z / |z|``, and 0 where ``z`` is 0."""
        length = np.sqrt(self.norm2())
        safe = np.where(length > 0.0, length, 1.0)
        return C(np.where(length > 0.0, self.re / safe, 0.0), np.where(length > 0.0, self.im / safe, 0.0))

    def where(self, mask: np.ndarray, other: C) -> C:
        return C(np.where(mask, self.re, other.re), np.where(mask, self.im, other.im))

    def at(self, index: np.ndarray | int) -> C:
        return C(self.re[index], self.im[index])


def topology(mesh: QuadMesh) -> Topology:
    """Neighbours across shared edges. Edges with three or more faces are non-manifold and left unconnected."""
    faces = mesh.faces
    m = len(faces)
    edges: dict[tuple[int, int], list[tuple[int, int, bool]]] = {}
    for f in range(m):
        for s in range(4):
            a, b = int(faces[f, s]), int(faces[f, (s + 1) % 4])
            key = (a, b) if a < b else (b, a)
            edges.setdefault(key, []).append((f, s, a < b))
    neighbour = np.full((m, 4), -1, dtype=np.int64)
    neighbour_side = np.full((m, 4), -1, dtype=np.int64)
    non_manifold: list[tuple[int, int]] = []
    flipped: list[tuple[int, int]] = []
    for key in sorted(edges):
        users = edges[key]
        if len(users) > 2:
            non_manifold.append(key)
            continue
        if len(users) == 2:
            (f, s, fwd_f), (g, t, fwd_g) = users
            if fwd_f == fwd_g:
                flipped.append(key)
            neighbour[f, s], neighbour_side[f, s] = g, t
            neighbour[g, t], neighbour_side[g, t] = f, s
    return Topology(neighbour, neighbour_side, tuple(non_manifold), tuple(flipped))


def vertex_normals(mesh: QuadMesh, face_normal_area: np.ndarray) -> np.ndarray:
    """Area-weighted average of the adjacent face normals, accumulated in face order."""
    acc = np.zeros_like(mesh.vertices, dtype=np.float64)
    for c in range(4):
        # np.add.at is an unbuffered loop in index order, so the sum order is fixed.
        np.add.at(acc, mesh.faces[:, c], face_normal_area)
    return _normalise(acc)


def compute(
    mesh: QuadMesh,
    *,
    curvature_weight: float = 1.0,
    guide: tuple[float, float, float] | None = None,
    guide_weight: float = 0.0,
    smooth_weight: float = 1.0,
    iterations: int = 200,
    follow: str = "mesh",
    topo: Topology | None = None,
) -> Field:
    """Curvature, line field and ``k`` per face.

    ``follow="mesh"`` combs the frames along the mesh's own edge flow, so seams appear only
    where irregular vertices force them, and then turns each connected piece as a whole to
    the family the curvature (and guide) prefer. ``follow="field"`` snaps every face to its
    own smoothed line, which follows curvature more closely and adds a seam wherever the
    curvature direction crosses a quad diagonal. Spike 7 compares the two.
    """
    topo = topo or topology(mesh)
    vertices = mesh.vertices.astype(np.float64)
    corners = vertices[mesh.faces]  # m x 4 x 3
    m = len(mesh.faces)

    # Face normal from the diagonals (Newell's formula for a quad); its length is twice the area.
    area_normal = _cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 1]) * 0.5
    normal = _normalise(area_normal)
    vnormal = vertex_normals(mesh, area_normal)[mesh.faces]  # m x 4 x 3

    # Tangent basis: u from the -X side midpoint to the +X side midpoint, projected; v = n x u.
    x_axis = (corners[:, 1] + corners[:, 2]) - (corners[:, 3] + corners[:, 0])
    u = _normalise(x_axis - _dot(x_axis, normal)[:, None] * normal)
    v = _cross(normal, u)

    # Shape operator S = [[a, b], [b, c]] by least squares over the four sides:
    # S (e.u, e.v) = (dn.u, dn.v) for each side's edge e and normal change dn.
    ata = np.zeros((m, 3, 3))
    aty = np.zeros((m, 3))
    for s in range(4):
        e = corners[:, (s + 1) % 4] - corners[:, s]
        dn = vnormal[:, (s + 1) % 4] - vnormal[:, s]
        eu, ev, du, dv = _dot(e, u), _dot(e, v), _dot(dn, u), _dot(dn, v)
        # Rows [eu, ev, 0] -> du and [0, eu, ev] -> dv.
        ata[:, 0, 0] += eu * eu
        ata[:, 0, 1] += eu * ev
        ata[:, 1, 1] += ev * ev + eu * eu
        ata[:, 1, 2] += eu * ev
        ata[:, 2, 2] += ev * ev
        aty[:, 0] += eu * du
        aty[:, 1] += ev * du + eu * dv
        aty[:, 2] += ev * dv
    ata[:, 1, 0], ata[:, 2, 1], ata[:, 2, 0] = ata[:, 0, 1], ata[:, 1, 2], ata[:, 0, 2]
    shape = _solve3(ata, aty)
    a, b, c = shape[:, 0], shape[:, 1], shape[:, 2]

    # Direction of largest |curvature| as a line e^{2i theta}. Eigenvalues m +- d with
    # m = (a + c) / 2, d = |w| / 2, w = (a - c) + 2bi; w points (in 2-theta space) along the
    # m + d eigenvector. Largest |lambda| is that branch when m >= 0, the other when m < 0.
    # Confidence | |l1| - |l2| | = 2 min(|m|, d): zero on spheres, planes and where k1 = -k2.
    w = C(a - c, 2.0 * b)
    mean = (a + c) * 0.5
    half = np.sqrt(w.norm2()) * 0.5
    confidence = 2.0 * np.minimum(np.abs(mean), half)
    flip = np.where(mean >= 0.0, 1.0, -1.0)
    line_target = w.scale(flip).unit().scale(confidence * curvature_weight)
    if guide is not None and guide_weight > 0.0:
        gx, gy, gz = (float(x) for x in guide)
        g = C(u[:, 0] * gx + u[:, 1] * gy + u[:, 2] * gz, v[:, 0] * gx + v[:, 1] * gy + v[:, 2] * gz)
        line_target = line_target + (g * g).unit().scale(g.norm2() * guide_weight)

    # Transport across each side: the neighbour's u carried over the shared edge into this
    # face's basis, as a unit complex r; a line in the neighbour's basis times r^2 is the same
    # line in this face's basis.
    zero = np.zeros(m)
    transport: list[C] = []
    for s in range(4):
        g = topo.neighbour[:, s]
        ok = g >= 0
        gi = np.where(ok, g, 0)
        e = corners[:, (s + 1) % 4] - corners[:, s]
        e_f = _normalise(e - _dot(e, normal)[:, None] * normal)
        e_g = _normalise(e - _dot(e, normal[gi])[:, None] * normal[gi])
        alpha = _dot(u[gi], e_g)
        beta = _dot(u[gi], _cross(normal[gi], e_g))
        carried = alpha[:, None] * e_f + beta[:, None] * _cross(normal, e_f)
        r = C(_dot(carried, u), _dot(carried, v)).unit()
        transport.append(r.where(ok, C(zero, zero)))

    # Smooth the line field: damped Jacobi iterations, neighbours added in side order. Plain
    # Jacobi flips sign every step on a quad mesh (its faces split into two alternating sets),
    # so each face keeps its own value with the weight of all four neighbours.
    has_target = line_target.norm2() > 0.0
    line = line_target.unit().where(has_target, C(np.ones(m), zero))
    rot2 = [r * r for r in transport]
    # Mesh mode only votes with line_target, so the smoothing would be wasted work.
    for _ in range(iterations if follow == "field" else 0):
        acc = line_target + line.scale(4.0 * smooth_weight)
        for s in range(4):
            g = topo.neighbour[:, s]
            ok = g >= 0
            term = (rot2[s] * line.at(np.where(ok, g, 0))).scale(smooth_weight)
            acc = acc + term.where(ok, C(zero, zero))
        line = acc.unit().where(acc.norm2() > 0.0, line)

    if follow == "mesh":
        k = _comb_mesh(line_target, topo, confidence)
    elif follow == "field":
        k = _comb(line, transport, topo, confidence)
    else:
        raise ValueError(f"follow must be 'mesh' or 'field', not {follow!r}")
    return Field(normal, u, v, shape, confidence, line_target, line, k, (transport[0], transport[1], transport[2], transport[3]))


def _solve3(ata: np.ndarray, aty: np.ndarray) -> np.ndarray:
    """Cramer's rule, element by element, so no LAPACK call changes the rounding."""

    def det(mat: np.ndarray) -> np.ndarray:
        return (
            mat[:, 0, 0] * (mat[:, 1, 1] * mat[:, 2, 2] - mat[:, 1, 2] * mat[:, 2, 1])
            - mat[:, 0, 1] * (mat[:, 1, 0] * mat[:, 2, 2] - mat[:, 1, 2] * mat[:, 2, 0])
            + mat[:, 0, 2] * (mat[:, 1, 0] * mat[:, 2, 1] - mat[:, 1, 1] * mat[:, 2, 0])
        )

    d = det(ata)
    safe = np.where(np.abs(d) > 0.0, d, 1.0)
    out = np.zeros_like(aty)
    for col in range(3):
        mat = ata.copy()
        mat[:, :, col] = aty
        out[:, col] = np.where(np.abs(d) > 0.0, det(mat) / safe, 0.0)
    return out


ARM: tuple[tuple[float, float], ...] = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))
"""+X for each ``k``, as a direction ``(re, im)`` in the face basis: ``u``, ``v``, ``-u``, ``-v``."""


def _turn(k: int, r: tuple[float, float]) -> tuple[float, float]:
    """Arm ``k`` turned by the unit complex ``r``: exact, because each arm has one nonzero part of size 1."""
    ar, ai = ARM[k]
    if ai == 0.0:
        return (ar * r[0], ar * r[1])
    return (-ai * r[1], ai * r[0])


def _along(k: int, z: tuple[float, float]) -> float:
    """Re(conj(arm k) * z): how far ``z`` points along arm ``k``. Exact for the same reason."""
    ar, ai = ARM[k]
    return ar * z[0] + ai * z[1]


def _comb(line: C, transport: list[C], topo: Topology, confidence: np.ndarray) -> np.ndarray:
    """Family from the line field, sign by breadth-first combing; seed per component is its most confident face."""
    m = len(confidence)
    # Family 0 (k = 0 or 2) when the line is closer to u than to v: Re(line) >= 0 in 2-theta space.
    family = np.where(line.re >= -TIE, 0, 1)
    k = np.full(m, -1, dtype=np.int64)
    order = sorted(range(m), key=lambda f: (-float(confidence[f]), f))
    for seed in order:
        if k[seed] >= 0:
            continue
        k[seed] = family[seed]
        queue = deque([seed])
        while queue:
            f = queue.popleft()
            for s in range(4):
                g = int(topo.neighbour[f, s])
                if g < 0 or k[g] >= 0:
                    continue
                t = int(topo.neighbour_side[f, s])
                # f's +X carried into g's basis: g's transport on side t maps f's basis into g's.
                carried = _turn(int(k[f]), (float(transport[t].re[g]), float(transport[t].im[g])))
                best, best_score = -1, -2.0
                for cand in (int(family[g]), int(family[g]) + 2):
                    score = _along(cand, carried)
                    if score > best_score + TIE or (abs(score - best_score) <= TIE and cand < best):
                        best, best_score = cand, score
                k[g] = best
                queue.append(g)
    return k


def _comb_mesh(line_target: C, topo: Topology, confidence: np.ndarray) -> np.ndarray:
    """Comb along the edge flow, integers only; then one family per connected piece.

    Breadth-first from each piece's most confident face, a neighbour takes the ``k`` that makes
    the edge to its parent regular (:func:`seam_turns` 0): ``k_g = k_f - s + t + 2``. Turning a
    whole piece by one quarter keeps every seam, so the piece then takes the turn whose +X
    family agrees best with ``line_target``, summed in face order.
    """
    m = len(confidence)
    k = np.full(m, -1, dtype=np.int64)
    piece = np.full(m, -1, dtype=np.int64)
    order = sorted(range(m), key=lambda f: (-float(confidence[f]), f))
    pieces = 0
    for seed in order:
        if k[seed] >= 0:
            continue
        k[seed], piece[seed] = 0, pieces
        queue = deque([seed])
        while queue:
            f = queue.popleft()
            for s in range(4):
                g = int(topo.neighbour[f, s])
                if g < 0 or k[g] >= 0:
                    continue
                t = int(topo.neighbour_side[f, s])
                k[g] = (int(k[f]) - s + t + 2) % 4
                piece[g] = pieces
                queue.append(g)
        pieces += 1
    # Family vote: +X as a line is +1 (even k) or -1 (odd k) in the face's 2-theta space, so
    # agreement is Re(line_target) times that sign. Sum per piece in face order.
    sign = np.where(k % 2 == 0, 1.0, -1.0)
    vote = np.zeros(pieces)
    for f in range(m):
        vote[piece[f]] += float(line_target.re[f]) * float(sign[f])
    turn = np.where(vote < -TIE, 1, 0)
    return (k + turn[piece]) % 4


def seam_turns(k: np.ndarray, topo: Topology) -> np.ndarray:
    """Quarter turns between the two frames across each side, from the labelling alone.

    Side ``s`` of ``f`` is port ``SIDE_PORT[(s - k_f) mod 4]``; across a regular edge the two
    ports are opposite. The result is 0 on regular edges, 1 to 3 at seams, -1 where there is
    no neighbour.
    """
    m = len(k)
    out = np.full((m, 4), -1, dtype=np.int64)
    for f in range(m):
        for s in range(4):
            g = int(topo.neighbour[f, s])
            if g < 0:
                continue
            t = int(topo.neighbour_side[f, s])
            out[f, s] = ((s - int(k[f])) - (t - int(k[g])) - 2) % 4
    return out


def seam_turns_geometric(fld: Field, topo: Topology) -> np.ndarray:
    """The same quarter turns measured from the transported +X axes, for checking :func:`seam_turns`."""
    m = len(fld.k)
    out = np.full((m, 4), -1, dtype=np.int64)
    for f in range(m):
        for s in range(4):
            g = int(topo.neighbour[f, s])
            if g < 0:
                continue
            t = int(topo.neighbour_side[f, s])
            carried = _turn(int(fld.k[f]), (float(fld.transport[t].re[g]), float(fld.transport[t].im[g])))
            # The turn q for which g's own +X turned by q quarter turns is closest to carried;
            # seam_turns counts f relative to g, so the sign flips.
            q = max(range(4), key=lambda i: (_along((int(fld.k[g]) + i) % 4, carried), -i))
            out[f, s] = (-q) % 4
    return out
