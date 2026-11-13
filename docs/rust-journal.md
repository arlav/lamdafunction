# Rust journal

Theo's record of learning Rust through LamdaFunction. Claude Code appends one entry after every task that touches Rust; newest entry at the bottom. Theo can add his own notes under any entry.

## Entry format

```
## YYYY-MM-DD · <task, e.g. "Learning path step 1: rng.rs">

Files: core/lamda-core/src/rng.rs, ...

Concepts introduced
- <concept>: <one-line meaning> (rng.rs:42) · Rust Book ch. N

Compiler errors met
- `error[E0499]: cannot borrow ... as mutable more than once`: what it meant, and the fix.

Exercise
- <a small change to try>, checked by `cargo test <name>` or by the compiler.
```

---

## 2026-10-15 · Learning path steps 1 to 14: `lamda-core`, `lamda-cli`, `lamda-py`

Built in one session at Theo's request ("build it full speed ahead"), one file
per step, tests run after each. Read the files in the order below; each header
lists the Rust it introduces and names the next file.

Files: `core/lamda-core/src/{rng,bitset,ids,assembly,graph,adjacency,progress,propagate,entropy,observe,trail,solve,attempts,validate,synthetic}.rs`,
`core/lamda-core/tests/office.rs`, `core/lamda-core/benches/propagate.rs`,
`core/lamda-cli/src/main.rs`, `core/lamda-py/src/lib.rs`, `Cargo.toml` (workspace).

Concepts introduced
- Structs, `impl`, `&mut self`, wrapping arithmetic, `as` truncation, doc tests (rng.rs:52, rng.rs:113) · Rust Book ch. 5, 3.2
- Ownership and moves, `&` vs `&mut`, slices, iterators, `impl Iterator`, operator traits, `Iterator` impl (bitset.rs:58, bitset.rs:250, bitset.rs:395) · ch. 4, 13, 19
- Newtypes, `Copy`, `From`/`Into`, derived `Ord`, `Display`, `macro_rules!` (ids.rs:40, ids.rs:190) · ch. 10, 20
- serde derive, `Result` and `?`, `thiserror` with `#[from]`, enums with data, `Option` fields, `impl AsRef<Path>` (assembly.rs:45, assembly.rs:308) · ch. 9, 6
- Lifetimes on a method returning a slice, written explicitly once (graph.rs:234) · ch. 10.3
- Nested `Vec<Option<Vec<Bitset>>>`, `as_deref`, `let .. else`, `cfg!(debug_assertions)` (adjacency.rs:88, adjacency.rs:160) · ch. 6, 8
- Atomics and interior mutability through `&self` (progress.rs:33) · ch. 16
- `VecDeque`, closures as `FnMut` parameters, `mem::replace` (propagate.rs:171, propagate.rs:224) · ch. 8, 13
- `f64` summation order, `libm::log`, integer keys, derived `Ord` field order (entropy.rs:134, entropy.rs:233) · ch. 3.2
- `BinaryHeap` with `Reverse`, lazy deletion by version, `loop` (observe.rs:44, observe.rs:150) · ch. 8
- Moves out of a `Vec`, `mem::take`, `sort_unstable` + `dedup` (trail.rs:176, trail.rs:243) · ch. 4, 8
- Traits, generics with bounds, a struct holding a reference (`Solver<'a>`), destructuring to borrow fields separately (solve.rs:40, solve.rs:188, solve.rs:383) · ch. 10
- `rayon` parallel iterators, `Send`/`Sync`, `Arc` (attempts.rs:37, attempts.rs:84) · ch. 16
- `HashSet` of tuples, `&dyn Fn`, `binary_search` (validate.rs:132, validate.rs:170) · ch. 8.3, 17
- `std::env::args`, `Box<dyn Error>`, slice patterns, collecting `Result`s (lamda-cli/src/main.rs:43, 104, 150) · ch. 12
- PyO3 `#[pymodule]`, `#[pyclass(frozen)]`, `#[getter]`, `allow_threads`, NumPy arrays (lamda-py/src/lib.rs:50, 180) · pyo3.rs

Compiler errors met
- `error: literal out of range for i32` on `0xa15c_02b7 as f64` in a doc test: an unsuffixed hex literal defaults to `i32`, and this value needs 32 unsigned bits. Fix: `0xa15c_02b7_u32`.
- clippy `needless_lifetimes` on `fn edges<'a>(&'a self, ..) -> &'a [Edge]`: elision would infer the same lifetime. Kept the explicit form as the lesson with `#[allow(clippy::needless_lifetimes)]` and a comment (graph.rs:230).
- clippy `unnecessary_map_or`: `opt.map_or(false, f)` is spelled `opt.is_some_and(f)` since Rust 1.70 (bitset.rs:200).
- clippy `missing_docs_in_private_items` on a `macro_rules!`: macros need `///` docs like any item (ids.rs:186).
- rustdoc `bare_urls` with `-D warnings`: every URL in a doc comment must be `<https://...>` to become a link.
- `error[E0659]: lamda_core is ambiguous` in lamda-py: the `#[pymodule] fn lamda_core` shadowed the crate `lamda_core`. Fix: `#[pymodule(name = "lamda_core")] fn lamda_py_module`. Then `E0618` because PyO3's expansion itself names a local `module`, so the function cannot be called `module` either.
- `error[E0464]: multiple candidates for rlib dependency lamda_core`: the bindings' `[lib] name` was also `lamda_core`, so doctests saw two crates with one name. Fix: Rust lib `lamda_py`, Python module name set by maturin and `#[pymodule(name)]`.

