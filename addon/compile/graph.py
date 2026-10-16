# SPDX-License-Identifier: GPL-3.0-or-later
"""The slot graph of a box-grid envelope in CSR form, plus its exposed ports.

Slots are the envelope's cells in order. Two cells one step apart along an
axis share an edge: the port is the face of the step direction, the
neighbour's port the opposite face, and the twist 0, because every face frame
on a box grid is the grid's own (``.claude/rules/solver-core.md``, "Twist
convention"). A port with no neighbour is *exposed*; the boundary policy
applies there.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.envelope import Cell
from .rotations import AXES

STEPS: tuple[Cell, ...] = AXES
"""Cell offset of each port, in port order (+X, -X, +Y, -Y, +Z, -Z)."""


@dataclass(frozen=True)
class SlotGraph:
    """CSR edges as the compiled assembly stores them, and the exposed (slot, port) list."""

    offsets: tuple[int, ...]
    port: tuple[int, ...]
    neighbour: tuple[int, ...]
    neighbour_port: tuple[int, ...]
    twist: tuple[int, ...]
    exposed: tuple[tuple[int, int], ...]

    @property
    def slot_count(self) -> int:
        """Number of slots."""
        return len(self.offsets) - 1

    @property
    def edge_count(self) -> int:
        """Number of directed edges (each touch counts twice)."""
        return len(self.port)

    def as_dict(self) -> dict[str, list[int]]:
        """The ``edges`` object of the compiled assembly."""
        return {
            "offsets": list(self.offsets),
            "port": list(self.port),
            "neighbour": list(self.neighbour),
            "neighbour_port": list(self.neighbour_port),
            "twist": list(self.twist),
        }


def build_graph(cells: list[Cell]) -> SlotGraph:
    """Edges between cells one step apart, in slot order then port order."""
    index = {cell: i for i, cell in enumerate(cells)}
    if len(index) != len(cells):
        raise ValueError("envelope cells are not distinct")
    offsets = [0]
    port: list[int] = []
    neighbour: list[int] = []
    neighbour_port: list[int] = []
    exposed: list[tuple[int, int]] = []
    for slot, (x, y, z) in enumerate(cells):
        for p, (dx, dy, dz) in enumerate(STEPS):
            other = index.get((x + dx, y + dy, z + dz))
            if other is None:
                exposed.append((slot, p))
                continue
            port.append(p)
            neighbour.append(other)
            neighbour_port.append(p ^ 1)
        offsets.append(len(port))
    return SlotGraph(
        tuple(offsets), tuple(port), tuple(neighbour), tuple(neighbour_port), (0,) * len(port), tuple(exposed)
    )
