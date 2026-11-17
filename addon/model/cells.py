# SPDX-License-Identifier: GPL-3.0-or-later
"""The cell envelope: slots as cells with corners, frames and explicit neighbours.

``docs/PLAN.md``, "Surfaces (phase 2)", "One cell structure for every source".
Every source of slots produces this one structure, and the compiler reads
only this:

- a box envelope (:func:`addon.compile.graph.envelope_cells`);
- a shell grown from a quad mesh (:mod:`addon.model.shell`);
- later (phase 4), a TopologicPy or SpaceTope importer, which can write the
  JSON form (:func:`to_dict`, ``schema/cell-envelope-v1.json``) without Blender.

**Cells.** Each cell has eight corners in unit-cube order, corner
``x + 2y + 4z``: ``x`` runs from the cell's -X face to its +X face, ``y`` from
-Y to +Y, ``z`` from -Z to +Z. A module vertex at unit coordinates
``(u, v, w)`` lands at the trilinear blend of the corners (spike 6). A cell
also has a frame ``(origin, x, y, z)`` for rigid placement (:func:`frame_of`
derives one from the corners) and a source id saying where it came from.

**Neighbours.** ``(cell, port, other, other_port, twist)``, listed in both
directions, sorted by ``(cell, port)``. Ports are the cell's own (+X, -X, +Y,
-Y, +Z, -Z = 0..5), so ``port`` of one cell may meet any port of another;
``twist`` is defined as in ``.claude/rules/solver-core.md``, the same from
both sides.

**Exposed ports.** ``(cell, port, group)``: a port with no neighbour. The
group (``top``, ``bottom``, ``rim`` on a shell; a world face name on a box)
chooses the boundary policy: ``SolveSettings.boundary_groups`` overrides the
default policy per group.

**Notes.** What the builder wants the author to know (skipped faces,
non-manifold edges, inverted cells). The compiler copies them into the audit.

Nothing here imports ``bpy`` or the compiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

CELLS_FORMAT = "lamdafunction-cells-1"
"""Format tag of the JSON form."""

PORTS = 6
"""Every cell is a hexahedron with the box's six ports."""

CORNER_OF_PORT: tuple[tuple[int, ...], ...] = (
    (1, 3, 5, 7),  # +X: x = 1
    (0, 2, 4, 6),  # -X: x = 0
    (2, 3, 6, 7),  # +Y: y = 1
    (0, 1, 4, 5),  # -Y: y = 0
    (4, 5, 6, 7),  # +Z: z = 1
    (0, 1, 2, 3),  # -Z: z = 0
)
"""The four corners on each port's face, by corner index ``x + 2y + 4z``."""


@dataclass(frozen=True, order=True)
class Neighbour:
    """One directed touch: ``cell``'s ``port`` meets ``other``'s ``other_port`` with ``twist``."""

    cell: int
    port: int
    other: int
    other_port: int
    twist: int

    def reverse(self) -> Neighbour:
        """The same touch seen from the other cell."""
        return Neighbour(self.other, self.other_port, self.cell, self.port, self.twist)


@dataclass(frozen=True, order=True)
class Exposed:
    """A port with no neighbour, and the boundary group it belongs to."""

    cell: int
    port: int
    group: str


@dataclass(frozen=True)
class Note:
    """A builder finding, copied into the compiler's audit.

    ``level`` is ``"error"``, ``"warning"`` or ``"info"``. ``target`` names
    what the UI should select: ``("mesh_face", f)``, ``("mesh_edge", a, b)``,
    ``("mesh_vertex", v)``, ``("slot", cell)`` or ``("kit", "")``.
    """

    level: str
    message: str
    target: tuple[Any, ...] = ("kit", "")


