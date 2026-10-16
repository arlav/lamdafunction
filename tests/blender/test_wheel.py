# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike 3: the ``lamda_core`` abi3 wheel loads in Blender's Python and solves on a worker thread.

Steps:

1. Unpack ``addon/wheels/lamda_core-*.whl`` into a temporary directory and put
   it on ``sys.path`` (a wheel is a zip; nothing is installed into Blender).
2. Solve the office fixture and compare with the golden seed-1 result.
3. Solve the spike-4 study kit (if ``target/release/lamda-cli`` exists to
   generate it) on a worker thread while the main thread polls progress: the
   counters must move before the solve ends, which proves the GIL is released.
4. Cancel a long solve from the main thread and check it returns promptly.

Runs headless: ``blender -b --factory-startup --python-exit-code 1 --python
tests/blender/test_wheel.py``. Also used by ``run_all.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SEED_1 = [0, 1, 2, 0, 1, 2, 0, 3, 4, 5, 3, 4, 5, 6]


def load_wheel() -> object:
    """Unpack the newest wheel in ``addon/wheels`` onto ``sys.path`` and import it."""
    wheels = sorted((ROOT / "addon" / "wheels").glob("lamda_core-*.whl"))
    if not wheels:
        raise SystemExit("no wheel in addon/wheels: run maturin build --release -m core/lamda-py/Cargo.toml --out addon/wheels")
    wheel = wheels[-1]
    target = Path(tempfile.mkdtemp(prefix="lamda_core_wheel_"))
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
    sys.path.insert(0, str(target))
    import lamda_core  # noqa: PLC0415 - imported after the path is set up

    print("wheel:", wheel.name)
    print("lamda_core", lamda_core.version(), "in Blender", bpy.app.version_string, "Python", sys.version.split()[0])
    return lamda_core


def office_text() -> str:
    """The office south strip fixture as JSON text."""
    return (ROOT / "tests" / "fixtures" / "office_south_strip.json").read_text(encoding="utf-8")


def check_office(lamda_core: object) -> None:
    """The golden fixture solves to the pinned result and validates."""
    result = lamda_core.solve_json(office_text())
    assert result.report["outcome"] == "solved", result.report
    assert list(result.variant) == GOLDEN_SEED_1, list(result.variant)
    assert lamda_core.validate_json(office_text(), result.variant, result.state) == []
    print("office fixture: solved, golden result, validated")


def study_kit(extra: int) -> str | None:
    """Generate the 30 x 30 x 21 study kit with ``lamda-cli bench-gen``, or ``None`` without the binary."""
    cli = ROOT / "target" / "release" / "lamda-cli"
    if not cli.exists():
        print("no target/release/lamda-cli: skipping the study-kit threading checks")
        return None
    out = Path(tempfile.mkdtemp(prefix="lamda_study_")) / f"study_{extra}.json"
    subprocess.run(
        [str(cli), "bench-gen", "--slots", "30x30x21", "--variants", "400", "--seed", "1", "--extra", str(extra), "--out", str(out)],
        check=True,
    )
    return out.read_text(encoding="utf-8")


def solve_in_thread(lamda_core: object, text: str, progress: object) -> tuple[threading.Thread, list[object]]:
    """Start a solve on a worker thread; the result lands in the returned list."""
    box: list[object] = []

    def work() -> None:
        box.append(lamda_core.solve_json(text, progress))

    thread = threading.Thread(target=work, name="lamda-solve", daemon=True)
    thread.start()
    return thread, box


def check_progress_while_solving(lamda_core: object, text: str) -> None:
    """The main thread sees the counters move while the worker solves."""
    progress = lamda_core.Progress()
    started = time.perf_counter()
    thread, box = solve_in_thread(lamda_core, text, progress)
    samples: list[int] = []
    while thread.is_alive():
        samples.append(progress.snapshot()["observations"])
        time.sleep(0.01)
    thread.join()
    elapsed = time.perf_counter() - started
    result = box[0]
    distinct = sorted(set(samples))
    print(f"study kit: {result.report['outcome']} in {elapsed:.2f} s, {len(samples)} polls, observations seen: {distinct[:3]} .. {distinct[-2:]}")
    assert result.report["outcome"] == "solved", result.report
    assert len(distinct) > 2, "progress counters never moved while the worker was solving: is the GIL released?"


def check_cancel(lamda_core: object, text: str) -> None:
    """A long solve stops within a fraction of a second of cancel()."""
    doc = json.loads(text)
    doc["settings"]["backtrack_budget"] = 1_000_000_000
    doc["settings"]["attempts"] = 1
    progress = lamda_core.Progress()
    thread, box = solve_in_thread(lamda_core, json.dumps(doc), progress)
    time.sleep(0.5)
    assert thread.is_alive(), "the tight kit solved before cancel could be tested"
    cancelled_at = time.perf_counter()
    progress.cancel()
    thread.join(timeout=5.0)
    latency = time.perf_counter() - cancelled_at
    assert not thread.is_alive(), "solve did not stop after cancel()"
    result = box[0]
    print(f"cancel: outcome {result.report['outcome']} after {latency * 1000:.0f} ms, {progress.snapshot()['backtracks']} backtracks")
    assert result.report["outcome"] == "cancelled"
    assert latency < 1.0


def main() -> None:
    """Run the four checks."""
    lamda_core = load_wheel()
    check_office(lamda_core)
    solvable = study_kit(40)
    if solvable is not None:
        check_progress_while_solving(lamda_core, solvable)
    tight = study_kit(8)
    if tight is not None:
        check_cancel(lamda_core, tight)
    print("spike 3: wheel loads, solves off the main thread, reports progress, cancels")


if __name__ == "__main__":
    main()
