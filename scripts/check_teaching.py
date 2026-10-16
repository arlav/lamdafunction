#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0
"""Check every .rs file under core/ against .claude/rules/rust-teaching.md.

Fails (exit 1) if a file lacks a `//!` header, a "Rust in this file" list or a
"Read next" line, or if a public function in lamda-core has no `# Examples`
doc test. Run from the repository root: `python scripts/check_teaching.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"
PUB_FN = re.compile(r"^\s*pub(?:\([^)]*\))?\s+(?:const\s+)?fn\s+(\w+)")


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    problems: list[str] = []
    if not any(line.startswith("//!") for line in lines[:5]):
        problems.append("no `//!` file header in the first five lines")
    if "Rust in this file" not in text:
        problems.append('no "Rust in this file" list')
    if "Read next" not in text:
        problems.append('no "Read next" line')
    if "SPDX-License-Identifier" not in lines[0]:
        problems.append("no SPDX line on line 1")
    if "lamda-core" in path.parts:
        problems.extend(check_examples(lines))
    return problems


def check_examples(lines: list[str]) -> list[str]:
    """Every `pub fn` outside the test module needs `# Examples` in its docs."""
    problems: list[str] = []
    in_tests = False
    for number, line in enumerate(lines):
        if line.strip() == "#[cfg(test)]":
            in_tests = True
        if in_tests:
            continue
        match = PUB_FN.match(line)
        if not match:
            continue
        # Walk back over doc comments and attributes.
        docs: list[str] = []
        i = number - 1
        while i >= 0 and (lines[i].strip().startswith("///") or lines[i].strip().startswith("#[")
                          or lines[i].strip().startswith("//")):
            docs.append(lines[i])
            i -= 1
        if not any("# Examples" in d for d in docs):
            problems.append(f"line {number + 1}: `pub fn {match.group(1)}` has no `# Examples` doc test")
    return problems


def main() -> int:
    failed = False
    for path in sorted(CORE.rglob("*.rs")):
        if "target" in path.parts:
            continue
        problems = check_file(path)
        for problem in problems:
            failed = True
            print(f"{path.relative_to(ROOT)}: {problem}")
    if failed:
        return 1
    print("teaching checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
