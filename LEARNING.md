# Learning Rust with LamdaFunction

The Rust in this repository is written to be read. Every file in `core/` doubles as a chapter of a small textbook: it explains the Rust it uses, the algorithm it implements, and why it was written that way. If you know Python and want to learn Rust on real code, rather than on toy examples, this page tells you how to use it.

You do not need to care about Blender, or about wave function collapse, to follow along. The solver is a self-contained library with its own tests, and the comments explain the algorithm as they go.

## Contents

- [What you need](#what-you-need)
- [How the code teaches](#how-the-code-teaches)
- [The reading order](#the-reading-order)
- [How to work through a file](#how-to-work-through-a-file)
- [Python next to Rust](#python-next-to-rust)
- [Exercises](#exercises)
- [Keeping a journal](#keeping-a-journal)
- [The guard rails](#the-guard-rails)
- [Further reading](#further-reading)

## What you need

- **Rust**, through [rustup](https://rustup.rs). The workspace needs Rust 1.85 or newer, edition 2024.
- **An editor with rust-analyzer** (VS Code, Zed, RustRover, Helix, …). Hovering over a variable to see its inferred type is half of learning Rust.
- **Python 3.11+**, optional, if you want to run the Python reference solver next to the Rust one.

Then, from the repository root:

```bash
cargo test -p lamda-core          # builds the library and runs every unit, property and doc test
cargo doc -p lamda-core --open    # the API documentation, generated from the same comments you will read
```

If both work, you are set up.

## How the code teaches

Comments come in three layers.

**1. File headers (`//!`).** Every `.rs` file opens with:
- what the module does and where it sits in the pipeline (compiled assembly → slot graph → adjacency tables → propagate and observe → result arrays);
- a **"Rust in this file"** list: each language concept the file uses *for the first time in the codebase*, with a one-line meaning and a link to The Rust Book or Rust by Example;
- a **"Read next"** line naming the following file.

**2. Item docs (`///`).** Every struct, field, enum, function and constant says what it is for, what its parameters and return value mean, what it costs in big-O (and what `n` is), and when it can fail (`# Errors`, `# Panics`). Every public function has an `# Examples` block, which is a real test: `cargo test` compiles and runs it.

**3. Line comments, tagged.** Inside function bodies, comments start with a tag so you can follow one thread at a time:

| Tag | What it explains |
| --- | --- |
| `// RUST:` | what the language is doing on this line: a borrow, a move, a cast, a `?`, a lifetime |
| `// WFC:` | which step of the algorithm this is and what it means |
| `// PERF:` | why this is fast, or what it costs |
| `// WHY:` | the design decision, and the alternative that was rejected |
| `// PY:` | the nearest Python equivalent, when that makes the Rust click |

The first time a concept appears in a file it gets a full explanation of a few lines; later occurrences get a short reminder that points back to it. Concepts explained in an earlier file get a one-line reminder with a pointer to that file.

Because the tags are plain text, `grep` turns them into study guides:

```bash
grep -n "// RUST:" core/lamda-core/src/bitset.rs     # just the language lessons in one file
grep -rn "// PY:" core/lamda-core/src                 # every Python comparison in the crate
grep -rn "lifetime" core/lamda-core/src               # find where a concept is discussed
```

[docs/rust-concepts.md](docs/rust-concepts.md) is the index: one row per concept, the file and item where it is first explained, and the matching chapter of The Rust Book.

## The reading order

Read the files in this order. Each one adds a handful of concepts to the ones before it, and each file's "Read next" line keeps you on the path. Paths are under `core/lamda-core/src/` unless noted.

| Step | File | What it does | Rust it introduces |
| --- | --- | --- | --- |
| 0 | `lib.rs` | The crate root: lists the modules, sets crate-wide lints | crate attributes, `pub mod`, `cfg_attr` |
| 1 | `rng.rs` | PCG32, a small deterministic random number generator | `struct`, `impl`, fixed-width integers, wrapping arithmetic, `as` casts, `#[derive]`, unit tests, doc tests |
| 2 | `bitset.rs` | A set of variants stored as `Vec<u64>`, one bit each | ownership and moves, `&` and `&mut` borrows, slices, iterators, implementing `Iterator`, operator traits, `debug_assert!` |
| 3 | `ids.rs` | `SlotId`, `VariantId`, `PortId`, `Twist`: typed indices | newtypes, `Copy`, `From`/`Into`, derived `Ord`, `macro_rules!` |
| 4 | `assembly.rs` | Loads the problem from JSON | `serde`, `Result` and `?`, `Option`, enums with data, `match`, `thiserror`, `impl AsRef<Path>` |
| 5 | `graph.rs` | The slot graph in compressed (CSR) arrays | lifetimes on methods that return slices, `Vec::with_capacity` |
| 6 | `adjacency.rs` | Which variants may sit next to which, per port and twist | nested `Vec`s, `as_deref`, `let … else`, `cfg!(debug_assertions)` |
| 6b | `progress.rs` | Counters and a cancel flag shared with another thread | atomics, interior mutability |
| 7 | `propagate.rs` | AC-3 constraint propagation | `VecDeque`, `while let`, closures as `FnMut` parameters, `mem::replace` |
| 8 | `entropy.rs`, `observe.rs` | Choosing which slot to decide next | `f64` and summation order, derived `Ord` field order, `BinaryHeap`, `Reverse`, `loop` |
| 9 | `trail.rs` | The undo log that makes backtracking possible | moving values out of a `Vec`, `mem::take` |
| 10 | `solve.rs` | The main loop, behind a `Domain` trait | traits, generics with bounds, a struct holding a reference, destructuring to borrow fields separately |
| 11 | `attempts.rs` | Several attempts in parallel | `rayon`, `Send`, `Sync`, `Arc` |
| 12 | `validate.rs`, then `synthetic.rs` | An independent checker; benchmark problems with a planted solution | `HashSet` of tuples, `&dyn Fn`, `binary_search`; `Default`, `BTreeSet` |
| 13 | `core/lamda-py/src/lib.rs` | Python bindings | PyO3 modules, classes and functions, NumPy arrays, releasing the GIL |
| 14 | `core/lamda-cli/src/main.rs` | A command-line tool | `std::env::args`, `Box<dyn Error>`, slice patterns, collecting `Result`s |

After step 12, read `core/lamda-core/tests/office.rs` (an integration test that sees only the public API) and `core/lamda-core/benches/propagate.rs` (criterion benchmarks). The workspace `Cargo.toml` is commented too: every dependency, feature and profile setting says what it does and why.

Steps 1 to 4 are the foundation. If you only have a weekend, read those four and do their exercises.

## How to work through a file

1. **Read the header**, especially the "Rust in this file" list, and open the linked Book chapter for anything new.
2. **Read top to bottom.** When a `// RUST:` comment explains something, hover over the names nearby in your editor and check that the types are the ones the comment says.
3. **Run that file's tests.** Test names include the module path, so a filter is enough:
   ```bash
   cargo test -p lamda-core bitset        # unit tests and doc tests of bitset.rs
   cargo test -p lamda-core --doc rng     # only the doc tests of rng.rs
   ```
4. **Break something on purpose.** Remove a `&`, add a `.clone()`, change a `u32` to `usize`, delete a `mut`. Read the compiler error in full. Rust's errors are long because they teach; `rustc --explain E0502` gives the long form of any error code.
5. **Put it back** and make sure the tests pass again (`git diff` shows what you touched; `git checkout -- <file>` undoes it).
6. **Do an exercise** (below) and note what you learned (see [Keeping a journal](#keeping-a-journal)).

## Python next to Rust

`reference/` holds the same solver written in Python and NumPy, function for function. The two implementations must give bit-identical results, and `tests/python/test_differential.py` checks that on thousands of random problems. This makes the repository a Rosetta Stone:

- Read `core/lamda-core/src/solve.rs` with `reference/solver.py` open beside it; `rng.rs` with `reference/pcg32.py`; `assembly.rs` with `reference/assembly.py`; `validate.rs` with `reference/validate.py`.
- Every `match`, `let … else`, `while let` and `?` has a Python twin, usually an `if`/`elif`, an early `return`, a `while` loop or an exception.
- Where the Rust has to be explicit about something Python hides (who owns this list? can this function change it? can this integer overflow?), the `// PY:` comments say what Python would do instead.

To run the differential tests, set up Python and build the bindings once:

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pytest maturin
.venv/bin/python -m maturin develop -m core/lamda-py/Cargo.toml
.venv/bin/python -m pytest tests/python -q
```

Change one comparison in the Python reference (a `<` to `<=` in the variant choice, say) and watch which cases diverge first. Then put it back.

## Exercises

Each entry in [docs/rust-journal.md](docs/rust-journal.md) ends with exercises tied to the code it describes. Good exercises can be checked by the compiler or a test, so you know when you are done. Some to start with:

- **`rng.rs`**: write a doc test that draws ten numbers from two generators with the same seed and asserts they are equal. Then make one generator `let` instead of `let mut` and read the error.
- **`bitset.rs`**: add `pub fn is_disjoint(&self, other: &Bitset) -> bool`, using `is_subset_of` and `intersects` as models, with an `# Examples` doc test.
- **`ids.rs`**: try to pass a `SlotId` where a `VariantId` is expected and read the error. That error is the whole point of newtypes.
- **`assembly.rs`**: load a JSON file with a field missing, then with a field of the wrong type, and compare the two error messages with the `LoadError` variants.
- **`propagate.rs`**, harder: make `Propagator::propagate` generic over `D: Domain` (the trait in `solve.rs`) instead of `Bitset`. The compiler will tell you which trait methods are missing.

After any change, the full check is:

```bash
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
python scripts/check_teaching.py      # every file still has its header, its "Rust in this file" list and its doc tests
```

## Keeping a journal

[docs/rust-journal.md](docs/rust-journal.md) records how this code was learned, one entry per task. Its most useful part is **"Compiler errors met"**: the exact error, what it meant, and the fix. Those are the lessons a book cannot give you, because they come from your own code.

If you work through the repository yourself, keep your own journal in the same shape:

```
## YYYY-MM-DD · <what you did>

Files: ...

Concepts introduced
- <concept>: <one-line meaning> (file.rs:line) · Rust Book ch. N

Compiler errors met
- `error[E0499]: cannot borrow ... as mutable more than once`: what it meant, and the fix.

Exercise
- <a small change to try>, checked by `cargo test <name>` or by the compiler.
```

## The guard rails

The crate settings are part of the lesson: they show what careful Rust looks like and they stop you from taking shortcuts.

- `#![forbid(unsafe_code)]`: no `unsafe` anywhere in `lamda-core`. Everything is checked by the compiler.
- `#![deny(missing_docs)]` plus clippy's `missing_docs_in_private_items`: every item must be documented, or the build fails.
- No `unwrap()` or `expect()` outside tests. Library code returns `Result` with a `thiserror` enum and lets the caller decide. Clippy enforces this.
- Clippy runs with `-D warnings`, so every lint is an error. When clippy suggests a different idiom, the comment usually says why it was taken, or why it was not.
- `scripts/check_teaching.py` fails the build if a file loses its header, its "Rust in this file" list or its "Read next" line, or if a public function has no `# Examples` block.
- Determinism: the solver must give the same answer on every OS and in both languages. That is why the random number generator and the bitset are hand-written (not taken from crates), why floating-point sums run in a fixed order, and why `libm::log` is used instead of the platform's `ln`. Look for `// WHY:` comments near those spots.

## Further reading

- [The Rust Programming Language](https://doc.rust-lang.org/book/), "the Book". The concepts index points to its chapters.
- [Rust by Example](https://doc.rust-lang.org/rust-by-example/), the same ground through short programs.
- [The standard library docs](https://doc.rust-lang.org/std/). Every type in them has examples you can run.
- [Clippy's lint list](https://rust-lang.github.io/rust-clippy/master/), to look up any lint by name.
- [PyO3 user guide](https://pyo3.rs), for step 13.
- [docs/PLAN.md](docs/PLAN.md), the design of the whole project, for the `// WFC:` side of the comments.
