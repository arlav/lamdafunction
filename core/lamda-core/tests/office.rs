// SPDX-License-Identifier: MIT OR Apache-2.0

//! Golden test on the office south strip (`docs/PLAN.md`, "Solver, step by
//! step"; numbers in `.claude/rules/solver-core.md`, "Golden fixtures").
//!
//! Rust in this file:
//! - integration tests: files in `tests/` are compiled as separate crates that
//!   use the library only through its public API.
//!   <https://doc.rust-lang.org/book/ch11-03-test-organization.html#integration-tests>
//! - `concat!` and `env!("CARGO_MANIFEST_DIR")` to find fixture files relative
//!   to the crate, whatever the working directory.
//! - `HashSet<Vec<i32>>` to count distinct solutions.
//!
//! Read next: `../src/attempts.rs`.

use std::collections::HashSet;

use lamda_core::assembly::Assembly;
use lamda_core::attempts::solve;
use lamda_core::ids::VariantId;
use lamda_core::progress::Progress;
use lamda_core::solve::{Outcome, Solver};
use lamda_core::validate::validate;

/// Path of the fixture, relative to this crate.
const FIXTURE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../tests/fixtures/office_south_strip.json"
);

/// Loads the fixture.
fn office() -> Assembly {
    Assembly::from_path(FIXTURE).expect("fixture loads")
}

/// Whether `(v, w)` is allowed across `port` with twist 0, read straight from the file.
fn allowed(assembly: &Assembly, port: u8, v: i32, w: i32) -> bool {
    assembly
        .allowed
        .iter()
        .find(|t| t.port.0 == port && t.twist.0 == 0)
        .map(|t| {
            t.pairs
                .contains(&(VariantId(v as u32), VariantId(w as u32)))
        })
        .unwrap_or(false)
}

/// Counts valid assignments by brute force over the domain classes and the
/// pair lists: an oracle that shares nothing with the solver.
fn count_valid(assembly: &Assembly, slots: &[usize]) -> usize {
    fn go(assembly: &Assembly, slots: &[usize], chosen: &mut Vec<i32>) -> usize {
        if chosen.len() == slots.len() {
            return 1;
        }
        let slot = slots[chosen.len()];
        let class = &assembly.domain_classes[assembly.slot_domain[slot] as usize];
        let mut total = 0;
        for v in class {
            let v = v.0 as i32;
            // Check every edge from this slot to an already-chosen slot.
            let start = assembly.edges.offsets[slot] as usize;
            let end = assembly.edges.offsets[slot + 1] as usize;
            let ok = (start..end).all(|e| {
                let n = assembly.edges.neighbour[e].0 as usize;
                match slots.iter().position(|s| *s == n) {
                    Some(i) if i < chosen.len() => {
                        allowed(assembly, assembly.edges.port[e].0, v, chosen[i])
                    }
                    _ => true,
                }
            });
            if ok {
                chosen.push(v);
                total += go(assembly, slots, chosen);
                chosen.pop();
            }
        }
        total
    }
    go(assembly, slots, &mut Vec::new())
}

/// The golden counts: 9 façades and 544 strips, found by exhaustive search.
#[test]
fn golden_counts() {
    let assembly = office();
    let panels: Vec<usize> = (0..7).collect();
    let all: Vec<usize> = (0..14).collect();
    assert_eq!(count_valid(&assembly, &panels), 9);
    assert_eq!(count_valid(&assembly, &all), 544);
}

/// Canonicalisation reaches "state 1 of the drawing": P1 narrows to {S, WL}
/// because P0 is S, and nothing else collapses yet.
#[test]
fn canonicalisation_matches_walkthrough() {
    let assembly = office();
    let solver = Solver::new(&assembly).unwrap();
    let domains = solver.canonical_domains();
    assert_eq!(domains[1].ones().collect::<Vec<_>>(), vec![0, 1]);
    assert_eq!(
        solver.canonical_report().collapsed,
        1,
        "only P0 is collapsed"
    );
    assert!(solver.canonical_report().dead_variants.is_empty());
}

/// Every seed must solve, validate, and land on one of the 544 strips; many
/// seeds together should reach a good share of them.
#[test]
fn many_seeds_solve_and_validate() {
    let mut assembly = office();
    let mut distinct: HashSet<Vec<i32>> = HashSet::new();
    for seed in 0..300u64 {
        assembly.settings.seed = seed;
        let solver = Solver::new(&assembly).unwrap();
        let result = solve(&solver, &Progress::new()).unwrap();
        assert_eq!(result.report.outcome, Outcome::Solved, "seed {seed}");
        assert!(validate(&assembly, &result).is_ok(), "seed {seed}");
        distinct.insert(result.variant.clone());
    }
    assert!(
        distinct.len() > 100,
        "only {} distinct strips from 300 seeds",
        distinct.len()
    );
}

/// The golden run for seed 1: pins determinism across platforms and against
/// the Python reference. If this changes, the specification changed.
#[test]
fn golden_seed_one() {
    let assembly = office();
    let solver = Solver::new(&assembly).unwrap();
    let result = solver.run_attempt(0, &Progress::new()).unwrap();
    assert_eq!(result.report.outcome, Outcome::Solved);
    assert!(validate(&assembly, &result).is_ok());
    assert_eq!(result.variant, GOLDEN_SEED_1);
    assert_eq!(result.report.observations, 7);
    assert_eq!(
        result.collapse_step,
        [0, 2, 2, 2, 4, 4, 4, 1, 3, 3, 6, 5, 5, 7]
    );
}

/// Expected variants per slot for seed 1, attempt 0: the façade S WL WR S WL WR S
/// over the cells E DL DR E DL DR ST, settled in 7 observations.
const GOLDEN_SEED_1: [i32; 14] = [0, 1, 2, 0, 1, 2, 0, 3, 4, 5, 3, 4, 5, 6];
