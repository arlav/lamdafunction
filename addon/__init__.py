# SPDX-License-Identifier: GPL-3.0-or-later
"""LamdaFunction: wave function collapse for Blender.

This is the extension's root package. It must not import ``bpy`` or
``addon.bl`` at module level (``.claude/rules/blender-addon.md``), so that
``pytest`` can import ``addon.model`` and ``addon.compile`` outside Blender.
Everything that touches Blender is imported inside :func:`register`.
"""

from __future__ import annotations


def register() -> None:
    """Register the Blender side of the extension."""
    from . import bl  # noqa: PLC0415 - bpy must not be imported at module level

    bl.register()


def unregister() -> None:
    """Undo :func:`register`."""
    from . import bl  # noqa: PLC0415

    bl.unregister()
