# LamdaFunction

Wave function collapse (WFC) for Blender: fill a mesh, or a SpaceTope layout, with a kit of modular parts. Named for the λ in λ = h/p. Owner: Theo Dounas, Adventurous Systems.

Theo is an architect and an experienced Python user who is **learning Rust through this project**. The Rust code is his textbook. **Before writing or editing any Rust, read `.claude/rules/rust-teaching.md`** (it also loads by itself once a `.rs` file is opened, but that can be after the first write). A Rust change without its teaching comments is not finished.

The full design lives in `docs/PLAN.md`; read the section a task touches before starting it. This file is the summary and the rules. Where `.claude/rules/` and `docs/PLAN.md` differ on a detail, the rules win: follow them and update PLAN.md to match.

## Current status

Update this block at the end of every session.

- Phase: 0 (spikes). Nothing built yet.
- Next: spike 2 (face frames and twist), and Rust learning path step 1 (`rng.rs`).
- Open questions for Theo: none yet.

## Non-negotiables

- **No "Monoceros" anywhere** in code, identifiers, UI strings, README, user docs, commit messages or package metadata. It may appear only in `docs/PLAN.md` as prior art. Work clean-room: generic WFC concepts are fine; never copy Monoceros text, UI, wording or code.
- **Licences.** `core/` and `reference/` are `MIT OR Apache-2.0`; `addon/` is `GPL-3.0-or-later`. Put an SPDX line at the top of every source file. Never move code from `addon/` into `core/` or `reference/` without asking Theo.
- **Borrowed code.** Do not copy from `bbstilson/wfc-rs` (no licence). Code adapted from `subdgtl/WFC` (Unlicense) or `mxgmn/WaveFunctionCollapse` (MIT) gets a note in `NOTICE.md`.
- **Determinism.** Same compiled assembly and seed give the same result on every OS, in Rust and in the Python reference. The exact rules are in `.claude/rules/solver-core.md`; read it before touching solver code in either language.
- **Safety.** `#![forbid(unsafe_code)]` in `lamda-core`. No `unwrap()` or `expect()` outside tests; return errors instead.
- **Phases.** Phase gates decide what ships in Blender. Do not start a phase until the previous gate passes and Theo says go. The Rust learning path below is a parallel track that starts in phase 0, because spikes 3 and 4 need its first steps.
- **Dependencies.** Ask before adding any crate or Python package. Pre-approved: `serde`, `serde_json` (with `float_roundtrip`), `thiserror`, `rayon`, `pyo3`, `numpy` (rust-numpy), `libm`, `proptest`, `criterion`; Python: `numpy`, `pytest`, `hypothesis`. Write the bitset and the RNG yourself: they are learning exercises and determinism-critical.

## Names

| Thing | Name |
| --- | --- |
| Product, Blender extension | LamdaFunction, extension id `lamdafunction` |
| Rust crates | `lamda-core` (library, lib name `lamda_core`), `lamda-py` (PyO3 bindings, lib name `lamda_py`), `lamda-cli` |
| Python package built from `lamda-py` | PyPI distribution `lamda-core`; maturin `module-name = "lamda_core"`, so Python imports `lamda_core` |
| Geometry Nodes assets | "LamdaFunction Library", "LamdaFunction Envelope", "LamdaFunction Inspect", "LamdaFunction Materialise" |
| Sidebar tab, module property group | "LamdaFunction", `lamdafunction` |
| Point attributes | `wfc_*` prefix (contract in `.claude/rules/blender-addon.md`) |

## Repository layout

