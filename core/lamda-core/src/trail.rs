// SPDX-License-Identifier: MIT OR Apache-2.0

//! The trail: an undo log of domain changes with decision marks, and backtracking.
//!
//! Where it sits: propagation appends every domain change to the trail
//! (`propagate.rs`, `DomainChange`); every observation first pushes a decision
//! mark. On a contradiction the solver backtracks: pop changes down to the last
//! decision, restoring old domains, then forbid that decision's variant in its
//! slot (logged under the previous decision) and propagate again
//! (`.claude/rules/solver-core.md`, "Trail, backtracking and attempts").
//!
//! Rust in this file:
//! - moves out of a `Vec`: `pop()` hands back the owned element; `truncate` and
//!   `drain` drop or yield a tail. Restoring a domain moves the saved Bitset back
//!   into the domains slice, no clone.
//! - `std::mem::take`: take a value out of a `&mut` place, leaving the default
//!   behind, used to give the caller an owned list of touched slots.
//!   <https://doc.rust-lang.org/std/mem/fn.take.html>
//! - `Vec::sort_unstable` and `dedup` to make a list of touched slots unique.
//!
//! Read next: `solve.rs`.

use crate::bitset::Bitset;
use crate::ids::{SlotId, VariantId};
use crate::propagate::DomainChange;

/// A decision mark: an observation that chose `variant` for `slot` when the
/// change log had `trail_len` entries.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decision {
    /// Length of `changes` just before the observation's own change was logged.
    pub trail_len: usize,
    /// The observed slot.
    pub slot: SlotId,
    /// The variant chosen for it.
    pub variant: VariantId,
}

/// What a backtrack undid: the decision, and every slot whose domain was restored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Backtrack {
    /// The decision that was undone.
    pub decision: Decision,
    /// Slots whose domains changed, sorted and unique; their entropy sums must
    /// be recomputed from scratch and their heap entries refreshed.
    pub touched: Vec<SlotId>,
}

/// The undo log: domain changes in order, plus the decision marks.
///
/// Invariant: `decisions` is ascending in `trail_len`, and every
/// `trail_len <= changes.len()`.
///
/// Cost: one owned `Bitset` per logged change. Memory grows with the number of
/// changes since the last decision that could still be undone.
#[derive(Debug, Clone, Default)]
pub struct Trail {
    /// Every domain change since the attempt began, oldest first.
    changes: Vec<DomainChange>,
    /// Decision marks, oldest first.
    decisions: Vec<Decision>,
}

impl Trail {
    /// Creates an empty trail.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::trail::Trail;
    /// assert_eq!(Trail::new().decision_count(), 0);
    /// ```
    pub fn new() -> Trail {
        Trail::default()
    }

    /// The change log, for propagation to append to.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::propagate::DomainChange;
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.changes_mut().push(DomainChange { slot: SlotId(0), old: Bitset::full(2) });
    /// assert_eq!(trail.change_count(), 1);
    /// ```
    pub fn changes_mut(&mut self) -> &mut Vec<DomainChange> {
        // RUST: returning `&mut Vec` lends the caller write access to the log
        // for as long as that borrow lives; the trail cannot be used through
        // another path meanwhile.
        &mut self.changes
    }

    /// Number of logged changes.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::trail::Trail;
    /// assert_eq!(Trail::new().change_count(), 0);
    /// ```
    pub fn change_count(&self) -> usize {
        self.changes.len()
    }

    /// Number of decisions that could still be undone.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{SlotId, VariantId};
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.push_decision(SlotId(0), VariantId(1));
    /// assert_eq!(trail.decision_count(), 1);
    /// ```
    pub fn decision_count(&self) -> usize {
        self.decisions.len()
    }

    /// Marks a decision at the current log length. Call this before logging the
    /// observation's own domain change.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{SlotId, VariantId};
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.push_decision(SlotId(3), VariantId(1));
    /// assert_eq!(trail.decision_count(), 1);
    /// ```
    pub fn push_decision(&mut self, slot: SlotId, variant: VariantId) {
        self.decisions.push(Decision {
            trail_len: self.changes.len(),
            slot,
            variant,
        });
    }

    /// Logs that `slot` held `old` before the solver changed it.
    ///
    /// Cost: O(1); `old` is moved in, not copied.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.push_change(SlotId(0), Bitset::full(4));
    /// assert_eq!(trail.change_count(), 1);
    /// ```
    pub fn push_change(&mut self, slot: SlotId, old: Bitset) {
        // RUST: `old: Bitset` by value: ownership moves from the caller into the
        // log. The caller's variable is unusable afterwards, which is right:
        // the domain it described no longer exists.
        self.changes.push(DomainChange { slot, old });
    }

