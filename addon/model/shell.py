# SPDX-License-Identifier: GPL-3.0-or-later
"""A shell grown from a polygon mesh: quads × layers through a thickness, as a cell envelope.

``docs/PLAN.md``, "Surfaces (phase 2)", "The shell model". Plain NumPy, no
``bpy``: ``addon/bl/`` reads the evaluated mesh into arrays and calls
:func:`build_shell`.

**Faces.** Quads become slots. Triangles and n-gons are skipped and listed in
the notes. Faces are first oriented consistently, piece by piece (two faces
sharing an edge must run along it in opposite directions); pieces that
touch only at a vertex are made to agree there, a free piece keeps the
orientation most of its faces already had, and every face turned over is
noted. A piece that cannot be oriented (a Möbius strip) is cut along the
edges that stay inconsistent: a cell reflected across an edge is a mirror
image, which no rotation can express. Edges shared by three or more faces
are left unconnected and reported as errors.

**Corners.** Layer boundary ``b`` (``0..layers``) at vertex ``v`` is
``v + n_v × (base + thickness · b / layers)``, where ``n_v`` is the
area-weighted smooth vertex normal and ``base`` is ``0`` (outward),
``-thickness`` (inward) or ``-thickness / 2`` (centred). Neighbouring cells
compute their shared corners from the same vertex and normal, so they share
them exactly.

**Labelling.** A quad's corners ``c0…c3`` come in loop order, counter-clockwise
seen from the normal. Turned by ``k`` quarter turns, ``c'_i = c_{(i + k) mod 4}``,
and the cell's corner ``x + 2y + 4z`` is ``(0,0) → c'0``, ``(1,0) → c'1``,
``(1,1) → c'2``, ``(0,1) → c'3``, with ``z`` from the lower layer boundary to
the upper one. So quad side ``s`` (from ``c_s`` to ``c_{s+1}``) is port
``SIDE_PORT[(s - k) mod 4]`` (-Y, +X, +Y, -X), -Z faces inward and +Z
outward. ``k`` comes from the direction field (S4); without one it is 0.

**Edges.** Across a mesh edge, side ``s`` of face ``f`` meets side ``t`` of
face ``g``. With a consistent orientation both frames share +Z, so the
relative rotation is the quarter turn about Z that makes ``g``'s port face
back across ``f``'s, decided from the labelling alone (spike 7: geometry
disagrees with it on skewed quads). Between layers, +Z of layer ``l`` meets
-Z of layer ``l + 1`` with twist 0.

**Order.** Cell ``layer × quads + i`` for the ``i``-th quad in mesh order, so
a flat grid with its quads in x-then-y order gives the same slot order as a
box envelope (x fastest, then y, then z).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..compile import frames as faces
from ..compile.graph import edge_twist
from ..compile.rotations import apply, rotations_about
from .cells import CellEnvelope, Exposed, Neighbour, Note, frame_of, inverted_cells
from .field import SIDE_PORT, QuadMesh, topology, vertex_normals

OFFSETS = ("outward", "inward", "centred")
"""Where the thickness goes relative to the surface."""

NOTE_LIMIT = 20
"""At most this many notes of one kind are listed one by one; the rest are counted."""

_Z_TURNS: tuple[int, ...] = rotations_about((0, 0, 1))


def side_port(side: int, k: int) -> int:
    """The port of quad side ``side`` when the labelling is turned by ``k``."""
    return SIDE_PORT[(side - k) % 4]


def _z_relative(port: int, neighbour_port: int) -> int:
    """The quarter turn about +Z taking ``neighbour_port``'s normal to the reverse of ``port``'s."""
    target = tuple(-x for x in faces.FACE_NORMAL[port])
    for r in _Z_TURNS:
        if apply(r, faces.FACE_NORMAL[neighbour_port]) == target:
            return r
    raise ValueError(f"ports {port} and {neighbour_port} are not both side ports")


def side_kind(side: int, k: int, other_side: int, other_k: int) -> tuple[int, int, int]:
    """``(port, neighbour port, twist)`` across a consistently oriented mesh edge."""
    p, q = side_port(side, k), side_port(other_side, other_k)
    return p, q, edge_twist(p, q, _z_relative(p, q))


def _limited(notes: list[Note], level: str, what: str, count: int) -> list[Note]:
    if count > NOTE_LIMIT:
        return notes[:NOTE_LIMIT] + [Note(level, f"{count} {what} in all")]
    return notes


def orient(mesh: QuadMesh) -> tuple[QuadMesh, list[int], list[tuple[int, int]]]:
    """Make neighbouring quads run opposite ways along their shared edges.

    Returns the oriented mesh, the faces that were turned over, and the edges
    that are still inconsistent (only on surfaces that cannot be oriented).
    Each connected piece is walked breadth-first from its lowest face. A
    piece that shares vertices with earlier pieces then turns to agree with
    them there (otherwise the vertex normal cancels and the cells around it
    fold); a piece that shares none keeps whichever orientation most of its
    faces had (ties keep the lowest face's). So a mesh that is already
    consistent comes back unchanged.
    """
    topo = topology(mesh)
    m = len(mesh.faces)
    flip = np.zeros(m, dtype=bool)
    piece = np.full(m, -1, dtype=np.int64)
    pieces: list[list[int]] = []
    for start in range(m):
        if piece[start] >= 0:
            continue
        members = [start]
        piece[start] = len(pieces)
        queue = deque([start])
        while queue:
            f = queue.popleft()
            for s in range(4):
                g = int(topo.neighbour[f, s])
                if g < 0 or piece[g] >= 0:
                    continue
                t = int(topo.neighbour_side[f, s])
                # Same direction along the edge in both faces means g must turn over relative to f.
                same = int(mesh.faces[f, s]) == int(mesh.faces[g, t])
                flip[g] = flip[f] != same
                piece[g] = piece[f]
                members.append(g)
                queue.append(g)
        pieces.append(members)
    # Pieces that share only a vertex must still agree there, or that vertex's normal
    # cancels out and the cells around it fold. Each piece, in order, turns to agree with
    # the pieces before it at the vertices they share; a piece sharing none keeps the
    # orientation most of its faces had.
    corners = mesh.vertices[mesh.faces]
    area_normal = np.cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 1])
    placed = np.zeros_like(mesh.vertices, dtype=np.float64)
    for members in pieces:
        normal = np.where(flip[members][:, None], -area_normal[members], area_normal[members])
        own = np.zeros_like(placed)
        for c in range(4):
            np.add.at(own, mesh.faces[members, c], normal)
        shared = (np.abs(placed).sum(axis=1) > 0.0) & (np.abs(own).sum(axis=1) > 0.0)
        if shared.any():
            agree = float((placed[shared] * own[shared]).sum())
            turn = agree < 0.0
        else:
            turn = 2 * int(flip[members].sum()) > len(members)
        if turn:
            flip[members] = ~flip[members]
            own = -own
        placed += own
    oriented = mesh.faces.copy()
    # Reversing c0 c1 c2 c3 as c0 c3 c2 c1 keeps c0 first, so an unflipped face keeps its labelling.
    oriented[flip] = oriented[flip][:, [0, 3, 2, 1]]
    result = QuadMesh(mesh.vertices, oriented)
    return result, [int(f) for f in np.nonzero(flip)[0]], list(topology(result).flipped)


def build_shell(
    vertices: np.ndarray,
    polygons: Sequence[Sequence[int]],
    *,
    thickness: float = 1.0,
    layers: int = 1,
    offset: str = "outward",
    k: Sequence[int] | None = None,
    face_inputs: dict[str, Any] | None = None,
) -> CellEnvelope:
    """The cell envelope of a shell over a polygon mesh.

    ``polygons`` lists each face's vertex indices in loop order. ``k`` gives
    the labelling turn per *quad* (in the order quads appear in
    ``polygons``, after :func:`orient`), from the direction field; ``None``
    means 0 everywhere.
    ``face_inputs`` may hold ``weights`` (name to one value per polygon),
    ``priority`` and ``fixed`` (one per polygon); every layer of a face takes
    the face's value. Sources are ``("face", polygon index, layer)``.
    """
    if layers < 1:
        raise ValueError("a shell needs at least one layer")
    if offset not in OFFSETS:
        raise ValueError(f"offset must be one of {OFFSETS}, got {offset!r}")
    if not thickness > 0.0:
        raise ValueError("thickness must be positive")
    notes: list[Note] = []

    quad_of: list[int] = []
    skipped: list[Note] = []
    for index, polygon in enumerate(polygons):
        if len(polygon) == 4:
            quad_of.append(index)
        else:
            skipped.append(Note("warning", f"face {index} has {len(polygon)} sides and is skipped; only quads become slots", ("mesh_face", index)))
    notes += _limited(skipped, "warning", "non-quad faces skipped", len(skipped))
    m = len(quad_of)
    labelling = np.zeros(m, dtype=np.int64) if k is None else np.asarray(k, dtype=np.int64) % 4
    if len(labelling) != m:
        raise ValueError(f"k has {len(labelling)} entries, the mesh has {m} quads")

    vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    quads = np.array([polygons[i] for i in quad_of], dtype=np.int64).reshape(-1, 4)
    mesh, turned_over, inconsistent = orient(QuadMesh(vertices, quads))
    if turned_over:
        listed = [Note("warning", f"face {quad_of[f]} was turned over to match its neighbours", ("mesh_face", quad_of[f])) for f in turned_over]
        notes += _limited(listed, "warning", "faces turned over", len(listed))
    topo = topology(mesh)
    for a, b in topo.non_manifold:
        notes.append(Note("error", f"edge {a}-{b} is shared by three or more faces; its sides are left open", ("mesh_edge", a, b)))
    cut = set(inconsistent)
    for a, b in inconsistent:
        notes.append(Note("warning", f"edge {a}-{b} joins faces that cannot be oriented alike (one-sided surface); it is cut", ("mesh_edge", a, b)))

    # Labelling: rotate each quad's corner list by its k, so c'0 is corner (0, 0).
    turned = np.array([np.roll(mesh.faces[f], -int(labelling[f])) for f in range(m)], dtype=np.int64).reshape(-1, 4)
    corners_q = vertices[mesh.faces]
    area_normal = np.cross(corners_q[:, 2] - corners_q[:, 0], corners_q[:, 3] - corners_q[:, 1]) * 0.5
    normals = vertex_normals(mesh, area_normal)
    base = {"outward": 0.0, "inward": -thickness, "centred": -thickness / 2.0}[offset]
    boundary = [vertices + normals * (base + thickness * b / layers) for b in range(layers + 1)]
    ring = turned[:, [0, 1, 3, 2]]  # corner x + 2y: (0,0) c'0, (1,0) c'1, (0,1) c'3, (1,1) c'2
    corners = np.concatenate(
        [np.concatenate([boundary[layer][ring], boundary[layer + 1][ring]], axis=1) for layer in range(layers)], axis=0
    ).reshape(-1, 8, 3)

    neighbours: list[Neighbour] = []
    exposed: list[Exposed] = []
    for layer in range(layers):
        for f in range(m):
            cell = layer * m + f
            ports: dict[int, Neighbour | Exposed] = {}
            for s in range(4):
                g = int(topo.neighbour[f, s])
                a, b = int(mesh.faces[f, s]), int(mesh.faces[f, (s + 1) % 4])
                p = side_port(s, int(labelling[f]))
                if g < 0 or (min(a, b), max(a, b)) in cut:
                    ports[p] = Exposed(cell, p, "rim")
                    continue
                t = int(topo.neighbour_side[f, s])
                _, q, tw = side_kind(s, int(labelling[f]), t, int(labelling[g]))
                ports[p] = Neighbour(cell, p, layer * m + g, q, tw)
            ports[4] = Neighbour(cell, 4, cell + m, 5, 0) if layer + 1 < layers else Exposed(cell, 4, "top")
            ports[5] = Neighbour(cell, 5, cell - m, 4, 0) if layer > 0 else Exposed(cell, 5, "bottom")
            for p in range(6):
                item = ports[p]
                (neighbours if isinstance(item, Neighbour) else exposed).append(item)  # type: ignore[arg-type]

    inputs = face_inputs or {}

    def per_cell(values: Sequence[Any]) -> list[Any]:
        if len(values) != len(polygons):
            raise ValueError(f"a face input has {len(values)} entries, the mesh has {len(polygons)} faces")
        return [values[quad_of[f]] for _ in range(layers) for f in range(m)]

    cells = CellEnvelope(
        corners=corners,
        frames=frame_of(corners),
        sources=[("face", quad_of[f], layer) for layer in range(layers) for f in range(m)],
        neighbours=neighbours,
        exposed=exposed,
        weights={str(name): [float(x) for x in per_cell(v)] for name, v in inputs.get("weights", {}).items()},
        priority=None if inputs.get("priority") is None else [int(x) for x in per_cell(inputs["priority"])],
        fixed=None if inputs.get("fixed") is None else [int(x) for x in per_cell(inputs["fixed"])],
        notes=notes,
    )
    cells.notes += inverted_cells(cells, NOTE_LIMIT)
    cells.check()
    return cells
