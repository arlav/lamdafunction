# LamdaFunction status

Updated at the end of every session. Dates are absolute.

## Phase and gates

| Phase | State | Gate |
| --- | --- | --- |
| 0 Spikes | **Signed off by Theo on 2026-10-16** | All five spikes ran; findings in `docs/spikes/01` to `05`; re-run on 5.2.2 LTS on 2026-10-17 |
| 1 MVP | **Built 2026-10-16; gate pending** | A newcomer rebuilds the pipes kit from `docs/pipes-tutorial.md` alone |
| 2 Parity | Not started (CI/release workflow written 2026-10-24, first run pending a push) | The building study solves in under 1 s per attempt (already true for the core: spike 4) |
| 3 Research | Not started | — |

Carried from phase 0 into phase 1 without blocking: run spike 3 on Windows x64, Linux x64 and macOS x64 (CI job). Done on 2026-10-17: spikes 1, 3 and 5 and the Blender tests re-run on Blender 5.2.2 LTS (Python 3.13.13, numpy 2.3.4), same results and the same attribute hash.

## What exists (2026-10-16)

Rust, `core/`:
- `lamda-core`: the full solver (rng, bitset, ids, assembly, graph, adjacency, progress, propagate, entropy, observe, trail, solve, attempts, validate, synthetic), 161 tests, clippy and rustdoc clean with `-D warnings`, teaching comments checked by `scripts/check_teaching.py`.
- `lamda-cli`: `solve`, `validate`, `bench-gen`, `bench`.
- `lamda-py`: abi3 bindings (`solve_json`, `canonicalise_json`, `validate_json`, `Progress`, `SolveResult` with NumPy arrays); the wheel is built into `addon/wheels/` (gitignored) by maturin; 6 smoke tests in `tests/python/test_bindings.py`.

Python, no Blender:
- `reference/`: the reference solver (`pcg32`, `assembly`, `solver`, `validate`), 9 golden tests. `tests/python/test_differential.py` solves 2,300 random assemblies (irregular grids, random rules, weights, priorities, fixed slots, boundaries, budgets, attempts, partial stops) in both implementations and requires identical arrays and reports. Backtracking is implemented in the reference too, so the differential tests cover it.
- `addon/model/`: `Kit`, `Module`, `Connector`, `ConnectorPair`, `Rule`, `Envelope`, `SolveSettings`, JSON manifests (`io.py`), 4 tests.
- `addon/compile/`: `rotations`, `frames` (spike 2), `variants`, `rules`, `graph`, `domains`, `audit`, `assemble.compile_assembly`; 37 tests including the tube-kit golden numbers (37 variants; 20 slots; initial domains 7/13/23; propagation 6/7/13/2 then 4/6) and random-kit symmetry.

Blender, `addon/bl/`:
- `properties` (module faces as flat properties, scene settings, pairs, rules, audit), `library` (library collection → `Kit`, geometry hashes per rotation from evaluated meshes), `envelope` (evaluated mesh → `Envelope`, box envelopes), `backend` (`lamda_core` or the vendored reference), `results` (assembly object with every `wfc_*` attribute, quaternions from rotation indices), `nodes` (Materialise and Inspect node groups built by script; hidden instancer collection with `0000`… children), `operators` (compile, solve as a modal operator on a worker thread with Esc cancel, blocking in background mode; materialise; inspect; select audit culprit; editing helpers), `panels` (six panels in the LamdaFunction tab), `overlay` (cage and connector arrows of the active module).
- `addon/blender_manifest.toml`; `scripts/build_extension.py` vendors `reference/` and builds four platform zips into `dist/` (only the macOS arm64 zip contains a wheel so far).
- `examples/pipes.blend` (built by `scripts/make_examples.py`), `docs/pipes-tutorial.md`.
- `tests/blender/`: `run_all.py`, `test_collection_order.py` (spike 1), `test_wheel.py` (spike 3), `test_pipes.py` (end to end through the operators; pins attribute hash `ee1b017f2c553301` for seed 1, identical with the wheel and with the reference), `spike_05_foreach_set.py`, `pipes_kit.py` (scene builder).

Docs: `docs/PLAN.md`, `docs/spikes/01` to `05`, `docs/pipes-tutorial.md`, `docs/rust-journal.md`, `docs/rust-concepts.md`.

## Spike results

| Spike | Result | Where |
| --- | --- | --- |
| 1 Collection order | Separate Children uses natural name order (digits numeric, case-insensitive); `collection.children` is link order; zero-padded names give library order; confirmed on 5.2.2 | `docs/spikes/01-collection-order.md` |
| 2 Face frames and twist | 24-rotation numbering fixed; twist defined from world arrows, same from both sides; `(2 − k_a − k_b) mod 4` in offsets; 1,536 pin-and-slot cases pass | `docs/spikes/02-face-frames-twist.md` |
| 3 Wheel in Blender | Loads in 4.3.2 (Python 3.11) and 5.2.2 (Python 3.13, same abi3 wheel); study kit solves on a worker thread in 0.55 s with live progress; cancel in under 10 ms; installed zip resolves the wheel; macOS arm64 only | `docs/spikes/03-wheel-in-blender.md` |
| 4 Bitset AC-3 | 18,900 slots × 400 variants: 0.15 s setup, about 0.4 s per attempt, single thread; 2,000 variants 1.1 s + 1.9 s; 16,000 variants needs the full-domain support cache and sparse domains | `docs/spikes/04-bitset-benchmark.md` |
| 5 foreach_set | 8 attributes × 200,000 points in 2.3 ms on 4.3.2 and 3.5 ms on 5.2.2, round-trip verified | `docs/spikes/05-foreach-set.md` |