```
lamdafunction/
  Cargo.toml                  workspace manifest; members are the crates in core/
  core/                       Rust crates, MIT OR Apache-2.0
    lamda-core/               load, adjacency bitsets, canonicalise, solve, validate
    lamda-py/                 PyO3 bindings -> PyPI wheel (abi3, Python >= 3.11)
    lamda-cli/                headless solves, benchmark generator, benchmarks
  reference/                  NumPy reference solver: test oracle and fallback
  addon/                      Blender extension, GPL-3.0-or-later
    blender_manifest.toml
    model/                    data model and schema helpers (no bpy)
    compile/                  rotation group, face frames, rule expansion, audit (no bpy)
    bl/                       properties, operators, panels, overlay, gizmos, handlers
    assets/lamda.blend        Materialise, Inspect, Envelope, Boundary node groups
    wheels/                   wheels copied unmodified from the PyPI build
  schema/                     compiled-assembly JSON schema, versioned
  examples/                   .blend kits: pipes, tubes, office, facade, house
  scripts/                    check_teaching.py, build_extension.py
  tests/python/  tests/blender/  tests/fixtures/
  docs/                       PLAN.md, spikes/, rust-journal.md, rust-concepts.md
```

## Architecture in one paragraph

Three layers, one contract. `addon/compile/` (Python) turns kits and envelopes into a **compiled assembly**: variants, allowed variant pairs per port and twist, the slot graph, initial domains, weights, priorities and fixed slots (exact contents in `.claude/rules/solver-core.md`). `lamda-core` (Rust) loads it, builds adjacency bitsets, canonicalises with one propagation pass, solves and returns plain arrays; `reference/` does the same in NumPy. The audit in Python reports compile problems plus the core's canonicalisation report. Nothing Blender-specific goes below the contract, so the CLI, tests and other hosts solve the same file.

## Commands

```bash
# Everything runs from the repository root.
# Rust: all of these before calling a Rust task done
cargo fmt --all
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace                               # unit, property and doc tests
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
python scripts/check_teaching.py                     # teaching-comment checks on every .rs file
cargo bench -p lamda-core                            # criterion; spike 4 and perf work only

# Python compiler and reference solver (no Blender needed)
python -m pytest addon/model addon/compile reference tests/python -q

# Bindings into the active virtualenv
maturin develop -m core/lamda-py/Cargo.toml

# Blender 5.2 LTS, headless; the exit code makes Python failures fail the run
blender -b --factory-startup --python-exit-code 1 --python tests/blender/run_all.py
python scripts/build_extension.py                    # vendors reference/, then runs blender --command extension build --split-platforms
```

If a command here is wrong or missing, fix this section in the same change.

## Build order and gates

**Phase 0, spikes (2 to 3 days each).** Spike code lives in the real tree and follows the teaching standard. Write findings to `docs/spikes/NN-name.md`: question, method, numbers, decision.
1. Collection Info with Separate Children sorts children by name. Confirm in 5.2 and fix a naming scheme (zero-padded index names in a hidden instancer collection) so instance index equals `wfc_module`.
2. Face-frame and twist spec with exhaustive tests: 24 rotations × 6 faces, and all 24 × 24 rotation pairs against a keyed pin-and-slot example. Before any other compiler code.
3. A minimal `lamda-py` abi3 wheel loads in Blender 5.2 on Windows, macOS and Linux, solves on a worker thread with the GIL released, reports progress and cancels. Pass: works on all three.
4. Bitset AC-3 benchmark on synthetic kits from `lamda-cli bench-gen` (planted solution, fixed seed). Pass: 19,000 slots × 400 variants fully propagates and solves in under 1 s single-threaded, release build. Record 2,000 and 16,000 variants; they decide whether sparse domains are needed.
5. `foreach_set` for 200,000 points × 8 attributes. Pass: under 0.5 s in total.

Gate: all five pass and Theo signs off.

**Phase 1, MVP (about 5 weeks).** Single-cell modules, connectors and rules, rotational freedom None/X/Y/Z/Full, box and mesh envelopes, boundary policy (free, or closed by a named connector such as "0"), compiled-assembly schema v1, Python reference solver with seeds and restart-on-contradiction attempts, Materialise node group, audit. Gate: a newcomer rebuilds the pipes kit from the docs alone.

**Phase 2, parity (about 8 weeks).** Rust core and wheels in the extension, backtracking, parallel attempts, multi-cell modules, connector twists and the full terminator UI, weights as attributes, growth mode with supply caps, partial re-solve, seed browser. Gate: the building study (`lamda-cli bench-gen --slots 30x30x21 --variants 400 --seed 1`) solves in under 1 s per attempt.

