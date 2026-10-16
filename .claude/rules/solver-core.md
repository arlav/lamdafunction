---
paths:
  - "core/**"
  - "reference/**"
  - "schema/**"
  - "tests/fixtures/**"
  - "addon/compile/**"
---

# Solver core specification

The solver is defined here precisely enough that `lamda-core` (Rust) and `reference/` (NumPy) produce **bit-identical results** for the same compiled assembly and seed. If you change behaviour described here, change both implementations and the differential tests in the same task. Background and reasoning: `docs/PLAN.md`, "Rule compiler", "Solver design" and "Solver, step by step".

## Compiled assembly, schema v1

Written by `addon/compile/` (Python), read by `lamda-core` and `reference/`. JSON, with a binary twin later for very large envelopes. Defined in `schema/compiled-assembly-v1.json`.

| Field | Contents |
| --- | --- |
| `schema_version`, `compile_hash` | `"1.0.0"`; hash of the inputs, for caching and reports |
| `modules` | name and library index per module |
| `variants` | one entry per variant: module index, rotation index (0..24), part index, anchor flag; optional `internal`: `[port, part]` pairs naming the sibling part of the same module and rotation that must sit across that port (multi-cell modules; checked by the validator only) |
| `ports` | port count per slot kind; box slots have 6 |
| `allowed` | for each (port, twist) used by the graph, a list of allowed `[v, w]` pairs: `w` may sit across that port from `v` |
| `slots`, `edges` | slot count; edges in CSR form (`offsets`, then `port`, `neighbour`, `neighbour_port`, `twist` per entry) |
| `boundary_classes`, `boundary` | a class is a connector name plus the sorted list of variants whose face carries it; `boundary` lists `{slot, port, class}` for every exposed port that is not free. Free ports are not listed. The core ANDs the class into the slot's domain and never sees connector names otherwise |
| `domain_classes`, `slot_domain` | distinct initial domains as sorted variant lists, and one class index per slot |
| `variant_weight`, `slot_module_weight` | base weight per variant (the module weight split across its variants); optional per-slot, per-module multiplier, already combined by the compiler (module attribute × tag attributes in sorted tag order) |
| `priority`, `fixed` | optional per-slot priority (int) and fixed variant (−1 = free) |
| `settings` | seed, attempts, threads, backtrack budget, max observations, mode (`collapse` or `grow`), supply caps |

`lamda-core` builds the bitset tables from `allowed`, asserts symmetry, applies `fixed` and `boundary` (and drops variants whose effective weight is ≤ 0), then **canonicalises**: one propagation pass before any observation. A contradiction there is a setup error, returned in a canonicalisation report (slots that emptied, variants that died everywhere) that the Python audit shows. Rule expansion, rotations and face frames stay in Python.

## Data model

- Index newtypes: `SlotId(u32)`, `VariantId(u32)`, `PortId(u8)`, `Twist(u8)` with twist in 0..=3.
- Box ports are numbered in the face-table order of `docs/PLAN.md`: 0 `+X`, 1 `-X`, 2 `+Y`, 3 `-Y`, 4 `+Z`, 5 `-Z`. Until the research track, a table is keyed by **(port index, twist)**. The side/top/bottom "port class" scheme in PLAN.md is for cell complexes and needs Theo's sign-off before it is built.
- **Twist convention:** an edge's twist is the number of quarter turns, counter-clockwise about the measuring slot's own outward port normal, that turns the frame its neighbour's port would have on a regular grid onto that port's actual frame. Measured from the other side, about that side's own normal, the number is the same, so the reverse edge carries the **same** twist. On box grids every twist is 0.
- **Symmetry invariant**, asserted when loading in debug builds and always in tests: `w ∈ table(p, t)[v]` exactly when `v ∈ table(opposite(p), t)[w]`.
- Domains: one `Bitset` per slot, behind a `Domain` trait so a sparse version can follow without touching the solver loop.
- Effective weight of variant `v` in slot `s`: `variant_weight[v] × slot_module_weight[s][module(v)]`, one multiplication, in `f64`. Weight ≤ 0 means the variant is not in the slot's initial domain.

