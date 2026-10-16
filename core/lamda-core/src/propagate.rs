// SPDX-License-Identifier: MIT OR Apache-2.0

//! Propagation: bitset AC-3 over the slot graph.
//!
//! Where it sits: the middle of the solver loop. After every observation (and
//! once at the start, to canonicalise) the changed slots go on a queue; each
//! popped slot pushes its constraints across every edge, shrinking neighbours,
//! which join the queue in turn, until nothing changes or a slot empties
//! (`docs/PLAN.md`, "Solver, step by step", step 2; pseudo-code there).
//! Entropy sums and the heap are updated by the caller through a callback, so
//! this file knows nothing about them (`entropy.rs`, `observe.rs`).
//!
//! Rust in this file:
//! - `VecDeque`: a double-ended queue; `push_back` and `pop_front` make a FIFO.
//!   <https://doc.rust-lang.org/std/collections/struct.VecDeque.html>
//! - closures as parameters with `FnMut`: the caller passes a function-like
//!   value that may mutate what it captured; we call it on every change.
//!   <https://doc.rust-lang.org/book/ch13-01-closures.html>
//! - early returns with `?` on `Result`, and `let ... else`.
//! - `std::mem::replace`: swap a new value in and get the old one out, without
//!   cloning. <https://doc.rust-lang.org/std/mem/fn.replace.html>
//! - loops: `while let Some(x) = queue.pop_front()` runs until the queue is empty.
//!
//! Read next: `entropy.rs`.

use std::collections::VecDeque;
use thiserror::Error;

use crate::adjacency::Adjacency;
use crate::bitset::Bitset;
use crate::graph::Graph;
use crate::ids::{PortId, SlotId, Twist};
use crate::progress::Progress;

/// How often, in queue pops, the cancel flag is checked.
const CANCEL_EVERY: u32 = 256;

/// Why propagation stopped before the queue emptied.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum PropagateError {
    /// A slot lost its last variant: the current choices cannot be completed.
    #[error("contradiction: slot {slot} has no variant left")]
    Contradiction {
        /// The slot that emptied.
        slot: SlotId,
    },
    /// The host raised the cancel flag.
    #[error("cancelled")]
    Cancelled,
    /// The graph has an edge whose (port, twist) has no adjacency table; a
    /// compiler bug, caught here rather than treated as "anything goes".
    #[error("no adjacency table for port {port} twist {twist}")]
    MissingTable {
        /// The port.
        port: PortId,
        /// The twist.
        twist: Twist,
    },
}

/// One logged change: `slot` held `old` before propagation shrank it.
///
/// WFC: the trail (`trail.rs`) is a stack of these; backtracking pops them and
/// restores `old`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DomainChange {
    /// The slot that changed.
    pub slot: SlotId,
    /// Its domain before the change. Owned, because the live domain moves on.
    pub old: Bitset,
}

/// The work queue: slots whose constraints must be pushed to their neighbours.
///
/// Invariant: `in_queue[s]` is true exactly when `s` is in `queue`, so a slot is
/// queued at most once at a time.
///
/// Cost: O(slots) memory.
#[derive(Debug, Clone)]
pub struct Propagator {
    /// FIFO of slots to process. WHY FIFO: the Python reference uses the same
    /// order, and order changes which contradiction is found first.
    queue: VecDeque<SlotId>,
    /// One flag per slot. `Vec<bool>` is fine here: one byte per slot, read and
    /// written one at a time, never combined word-wise like a domain.
    in_queue: Vec<bool>,
}

impl Propagator {
    /// Creates an empty queue for `slots` slots.
    ///
    /// Cost: O(slots).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::propagate::Propagator;
    /// let propagator = Propagator::new(10);
    /// assert!(propagator.is_empty());
    /// ```
    pub fn new(slots: usize) -> Propagator {
        Propagator {
            queue: VecDeque::with_capacity(slots),
            in_queue: vec![false; slots],
        }
    }