## Decisions

Taken while building; any can be reopened, say so and the code changes in one task.

1. **Boundary encoding.** The compiled assembly carries `boundary_classes` (connector name plus the sorted variants whose face carries it) and `boundary` (`{slot, port, class}` per exposed, non-free port). The compiler emits one class per exposed world face: the variants whose face carries the boundary connector *or* is flagged as a terminator. The core never sees connector names.
2. **Benchmark generator.** `lamda-cli bench-gen` and `BenchSpec::default()` use 40 extra random allowed pairs per variant and port; 8 makes the 400-variant study kit unsolvable within budget and is kept as a backtracking stress case.
3. **Twist convention.** Twist is the number of quarter turns counter-clockwise about A's outward normal taking A's world arrow onto B's; the frames of opposite faces are mirror images, so offset 0 on +X meets offset 2 on −X at twist 0. The overlay draws world arrows.
4. **Variant deduplication.** Two rotations of a module merge when their world-face signatures and their geometry hashes agree. The signature ignores the offset of a connector that pairs at every twist (no rule can tell such offsets apart), which is what gives the tube kit its 37 variants. Without geometry hashes nothing merges; the add-on always computes hashes from the evaluated meshes.
5. **Explicit rules under rotation** (`addon/compile/rules.py`). An allowed rule `a:fa -> b:fb` describes a reference placement (b turned by the reference rotation that brings `fb` opposite `fa`); with both modules rotatable the pair rotates rigidly, with at most one rotatable that side takes every rotation that puts its face across. Disallowed removes the two faces' touches in every rotation. Exclusive keeps only each other on those faces.
6. **Fixed slots** (`wfc_fix`, a module index) restrict the slot's domain to that module's variants; the compiled `fixed` field (a variant index) stays unused by the compiler.
7. **Node groups are built by script** in `addon/bl/nodes.py`, not shipped in `assets/lamda.blend`; the rule file was updated. An export to a .blend can follow.
8. **Multi-cell completeness** is checked by the validator through an optional `internal` list per variant (`[port, part]` pairs); the solver only sees allowed pairs. Multi-cell modules themselves are phase 2.
9. **Reference solver scope.** The spec said the phase 1 reference has no backtracking; it has it, mirroring `trail.rs`, so the differential tests cover the backtracking path too. Both languages still agree bit for bit.

## Phase 1, what is left before the gate

- Someone follows `docs/pipes-tutorial.md` in Blender 5.2. The extension is installed and enabled in Theo's 5.2 user repository (`blender --command extension install-file -r user_default --enable`, 2026-10-17) and loads the wheel from the extension's site-packages. The UI has only been exercised headless: the modal solve with its timer, the status bar, the overlay drawing and the panels need an interactive pass.
- First run of `.github/workflows/ci.yml` (written 2026-10-24, untested until pushed): it builds the wheels for Windows x64, Linux x64, macOS arm64 and x64, runs the Blender suite on Linux and Windows with the matching wheel (spike 3 on those platforms), and on a `v*` tag releases the zips, `docs/pipes-tutorial.pdf` and `examples/pipes.blend`. `scripts/build_extension.py` now generates the manifest's wheel list from `addon/wheels/`, and the Blender tests pick the wheel of their platform.
- Nice to have, not gating: "rule from two selected faces", auto re-solve, the "Bake" to real objects, rotation of the overlay cage when the module collection is placed elsewhere.

## Open

- Blender 5.0 changed modifier inputs from ID properties to `modifier.properties.inputs.<identifier>.value`; `addon/bl/nodes.py` supports both and the Blender tests run on 4.3.2 and 5.2.2. Drop the 4.x path when 4.x stops being used for development.
- `docs/pipes-tutorial.md` points students at the GitHub releases page; no release exists until the first `v0.1.0` tag is pushed and CI succeeds. `docs/pipes-tutorial.pdf` is generated by `scripts/md2pdf.py` (headless Chrome) and should be regenerated after edits.
- `examples/pipes.blend` is now saved by 5.2.2; 4.3 can still open it for the tests, which rebuild the scene rather than load the file.
- `ruff` is not installed in `.venv` (adding a Python package needs Theo's go); the Python code is written to be `ruff` clean but unchecked.
- `math.log` (Python) and `libm::log` (Rust) agreed on every entropy key in 2,300 random cases; a last-ulp disagreement is still possible in principle and would show up as a differential failure, which is why the test keeps running.
