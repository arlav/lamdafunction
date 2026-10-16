// SPDX-License-Identifier: MIT OR Apache-2.0

//! The solver: setup, canonicalisation, and the observe–propagate–backtrack loop
//! of one attempt.
//!
//! Where it sits: the top of the single-attempt pipeline. `Solver::new` turns a
//! compiled assembly into graph, tables, weights and canonical domains (one
//! propagation pass before any observation). `Solver::run_attempt` then runs
//! the loop of `docs/PLAN.md`, "Solver, step by step": observe the lowest-key
//! open slot, draw a variant, propagate, and on a contradiction backtrack to the
//! last decision until the budget runs out. Parallel attempts live in
//! `attempts.rs`; every result is checked by `validate.rs` in debug builds.
//!
//! Rust in this file:
//! - traits: `Domain` lists the operations the loop needs from a domain type, so
//!   a sparse domain can implement it later; `impl Domain for Bitset` is the
//!   only implementation today. <https://doc.rust-lang.org/book/ch10-02-traits.html>
//! - generics with trait bounds, `fn f<D: Domain>(..)`, on the small helpers
//!   that only need the trait. <https://doc.rust-lang.org/book/ch10-01-syntax.html>
//! - a struct holding a reference, `Solver<'a>`: the solver borrows the
//!   assembly and cannot outlive it.
//!   <https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html#lifetime-annotations-in-struct-definitions>
//! - destructuring a struct, `let Attempt { domains, sums, .. } = &mut attempt;`,
//!   to borrow several fields mutably at once (the borrow checker tracks fields
//!   separately only when they are named separately).
//! - `std::time::Instant` for elapsed time.
//! - `thiserror` enums with `#[from]` chaining several lower-level errors.
//!
//! Read next: `attempts.rs`.

use serde::{Deserialize, Serialize};
use std::time::Instant;
use thiserror::Error;

use crate::adjacency::{Adjacency, AdjacencyError};
use crate::assembly::{Assembly, LoadError};
use crate::bitset::Bitset;
use crate::entropy::{EntropySums, Weights};
use crate::graph::{Graph, GraphError};
use crate::ids::{PortId, SlotId, Twist, VariantId};
use crate::observe::{Agenda, choose_variant};
use crate::progress::Progress;
use crate::propagate::{PropagateError, Propagator};
use crate::rng::Pcg32;
use crate::trail::Trail;

/// What the solver loop needs from a domain. `Bitset` implements it; a sparse
/// domain for very large variant counts can implement it later without changing
/// the loop (`docs/PLAN.md`, "State").
///
/// WHY a trait: the loop is written against these operations only, so the data
/// structure can change under it. The lower modules (`propagate.rs`,
/// `entropy.rs`) still take `Bitset` directly; making them generic is the
/// follow-up step if spike 4 shows sparse domains are needed.
pub trait Domain {
    /// Number of variants still allowed.
    fn count(&self) -> usize;
    /// Whether no variant is allowed: a contradiction.
    fn is_empty(&self) -> bool;
    /// The only variant left, if exactly one is.
    fn single(&self) -> Option<usize>;
    /// Whether `variant` is allowed.
    fn contains(&self, variant: usize) -> bool;
}

impl Domain for Bitset {
    // RUST: an `impl Trait for Type` block provides each method of the trait;
    // the compiler checks nothing is missing. These forward to the inherent
    // methods of the same name.

    /// Forwards to [`Bitset::count`].
    fn count(&self) -> usize {
        Bitset::count(self)
    }

    /// Forwards to [`Bitset::is_empty`].
    fn is_empty(&self) -> bool {
        Bitset::is_empty(self)
    }

    /// Forwards to [`Bitset::single`].
    fn single(&self) -> Option<usize> {
        Bitset::single(self)
    }

    /// Forwards to [`Bitset::contains`].
    fn contains(&self, variant: usize) -> bool {
        Bitset::contains(self, variant)
    }
}

