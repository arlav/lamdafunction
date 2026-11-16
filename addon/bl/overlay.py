# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport overlay: the active module's cage and connector arrows, and the surface field.

Arrows are drawn in world space from the face centre along
``frames.arrow(face, offset)``, coloured by the face's axis (X red, Y green,
Z blue), so an author sees whether two connectors' arrows will coincide
(twist 0) before solving. Terminator faces get a short white tick.

In surface mode the last compile also leaves the field here (:func:`set_field`):
each inner-layer cell's +X as a red line from its base, and the seams, where
neighbouring frames turn, in yellow. It is computed once per compile, not per
redraw, and drawn while the scene's ``show_field`` is on.
"""

from __future__ import annotations

import numpy as np

import bpy

from ..compile import frames
from ..model.cells import CellEnvelope
from ..model.placement import field_marks
from .panels import active_module

_HANDLE = None
_FIELD: tuple[list[tuple[float, float, float]], list[tuple[float, float, float, float]]] = ([], [])

ARROW = (0.95, 0.25, 0.2, 1.0)
SEAM = (1.0, 0.85, 0.1, 1.0)


def set_field(cells: CellEnvelope | None) -> None:
    """Keep the field lines of a compiled shell for drawing (``None`` clears them)."""
    global _FIELD
    if cells is None:
        _FIELD = ([], [])
        return
    arrows, seams = field_marks(cells)
    coords = [tuple(float(x) for x in p) for p in np.concatenate([arrows, seams]).reshape(-1, 3)]
    colours = [ARROW] * (2 * len(arrows)) + [SEAM] * (2 * len(seams))
    _FIELD = (coords, colours)  # type: ignore[assignment]


def field_lines() -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float, float]]]:
    """The field lines kept by the last compile."""
    return _FIELD


def _lines(context: bpy.types.Context) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float, float]]]:
    """Line segments and per-vertex colours for the active module."""
    module = active_module(context)
    if module is None:
        return [], []
    props = module.lamdafunction
    size = context.scene.lamdafunction.cell_size
    h = size / 2.0
    ox, oy, oz = module.instance_offset
    coords: list[tuple[float, float, float]] = []
    colours: list[tuple[float, float, float, float]] = []
    grey = (0.6, 0.6, 0.6, 1.0)
    corners = [(ox + sx * h, oy + sy * h, oz + sz * h) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    for a, b in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)):
        coords += [corners[a], corners[b]]
        colours += [grey, grey]
    axis_colour = ((0.9, 0.2, 0.2, 1.0), (0.2, 0.8, 0.2, 1.0), (0.2, 0.4, 0.9, 1.0))
    for face in range(6):
        n = frames.FACE_NORMAL[face]
        centre = (ox + n[0] * h, oy + n[1] * h, oz + n[2] * h)
        name = getattr(props, f"connector_{face}")
        if name:
            a = frames.arrow(face, getattr(props, f"offset_{face}"))
            tip = (centre[0] + a[0] * h * 0.8, centre[1] + a[1] * h * 0.8, centre[2] + a[2] * h * 0.8)
            colour = axis_colour[face // 2]
            coords += [centre, tip]
            colours += [colour, colour]
            # Arrow head: two short lines back from the tip.
            v = frames.FACE_V[face] if a == frames.FACE_U[face] or a == tuple(-c for c in frames.FACE_U[face]) else frames.FACE_U[face]
            for sign in (1, -1):
                back = (
                    tip[0] - a[0] * h * 0.2 + sign * v[0] * h * 0.1,
                    tip[1] - a[1] * h * 0.2 + sign * v[1] * h * 0.1,
                    tip[2] - a[2] * h * 0.2 + sign * v[2] * h * 0.1,
                )
                coords += [tip, back]
                colours += [colour, colour]
        if getattr(props, f"terminator_{face}"):
            out = (centre[0] + n[0] * h * 0.3, centre[1] + n[1] * h * 0.3, centre[2] + n[2] * h * 0.3)
            coords += [centre, out]
            colours += [(1.0, 1.0, 1.0, 1.0)] * 2
    return coords, colours


def _draw() -> None:
    """The draw callback."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    context = bpy.context
    coords, colours = _lines(context)
    if context.scene is not None and context.scene.lamdafunction.show_field and context.scene.lamdafunction.envelope_mode == "SURFACE":
        coords = coords + _FIELD[0]
        colours = colours + _FIELD[1]
    if not coords:
        return
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": coords, "color": colours})
    gpu.state.line_width_set(2.0)
    shader.bind()
    batch.draw(shader)
    gpu.state.line_width_set(1.0)


def register() -> None:
    """Add the draw handler (not in background mode, where there is no viewport)."""
    global _HANDLE
    if bpy.app.background or _HANDLE is not None:
        return
    _HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister() -> None:
    """Remove the draw handler."""
    global _HANDLE
    if _HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLE, "WINDOW")
        _HANDLE = None
