// SPDX-License-Identifier: MIT OR Apache-2.0

//! Attempts: run attempt 0 alone, then further attempts in parallel batches.
//!
//! Where it sits: the top of the solving pipeline. Attempt 0 runs on the
//! calling thread with the given seed. If it fails, attempts 1, 2, … run in
//! batches of `threads` on a rayon pool, and the result is the lowest-numbered
//! successful attempt in the first batch with any success, never whichever
//! thread finished first, so results stay deterministic
//! (`.claude/rules/solver-core.md`, "Trail, backtracking and attempts").
//!
//! Rust in this file:
//! - `rayon`: `par_iter()` runs a closure on every element across a thread
//!   pool; `collect()` keeps the input order. <https://docs.rs/rayon>
//! - `Send` and `Sync`: marker traits the compiler checks. `T: Send` means a
//!   value may move to another thread; `T: Sync` means `&T` may be shared
//!   between threads. `Solver` is `Sync` because every field is, so `&Solver`
//!   can be used from all workers at once; a `Cell` or `Rc` field would make it
//!   not `Sync` and rayon would refuse to compile the call.
//!   <https://doc.rust-lang.org/book/ch16-04-extensible-concurrency-sync-and-send.html>
//! - `Arc<T>`: atomically reference-counted shared ownership, for a `Progress`
//!   that the host thread keeps while the solve runs elsewhere (`lamda-py`).
//!   Inside this file plain `&Progress` is enough, because rayon's scoped
//!   parallelism guarantees the workers finish before the borrow ends.
//!   <https://doc.rust-lang.org/book/ch16-03-shared-state.html>
//! - closures capturing references across threads: `|k| solver.run_attempt(..)`
//!   captures `solver` and `progress` by shared reference, which is allowed
//!   because both are `Sync`.
//!
//! Read next: `validate.rs`.

use rayon::prelude::*;
// RUST: the prelude brings `par_iter` and friends into scope as methods.

use crate::progress::Progress;
use crate::solve::{Outcome, SolveError, SolveResult, Solver};

/// Runs attempts as the settings ask and returns the chosen result.
///
/// Returns attempt 0's result if it solved (or stopped at `max_observations`);
/// otherwise the lowest-numbered success of the first parallel batch that has
/// one; otherwise the last failed result, whose report says why. A cancel
/// returns at once with a `Cancelled` outcome.
///
/// Cost: up to `attempts` solves, `threads` at a time after the first.
///
/// # Errors
/// [`SolveError`] from a failed attempt or thread-pool construction.
///
/// # Examples
/// ```
/// use lamda_core::assembly::{Assembly, examples};
/// use lamda_core::attempts::solve;
/// use lamda_core::progress::Progress;
/// use lamda_core::solve::{Outcome, Solver};
/// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
/// let solver = Solver::new(&assembly).unwrap();
/// let result = solve(&solver, &Progress::new()).unwrap();
/// assert_eq!(result.report.outcome, Outcome::Solved);
/// assert_eq!(result.report.attempt, 0);
/// ```
pub fn solve(solver: &Solver<'_>, progress: &Progress) -> Result<SolveResult, SolveError> {
    // RUST: `Solver<'_>`: we do not care how long the assembly lives, only that
    // the solver is valid for this call.
    let settings = &solver.assembly().settings;
    let attempts = u64::from(settings.attempts.max(1));
    // WFC: attempt 0 always runs alone, on the calling thread.
    let first = solver.run_attempt(0, progress)?;
    if is_final(first.report.outcome) {
        return Ok(first);
    }
    let threads = match settings.threads {
        0 => rayon::current_num_threads(),
        n => n as usize,
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| SolveError::ThreadPool(e.to_string()))?;
    // RUST: `map_err` converts rayon's error into ours before `?` returns it.
    let mut last = first;
    let mut next = 1u64;
    while next < attempts {
        let batch: Vec<u64> = (next..attempts.min(next + threads as u64)).collect();
        // RUST: `pool.install(|| ..)` runs the closure inside the pool so that
        // `par_iter` uses exactly `threads` workers.
        let results: Vec<Result<SolveResult, SolveError>> = pool.install(|| {
            batch
                .par_iter()
                .map(|k| solver.run_attempt(*k, progress))
                .collect()
        });
        // RUST: `collect` on a parallel iterator keeps the batch order, so index
        // `i` is attempt `batch[i]`: the lowest-numbered success is the first
        // `Ok` with a final outcome, regardless of which thread finished first.
        for result in results {
            let result = result?;
            if is_final(result.report.outcome) {
                return Ok(result);
            }
            last = result;
        }
        if last.report.outcome == Outcome::Cancelled {
            return Ok(last);
        }
        next += batch.len() as u64;
    }
    Ok(last)
}

/// Whether an outcome ends the search: solved, stopped on purpose, or cancelled.
///
/// Cost: O(1).
fn is_final(outcome: Outcome) -> bool {
    // RUST: `matches!` with `|` patterns: true for any of the listed variants.
    matches!(
        outcome,
        Outcome::Solved | Outcome::Partial | Outcome::Cancelled
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::{Assembly, examples};
    use crate::ids::VariantId;
    use std::sync::Arc;

    /// An assembly where every pair is allowed, so attempts never fail, with a
    /// budget of zero backtracks so a failure would be immediate if it happened.
    fn free_assembly() -> Assembly {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let all = vec![
            (VariantId(0), VariantId(0)),
            (VariantId(0), VariantId(1)),
            (VariantId(1), VariantId(0)),
            (VariantId(1), VariantId(1)),
        ];
        assembly.allowed[0].pairs = all.clone();
        assembly.allowed[1].pairs = all;
        assembly.settings.attempts = 4;
        assembly.settings.threads = 2;
        assembly
    }

    /// `Solver` must be `Sync` and `Send`, or rayon could not share it. This
    /// function only compiles if it is.
    #[test]
    fn solver_is_sync_and_send() {
        fn assert_sync_send<T: Sync + Send>() {}
        assert_sync_send::<Solver<'static>>();
        assert_sync_send::<Progress>();
    }

    /// A cancel raised from another thread through an `Arc<Progress>` must end
    /// the solve with `Cancelled`.
    #[test]
    fn cancel_through_arc_from_another_thread() {
        let assembly = free_assembly();
        let solver = Solver::new(&assembly).unwrap();
        let progress = Arc::new(Progress::new());
        // RUST: `Arc::clone` bumps the count; the thread owns its clone and the
        // test keeps the original. When both are dropped the Progress is freed.
        let shared = Arc::clone(&progress);
        let canceller = std::thread::spawn(move || shared.cancel());
        canceller.join().unwrap();
        let result = solve(&solver, &progress).unwrap();
        assert_eq!(result.report.outcome, Outcome::Cancelled);
    }

    /// With attempt 0 succeeding, no further attempt runs.
    #[test]
    fn first_success_is_returned() {
        let assembly = free_assembly();
        let solver = Solver::new(&assembly).unwrap();
        let result = solve(&solver, &Progress::new()).unwrap();
        assert_eq!(result.report.attempt, 0);
        assert_eq!(result.report.outcome, Outcome::Solved);
    }
}