/// Why an assembly could not be set up for solving.
#[derive(Debug, Error)]
pub enum SolveError {
    /// The assembly file failed to load or check.
    #[error(transparent)]
    // RUST: `transparent` forwards `Display` to the inner error unchanged.
    Load(#[from] LoadError),
    /// The slot graph is malformed.
    #[error(transparent)]
    Graph(#[from] GraphError),
    /// The adjacency tables are malformed.
    #[error(transparent)]
    Adjacency(#[from] AdjacencyError),
    /// An edge uses a (port, twist) with no table.
    #[error("no adjacency table for port {port} twist {twist}, used by slot {slot}")]
    MissingTable {
        /// The slot owning the edge.
        slot: SlotId,
        /// The port.
        port: PortId,
        /// The twist.
        twist: Twist,
    },
    /// The rayon thread pool for parallel attempts could not be built.
    #[error("cannot build thread pool: {0}")]
    ThreadPool(String),
    /// Canonicalisation emptied a slot: the setup itself is contradictory.
    #[error("canonicalisation contradiction at slot {}", .0.contradiction.map_or(-1, |s| s.0 as i64))]
    Canonical(CanonicalReport),
}

/// What canonicalisation found, for the Python audit.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CanonicalReport {
    /// The slot that emptied, if canonicalisation hit a contradiction.
    pub contradiction: Option<SlotId>,
    /// Variants left in no slot's domain after canonicalisation.
    pub dead_variants: Vec<VariantId>,
    /// Slots already collapsed to one variant by canonicalisation.
    pub collapsed: usize,
}

/// How an attempt ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Outcome {
    /// Every slot holds exactly one variant.
    Solved,
    /// Stopped at `max_observations`; open slots remain.
    Partial,
    /// The backtrack budget ran out, or no decision was left to undo.
    Contradiction,
    /// The host cancelled.
    Cancelled,
}

/// Numbers about one attempt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Report {
    /// Seed of attempt 0.
    pub seed: u64,
    /// Which attempt this is.
    pub attempt: u64,
    /// Observations made.
    pub observations: u64,
    /// Backtracks taken.
    pub backtracks: u64,
    /// Wall-clock seconds for this attempt.
    pub elapsed: f64,
    /// How it ended.
    pub outcome: Outcome,
}

/// Per-slot state values in [`SolveResult::state`].
pub mod state {
    /// The slot holds exactly one variant.
    pub const SOLVED: u8 = 0;
    /// The slot still has several variants.
    pub const OPEN: u8 = 1;
    /// The slot has no variant left.
    pub const CONTRADICTION: u8 = 2;
}

/// The result arrays, one entry per slot, as the specification lists them
/// (`.claude/rules/solver-core.md`, "Output arrays").
///
/// WHY plain `Vec`s: `lamda-py` hands them to NumPy without conversion, and the
/// CLI writes them as JSON (hence the serde derives).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SolveResult {
    /// Variant id, or −1 if the slot is not solved.
    pub variant: Vec<i32>,
    /// Module index, or −1.
    pub module: Vec<i32>,
    /// Rotation index in `0..24`, or −1.
    pub rotation: Vec<i32>,
    /// Part index, or −1.
    pub part: Vec<i32>,
    /// Whether the placed variant is an anchor part (false if unsolved).
    pub anchor: Vec<bool>,
    /// 0 solved, 1 open, 2 contradiction (see [`state`]).
    pub state: Vec<u8>,
    /// Variants still allowed.
    pub options_left: Vec<u32>,
    /// Observation count at which the slot collapsed (0 = by canonicalisation),
    /// or −1 if still open.
    pub collapse_step: Vec<i64>,
    /// Numbers about the attempt.
    pub report: Report,
}

/// Everything shared by all attempts on one assembly.
///
/// Invariant: `canonical` is the fixed point of one propagation pass over the
/// initial domains (classes ∩ fixed ∩ boundary, minus non-positive weights), and
/// every edge of `graph` has a table in `adjacency`.
#[derive(Debug)]
pub struct Solver<'a> {
    // RUST: `'a` names the lifetime of the borrowed assembly. A `Solver<'a>`
    // may not outlive the `Assembly` it points at; the compiler enforces it.
    /// The assembly, for variant metadata, settings and priorities.
    assembly: &'a Assembly,
    /// The slot graph.
    graph: Graph,
    /// The adjacency tables.
    adjacency: Adjacency,
    /// Effective weights.
    weights: Weights,
    /// Canonical domains, cloned at the start of every attempt.
    canonical: Vec<Bitset>,
    /// What canonicalisation found.
    report: CanonicalReport,
}