    /// Undoes everything since the last decision and forbids that decision's
    /// variant in its slot.
    ///
    /// Restores domains newest-first (so a slot changed twice ends at its oldest
    /// logged state), pops the decision, then removes the chosen variant from the
    /// slot and logs that removal, which now belongs to the previous decision.
    /// Returns `None` when there is no decision to undo: the attempt has failed.
    ///
    /// Cost: O(changes since the decision) moves plus a sort of the touched list.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::{SlotId, VariantId};
    /// use lamda_core::trail::Trail;
    /// let mut domains = vec![Bitset::from_bits(2, &[0]), Bitset::from_bits(2, &[1])];
    /// let mut trail = Trail::new();
    /// // Decide slot 0 = variant 0 (it was {0, 1}), which forced slot 1 = 1 (was {0, 1}).
    /// trail.push_decision(SlotId(0), VariantId(0));
    /// trail.push_change(SlotId(0), Bitset::full(2));
    /// trail.push_change(SlotId(1), Bitset::full(2));
    /// let undone = trail.backtrack(&mut domains).unwrap();
    /// assert_eq!(undone.touched, vec![SlotId(0), SlotId(1)]);
    /// // Slot 0 is back to {0, 1} minus the failed choice 0; slot 1 is {0, 1} again.
    /// assert_eq!(domains[0].single(), Some(1));
    /// assert_eq!(domains[1].count(), 2);
    /// assert_eq!(trail.decision_count(), 0);
    /// assert_eq!(trail.change_count(), 1); // the removal of variant 0, under no decision
    /// ```
    pub fn backtrack(&mut self, domains: &mut [Bitset]) -> Option<Backtrack> {
        let decision = self.decisions.pop()?;
        // RUST: `pop()?` returns None from this function if there is no decision.
        let mut touched = Vec::new();
        // WFC: restore newest-first down to the decision mark.
        while self.changes.len() > decision.trail_len {
            // RUST: the `while` condition guarantees `pop()` finds an element, but
            // `let Some(..) = .. else` keeps the code panic-free rather than unwrapping.
            let Some(change) = self.changes.pop() else {
                break;
            };
            domains[change.slot.index()] = change.old;
            // RUST: `change.old` moves out of the popped struct into the slice; the
            // Bitset that was there is dropped. No clone anywhere.
            touched.push(change.slot);
        }
        // WFC: forbid the failed choice, logged under the previous decision.
        let slot = decision.slot;
        let old = domains[slot.index()].clone();
        // RUST: `.clone()` here is needed: the log must keep the state before the
        // removal while the live domain moves on.
        domains[slot.index()].remove(decision.variant.index());
        self.changes.push(DomainChange { slot, old });
        touched.push(slot);
        touched.sort_unstable();
        touched.dedup();
        // RUST: `sort_unstable` is fine for plain integers (no equal-element order
        // to preserve) and faster than `sort`; `dedup` drops adjacent duplicates.
        Some(Backtrack { decision, touched })
    }

    /// Forgets every change and decision, keeping allocations.
    ///
    /// Cost: O(entries).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{SlotId, VariantId};
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.push_decision(SlotId(0), VariantId(0));
    /// trail.clear();
    /// assert_eq!(trail.decision_count(), 0);
    /// ```
    pub fn clear(&mut self) {
        self.changes.clear();
        self.decisions.clear();
    }

    /// Takes the whole change log out, leaving the trail empty of changes; used
    /// when a result is final and the memory can go.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::trail::Trail;
    /// let mut trail = Trail::new();
    /// trail.push_change(SlotId(0), Bitset::full(2));
    /// let log = trail.take_changes();
    /// assert_eq!(log.len(), 1);
    /// assert_eq!(trail.change_count(), 0);
    /// ```
    pub fn take_changes(&mut self) -> Vec<DomainChange> {
        // RUST: `mem::take` swaps in `Vec::default()` (empty) and returns the old
        // Vec by value, so the caller owns it without a copy.
        std::mem::take(&mut self.changes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A slot changed twice since the decision must end at its oldest logged
    /// state, which newest-first restoration guarantees.
    #[test]
    fn restores_oldest_state_for_repeated_slot() {
        let mut domains = vec![Bitset::from_bits(4, &[0])];
        let mut trail = Trail::new();
        trail.push_decision(SlotId(0), VariantId(0));
        trail.push_change(SlotId(0), Bitset::full(4));
        trail.push_change(SlotId(0), Bitset::from_bits(4, &[0, 1]));
        let undone = trail.backtrack(&mut domains).unwrap();
        assert_eq!(undone.touched, vec![SlotId(0)]);
        // Full {0,1,2,3} restored, then variant 0 forbidden.
        assert_eq!(domains[0].ones().collect::<Vec<_>>(), vec![1, 2, 3]);
    }

    /// With no decision left, backtracking reports failure and changes nothing.
    #[test]
    fn no_decision_means_none() {
        let mut domains = vec![Bitset::full(2)];
        let mut trail = Trail::new();
        trail.push_change(SlotId(0), Bitset::full(2));
        assert_eq!(trail.backtrack(&mut domains), None);
        assert_eq!(trail.change_count(), 1);
    }

    /// Two nested decisions undo one at a time, newest first.
    #[test]
    fn nested_decisions_undo_in_order() {
        let mut domains = vec![Bitset::from_bits(3, &[0]), Bitset::from_bits(3, &[1])];
        let mut trail = Trail::new();
        trail.push_decision(SlotId(0), VariantId(0));
        trail.push_change(SlotId(0), Bitset::full(3));
        trail.push_decision(SlotId(1), VariantId(1));
        trail.push_change(SlotId(1), Bitset::full(3));
        let first = trail.backtrack(&mut domains).unwrap();
        assert_eq!(first.decision.slot, SlotId(1));
        assert_eq!(domains[1].ones().collect::<Vec<_>>(), vec![0, 2]);
        assert_eq!(
            domains[0].single(),
            Some(0),
            "the older decision is untouched"
        );
        let second = trail.backtrack(&mut domains).unwrap();
        assert_eq!(second.decision.slot, SlotId(0));
        assert_eq!(second.touched, vec![SlotId(0), SlotId(1)]);
        assert_eq!(domains[0].ones().collect::<Vec<_>>(), vec![1, 2]);
        assert_eq!(
            domains[1].count(),
            3,
            "slot 1's removal was logged under decision 0 and is undone"
        );
    }
}
