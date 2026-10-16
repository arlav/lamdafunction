# SPDX-License-Identifier: GPL-3.0-or-later
"""Envelopes: the slots to fill, their per-slot inputs, and the solve settings.

In Blender an envelope is any mesh whose evaluated vertices are the slots,
read through the point attributes of ``.claude/rules/blender-addon.md``.
Here it is plain lists, so the compiler and the tests never see ``bpy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Cell = tuple[int, int, int]
"""Grid coordinates of a slot (``wfc_cell``, rounded to whole numbers)."""


@dataclass
class Envelope:
    """Slots on a box grid, in slot order, with optional per-slot inputs.

    ``cells`` lists each slot's grid coordinates; two slots are neighbours
    when their cells differ by one along one axis. ``weights`` maps a module
    name or ``#tag`` (the ``wfc_w:`` attributes without their prefix) to one
    multiplier per slot; ``priority`` and ``fixed`` (module index, −1 free)
    are per slot too.
    """

    cells: list[Cell]
    weights: dict[str, list[float]] = field(default_factory=dict)
    priority: list[int] | None = None
    fixed: list[int] | None = None

    def check(self) -> None:
        """Cells distinct; every per-slot list as long as ``cells``."""
        if len(set(self.cells)) != len(self.cells):
            raise ValueError("envelope cells are not distinct")
        n = len(self.cells)
        for key, values in self.weights.items():
            if len(values) != n:
                raise ValueError(f"weights[{key!r}] has {len(values)} entries, envelope has {n} slots")
        if self.priority is not None and len(self.priority) != n:
            raise ValueError(f"priority has {len(self.priority)} entries, envelope has {n} slots")
        if self.fixed is not None and len(self.fixed) != n:
            raise ValueError(f"fixed has {len(self.fixed)} entries, envelope has {n} slots")

    @classmethod
    def box(cls, size: tuple[int, int, int]) -> Envelope:
        """A full box of ``size`` cells, x fastest, then y, then z."""
        width, height, depth = size
        return cls([(x, y, z) for z in range(depth) for y in range(height) for x in range(width)])


@dataclass
class SolveSettings:
    """Solver settings and the boundary policy.

    ``closed_by`` is the boundary policy: ``None`` leaves exposed faces free;
    a connector name (such as ``"0"``) requires every exposed face to carry
    that connector or to be flagged as a terminator.
    """

    seed: int = 1
    attempts: int = 8
    threads: int = 0
    backtrack_budget: int = 100
    max_observations: int = 0
    closed_by: str | None = None
