# SPDX-License-Identifier: MIT OR Apache-2.0
"""The reference solver: the same algorithm as ``lamda-core``, in plain Python.

It exists for two reasons (``docs/PLAN.md``, "Testing and verification"):

- as the **test oracle**: the differential tests in ``tests/python/`` solve
  thousands of random small assemblies here and in ``lamda_core`` and require
  bit-identical results;
- as the **fallback** inside the Blender extension when the native wheel
  cannot load on a platform.

Every rule it follows is written down in ``.claude/rules/solver-core.md``.
Each module names the Rust file it mirrors, so a difference can be traced
line by line:

- :mod:`pcg32` mirrors ``core/lamda-core/src/rng.rs``
- :mod:`assembly` mirrors ``assembly.rs`` (loading and checks)
- :mod:`solver` mirrors ``graph.rs``, ``adjacency.rs``, ``propagate.rs``,
  ``entropy.rs``, ``observe.rs``, ``trail.rs``, ``solve.rs`` and ``attempts.rs``
- :mod:`validate` mirrors ``validate.rs``

Domains are Python integers used as bitsets (bit ``v`` set means variant
``v`` is still allowed), which makes AND, OR and subset tests one operation
each, exactly like the ``u64`` words in Rust. NumPy is used for the result
arrays, never for solver arithmetic: every sum is a left-to-right loop in
ascending variant order, so that floating point matches Rust.
"""

from __future__ import annotations

from .assembly import Assembly, load_assembly
from .solver import CanonicalError, CanonicalReport, SolveResult, Solver, solve
from .validate import validate

__all__ = [
    "Assembly",
    "CanonicalError",
    "CanonicalReport",
    "SolveResult",
    "Solver",
    "load_assembly",
    "solve",
    "validate",
]
