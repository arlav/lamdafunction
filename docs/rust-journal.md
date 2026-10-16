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