@dataclass
class CellEnvelope:
    """Cells with corners, frames and sources; neighbours; exposed ports; per-cell inputs; notes.

    ``corners`` is ``n x 8 x 3`` and ``frames`` ``n x 4 x 3`` (origin, x, y,
    z), both float64. ``weights``, ``priority`` and ``fixed`` mean what they
    mean on :class:`addon.model.envelope.Envelope`.
    """

    corners: np.ndarray
    frames: np.ndarray
    sources: list[tuple[Any, ...]]
    neighbours: list[Neighbour]
    exposed: list[Exposed]
    weights: dict[str, list[float]] = field(default_factory=dict)
    priority: list[int] | None = None
    fixed: list[int] | None = None
    notes: list[Note] = field(default_factory=list)

    @property
    def slot_count(self) -> int:
        """Number of cells, which are the slots."""
        return len(self.corners)

    def check(self) -> None:
        """Shapes agree; every port is either one neighbour or exposed; every touch has its reverse.

        These are the invariants the compiler relies on; a cell envelope written
        by another host is checked here before anything else reads it.
        """
        n = self.slot_count
        if self.corners.shape != (n, 8, 3):
            raise ValueError(f"corners must be n x 8 x 3, got {self.corners.shape}")
        if self.frames.shape != (n, 4, 3):
            raise ValueError(f"frames must be n x 4 x 3, got {self.frames.shape}")
        if len(self.sources) != n:
            raise ValueError(f"sources has {len(self.sources)} entries, envelope has {n} cells")
        for key, values in self.weights.items():
            if len(values) != n:
                raise ValueError(f"weights[{key!r}] has {len(values)} entries, envelope has {n} cells")
        for name, values in (("priority", self.priority), ("fixed", self.fixed)):
            if values is not None and len(values) != n:
                raise ValueError(f"{name} has {len(values)} entries, envelope has {n} cells")
        if self.neighbours != sorted(self.neighbours):
            raise ValueError("neighbours must be sorted by (cell, port)")
        if self.exposed != sorted(self.exposed):
            raise ValueError("exposed ports must be sorted by (cell, port)")
        used: set[tuple[int, int]] = set()
        touches = set(self.neighbours)
        for item in self.neighbours:
            if not (0 <= item.cell < n and 0 <= item.other < n):
                raise ValueError(f"neighbour {item} names a cell outside 0..{n}")
            if not (0 <= item.port < PORTS and 0 <= item.other_port < PORTS and 0 <= item.twist < 4):
                raise ValueError(f"neighbour {item} has a port outside 0..6 or a twist outside 0..4")
            if item.cell == item.other:
                raise ValueError(f"neighbour {item} touches its own cell")
            if (item.cell, item.port) in used:
                raise ValueError(f"cell {item.cell} port {item.port} has two neighbours")
            used.add((item.cell, item.port))
            if item.reverse() not in touches:
                raise ValueError(f"neighbour {item} has no reverse {item.reverse()}")
        for item in self.exposed:
            if (item.cell, item.port) in used:
                raise ValueError(f"cell {item.cell} port {item.port} is both a neighbour and exposed")
            used.add((item.cell, item.port))
        if len(used) != n * PORTS:
            raise ValueError(f"{n * PORTS - len(used)} ports are neither a neighbour nor exposed")


def frame_of(corners: np.ndarray) -> np.ndarray:
    """Frames from corners: origin at the centroid, each axis from the -face centre to the +face centre.

    ``corners`` is ``n x 8 x 3``; the result is ``n x 4 x 3``. The axes are not
    normalised, so their lengths are the cell's size along each axis.
    """
    origin = corners.mean(axis=1)
    axes = [corners[:, CORNER_OF_PORT[2 * a]].mean(axis=1) - corners[:, CORNER_OF_PORT[2 * a + 1]].mean(axis=1) for a in range(3)]
    return np.stack([origin, *axes], axis=1)


def corner_jacobians(corners: np.ndarray) -> np.ndarray:
    """The trilinear map's Jacobian determinant at each of the eight corners, ``n x 8``.

    At a corner the map's derivative is spanned by the three cell edges that
    leave it, each pointed along +x, +y or +z. A determinant at or below zero
    means the cell is folded or inside out there, and deformed modules would
    turn inside out with it (spike 6, "Inverted cells").
    """
    out = np.empty(corners.shape[:2])
    for c in range(8):
        edges = []
        for bit in (1, 2, 4):
            other = c ^ bit
            # Edge from the lower corner to the upper one along this axis, whichever end c is.
            low, high = (c, other) if c & bit == 0 else (other, c)
            edges.append(corners[:, high] - corners[:, low])
        ex, ey, ez = edges
        cross = np.stack(
            (
                ey[:, 1] * ez[:, 2] - ey[:, 2] * ez[:, 1],
                ey[:, 2] * ez[:, 0] - ey[:, 0] * ez[:, 2],
                ey[:, 0] * ez[:, 1] - ey[:, 1] * ez[:, 0],
            ),
            axis=-1,
        )
        out[:, c] = ex[:, 0] * cross[:, 0] + ex[:, 1] * cross[:, 1] + ex[:, 2] * cross[:, 2]
    return out


PARALLEL_EDGES: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 1), (2, 3), (4, 5), (6, 7)),
    ((0, 2), (1, 3), (4, 6), (5, 7)),
    ((0, 4), (1, 5), (2, 6), (3, 7)),
)
"""Per axis, the cell's four edges along it, each from its low corner to its high corner."""