/// The mutable state of one attempt. Kept as a struct so the loop's helpers
/// can borrow its fields independently.
struct Attempt<'a> {
    /// Live domains, one per slot.
    domains: Vec<Bitset>,
    /// Running entropy sums.
    sums: EntropySums,
    /// The observation heap and versions.
    agenda: Agenda,
    /// The undo log.
    trail: Trail,
    /// The propagation queue.
    propagator: Propagator,
    /// This attempt's generator.
    rng: Pcg32,
    /// Observation count at which each slot collapsed, −1 if open.
    step: Vec<i64>,
    /// Observations so far.
    observations: u64,
    /// Backtracks so far.
    backtracks: u64,
    /// The host's counters and cancel flag.
    progress: &'a Progress,
}

impl<'a> Solver<'a> {
    // RUST: `impl<'a> Solver<'a>` declares the lifetime for the whole block.

    /// Builds the graph, tables and weights, applies fixed slots, boundary
    /// requirements and non-positive weights, checks every edge has a table,
    /// and canonicalises.
    ///
    /// Cost: the costs of `Graph::from_assembly`, `Adjacency::from_assembly` and
    /// one full propagation pass.
    ///
    /// # Errors
    /// Any [`SolveError`]; [`SolveError::Canonical`] carries the report.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::solve::Solver;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let solver = Solver::new(&assembly).unwrap();
    /// // The two-slot example is fully determined by propagation alone.
    /// assert_eq!(solver.canonical_report().collapsed, 2);
    /// ```
    pub fn new(assembly: &'a Assembly) -> Result<Solver<'a>, SolveError> {
        let graph = Graph::from_assembly(assembly)?;
        // RUST: `?` converts `GraphError` into `SolveError::Graph` via `#[from]`.
        let adjacency = Adjacency::from_assembly(assembly)?;
        let weights = Weights::from_assembly(assembly);
        check_tables(&graph, &adjacency)?;
        let mut canonical = initial_domains(assembly, &weights);
        let mut report = CanonicalReport::default();
        // WFC: canonicalise: one propagation pass before any observation
        // (`docs/PLAN.md`, "Build order", step 12).
        let mut propagator = Propagator::new(assembly.slot_count());
        propagator.enqueue_all();
        let mut discard = Vec::new();
        let progress = Progress::new();
        let pass = propagator.propagate(
            &mut canonical,
            &graph,
            &adjacency,
            &mut discard,
            &progress,
            |_, _, _| {},
        );
        // RUST: the closure `|_, _, _| {}` ignores all three arguments: there are
        // no entropy sums to maintain yet.
        match pass {
            Ok(()) => {}
            Err(PropagateError::Contradiction { slot }) => {
                report.contradiction = Some(slot);
            }
            Err(PropagateError::MissingTable { port, twist }) => {
                return Err(SolveError::MissingTable {
                    slot: SlotId(0),
                    port,
                    twist,
                });
            }
            Err(PropagateError::Cancelled) => {}
        }
        report.dead_variants = dead_variants(&canonical, assembly.variant_count());
        report.collapsed = canonical
            .iter()
            .filter(|domain| domain.count() == 1)
            .count();
        if report.contradiction.is_some() {
            return Err(SolveError::Canonical(report));
        }
        Ok(Solver {
            assembly,
            graph,
            adjacency,
            weights,
            canonical,
            report,
        })
    }

    /// What canonicalisation found: collapsed slots and variants no slot can take.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::solve::Solver;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert!(Solver::new(&assembly).unwrap().canonical_report().dead_variants.is_empty());
    /// ```
    pub fn canonical_report(&self) -> &CanonicalReport {
        &self.report
    }

    /// The canonical domains every attempt starts from.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::solve::Solver;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let solver = Solver::new(&assembly).unwrap();
    /// assert_eq!(solver.canonical_domains()[0].single(), Some(0));
    /// ```
    pub fn canonical_domains(&self) -> &[Bitset] {
        &self.canonical
    }

    /// The assembly this solver was built from.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::solve::Solver;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Solver::new(&assembly).unwrap().assembly().slots, 2);
    /// ```
    pub fn assembly(&self) -> &'a Assembly {
        // RUST: the returned reference has lifetime `'a`, the assembly's, not the
        // shorter borrow of `self`: the solver only holds a copy of the pointer.
        self.assembly
    }

    /// The slot graph.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::solve::Solver;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Solver::new(&assembly).unwrap().graph().edge_count(), 2);
    /// ```
    pub fn graph(&self) -> &Graph {
        &self.graph
    }

