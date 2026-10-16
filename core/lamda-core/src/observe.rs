// SPDX-License-Identifier: MIT OR Apache-2.0

//! Observation: which slot next, and which variant for it.
//!
//! Where it sits: after propagation settles, the solver observes: it takes the
//! open slot with the smallest key (priority, entropy, noise, index) and draws
//! one of its variants in proportion to the effective weights
//! (`docs/PLAN.md`, "Solver, step by step", step 3). The slot order lives in a
//! binary heap with lazy deletion: every domain change bumps the slot's version
//! and pushes a fresh entry; stale entries are skipped when popped
//! (`.claude/rules/solver-core.md`, "Entropy and observation").
//!
//! Rust in this file:
//! - `BinaryHeap`: a max-heap; `Reverse(x)` flips the order so `pop` returns the
//!   smallest key. <https://doc.rust-lang.org/std/collections/struct.BinaryHeap.html>
//! - `std::cmp::Reverse`: a wrapper whose `Ord` is the reverse of its contents.
//!   <https://doc.rust-lang.org/std/cmp/struct.Reverse.html>
//! - derived `Ord` on a struct with several fields (declaration order), as in
//!   `entropy.rs`.
//! - `loop` with `break value`: a loop that returns a value.
//!   <https://doc.rust-lang.org/book/ch03-05-control-flow.html#returning-values-from-loops>
//!
//! Read next: `trail.rs`.

use std::cmp::Reverse;
use std::collections::BinaryHeap;

use crate::assembly::Assembly;
use crate::bitset::Bitset;
use crate::entropy::{SlotKey, Weights};
use crate::ids::{SlotId, VariantId};
use crate::rng::Pcg32;

/// One heap entry: a key and the slot version it was computed for.
///
/// WHY the version is inside the entry: when an entry is popped, its version is
/// compared with the slot's current version; a mismatch means the slot changed
/// since and a newer entry exists, so this one is skipped. Entropy can go up as
/// well as down, so keys cannot be compared to decide staleness.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Entry {
    /// The order key; compared first.
    key: SlotKey,
    /// The slot's version when this entry was pushed; compared only on exact ties,
    /// which cannot happen because `key` contains the slot index.
    version: u32,
}

/// The observation agenda: per-slot versions, noise and priority, and the heap.
///
/// Invariant: for every open slot, the heap holds an entry with the slot's
/// current version (possibly among stale ones).
///
/// Cost: O(slots) memory plus one heap entry per domain change; entries are
/// released as they are popped.
#[derive(Debug, Clone)]
pub struct Agenda {
    /// Min-heap of entries (through `Reverse`).
    heap: BinaryHeap<Reverse<Entry>>,
    /// Version per slot, bumped on every domain change. `u32` wraps after four
    /// billion changes of one slot, which no solve approaches.
    version: Vec<u32>,
    /// Tie-break noise per slot, drawn once per attempt in slot order.
    noise: Vec<u32>,
    /// Priority per slot; 0 when the assembly has none.
    priority: Vec<i32>,
}

impl Agenda {
    /// Creates the agenda for an attempt, drawing one `next_u32` per slot from
    /// `rng` in slot order. This is the first use of the attempt's stream, as
    /// the specification fixes it. The heap starts empty; call `touch` for every
    /// slot once its entropy is known.
    ///
    /// Cost: O(slots).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
    /// assert_eq!(agenda.version(lamda_core::ids::SlotId(0)), 0);
    /// ```
    pub fn new(assembly: &Assembly, rng: &mut Pcg32) -> Agenda {
        let slots = assembly.slot_count();
        // RUST: `(0..slots).map(|_| rng.next_u32()).collect()` draws in order; the
        // closure captures `rng` by mutable reference, so nothing else may use
        // the generator until the Vec is built.
        let noise: Vec<u32> = (0..slots).map(|_| rng.next_u32()).collect();
        let priority = match &assembly.priority {
            Some(values) => values.clone(),
            None => vec![0; slots],
        };
        Agenda {
            heap: BinaryHeap::with_capacity(slots * 2),
            version: vec![0; slots],
            noise,
            priority,
        }
    }