def reversed_edges(corners: np.ndarray) -> np.ndarray:
    """Per cell, whether two parallel edges on one face point against each other (dot product at or below zero).

    In a sound cell the edges along an axis point roughly the same way. Where
    two on a common face point against each other, the edge blended between
    them passes through (or next to) zero inside the cell: the cell is pinched
    there. Only edges sharing a face are compared: on a shell over a cube the
    vertical edges at opposite corners splay apart by more than 90 degrees,
    which is sound.
    """
    out = np.zeros(len(corners), dtype=bool)
    for edges in PARALLEL_EDGES:
        vectors = [corners[:, high] - corners[:, low] for low, high in edges]
        for i in range(4):
            for j in range(i + 1, 4):
                if bin(edges[i][0] ^ edges[j][0]).count("1") != 1:
                    continue  # diagonally opposite: no common face
                a, b = vectors[i], vectors[j]
                out |= (a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1] + a[:, 2] * b[:, 2]) <= 0.0
    return out


def inverted_cells(cells: CellEnvelope, limit: int = 20) -> list[Note]:
    """An error note per cell whose trilinear map folds (at most ``limit`` notes, then a count).

    The corners catch a cell turned inside out. A cell pinched through a point
    (a shell grown inward past the centre of curvature, so the inner face comes
    out mirrored on the far side) keeps positive corners while its middle
    collapses; :func:`reversed_edges` catches that, because its inner edges
    point against its outer ones.
    """
    at_corners = (corner_jacobians(cells.corners) <= 0.0).any(axis=1)
    inside = reversed_edges(cells.corners)
    notes: list[Note] = []
    bad = np.nonzero(at_corners | inside)[0]
    for c in bad[:limit]:
        where = f"cell {int(c)} ({_source_text(cells.sources[int(c)])})"
        if at_corners[c]:
            text = f"{where} is folded or inside out; modules would invert in it"
        else:
            text = f"{where} is pinched inside; the shell is likely thicker than the surface's radius of curvature there"
        notes.append(Note("error", text, ("slot", int(c))))
    if len(bad) > limit:
        notes.append(Note("error", f"{len(bad)} cells are folded, inside out or pinched"))
    return notes


def _source_text(source: tuple[Any, ...]) -> str:
    return " ".join(str(s) for s in source)


def to_dict(cells: CellEnvelope) -> dict[str, Any]:
    """The JSON form (``schema/cell-envelope-v1.json``). Floats are written as Python ``repr`` gives them, which round-trips."""
    data: dict[str, Any] = {
        "format": CELLS_FORMAT,
        "corners": cells.corners.tolist(),
        "frames": cells.frames.tolist(),
        "sources": [list(s) for s in cells.sources],
        "neighbours": [[x.cell, x.port, x.other, x.other_port, x.twist] for x in cells.neighbours],
        "exposed": [[x.cell, x.port, x.group] for x in cells.exposed],
        "weights": {k: list(v) for k, v in cells.weights.items()},
        "notes": [{"level": n.level, "message": n.message, "target": list(n.target)} for n in cells.notes],
    }
    if cells.priority is not None:
        data["priority"] = list(cells.priority)
    if cells.fixed is not None:
        data["fixed"] = list(cells.fixed)
    return data


def from_dict(data: dict[str, Any]) -> CellEnvelope:
    """Inverse of :func:`to_dict`; checks the format tag and the invariants."""
    if data.get("format") != CELLS_FORMAT:
        raise ValueError(f"not a cell envelope: format {data.get('format')!r}, expected {CELLS_FORMAT!r}")
    corners = np.array(data["corners"], dtype=np.float64).reshape(-1, 8, 3)
    frames = data.get("frames")
    cells = CellEnvelope(
        corners=corners,
        frames=frame_of(corners) if frames is None else np.array(frames, dtype=np.float64).reshape(-1, 4, 3),
        sources=[tuple(s) for s in data.get("sources", [[i] for i in range(len(corners))])],
        neighbours=sorted(Neighbour(*(int(v) for v in x)) for x in data["neighbours"]),
        exposed=sorted(Exposed(int(x[0]), int(x[1]), str(x[2])) for x in data["exposed"]),
        weights={str(k): [float(x) for x in v] for k, v in data.get("weights", {}).items()},
        priority=None if data.get("priority") is None else [int(p) for p in data["priority"]],
        fixed=None if data.get("fixed") is None else [int(f) for f in data["fixed"]],
        notes=[Note(str(n["level"]), str(n["message"]), tuple(n.get("target", ("kit", "")))) for n in data.get("notes", [])],
    )
    cells.check()
    return cells
