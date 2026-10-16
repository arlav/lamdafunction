# Spike 4: bitset AC-3 benchmark

**Question.** Does bitset AC-3 over a slot graph reach the phase 2 target: 19,000 slots × 400 variants fully propagated and solved in under 1 s per attempt, single-threaded, release build? What happens at 2,000 and 16,000 variants, and do we need sparse domains?

**Method.** `lamda-cli bench-gen` builds a 30 × 30 × 21 box grid (18,900 slots) with a planted solution: a random variant per slot, every planted neighbour pair allowed, plus `--extra` random allowed neighbours per variant and port (default 40). `lamda-cli bench` times `Solver::new` (table build plus the canonicalising propagation pass) and `run_attempt` for a few attempts. Machine: Apple Silicon laptop, macOS, Rust 1.97, `cargo build --release` (LTO fat, one codegen unit), one thread. Numbers are single runs, not criterion statistics; `cargo bench -p lamda-core` has the same cases for repeatable measurement.

**Numbers.**

| Variants | Extra pairs | Setup | Attempt | Outcome |
| --- | --- | --- | --- | --- |
| 400 | 8 | 0.14 s | 0.30 to 0.44 s | contradiction after 1,001 backtracks (kit too tight) |
| 400 | 40 | 0.15 s | 0.39 s | solved, 18,880 observations, 0 backtracks |
| 400 | 120 | 0.15 s | 0.26 s | solved, 18,900 observations, 0 backtracks |
| 2,000 | 200 | 1.1 s | 1.9 s | solved, 18,899 observations, 0 backtracks |
| 16,000 | 20 | 61 s | not finished after 5 min, killed | setup alone fails the budget |

The 16,000-variant file is 53 MB of JSON; its tables are 6 × 16,000 × 16,000 bits = 192 MB.

**Reading the numbers.** Setup is dominated by the canonicalising pass with full domains: each edge ORs one table row per variant in the domain, so it costs edges × variants × words. At 400 variants that is 113,000 × 400 × 7 word operations, 0.14 s; at 16,000 it is 113,000 × 16,000 × 250, about 450 billion, the 61 s. The first optimisation in `.claude/rules/solver-core.md` ("cache the support of a full domain") removes exactly this term: a full domain's support is the same for every edge of a (port, twist) and can be computed once per table. The second ("build support from the removed variants when fewer were removed than remain") is what makes the first few observations cheap at large variant counts.

**Decision.** The 400-variant target passes with room to spare, with no optimisation yet. 2,000 variants is usable (about 3 s for setup plus one attempt) and will drop well under 1 s with the full-domain cache. 16,000 variants needs both the full-domain cache and sparse domains behind the `Domain` trait in `solve.rs`; implement the cache first, re-measure, then decide on sparse domains with numbers. Also: the benchmark generator's default of 40 extra pairs makes solvable kits; 8 does not, and that is itself a useful stress case for backtracking.