    /// The current version of `slot`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let mut agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
    /// agenda.touch(SlotId(1), 42, 2);
    /// assert_eq!(agenda.version(SlotId(1)), 1);
    /// ```
    pub fn version(&self, slot: SlotId) -> u32 {
        self.version[slot.index()]
    }

    /// Records that `slot`'s domain changed: bumps its version and, if it is
    /// still open (`count > 1`), pushes a fresh entry with `entropy_key`.
    ///
    /// Cost: O(log heap size).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let mut agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
    /// agenda.touch(SlotId(0), 500, 2);
    /// agenda.touch(SlotId(1), 100, 2);
    /// let domains = vec![Bitset::full(2), Bitset::full(2)];
    /// assert_eq!(agenda.next_open(&domains), Some(SlotId(1))); // lower key first
    /// ```
    pub fn touch(&mut self, slot: SlotId, entropy_key: i64, count: usize) {
        let version = &mut self.version[slot.index()];
        // RUST: `version` is a `&mut u32` into the Vec; `*version` reads/writes it.
        *version = version.wrapping_add(1);
        if count > 1 {
            // PERF: a collapsed slot would be skipped on pop anyway; not pushing it
            // keeps the heap smaller. Results are identical either way.
            let key = SlotKey::new(
                slot,
                self.priority[slot.index()],
                entropy_key,
                self.noise[slot.index()],
            );
            self.heap.push(Reverse(Entry {
                key,
                version: *version,
            }));
        }
    }

    /// Pops entries until one is current and its slot is still open, and returns
    /// that slot; `None` when every slot is collapsed (or empty).
    ///
    /// Cost: amortised O(log heap size) per popped entry; each entry is popped
    /// at most once over the whole attempt.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let mut agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
    /// agenda.touch(SlotId(0), 1, 2);
    /// // Slot 0 collapses before it is popped: the entry is stale by domain.
    /// let domains = vec![Bitset::from_bits(2, &[0]), Bitset::full(2)];
    /// assert_eq!(agenda.next_open(&domains), None);
    /// ```
    pub fn next_open(&mut self, domains: &[Bitset]) -> Option<SlotId> {
        // RUST: `loop` runs until `break` or `return`; here every path returns.
        loop {
            // RUST: `pop()` is `Option<Reverse<Entry>>`; the `?` returns `None`
            // from this function when the heap is empty. `Reverse(entry)` in the
            // pattern unwraps the wrapper.
            let Reverse(entry) = self.heap.pop()?;
            let slot = SlotId(entry.key.slot);
            let current = entry.version == self.version[slot.index()];
            let open = domains[slot.index()].count() > 1;
            // WFC: use the entry only if nothing changed since it was pushed and
            // the slot still has a choice to make.
            if current && open {
                return Some(slot);
            }
        }
    }

    /// Number of entries in the heap, stale ones included; for tests and tuning.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0)).heap_len(), 0);
    /// ```
    pub fn heap_len(&self) -> usize {
        self.heap.len()
    }

    /// Drops every entry; versions are kept, so later `touch` calls stay current.
    ///
    /// Cost: O(entries).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::SlotId;
    /// use lamda_core::observe::Agenda;
    /// use lamda_core::rng::Pcg32;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let mut agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
    /// agenda.touch(SlotId(0), 1, 2);
    /// agenda.clear();
    /// assert_eq!(agenda.heap_len(), 0);
    /// ```
    pub fn clear(&mut self) {
        self.heap.clear();
    }
}

