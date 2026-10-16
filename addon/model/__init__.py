# SPDX-License-Identifier: GPL-3.0-or-later
"""The data model: what an author describes, independent of Blender.

Nothing here imports ``bpy``. ``addon/bl/`` fills these records from the
``lamdafunction`` property groups and attributes; the compiler
(``addon/compile/``) reads them; tests build them directly.

- :mod:`kit`: connectors, modules, connector pairs, rules and the kit.
- :mod:`envelope`: the slots to fill, their per-slot inputs, and the solve settings.
- :mod:`io`: JSON round-trip for kit manifests and envelopes.
"""

from __future__ import annotations

from .envelope import Envelope, SolveSettings
from .kit import FACE_NAMES, Connector, ConnectorPair, Kit, Module, Rule

__all__ = [
    "FACE_NAMES",
    "Connector",
    "ConnectorPair",
    "Envelope",
    "Kit",
    "Module",
    "Rule",
    "SolveSettings",
]
