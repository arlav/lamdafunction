# SPDX-License-Identifier: GPL-3.0-or-later
"""The data model: what an author describes, independent of Blender.

Nothing here imports ``bpy``. ``addon/bl/`` fills these records from the
``lamdafunction`` property groups and attributes; the compiler
(``addon/compile/``) reads them; tests build them directly.

- :mod:`kit`: connectors, modules, connector pairs, rules and the kit.
- :mod:`envelope`: the slots to fill, their per-slot inputs, and the solve settings.
- :mod:`io`: JSON round-trip for kit manifests and envelopes.
- :mod:`cells`: the host-neutral cell envelope (corners, frames, neighbours, exposed ports) and its JSON form.
- :mod:`shell`: a quad mesh plus a thickness and layers, as a cell envelope (phase 2).
- :mod:`field`: the direction field on a quad mesh (phase 2).
"""

from __future__ import annotations

from .cells import CellEnvelope
from .envelope import Envelope, SolveSettings
from .kit import FACE_NAMES, Connector, ConnectorPair, Kit, Module, Rule

__all__ = [
    "CellEnvelope",
    "FACE_NAMES",
    "Connector",
    "ConnectorPair",
    "Envelope",
    "Kit",
    "Module",
    "Rule",
    "SolveSettings",
]