/// Draws one variant from `domain` for `slot`, with probability proportional to
/// its effective weight, using one `unit_f64` from `rng`.
///
/// WFC: `W'` is summed afresh over the domain in ascending order (not the
/// running sum, whose rounding differs), `r = unit × W'`, then the variants are
/// walked in ascending order subtracting each weight until `r < 0`. If rounding
/// leaves none, the last variant is chosen (`.claude/rules/solver-core.md`,
/// "Variant choice").
///
/// Cost: O(count(domain)).
///
/// # Panics
/// If `domain` is empty (a solver bug: observation only happens on open slots).
///
/// # Examples
/// ```
/// use lamda_core::assembly::{Assembly, examples};
/// use lamda_core::bitset::Bitset;
/// use lamda_core::entropy::Weights;
/// use lamda_core::ids::{SlotId, VariantId};
/// use lamda_core::observe::choose_variant;
/// use lamda_core::rng::Pcg32;
/// let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
/// assembly.variant_weight = vec![1.0, 0.0];
/// let weights = Weights::from_assembly(&assembly);
/// let mut rng = Pcg32::for_attempt(3, 0);
/// // Variant 1 has weight 0, so variant 0 is drawn whatever the random number.
/// let v = choose_variant(SlotId(0), &Bitset::full(2), &weights, &mut rng);
/// assert_eq!(v, VariantId(0));
/// ```
pub fn choose_variant(
    slot: SlotId,
    domain: &Bitset,
    weights: &Weights,
    rng: &mut Pcg32,
) -> VariantId {
    let mut total = 0.0;
    for v in domain.ones() {
        total += weights.effective(slot, VariantId(v as u32));
    }
    let mut r = rng.unit_f64() * total;
    let mut last = None;
    for v in domain.ones() {
        let variant = VariantId(v as u32);
        r -= weights.effective(slot, variant);
        if r < 0.0 {
            return variant;
        }
        last = Some(variant);
    }
    // RUST: `expect` is allowed here because the caller guarantees a non-empty
    // domain, and the message documents that contract; clippy's `expect_used`
    // lint is denied outside tests, so this line needs an explicit allow.
    #[allow(clippy::expect_used)]
    last.expect("choose_variant called on an empty domain")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::examples;

    /// Weights for three variants, no per-slot multipliers.
    fn weights(values: &[f64]) -> Weights {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.variant_weight = values.to_vec();
        for _ in 2..values.len() {
            let extra = assembly.variants[0].clone();
            assembly.variants.push(extra);
        }
        Weights::from_assembly(&assembly)
    }

    /// Stale entries must be skipped: after a second `touch`, the first entry's
    /// version no longer matches and must not be returned, even if its key is
    /// smaller (entropy may have gone up).
    #[test]
    fn stale_entries_are_skipped() {
        let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let mut agenda = Agenda::new(&assembly, &mut Pcg32::for_attempt(1, 0));
        agenda.touch(SlotId(0), 1, 2);
        agenda.touch(SlotId(0), 900, 2);
        agenda.touch(SlotId(1), 500, 2);
        let domains = vec![Bitset::full(2), Bitset::full(2)];
        assert_eq!(agenda.next_open(&domains), Some(SlotId(1)));
        assert_eq!(agenda.next_open(&domains), Some(SlotId(0)));
        assert_eq!(agenda.next_open(&domains), None);
    }

    /// The draw must follow the weights: with weights 1, 2, 3 and r walking in
    /// ascending order, unit values in [0, 1/6) give v0, [1/6, 1/2) give v1, the
    /// rest v2. We check by counting over many seeds.
    #[test]
    fn choice_follows_weights() {
        let weights = weights(&[1.0, 2.0, 3.0]);
        let domain = Bitset::full(3);
        let mut counts = [0usize; 3];
        for seed in 0..6000u64 {
            let mut rng = Pcg32::for_attempt(seed, 0);
            let v = choose_variant(SlotId(0), &domain, &weights, &mut rng);
            counts[v.index()] += 1;
        }
        // Expected about 1000, 2000, 3000; allow a wide margin.
        assert!((800..1200).contains(&counts[0]), "{counts:?}");
        assert!((1750..2250).contains(&counts[1]), "{counts:?}");
        assert!((2700..3300).contains(&counts[2]), "{counts:?}");
    }

    /// Variants outside the domain must never be drawn.
    #[test]
    fn choice_stays_in_domain() {
        let weights = weights(&[1.0, 1.0, 1.0]);
        let domain = Bitset::from_bits(3, &[2]);
        for seed in 0..50u64 {
            let mut rng = Pcg32::for_attempt(seed, 0);
            assert_eq!(
                choose_variant(SlotId(0), &domain, &weights, &mut rng),
                VariantId(2)
            );
        }
    }
}
