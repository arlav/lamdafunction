# SPDX-License-Identifier: GPL-3.0-or-later
"""Run every ``tests/blender/test_*.py`` inside one Blender process.

Usage, from the repository root::

    blender -b --factory-startup --python-exit-code 1 --python tests/blender/run_all.py

Each test file is a plain script: it runs top to bottom and raises on
failure. ``--python-exit-code 1`` makes Blender exit with status 1 when this
script raises, which it does at the end if any test failed; without that flag
Blender exits 0 whatever happened.
"""

from __future__ import annotations

import runpy
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    """Run the tests in name order and raise if any failed."""
    failures: list[str] = []
    tests = sorted(HERE.glob("test_*.py"))
    for test in tests:
        print(f"=== {test.name}")
        try:
            runpy.run_path(str(test), run_name="__main__")
        except Exception:  # noqa: BLE001 - report and keep going
            traceback.print_exc()
            failures.append(test.name)
    print(f"=== {len(tests) - len(failures)} of {len(tests)} test files passed")
    if failures:
        raise SystemExit(f"failed: {', '.join(failures)}")


if __name__ == "__main__":
    # Blender passes its own arguments to sys.argv; nothing here reads them.
    sys.stdout.flush()
    main()
