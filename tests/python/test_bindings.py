# SPDX-License-Identifier: MIT OR Apache-2.0
"""Smoke tests for the lamda_core bindings. Skipped unless the module is built
(`maturin develop -m core/lamda-py/Cargo.toml`)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

lamda_core = pytest.importorskip("lamda_core")

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "office_south_strip.json"


def office_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_version_is_a_string() -> None:
    assert isinstance(lamda_core.version(), str)


def test_office_solves_and_validates() -> None:
    result = lamda_core.solve_json(office_text())
    assert result.report["outcome"] == "solved"
    assert list(result.variant) == [0, 1, 2, 0, 1, 2, 0, 3, 4, 5, 3, 4, 5, 6]
    assert result.state.tolist() == [0] * 14
    assert lamda_core.validate_json(office_text(), result.variant, result.state) == []


def test_validate_reports_violations() -> None:
    result = lamda_core.solve_json(office_text())
    variant = result.variant.copy()
    variant[1], variant[2] = variant[2], variant[1]
    violations = lamda_core.validate_json(office_text(), variant, result.state)
    assert violations and "not allowed" in violations[0]


def test_canonicalise_reports_collapsed() -> None:
    report = lamda_core.canonicalise_json(office_text())
    assert report == {"contradiction": None, "dead_variants": [], "collapsed": 1}


def test_bad_json_is_value_error() -> None:
    with pytest.raises(ValueError):
        lamda_core.solve_json("{not json")


def test_cancel_from_another_thread() -> None:
    doc = json.loads(office_text())
    progress = lamda_core.Progress()
    progress.cancel()
    result = lamda_core.solve_json(json.dumps(doc), progress)
    assert result.report["outcome"] == "cancelled"
    assert progress.snapshot()["cancelled"] is True
    # The GIL is released during the solve, so another thread can run meanwhile.
    progress.reset()
    ticks: list[int] = []
    stop = threading.Event()

    def ticker() -> None:
        while not stop.is_set():
            ticks.append(1)

    thread = threading.Thread(target=ticker)
    thread.start()
    lamda_core.solve_json(json.dumps(doc), progress)
    stop.set()
    thread.join()
    assert ticks