## Random numbers: PCG32

One generator per attempt, PCG-XSH-RR 64/32 (O'Neill), written by hand in both languages.

```
new(initstate, initseq): state = 0; inc = (initseq << 1) | 1; next(); state += initstate; next()
next() -> u32:
    old = state
    state = old * 6364136223846793005 + inc          # wrapping 64-bit arithmetic
    xorshifted = (((old >> 18) ^ old) >> 27) as u32
    rot = (old >> 59) as u32
    return xorshifted.rotate_right(rot)
unit_f64() -> f64: next() as f64 / 4294967296.0     # in [0, 1)
```

- Rust: `wrapping_mul`, `wrapping_add`, `rotate_right`. Python: mask with `& 0xFFFF_FFFF_FFFF_FFFF` after every multiply and add, and with `& 0xFFFF_FFFF` on the output.
- Attempt `k` uses `new(seed + k, 0x4C414D44)` (the stream constant spells "LAMD").
- Test vector, must pass in both languages: `new(42, 54)` gives `0xa15c02b7 0x7b47f409 0xba1d3330 0x83d2f293 0xbfa4784b 0xcbed606e`.
- The stream is consumed in exactly this order and nowhere else: at start, one `next()` per slot in slot order, kept as that slot's tie-break noise; then one `unit_f64()` per observation, for the variant choice.

## Floating point, so both languages agree

- Read JSON floats exactly: `serde_json` with the `float_roundtrip` feature; Python's `json` already does.
- Every sum is a plain left-to-right loop in `f64`, in ascending variant order. In Python never use `np.sum`, `sum` over a NumPy array or `math.fsum` for solver quantities: they add in a different order.
- Basic arithmetic (`+ - × ÷`) is identical on every platform. `ln` is not, so entropies are never compared raw; they become integer keys (below).

## Entropy and observation

- Per slot keep `W = Σ w` and `S = Σ w·ln w` over the variants in its domain. When propagation removes variants from a slot, subtract them in ascending variant order. After backtracking restores domains, recompute `W` and `S` from scratch for every slot it touched. A slot with one variant has entropy exactly 0.
- `H = ln W − S / W`, with `libm::log` in Rust and `math.log` in Python.
- Entropy key: `floor(H × 1e6 + 0.5)` as a 64-bit integer, the same formula in both languages. Do not use Python's `round()` (it rounds halves to even) or Rust's `f64::round` (it rounds halves away from zero).
- Next slot to observe: the open slot (more than one variant) with the smallest key `(−priority, entropy_key, noise, slot index)`. Slot index makes the order total, so there are never ties.
- Rust uses a `BinaryHeap` of `Reverse((key, slot, version))`. Each slot has a version counter, bumped whenever its domain changes; push a fresh entry then. A popped entry is used only if its version is current and the slot is still open. Entropy can go **up** when a heavy variant is removed, so never compare keys to decide staleness; compare versions. The Python reference may scan all open slots linearly; the order is the same.
- Variant choice: `W' =` the slot's effective weights summed afresh in ascending variant order (not the running `W`), `r = unit_f64() × W'`, then walk the variants in ascending order subtracting each weight from `r`, and pick the first where `r < 0`. If rounding leaves none, pick the last variant in the domain.

## Propagation (bitset AC-3)

- FIFO queue (`VecDeque`) plus an `in_queue` flag per slot, so a slot is queued at most once at a time.
- Pop slot `s`. For each edge of `s` in CSR order: `support` = OR over `v` in `domain[s]` (ascending) of `table(port, twist)[v]`; `new = domain[n] & support`. If `new` differs from `domain[n]`: log the old domain on the trail, update `W` and `S` and the version, then return `Contradiction { slot: n }` if `new` is empty, otherwise enqueue `n` if it is not already queued.
- Optimise only with benchmark evidence, in this order: cache the support of a full domain; build support from the removed variants when fewer were removed than remain; AC-4 counters for very large variant counts. Each optimisation must leave results unchanged.

## Trail, backtracking and attempts

- Trail entries: `Domain { slot, old }` and `Decision { trail_len, slot, variant }`.
- On contradiction: pop to the last decision, restore domains, remove that decision's variant from its slot (logged under the previous decision), recompute `W` and `S` for touched slots, and propagate. If that contradicts too, backtrack again. Stop when the per-attempt backtrack budget runs out.
- Phase 1 Python reference: no backtracking; a contradiction ends the attempt and the next seed runs.
- Attempt 0 runs alone. If it fails, attempts 1, 2, … run in parallel batches of `threads`. The result is the **lowest-numbered successful attempt in the first batch that has any success**, never whichever thread finished first.
- `max_observations` stops early and returns a partial result whose domains can be saved and resumed.
- Growth mode: an internal "empty" candidate that accepts any neighbour; grow from the frontier of slots touching placed modules; decrement supply caps once per module instance; settling on empty is a valid outcome.
- Partial re-solve: lock every slot outside the region, reset the region's domains, propagate inward from its border, solve.

## Progress and cancel

An `Arc` shared with the caller holds `AtomicU64` counters (observations, collapsed slots, backtracks, attempt), written with `Ordering::Relaxed`, and an `AtomicBool` cancel flag checked every 256 queue pops and at every observation. `lamda-py` releases the GIL for the whole solve with PyO3's `allow_threads` (renamed `detach` in newer PyO3; use whichever the pinned version provides). Let maturin enable `pyo3/extension-module` (in `pyproject.toml`), not `Cargo.toml`, so `cargo test --workspace` still links.

## Output arrays

Per slot: variant id (−1 if open), module index, rotation index (0..24; the addon maps it to a quaternion), part index, anchor flag, state (0 solved, 1 open, 2 contradiction), options left, collapse step (observation count at which the slot collapsed, 0 for canonicalisation, −1 if open). Rust types: `i32` for ids and indices, `bool`, `u8` state, `u32` options, `i64` step. Plus a report: seed, attempt used, observations, backtracks, elapsed time, outcome (`solved`, `partial`, `contradiction`, `cancelled`).

Attempt policy: an attempt is final when it is solved, stopped at `max_observations` (partial) or cancelled; otherwise the next batch runs. If every attempt fails, the last failed result is returned with its outcome.

## Validator

`validate.rs` and `reference/validate.py` are written without reusing solver code. They check that every edge's pair of chosen variants is allowed, every slot's variant was in its initial domain, every boundary port is satisfied, and every multi-cell module is complete. Tests and debug builds run them on every result.

## Golden fixtures

Put these in `tests/fixtures/` and test both implementations against them. Every number below was computed by exhaustive search or full propagation.

- **PCG32:** the test vector above.
- **Tube kit** (images 3 to 6 in `docs/PLAN.md`): modules straight, elbow, tee, cross, cap and empty; connector "t" where a tube meets a face, "0" where blank; Full rotation. The compiler must produce **37 variants**: straight 3, elbow 12, tee 12, cross 3, cap 6, empty 1.
- **Tube envelope:** a 4 × 3 × 2 block minus the cells with x ≥ 2 and y = 2, so **20 slots**; every boundary port requires connector "0". Initial domains: corner slot (0,0,1) **7**, edge slot (1,0,1) **13**, top-face slot (1,1,1) **23**.
- **Tube propagation:** fix (0,0,1) = cap open to +Y, (0,1,1) = elbow open to −Y and −Z, (1,2,1) = elbow open to −Y and −Z, then propagate: (1,1,1) has **6**, (1,0,1) **7**, (2,1,1) **13**, (0,2,1) **2**. Then fix (1,1,1) = elbow open to +X and +Y and propagate: (1,0,1) falls to **4**, (2,1,1) to **6**.
- **Office south strip** (`docs/PLAN.md`, "How WFC fills the office, slot by slot"): 7 façade panels and 7 floor cells, 19 edges. Weights S 1, WL 2, WR 2, E 2, ST 1, DL 8, DR 8. Exhaustive enumeration finds **9 valid façades** and **544 valid strips**. Entropies: {S, WL} and {E, ST} **0.637**, {E, DL} **0.500**, a full floor cell **1.120** (to 3 decimal places).