    /// Runs attempt number `attempt` (seed + attempt) to its end.
    ///
    /// Cost: O(observations × (log slots + variants)) plus propagation, which
    /// over the attempt is bounded by slots × variants × degree × words.
    ///
    /// # Errors
    /// [`SolveError::MissingTable`] only if the invariant of `new` was broken,
    /// which cannot happen through this API.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::progress::Progress;
    /// use lamda_core::solve::{Outcome, Solver};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let solver = Solver::new(&assembly).unwrap();
    /// let result = solver.run_attempt(0, &Progress::new()).unwrap();
    /// assert_eq!(result.report.outcome, Outcome::Solved);
    /// assert_eq!(result.variant, vec![0, 1]);
    /// assert_eq!(result.collapse_step, vec![0, 0]); // both forced by canonicalisation
    /// ```
    pub fn run_attempt(
        &self,
        attempt: u64,
        progress: &Progress,
    ) -> Result<SolveResult, SolveError> {
        let started = Instant::now();
        progress.start_attempt(attempt);
        let mut state = self.start_attempt(attempt, progress);
        let outcome = self.run_loop(&mut state)?;
        let elapsed = started.elapsed().as_secs_f64();
        let report = Report {
            seed: self.assembly.settings.seed,
            attempt,
            observations: state.observations,
            backtracks: state.backtracks,
            elapsed,
            outcome,
        };
        let result = self.collect(&state, report);
        // WFC: every result is checked by the independent validator in debug and
        // test builds (`.claude/rules/solver-core.md`, "Validator").
        debug_assert!(
            outcome != Outcome::Solved || crate::validate::validate(self.assembly, &result).is_ok(),
            "solver produced an invalid result: {:?}",
            crate::validate::validate(self.assembly, &result)
        );
        Ok(result)
    }