**Phase 3 and research track.** Global constraints (connectivity, support, counts, symmetry, separation), solving in blocks, learning rules from examples; SpaceTope importer, cell-complex envelopes, trilinear deformation, IFC through Bonsai. See `docs/PLAN.md`, "Beyond parity" and "Integration milestones".

## Rust learning path

Build `lamda-core` in this order so each file adds a few concepts to the last. Steps 1 to 3 and a minimal 7 feed spike 4; a minimal 13 feeds spike 3. Record each step in `docs/rust-journal.md`.

1. `rng.rs`: PCG32. Structs, `impl`, integer types, wrapping arithmetic, unit tests, doc tests.
2. `bitset.rs`: domains as `Vec<u64>`. Ownership, borrowing, `&mut`, slices, iterators, operator traits.
3. `ids.rs`: newtypes (`SlotId`, `VariantId`, `PortId`, `Twist`). `derive`, `Copy`, `From`, `Ord`.
4. `assembly.rs`: load the compiled assembly. `serde`, `Result`, `?`, `Option`, enums, `match`, `thiserror`.
5. `graph.rs`: slot graph in compressed (CSR) arrays. Lifetimes on methods that return slices.
6. `adjacency.rs`: tables per port and twist. Nested `Vec`s, indexing, `debug_assert!`.
7. `propagate.rs`: AC-3 with a `VecDeque`. Loops, early returns, error enums.
8. `entropy.rs`, `observe.rs`: entropy keys and the versioned heap. `f64`, `BinaryHeap`, `Ord`, `Reverse`.
9. `trail.rs`: undo log and backtracking. Moves, `std::mem::take` and `replace`.
10. `solve.rs`: the loop, with a `Domain` trait so sparse domains can come later. Traits, generics.
11. `attempts.rs`: parallel attempts. `rayon`, closures, `Send`, `Sync`, `Arc`, atomics.
12. `validate.rs`: independent result checker, written without reusing solver code.
13. `lamda-py`: PyO3 functions and classes, NumPy arrays, releasing the GIL.
14. `lamda-cli`: argument parsing with `std::env`, file IO, the benchmark generator, criterion benches.

## How to work with Theo

- Before a new Rust file: say in two or three lines what it does and which Rust concepts it introduces, then write it.
- Keep changes small: one concept-heavy file per change, tests run after each.
- After each task: a short "What you learned" list in chat (concept, `file:line`), the same entry appended to `docs/rust-journal.md`, and one small exercise Theo can try, checkable by the compiler or a test.
- When the compiler rejected something along the way, put the error, its meaning and the fix in the journal. Those are the most useful lessons.
- Prefer clear code over clever code. Never avoid a Rust feature the design needs; explain it instead.
- Commit only when Theo asks. Message style: imperative, prefixed with the phase or spike, for example `spike 2: face-frame table and twist tests`.

## Tests that must exist

- Rotation group closed under composition; face permutations bijective; four quarter turns give identity; twist table matches world-space arrows for all cases.
- Compiler: adjacency symmetric; multi-cell internal rules exclusive; Indifference only on uncovered faces; property tests on random libraries.
- Solver validity: every solved neighbour pair allowed, every slot inside its initial domain, multi-cell modules complete, checked by `validate.rs` on every result in tests and debug builds.
- Differential: Rust and the Python reference agree bit for bit on thousands of random small cases.
- Golden numbers: "Golden fixtures" in `.claude/rules/solver-core.md`.
- Blender: example files compile, solve with a fixed seed and match a golden attribute hash.

## Glossary

slot (a cell or panel to fill) · port (a face of a slot) · edge (two ports that touch, plus twist) · twist (quarter turns between two face frames) · module (an authored part) · part (one cell of a multi-cell module) · variant (module × rotation × part, what the solver places) · connector (a name on a face) · terminator (a face allowed at the boundary) · domain (a slot's still-allowed variants, as a bitset) · support (the OR of allowed neighbours) · observe, propagate, contradiction, trail, attempt, growth, supply cap.
