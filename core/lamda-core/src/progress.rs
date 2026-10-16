// SPDX-License-Identifier: MIT OR Apache-2.0

//! Progress counters and the cancel flag shared between the solver and its host.
//!
//! Where it sits: the host (Blender through `lamda-py`, or the CLI) creates one
//! `Progress`, hands the solver a shared handle, and polls the counters while
//! the solve runs on another thread. The solver checks the cancel flag every
//! 256 queue pops and at every observation (`.claude/rules/solver-core.md`,
//! "Progress and cancel").
//!
//! Rust in this file:
//! - atomics: `AtomicU64` and `AtomicBool` are integers and booleans that
//!   several threads may read and write at once without a lock. Each access
//!   names an `Ordering`; `Relaxed` means "no ordering with other memory", which
//!   is enough for independent counters. A plain `u64` field would need
//!   `&mut`, and two threads can never both hold `&mut` to one value.
//!   <https://doc.rust-lang.org/std/sync/atomic/index.html>
//! - `Default` derive: `Progress::default()` builds a zeroed instance.
//!   <https://doc.rust-lang.org/std/default/trait.Default.html>
//! - `&self` methods that mutate through atomics: interior mutability.
//!   <https://doc.rust-lang.org/book/ch15-05-interior-mutability.html>
//!
//! Sharing the handle between threads (`Arc<Progress>`) is explained in
//! `attempts.rs`.
//!
//! Read next: `propagate.rs`.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// Live counters the host can poll, and the flag it can raise to stop the solve.
///
/// Cost: five words of memory; every update is one atomic instruction.
#[derive(Debug, Default)]
// RUST: `Default` is derivable because every atomic type has a zero default.
pub struct Progress {
    /// Observations made so far in the current attempt.
    observations: AtomicU64,
    /// Slots with exactly one variant left, last time the solver counted.
    collapsed: AtomicU64,
    /// Backtracks so far in the current attempt.
    backtracks: AtomicU64,
    /// Index of the attempt now running.
    attempt: AtomicU64,
    /// Raised by the host to stop; the solver returns `Cancelled` soon after.
    cancel: AtomicBool,
}

/// A plain copy of the counters at one instant, for reports and Python.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Snapshot {
    /// Observations so far.
    pub observations: u64,
    /// Collapsed slots at the last count.
    pub collapsed: u64,
    /// Backtracks so far.
    pub backtracks: u64,
    /// Attempt index now running.
    pub attempt: u64,
    /// Whether cancel was requested.
    pub cancelled: bool,
}

impl Progress {
    /// Creates zeroed counters with the cancel flag down.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// assert!(!progress.is_cancelled());
    /// assert_eq!(progress.snapshot().observations, 0);
    /// ```
    pub fn new() -> Progress {
        Progress::default()
    }

    /// Asks the solver to stop. Safe to call from any thread, any number of times.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.cancel();
    /// assert!(progress.is_cancelled());
    /// ```
    pub fn cancel(&self) {
        // RUST: `&self`, not `&mut self`, yet the value changes: atomics allow
        // mutation through a shared reference because the hardware guarantees
        // the write is indivisible. This is "interior mutability".
        self.cancel.store(true, Ordering::Relaxed);
    }

    /// Whether cancel has been requested.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// assert!(!Progress::new().is_cancelled());
    /// ```
    pub fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }

    /// Lowers the cancel flag and zeroes the counters, for reuse between solves.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.cancel();
    /// progress.reset();
    /// assert!(!progress.is_cancelled());
    /// ```
    pub fn reset(&self) {
        self.observations.store(0, Ordering::Relaxed);
        self.collapsed.store(0, Ordering::Relaxed);
        self.backtracks.store(0, Ordering::Relaxed);
        self.attempt.store(0, Ordering::Relaxed);
        self.cancel.store(false, Ordering::Relaxed);
    }

    /// Counts one observation.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.add_observation();
    /// assert_eq!(progress.snapshot().observations, 1);
    /// ```
    pub fn add_observation(&self) {
        // RUST: `fetch_add` adds and returns the old value atomically; the old
        // value is not needed, so it is discarded.
        self.observations.fetch_add(1, Ordering::Relaxed);
    }

    /// Counts one backtrack.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.add_backtrack();
    /// assert_eq!(progress.snapshot().backtracks, 1);
    /// ```
    pub fn add_backtrack(&self) {
        self.backtracks.fetch_add(1, Ordering::Relaxed);
    }

    /// Publishes the number of collapsed slots.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.set_collapsed(12);
    /// assert_eq!(progress.snapshot().collapsed, 12);
    /// ```
    pub fn set_collapsed(&self, count: u64) {
        self.collapsed.store(count, Ordering::Relaxed);
    }

    /// Publishes the attempt index and zeroes the per-attempt counters.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let progress = Progress::new();
    /// progress.add_observation();
    /// progress.start_attempt(3);
    /// let snap = progress.snapshot();
    /// assert_eq!((snap.attempt, snap.observations), (3, 0));
    /// ```
    pub fn start_attempt(&self, attempt: u64) {
        self.attempt.store(attempt, Ordering::Relaxed);
        self.observations.store(0, Ordering::Relaxed);
        self.backtracks.store(0, Ordering::Relaxed);
        self.collapsed.store(0, Ordering::Relaxed);
    }

    /// Reads every counter at once. The values may come from slightly different
    /// instants, which is fine for a progress bar.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::progress::Progress;
    /// let snap = Progress::new().snapshot();
    /// assert_eq!(snap.attempt, 0);
    /// ```
    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            observations: self.observations.load(Ordering::Relaxed),
            collapsed: self.collapsed.load(Ordering::Relaxed),
            backtracks: self.backtracks.load(Ordering::Relaxed),
            attempt: self.attempt.load(Ordering::Relaxed),
            cancelled: self.is_cancelled(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    /// A cancel raised on another thread must be visible to the solver's thread;
    /// this is the whole point of using atomics instead of a plain bool.
    #[test]
    fn cancel_crosses_threads() {
        let progress = Arc::new(Progress::new());
        // RUST: `Arc` (explained in attempts.rs) lets two threads share one value.
        let handle = {
            let progress = Arc::clone(&progress);
            std::thread::spawn(move || progress.cancel())
            // RUST: `move` gives the closure its own Arc clone.
        };
        handle.join().unwrap();
        assert!(progress.is_cancelled());
    }

    /// `start_attempt` must reset the per-attempt counters, so a progress bar
    /// restarts from zero for each attempt.
    #[test]
    fn start_attempt_resets_counters() {
        let progress = Progress::new();
        progress.add_observation();
        progress.add_backtrack();
        progress.set_collapsed(5);
        progress.start_attempt(2);
        assert_eq!(
            progress.snapshot(),
            Snapshot {
                attempt: 2,
                ..Snapshot::default()
            }
        );
        // RUST: `..Default::default()` fills the remaining fields from a default.
    }
}
