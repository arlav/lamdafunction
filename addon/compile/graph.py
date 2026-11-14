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
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.envelope import Cell
from . import frames as faces
from .rotations import AXES, IDENTITY, apply, compose, inverse, neg

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