    /// Whether no slot is waiting.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::propagate::Propagator;
    /// let mut propagator = Propagator::new(2);
    /// propagator.enqueue(SlotId(1));
    /// assert!(!propagator.is_empty());
    /// ```
    pub fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }

    /// Adds `slot` to the queue unless it is already there.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::propagate::Propagator;
    /// let mut propagator = Propagator::new(2);
    /// propagator.enqueue(SlotId(1));
    /// propagator.enqueue(SlotId(1)); // second call is a no-op
    /// assert_eq!(propagator.len(), 1);
    /// ```
    pub fn enqueue(&mut self, slot: SlotId) {
        // RUST: `&mut self` because both fields change.
        if !self.in_queue[slot.index()] {
            self.in_queue[slot.index()] = true;
            self.queue.push_back(slot);
        }
    }

    /// Adds every slot, in slot order; used to canonicalise at the start.
    ///
    /// Cost: O(slots).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::propagate::Propagator;
    /// let mut propagator = Propagator::new(3);
    /// propagator.enqueue_all();
    /// assert_eq!(propagator.len(), 3);
    /// ```
    pub fn enqueue_all(&mut self) {
        for index in 0..self.in_queue.len() {
            self.enqueue(SlotId(index as u32));
            // RUST: `as u32` is safe: the slot count came from a u32.
        }
    }

    /// Number of slots waiting.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::propagate::Propagator;
    /// assert_eq!(Propagator::new(4).len(), 0);
    /// ```
    pub fn len(&self) -> usize {
        self.queue.len()
    }

    /// Empties the queue, for example after a contradiction before backtracking.
    ///
    /// Cost: O(queued slots).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::propagate::Propagator;
    /// let mut propagator = Propagator::new(3);
    /// propagator.enqueue_all();
    /// propagator.clear();
    /// assert!(propagator.is_empty());
    /// ```
    pub fn clear(&mut self) {
        // RUST: `drain(..)` removes and yields every element; the loop consumes
        // them so the flags can be reset one by one.
        for slot in self.queue.drain(..) {
            self.in_queue[slot.index()] = false;
        }
    }

    /// Runs AC-3 until the queue is empty, a slot empties, or cancel is raised.
    ///
    /// For each popped slot `s` and each of its edges in CSR order: the support is
    /// the OR of the table rows of `s`'s remaining variants; the neighbour keeps
    /// only variants in the support. When a neighbour shrinks, its old domain is
    /// pushed on `changes`, `on_change(neighbour, removed, new)` is called, and
    /// the neighbour is queued (or a contradiction returned if it emptied).
    /// Every loop over a domain runs in ascending variant order.
    ///
    /// Cost: each variant leaves each slot at most once, so the total over a
    /// whole solve is O(slots × variants × degree × words) in the worst case,
    /// where words = variants / 64 (`docs/PLAN.md`, "Solver, step by step").
    ///
    /// # Errors
    /// [`PropagateError::Contradiction`] with the emptied slot (its change is
    /// already logged, so the trail can restore it); [`PropagateError::Cancelled`];
    /// [`PropagateError::MissingTable`] for an edge type without a table.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::graph::Graph;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::progress::Progress;
    /// use lamda_core::propagate::Propagator;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let graph = Graph::from_assembly(&assembly).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // Both slots start as {a, b}; the only rule is "a west of b".
    /// let mut domains = vec![Bitset::full(2), Bitset::full(2)];
    /// let mut propagator = Propagator::new(2);
    /// propagator.enqueue_all();
    /// let mut changes = Vec::new();
    /// propagator
    ///     .propagate(&mut domains, &graph, &adjacency, &mut changes, &Progress::new(), |_, _, _| {})
    ///     .unwrap();
    /// // Slot 0 must be a (it needs an allowed east neighbour) and slot 1 must be b.
    /// assert_eq!(domains[0].single(), Some(0));
    /// assert_eq!(domains[1].single(), Some(1));
    /// assert_eq!(changes.len(), 2);
    /// ```
    pub fn propagate<F>(
        &mut self,
        domains: &mut [Bitset],
        graph: &Graph,
        adjacency: &Adjacency,
        changes: &mut Vec<DomainChange>,
        progress: &Progress,
        mut on_change: F,
    ) -> Result<(), PropagateError>
    where
        F: FnMut(SlotId, &Bitset, &Bitset),
    {
        // RUST: `F` is a generic type parameter bounded by `FnMut(SlotId, &Bitset,
        // &Bitset)`: the caller may pass any closure (or function) with that
        // signature, and it may mutate its captured state each call, so the
        // binding is `mut on_change`. `where` just moves the bound off the line.
        // RUST: `domains: &mut [Bitset]` is a mutable slice: we change domains
        // in place but cannot grow or shrink the collection.
        let mut pops: u32 = 0;
        // RUST: `while let` loops as long as the pattern matches; `pop_front`
        // returns `None` when the queue is empty, which ends the loop.
        while let Some(slot) = self.queue.pop_front() {
            self.in_queue[slot.index()] = false;
            pops = pops.wrapping_add(1);
            // RUST: wrapping so a very long propagation cannot panic on overflow.
            if pops % CANCEL_EVERY == 0 && progress.is_cancelled() {
                return Err(PropagateError::Cancelled);
            }
            // WFC: one edge at a time, in the order the file lists them.
            for edge in graph.edges(slot) {
                // RUST: `graph.edges(slot)` borrows `graph` immutably; `domains` is a
                // separate borrow, so both can be live at once.
                let support = adjacency
                    .support(edge.port, edge.twist, &domains[slot.index()])
                    .ok_or(PropagateError::MissingTable {
                        port: edge.port,
                        twist: edge.twist,
                    })?;
                // RUST: `ok_or` turns `Option` into `Result`, then `?` returns the
                // error if there was no table.
                let neighbour = edge.neighbour;
                // PERF: test before cloning; most edge visits change nothing.
                if domains[neighbour.index()].is_subset_of(&support) {
                    continue;
                }
                self.shrink(domains, neighbour, &support, changes, &mut on_change)?;
                // RUST: `&mut on_change` lends the closure to `shrink` for one call;
                // we keep ownership of it for the next loop iteration.
            }
        }
        Ok(())
    }

    /// Applies `support` to `neighbour`, logs the change, notifies the caller,
    /// and queues the neighbour or reports a contradiction.
    ///
    /// Cost: O(words), plus the callback.
    fn shrink<F>(
        &mut self,
        domains: &mut [Bitset],
        neighbour: SlotId,
        support: &Bitset,
        changes: &mut Vec<DomainChange>,
        on_change: &mut F,
    ) -> Result<(), PropagateError>
    where
        F: FnMut(SlotId, &Bitset, &Bitset),
    {
        // WFC: new = domain & support, keeping the old domain for the trail.
        let new = &domains[neighbour.index()] & support;
        // RUST: `&a & &b` uses the `BitAnd for &Bitset` impl from bitset.rs and
        // returns a fresh Bitset; neither operand is consumed.
        let old = std::mem::replace(&mut domains[neighbour.index()], new);
        // RUST: `mem::replace` puts `new` into the slot and hands us the previous
        // Bitset by value, no clone. `new` is moved and can no longer be named.
        let removed = old.difference(&domains[neighbour.index()]);
        // WFC: tell the caller what left, so it can subtract weights in ascending
        // order (entropy.rs) and bump the heap version (observe.rs).
        on_change(neighbour, &removed, &domains[neighbour.index()]);
        let emptied = domains[neighbour.index()].is_empty();
        changes.push(DomainChange {
            slot: neighbour,
            old,
        });
        // RUST: `old` moves into the change record; the trail now owns it.
        if emptied {
            // WFC: contradiction. The change is logged, so backtracking can undo it.
            return Err(PropagateError::Contradiction { slot: neighbour });
        }
        self.enqueue(neighbour);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::{Assembly, examples};

    /// Builds the two-slot example and its tables.
    fn two_slots() -> (Assembly, Graph, Adjacency) {
        let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let graph = Graph::from_assembly(&assembly).unwrap();
        let adjacency = Adjacency::from_assembly(&assembly).unwrap();
        (assembly, graph, adjacency)
    }

    /// A contradiction must name the slot that emptied and leave its change on
    /// the log, so the trail can restore it.
    #[test]
    fn contradiction_is_logged_then_reported() {
        let (_, graph, adjacency) = two_slots();
        // Slot 0 = b, slot 1 = b: b has no allowed east neighbour.
        let mut domains = vec![Bitset::from_bits(2, &[1]), Bitset::from_bits(2, &[1])];
        let mut propagator = Propagator::new(2);
        propagator.enqueue(SlotId(0));
        let mut changes = Vec::new();
        let err = propagator
            .propagate(
                &mut domains,
                &graph,
                &adjacency,
                &mut changes,
                &Progress::new(),
                |_, _, _| {},
            )
            .unwrap_err();
        assert_eq!(err, PropagateError::Contradiction { slot: SlotId(1) });
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0].old, Bitset::from_bits(2, &[1]));
        assert!(domains[1].is_empty());
    }

    /// The callback must receive exactly the removed variants and the new domain,
    /// because the entropy sums depend on both.
    #[test]
    fn callback_sees_removed_and_new() {
        let (_, graph, adjacency) = two_slots();
        let mut domains = vec![Bitset::from_bits(2, &[0]), Bitset::full(2)];
        let mut propagator = Propagator::new(2);
        propagator.enqueue(SlotId(0));
        let mut changes = Vec::new();
        let mut seen = Vec::new();
        propagator
            .propagate(
                &mut domains,
                &graph,
                &adjacency,
                &mut changes,
                &Progress::new(),
                |slot, removed, new| {
                    // RUST: this closure captures `seen` by mutable reference.
                    seen.push((slot, removed.clone(), new.clone()));
                },
            )
            .unwrap();
        assert_eq!(
            seen,
            vec![(
                SlotId(1),
                Bitset::from_bits(2, &[0]),
                Bitset::from_bits(2, &[1])
            )]
        );
    }

    /// A raised cancel flag must stop propagation within 256 pops.
    #[test]
    fn cancel_stops_propagation() {
        let (_, graph, adjacency) = two_slots();
        let mut domains = vec![Bitset::full(2), Bitset::full(2)];
        let mut propagator = Propagator::new(2);
        // Queue 256 pops' worth of work by re-enqueueing through the callback is
        // awkward; instead check the flag path directly with a pre-filled queue.
        for _ in 0..CANCEL_EVERY {
            propagator.queue.push_back(SlotId(0));
        }
        let progress = Progress::new();
        progress.cancel();
        let err = propagator
            .propagate(
                &mut domains,
                &graph,
                &adjacency,
                &mut Vec::new(),
                &progress,
                |_, _, _| {},
            )
            .unwrap_err();
        assert_eq!(err, PropagateError::Cancelled);
    }

    /// `clear` must reset the flags, or slots could never be queued again.
    #[test]
    fn clear_resets_flags() {
        let mut propagator = Propagator::new(2);
        propagator.enqueue_all();
        propagator.clear();
        propagator.enqueue(SlotId(1));
        assert_eq!(propagator.len(), 1);
    }
}
