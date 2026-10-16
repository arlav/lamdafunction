# SPDX-License-Identifier: GPL-3.0-or-later
"""Pick the solver: ``lamda_core`` (Rust wheel) or the vendored Python reference.

Both expose the same three calls to the operators: ``canonicalise(text)``,
``solve(text, progress)`` and a progress object with ``snapshot()`` and
``cancel()``. The reference cannot be cancelled mid-attempt and reports no
live counters; the status bar says so.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol

import numpy as np


class ResultLike(Protocol):
    """What a solve returns, in either backend."""

    variant: Any
    module: Any
    rotation: Any
    part: Any
    anchor: Any
    state: Any
    options_left: Any
    collapse_step: Any
    report: dict[str, Any]


class _NoProgress:
    """Progress stand-in for the reference backend."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        """Remembered, but the reference only honours it between attempts."""
        self.cancelled = True

    def snapshot(self) -> dict[str, Any]:
        """No live counters."""
        return {"observations": 0, "collapsed": 0, "backtracks": 0, "attempt": 0, "cancelled": self.cancelled}


@dataclass
class Backend:
    """The chosen solver and its name for the UI."""

    name: str
    core: ModuleType | None
    reference: ModuleType | None

    def new_progress(self) -> Any:
        """A progress object for one solve."""
        if self.core is not None:
            return self.core.Progress()
        return _NoProgress()

    def canonicalise(self, text: str) -> dict[str, Any]:
        """The canonicalisation report as a dict."""
        if self.core is not None:
            return self.core.canonicalise_json(text)
        assert self.reference is not None
        try:
            return self.reference.Solver(self.reference.load_assembly(text)).report.as_dict()
        except self.reference.CanonicalError as error:
            return error.report.as_dict()

    def solve(self, text: str, progress: Any) -> ResultLike:
        """Solve; arrays come back as NumPy arrays in both cases."""
        if self.core is not None:
            return self.core.solve_json(text, progress)
        assert self.reference is not None
        return self.reference.solve(self.reference.load_assembly(text))

    def validate(self, text: str, variant: np.ndarray, state: np.ndarray) -> list[str]:
        """Violations of a result, as messages."""
        if self.core is not None:
            return self.core.validate_json(text, np.ascontiguousarray(variant, dtype=np.int32), np.ascontiguousarray(state, dtype=np.uint8))
        assert self.reference is not None
        return self.reference.validate(self.reference.load_assembly(text), variant, state)


def _import_optional(name: str) -> ModuleType | None:
    """Import a module or return ``None``."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def choose_backend() -> Backend:
    """``lamda_core`` when the wheel loads; else the reference, vendored or from the repository."""
    core = _import_optional("lamda_core")
    reference = _import_optional(f"{__package__.rsplit('.', 1)[0]}.vendor.lamda_reference") or _import_optional("reference")
    if core is not None:
        return Backend(f"lamda_core {core.version()}", core, reference)
    if reference is not None:
        return Backend("Python reference (no native wheel)", None, reference)
    raise ImportError("neither lamda_core nor the reference solver is available")
