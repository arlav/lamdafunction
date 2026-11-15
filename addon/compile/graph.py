# SPDX-License-Identifier: GPL-3.0-or-later
"""The slot graph of a box-grid envelope in CSR form, plus its exposed ports.

Slots are the envelope's cells in order. Two cells one step apart along a
world axis share an edge. Each slot may have a frame (a rotation index taking
its local axes to world axes, ``Envelope.frames``); ports are named in the
slot's own frame, so with turned frames slot A's +X may meet slot B's +Y.

Per directed edge the graph stores the port on each side, the twist, and the
relative rotation ``R = F_a⁻¹ · F_b`` that takes the neighbour's frame into
this slot's frame. The rule tables for an edge come from ``R``
(``rules.edge_kind_pairs``).

**Twist of an edge** (``.claude/rules/solver-core.md``, "Twist convention"):
quarter turns, counter-clockwise about this slot's outward port normal, that
take the offset-0 arrow the neighbour's port would have on a regular grid
onto the neighbour port's actual offset-0 arrow, both seen in this slot's
frame. With identical frames it is 0. Measured from the other side it is the
same number (``test_graph.py`` checks all 24 × 24 frame pairs).

Without frames every edge is the box-grid kind (port, opposite port, 0) and
``R`` is the identity, exactly as in phase 1.

A port with no neighbour is *exposed*, named in the slot's own frame; the
boundary policy applies there.

Since phase 2 S3 the compiler reads every envelope as a cell envelope
(:mod:`addon.model.cells`): :func:`envelope_cells` converts a box envelope,
and :func:`graph_from_cells` turns any cell envelope's neighbour list into
the CSR graph, recovering each edge's relative rotation from its kind
(:func:`relative_rotation`). A cell envelope from a shell or another host
never has to state rotations, only ports and twists.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..model.cells import CellEnvelope, Exposed, Neighbour, frame_of
from ..model.envelope import Cell, Envelope
from . import frames as faces
from .rotations import AXES, AXIS_NAMES, IDENTITY, ROTATIONS, apply, column, compose, inverse, neg

STEPS: tuple[Cell, ...] = AXES
"""Cell offset of each world direction, in port order (+X, -X, +Y, -Y, +Z, -Z)."""

EdgeKind = tuple[int, int, int]
"""``(port, neighbour port, twist)``, the key of an adjacency table."""


@dataclass(frozen=True)
class SlotGraph:
    """CSR edges as the compiled assembly stores them, the relative rotation per edge, and the exposed (slot, port) list."""

    offsets: tuple[int, ...]
    port: tuple[int, ...]
    neighbour: tuple[int, ...]
    neighbour_port: tuple[int, ...]
    twist: tuple[int, ...]
    relative: tuple[int, ...]
    exposed: tuple[tuple[int, int], ...]

    @property
    def slot_count(self) -> int:
        """Number of slots."""
        return len(self.offsets) - 1

    @property
    def edge_count(self) -> int:
        """Number of directed edges (each touch counts twice)."""
        return len(self.port)

    def kinds(self) -> dict[EdgeKind, int]:
        """Every edge kind in use, with the relative rotation it stands for (the same for every edge of a kind)."""
        found: dict[EdgeKind, int] = {}
        for i in range(self.edge_count):
            kind = (self.port[i], self.neighbour_port[i], self.twist[i])
            previous = found.setdefault(kind, self.relative[i])
            if previous != self.relative[i]:
                raise AssertionError(f"edge kind {kind} stands for two relative rotations {previous} and {self.relative[i]}")
        return found

    def is_box_grid(self) -> bool:
        """Whether every edge is the plain kind: opposite ports, twist 0, no relative rotation."""
        return all(r == IDENTITY for r in self.relative)

    def as_dict(self) -> dict[str, list[int]]:
        """The ``edges`` object of the compiled assembly."""
        return {
            "offsets": list(self.offsets),
            "port": list(self.port),
            "neighbour": list(self.neighbour),
            "neighbour_port": list(self.neighbour_port),
            "twist": list(self.twist),
        }


def local_face(frame: int, world_face: int) -> int:
    """The face of a slot with ``frame`` that points along world face ``world_face``."""
    return faces.face_of_normal(apply(inverse(frame), faces.FACE_NORMAL[world_face]))


def edge_twist(port: int, neighbour_port: int, relative: int) -> int:
    """Twist of an edge from this slot's port, the neighbour's port and the relative rotation.

    In this slot's frame the neighbour's port faces back along ``-normal(port)``.
    On a regular grid its offset-0 arrow would be ``FACE_U[opposite(port)]``; its
    actual arrow is ``FACE_U[neighbour_port]`` turned by ``relative``.
    """
    normal = faces.FACE_NORMAL[port]
    regular = faces.FACE_U[faces.opposite(port)]
    actual = apply(relative, faces.FACE_U[neighbour_port])
    if apply(relative, faces.FACE_NORMAL[neighbour_port]) != neg(normal):
        raise ValueError(f"port {neighbour_port} turned by {relative} does not face port {port}")
    return faces.twist_from_arrows(normal, regular, actual)


def build_graph(cells: list[Cell], frames: list[int] | None = None) -> SlotGraph:
    """Edges between cells one step apart, in slot order then port order (the slot's own ports)."""
    index = {cell: i for i, cell in enumerate(cells)}
    if len(index) != len(cells):
        raise ValueError("envelope cells are not distinct")
    frame = frames if frames is not None else [IDENTITY] * len(cells)
    offsets = [0]
    port: list[int] = []
    neighbour: list[int] = []
    neighbour_port: list[int] = []
    twist: list[int] = []
    relative: list[int] = []
    exposed: list[tuple[int, int]] = []
    for slot, (x, y, z) in enumerate(cells):
        # The slot's ports in port order, each with the world direction it faces,
        # so the CSR order stays "port order" in the slot's own frame.
        for p in range(6):
            world = faces.face_of_normal(apply(frame[slot], faces.FACE_NORMAL[p]))
            dx, dy, dz = STEPS[world]
            other = index.get((x + dx, y + dy, z + dz))
            if other is None:
                exposed.append((slot, p))
                continue
            q = local_face(frame[other], faces.opposite(world))
            r = compose(inverse(frame[slot]), frame[other])
            port.append(p)
            neighbour.append(other)
            neighbour_port.append(q)
            twist.append(edge_twist(p, q, r))
            relative.append(r)
        offsets.append(len(port))
    return SlotGraph(
        tuple(offsets), tuple(port), tuple(neighbour), tuple(neighbour_port), tuple(twist), tuple(relative), tuple(exposed)
    )


def _kind_table() -> dict[EdgeKind, int]:
    """Every edge kind with the relative rotation it stands for (24 per port, 144 in all)."""
    table: dict[EdgeKind, int] = {}
    for r in range(len(ROTATIONS)):
        for p in range(6):
            # The neighbour port is the face that r turns to face back across p.
            q = next(f for f in range(6) if apply(r, faces.FACE_NORMAL[f]) == neg(faces.FACE_NORMAL[p]))
            table[(p, q, edge_twist(p, q, r))] = r
    return table


KIND_ROTATION: dict[EdgeKind, int] = _kind_table()
"""``(port, neighbour port, twist)`` to the relative rotation ``R`` it stands for."""


def relative_rotation(kind: EdgeKind) -> int:
    """The relative rotation of an edge kind; ``ValueError`` if no rotation makes the two ports face each other."""
    try:
        return KIND_ROTATION[kind]
    except KeyError:
        raise ValueError(f"edge kind {kind} is not a touch: no rotation makes those ports face each other with that twist") from None


def graph_from_cells(cells: CellEnvelope) -> SlotGraph:
    """The CSR graph of a cell envelope: its neighbours in (cell, port) order, relative rotations from their kinds."""
    cells.check()
    offsets = [0]
    port: list[int] = []
    neighbour: list[int] = []
    neighbour_port: list[int] = []
    twist: list[int] = []
    relative: list[int] = []
    items = iter(cells.neighbours)
    item = next(items, None)
    for slot in range(cells.slot_count):
        while item is not None and item.cell == slot:
            port.append(item.port)
            neighbour.append(item.other)
            neighbour_port.append(item.other_port)
            twist.append(item.twist)
            relative.append(relative_rotation((item.port, item.other_port, item.twist)))
            item = next(items, None)
        offsets.append(len(port))
    exposed = tuple((x.cell, x.port) for x in cells.exposed)
    return SlotGraph(
        tuple(offsets), tuple(port), tuple(neighbour), tuple(neighbour_port), tuple(twist), tuple(relative), exposed
    )


UNIT_CORNERS: np.ndarray = np.array([[(c & 1), (c >> 1) & 1, (c >> 2) & 1] for c in range(8)], dtype=np.float64)
"""Corner ``x + 2y + 4z`` of the unit cube, as (x, y, z) in 0..1."""


def envelope_cells(envelope: Envelope) -> CellEnvelope:
    """A box envelope as a cell envelope.

    Cell ``(x, y, z)`` fills ``[x, x + 1] × [y, y + 1] × [z, z + 1]`` in grid
    units, with its corners in the slot's own frame (so a turned frame turns
    the corner order too). Exposed ports are grouped by the world face they
    look along (``"+X"`` … ``"-Z"``).
    """
    envelope.check()
    graph = build_graph(envelope.cells, envelope.frames)
    frames = envelope.frames if envelope.frames is not None else [IDENTITY] * len(envelope.cells)
    centre = np.array(envelope.cells, dtype=np.float64).reshape(-1, 3) + 0.5
    matrices = np.array(
        [[column(ROTATIONS[f], c) for c in range(3)] for f in frames], dtype=np.float64
    ).reshape(-1, 3, 3)  # rows are the local axes in world coordinates
    corners = centre[:, None, :] + (UNIT_CORNERS - 0.5) @ matrices
    neighbours = [
        Neighbour(slot, graph.port[i], graph.neighbour[i], graph.neighbour_port[i], graph.twist[i])
        for slot in range(graph.slot_count)
        for i in range(graph.offsets[slot], graph.offsets[slot + 1])
    ]
    exposed = [
        Exposed(slot, p, AXIS_NAMES[faces.face_of_normal(apply(frames[slot], faces.FACE_NORMAL[p]))])
        for slot, p in graph.exposed
    ]
    return CellEnvelope(
        corners=corners,
        frames=frame_of(corners),
        sources=[("cell", *c) for c in envelope.cells],
        neighbours=neighbours,
        exposed=exposed,
        weights=dict(envelope.weights),
        priority=None if envelope.priority is None else list(envelope.priority),
        fixed=None if envelope.fixed is None else list(envelope.fixed),
    )