Exercise
- In `bitset.rs`, add `pub fn is_disjoint(&self, other: &Bitset) -> bool` with a doc test, using `is_subset_of` and `intersects` as models. `cargo test -p lamda-core bitset` and `python scripts/check_teaching.py` must both pass (the second will fail until the `# Examples` block exists).
- Harder: make `Propagator::propagate` generic over `D: Domain` (the trait in `solve.rs`) instead of `Bitset`. The compiler will tell you which trait methods are missing; add them to the trait.

---

## 2026-10-16 · Spikes, reference solver, compiler and add-on (one Rust change)

Files: `core/lamda-core/src/synthetic.rs` (only Rust change; the rest of the day was Python: `addon/`, `reference/`, `tests/blender/`).

Concepts introduced
- None new. `BenchSpec::default()` now uses 40 extra pairs, matching `lamda-cli bench-gen`; the field doc explains why (synthetic.rs:38). A `Default` impl is the Rust home for "the sensible value", so a doc comment on the field is where a Python reader looks for the default's reasoning.

Compiler errors met
- None.

Exercise
- The Python reference (`reference/solver.py`) mirrors `solve.rs` function by function. Read `Solver::run_loop` in Rust next to `Solver._run_loop` in Python: every `let .. else`, `match` and `while let` has a Python twin. Then run `.venv/bin/python -m pytest tests/python/test_differential.py -q`: it solves 2,300 random assemblies in both and compares bit for bit. Change one `<` to `<=` in `_choose_variant` and watch which cases diverge first.

---

## 2026-11-13 · Phase 2, S1: edge types (port, neighbour port, twist), schema 1.1

Files: `core/lamda-core/src/ids.rs`, `assembly.rs`, `adjacency.rs`, `graph.rs`, `propagate.rs`, `solve.rs`, `validate.rs`, `synthetic.rs`. Python twins in `reference/assembly.py`, `reference/solver.py`, `reference/validate.py`; new turned-frame cases in `tests/python/test_differential.py`.

Why: on a surface shell each slot's frame turns from face to face, so slot A's +X can meet slot B's +Y. A table keyed by (port, twist) assumed the neighbour always shows the opposite port. Now every table is keyed by the edge's kind, `EdgeType { port, neighbour_port, twist }`, and a 1.0 file reads as before because a missing `neighbour_port` means "the opposite port".

Concepts introduced
- A struct with named fields used as a value and a key, and field init shorthand `EdgeType { port, .. }` (ids.rs:195, ids.rs:225). WHY a struct and not a tuple: both ports are `PortId`, so a tuple would let them be swapped silently.
- `impl fmt::Display` with an elided lifetime `Formatter<'_>`, so errors print `port 0 to 2 twist 1` (ids.rs:272).
- A fixed-size array constant `[&str; 2]` and `contains(&x.as_str())` (assembly.rs:49, assembly.rs:394).
- serde field attributes `#[serde(default, skip_serializing_if = "Option::is_none")]`: a missing key reads as `None`, and `None` is left out when writing, so 1.0 files round-trip byte-shape unchanged (assembly.rs:173).
- `match` on an `Option` to fill a default (assembly.rs:196); `if let Some(q)` for an optional range check.
- A type alias, `type PairKey = (u8, u8, u8, u32, u32)` (validate.rs:37): a second name, not a new type.
- `serde_json::Value` indexing in a test, `value["allowed"][0].get("neighbour_port")` (assembly.rs, test `box_grid_tables_write_no_neighbour_port`).

Compiler errors met
- `error[E0425]: cannot find function, tuple struct or tuple variant PortId in this scope` at solve.rs:804, in the tests. `use super::*` gives the test module everything the parent module imported. After `solve.rs` stopped importing `PortId` (edges are named by `EdgeType` now), the test that builds a `BoundaryPort { port: PortId(1), .. }` lost it. Fix: `use crate::ids::PortId;` in `mod tests` (solve.rs:766). Lesson: `super::*` imports the parent's *imports* too, so removing an unused-looking import can break the tests.

Numbers
- Spike 4 study (18,900 slots × 400 variants), old and new cores alternated on the same file: 0.65 to 0.86 s per attempt for both, so the wider key costs nothing measurable. (This machine ran slower than on 2026-10-16, when spike 4 measured about 0.4 s.)
- Differential: 1,700 new turned cases, 84 % of edges joining non-opposite ports, 75 % with a nonzero twist, 4,370 edges whose two ports are equal (a table that is its own reverse). Rust and Python identical on all.

Exercise
- In `ids.rs`, add `pub fn is_box_grid(self) -> bool` to `EdgeType`: true when `neighbour_port == port.opposite()`, with a doc test showing one true and one false case. Then use it in `adjacency.rs`'s doc comment example for `table`. `cargo test -p lamda-core ids` and `python scripts/check_teaching.py` must pass.