    /// Prepares the per-attempt state: generator, noise, domains, sums, heap.
    ///
    /// Cost: O(slots × variants) for the sums.
    fn start_attempt(&self, attempt: u64, progress: &'a Progress) -> Attempt<'a> {
        let slots = self.assembly.slot_count();
        let mut rng = Pcg32::for_attempt(self.assembly.settings.seed, attempt);
        // WFC: the noise draw happens inside `Agenda::new`, first use of the stream.
        let mut agenda = Agenda::new(self.assembly, &mut rng);
        let domains = self.canonical.clone();
        // RUST: `.clone()` copies every canonical Bitset: this attempt owns and
        // mutates its domains while the solver keeps the originals for the next.
        let mut sums = EntropySums::new(slots);
        let mut step = vec![-1i64; slots];
        for index in 0..slots {
            let slot = SlotId(index as u32);
            let count = domains[index].count();
            sums.recompute(slot, &domains[index], &self.weights);
            agenda.touch(slot, sums.key(slot, count), count);
            if count == 1 {
                step[index] = 0;
            }
        }
        Attempt {
            domains,
            sums,
            agenda,
            trail: Trail::new(),
            propagator: Propagator::new(slots),
            rng,
            step,
            observations: 0,
            backtracks: 0,
            progress,
        }
    }

    /// The observe–propagate–backtrack loop. Returns how the attempt ended.
    ///
    /// Cost: see `run_attempt`.
    fn run_loop(&self, state: &mut Attempt<'_>) -> Result<Outcome, SolveError> {
        let max_observations = self.assembly.settings.max_observations;
        loop {
            if state.progress.is_cancelled() {
                return Ok(Outcome::Cancelled);
            }
            if max_observations > 0 && state.observations >= max_observations {
                return Ok(Outcome::Partial);
            }
            // WFC: pick the open slot with the smallest key; none left means solved.
            let Some(slot) = state.agenda.next_open(&state.domains) else {
                return Ok(Outcome::Solved);
            };
            let variant = choose_variant(
                slot,
                &state.domains[slot.index()],
                &self.weights,
                &mut state.rng,
            );
            state.observations += 1;
            state.progress.add_observation();
            self.observe(state, slot, variant);
            // WFC: propagate, and on contradiction backtrack and propagate again
            // until it settles, the budget runs out, or no decision is left.
            let mut result = self.propagate(state);
            while let Err(PropagateError::Contradiction { .. }) = result {
                state.backtracks += 1;
                state.progress.add_backtrack();
                if state.backtracks > u64::from(self.assembly.settings.backtrack_budget) {
                    return Ok(Outcome::Contradiction);
                }
                // RUST: `u64::from(u32)` widens losslessly; `as` would too, but
                // `from` says so in the type system.
                let Some(decision_slot) = self.backtrack(state) else {
                    return Ok(Outcome::Contradiction);
                };
                if state.domains[decision_slot.index()].is_empty() {
                    // WFC: forbidding the failed variant emptied the slot: another
                    // contradiction, so back up once more.
                    result = Err(PropagateError::Contradiction {
                        slot: decision_slot,
                    });
                    continue;
                }
                state.propagator.enqueue(decision_slot);
                result = self.propagate(state);
            }
            match result {
                Ok(()) => {}
                Err(PropagateError::Cancelled) => return Ok(Outcome::Cancelled),
                Err(PropagateError::MissingTable { port, twist }) => {
                    return Err(SolveError::MissingTable { slot, port, twist });
                }
                Err(PropagateError::Contradiction { .. }) => {} // RUST: `match` must be exhaustive; the loop above guarantees this
                                                                // arm is never taken, but the compiler cannot know that.
            }
            state
                .progress
                .set_collapsed(state.step.iter().filter(|s| **s >= 0).count() as u64);
        }
    }

    /// Applies an observation: decision mark, domain collapse, sums, heap, step.
    ///
    /// Cost: O(variants / 64 + count(removed)).
    fn observe(&self, state: &mut Attempt<'_>, slot: SlotId, variant: VariantId) {
        state.trail.push_decision(slot, variant);
        let mut chosen = Bitset::new(self.assembly.variant_count());
        chosen.insert(variant.index());
        let old = std::mem::replace(&mut state.domains[slot.index()], chosen);
        // RUST: `mem::replace` as in propagate.rs: the old domain comes out by value.
        let removed = old.difference(&state.domains[slot.index()]);
        state.sums.remove(slot, &removed, &self.weights);
        state.agenda.touch(slot, state.sums.key(slot, 1), 1);
        state.step[slot.index()] = state.observations as i64;
        // RUST: `u64 as i64` would wrap above 2^63 observations; impossible here.
        state.trail.push_change(slot, old);
        state.propagator.enqueue(slot);
    }

    /// Runs propagation with the callback that keeps sums, heap and steps current.
    ///
    /// Cost: see `Propagator::propagate`.
    fn propagate(&self, state: &mut Attempt<'_>) -> Result<(), PropagateError> {
        // RUST: destructuring borrows each field separately, so the closure can
        // hold `&mut sums` and `&mut agenda` while `propagate` holds
        // `&mut domains` and `&mut propagator`. Writing `state.sums` inside the
        // closure and `state.domains` outside would be rejected as two mutable
        // borrows of `state`.
        let Attempt {
            domains,
            sums,
            agenda,
            trail,
            propagator,
            step,
            observations,
            progress,
            ..
        } = state;
        let weights = &self.weights;
        let observation = *observations as i64;
        propagator.propagate(
            domains,
            &self.graph,
            &self.adjacency,
            trail.changes_mut(),
            progress,
            |slot, removed, new| {
                // WFC: subtract the removed weights in ascending order, bump the
                // version and push a fresh heap entry.
                sums.remove(slot, removed, weights);
                let count = new.count();
                agenda.touch(slot, sums.key(slot, count), count);
                if count == 1 {
                    step[slot.index()] = observation;
                }
            },
        )
    }

    /// Undoes the last decision; recomputes sums and heap entries for every
    /// restored slot. Returns the decision's slot, or `None` if there was none.
    ///
    /// Cost: O(restored changes + touched × variants).
    fn backtrack(&self, state: &mut Attempt<'_>) -> Option<SlotId> {
        state.propagator.clear();
        let undone = state.trail.backtrack(&mut state.domains)?;
        for slot in &undone.touched {
            // WFC: after a restore the sums are recomputed from scratch, never
            // patched, so both implementations hold the same floats.
            let count = state.domains[slot.index()].count();
            state
                .sums
                .recompute(*slot, &state.domains[slot.index()], &self.weights);
            state
                .agenda
                .touch(*slot, state.sums.key(*slot, count), count);
            state.step[slot.index()] = if count == 1 {
                state.observations as i64
            } else {
                -1
            };
        }
        Some(undone.decision.slot)
    }

    /// Reads the result arrays off the domains.
    ///
    /// Cost: O(slots × variants / 64).
    fn collect(&self, state: &Attempt<'_>, report: Report) -> SolveResult {
        let slots = self.assembly.slot_count();
        let mut result = SolveResult {
            variant: vec![-1; slots],
            module: vec![-1; slots],
            rotation: vec![-1; slots],
            part: vec![-1; slots],
            anchor: vec![false; slots],
            state: vec![state::OPEN; slots],
            options_left: vec![0; slots],
            collapse_step: state.step.clone(),
            report,
        };
        for (index, domain) in state.domains.iter().enumerate() {
            result.options_left[index] = domain.count() as u32;
            // RUST: `as u32` cannot truncate: a count is at most the variant count.
            if domain.is_empty() {
                result.state[index] = state::CONTRADICTION;
            } else if let Some(v) = domain.single() {
                let variant = &self.assembly.variants[v];
                result.variant[index] = v as i32;
                result.module[index] = variant.module as i32;
                result.rotation[index] = i32::from(variant.rotation);
                result.part[index] = variant.part as i32;
                result.anchor[index] = variant.anchor;
                result.state[index] = state::SOLVED;
            }
        }
        result
    }
}

/// Every edge's (port, twist) must have a table.
///
/// Cost: O(edges).
fn check_tables(graph: &Graph, adjacency: &Adjacency) -> Result<(), SolveError> {
    for index in 0..graph.slot_count() {
        let slot = SlotId(index as u32);
        for edge in graph.edges(slot) {
            if adjacency.table(edge.port, edge.twist).is_none() {
                return Err(SolveError::MissingTable {
                    slot,
                    port: edge.port,
                    twist: edge.twist,
                });
            }
        }
    }
    Ok(())
}

/// Initial domains: the slot's class, intersected with its fixed variant and
/// boundary classes, minus variants with non-positive effective weight.
///
/// Cost: O(slots × variants / 64 + boundary ports × variants / 64).
fn initial_domains(assembly: &Assembly, weights: &Weights) -> Vec<Bitset> {
    let variants = assembly.variant_count();
    // WFC: one Bitset per class, then one clone per slot.
    let classes: Vec<Bitset> = assembly
        .domain_classes
        .iter()
        .map(|class| {
            let bits: Vec<usize> = class.iter().map(|v| v.index()).collect();
            Bitset::from_bits(variants, &bits)
        })
        .collect();
    let mut domains: Vec<Bitset> = assembly
        .slot_domain
        .iter()
        .map(|class| classes[*class as usize].clone())
        .collect();
    if let Some(fixed) = &assembly.fixed {
        for (index, value) in fixed.iter().enumerate() {
            if *value >= 0 {
                let mut only = Bitset::new(variants);
                only.insert(*value as usize);
                // RUST: `i32 as usize` is safe here: the value was range-checked
                // on load and is non-negative.
                domains[index].intersect_with(&only);
            }
        }
    }
    for port in &assembly.boundary {
        let class = &assembly.boundary_classes[port.class as usize];
        let bits: Vec<usize> = class.variants.iter().map(|v| v.index()).collect();
        domains[port.slot.index()].intersect_with(&Bitset::from_bits(variants, &bits));
    }
    for (index, domain) in domains.iter_mut().enumerate() {
        // RUST: `iter_mut` so each domain can be edited in place. Collect first:
        // removing while iterating `ones()` would borrow the domain twice.
        let dead: Vec<usize> = domain
            .ones()
            .filter(|v| weights.effective(SlotId(index as u32), VariantId(*v as u32)) <= 0.0)
            .collect();
        for v in dead {
            domain.remove(v);
        }
    }
    domains
}

/// Variants present in no domain.
///
/// Cost: O(slots × variants / 64).
fn dead_variants(domains: &[Bitset], variants: usize) -> Vec<VariantId> {
    let mut alive = Bitset::new(variants);
    for domain in domains {
        alive.union_with(domain);
    }
    // RUST: `Bitset::full(n).difference(&alive)` is "everything minus alive".
    Bitset::full(variants)
        .difference(&alive)
        .ones()
        .map(|v| VariantId(v as u32))
        .collect()
}

/// Whether every domain holds exactly one variant, through the `Domain` trait.
///
/// Cost: O(slots × cost of `count`).
///
/// # Examples
/// ```
/// use lamda_core::bitset::Bitset;
/// use lamda_core::solve::all_collapsed;
/// let domains = vec![Bitset::from_bits(2, &[0]), Bitset::from_bits(2, &[1])];
/// assert!(all_collapsed(&domains));
/// assert!(!all_collapsed(&[Bitset::full(2)]));
/// ```
pub fn all_collapsed<D: Domain>(domains: &[D]) -> bool {
    // RUST: `D: Domain` lets the caller pass a slice of any domain type; inside,
    // only the trait's methods are available on `domain`.
    domains.iter().all(|domain| domain.count() == 1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::examples;

    /// A fixed variant that contradicts the rules must surface as a
    /// canonicalisation error, not as a failed attempt.
    #[test]
    fn contradictory_fixed_slot_is_a_setup_error() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.fixed = Some(vec![1, -1]); // b in the west slot has no east neighbour
        let err = Solver::new(&assembly).unwrap_err();
        assert!(matches!(
            err,
            SolveError::Canonical(CanonicalReport {
                contradiction: Some(SlotId(1)),
                ..
            })
        ));
    }

    /// Non-positive weights remove a variant from the initial domain.
    #[test]
    fn zero_weight_removes_variant() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.variant_weight = vec![1.0, 0.0];
        // With b gone everywhere, a has no east neighbour: contradiction at setup.
        let err = Solver::new(&assembly).unwrap_err();
        assert!(matches!(err, SolveError::Canonical(_)));
    }

