# SPDX-License-Identifier: GPL-3.0-or-later
"""Everything that touches Blender: properties, operators, panels, overlay.

Imported only from ``addon.register()``. Modules:

- :mod:`properties`: the ``lamdafunction`` property groups on collections and the scene.
- :mod:`library`: library collection ↔ :class:`addon.model.Kit`, geometry hashes.
- :mod:`envelope`: envelope object ↔ :class:`addon.model.Envelope`, box envelopes.
- :mod:`backend`: picks ``lamda_core`` or the vendored reference solver.
- :mod:`results`: the assembly object and its ``wfc_*`` attributes.
- :mod:`nodes`: the Materialise and Inspect node groups and the instancer collection.
- :mod:`operators`: compile, solve (modal, worker thread, Esc cancels), materialise, editing helpers.
- :mod:`panels`: the LamdaFunction sidebar tab.
- :mod:`overlay`: face arrows and cages for the active module.
"""

from __future__ import annotations

from . import operators, overlay, panels, properties


def register() -> None:
    """Register in dependency order."""
    properties.register()
    operators.register()
    panels.register()
    overlay.register()


def unregister() -> None:
    """Unregister in reverse order."""
    overlay.unregister()
    panels.unregister()
    operators.unregister()
    properties.unregister()