    /// A boundary class restricts the slot like a domain class.
    #[test]
    fn boundary_class_applies() {
        use crate::assembly::{BoundaryClass, BoundaryPort};
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.boundary_classes = vec![BoundaryClass {
            connector: "0".into(),
            variants: vec![VariantId(1)],
        }];
        assembly.boundary = vec![BoundaryPort {
            slot: SlotId(0),
            port: PortId(1),
            class: 0,
        }];
        // Slot 0 must be b at its west boundary, but b has no east neighbour.
        assert!(matches!(
            Solver::new(&assembly),
            Err(SolveError::Canonical(_))
        ));
    }

    /// Cancel before the first observation yields `Cancelled` and open slots.
    #[test]
    fn cancel_is_reported() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        // Make slots undetermined: allow every pair.
        assembly.allowed[0].pairs = vec![
            (VariantId(0), VariantId(0)),
            (VariantId(0), VariantId(1)),
            (VariantId(1), VariantId(0)),
            (VariantId(1), VariantId(1)),
        ];
        assembly.allowed[1].pairs = assembly.allowed[0].pairs.clone();
        let solver = Solver::new(&assembly).unwrap();
        let progress = Progress::new();
        progress.cancel();
        let result = solver.run_attempt(0, &progress).unwrap();
        assert_eq!(result.report.outcome, Outcome::Cancelled);
        assert_eq!(result.state, vec![state::OPEN, state::OPEN]);
    }

    /// `max_observations` stops early with a partial result.
    #[test]
    fn max_observations_gives_partial() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.allowed[0].pairs = vec![
            (VariantId(0), VariantId(0)),
            (VariantId(0), VariantId(1)),
            (VariantId(1), VariantId(0)),
            (VariantId(1), VariantId(1)),
        ];
        assembly.allowed[1].pairs = assembly.allowed[0].pairs.clone();
        assembly.settings.max_observations = 1;
        let solver = Solver::new(&assembly).unwrap();
        let result = solver.run_attempt(0, &Progress::new()).unwrap();
        assert_eq!(result.report.outcome, Outcome::Partial);
        assert_eq!(result.report.observations, 1);
        assert_eq!(
            result.state.iter().filter(|s| **s == state::SOLVED).count(),
            1
        );
    }

    /// The same seed must give the same result twice: determinism in one process.
    #[test]
    fn same_seed_same_result() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.allowed[0].pairs = vec![
            (VariantId(0), VariantId(0)),
            (VariantId(0), VariantId(1)),
            (VariantId(1), VariantId(0)),
            (VariantId(1), VariantId(1)),
        ];
        assembly.allowed[1].pairs = assembly.allowed[0].pairs.clone();
        let solver = Solver::new(&assembly).unwrap();
        let a = solver.run_attempt(0, &Progress::new()).unwrap();
        let b = solver.run_attempt(0, &Progress::new()).unwrap();
        assert_eq!(a.variant, b.variant);
        assert_eq!(a.collapse_step, b.collapse_step);
    }
}
