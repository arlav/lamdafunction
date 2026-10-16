// SPDX-License-Identifier: MIT OR Apache-2.0

//! The validator: an independent check of a result against the assembly.
//!
//! Where it sits: after a solve, in tests and debug builds on every result, and
//! on demand from the CLI and Python. It is written without the solver's
//! bitsets or tables: it reads the assembly's pair lists and CSR arrays
//! directly, so a bug shared by solver and validator is unlikely
//! (`.claude/rules/solver-core.md`, "Validator").
//!
//! Checks: every edge's pair of chosen variants is in `allowed`; every solved
//! slot's variant is in its initial domain class and its fixed variant; every
//! boundary port's variant is in its class; every multi-cell part has its
//! sibling parts across its internal ports.
//!
//! Rust in this file:
//! - `HashSet` and `HashMap` with tuple keys, for O(1) membership tests.
//!   <https://doc.rust-lang.org/book/ch08-03-hash-maps.html>
//! - `Vec<Error>` as a report: collecting every problem instead of stopping at
//!   the first, with a `Result<(), Vec<Violation>>` return.
//! - `binary_search` on sorted slices.
//!
//! Read next: `lamda-py/src/lib.rs`, or `lamda-cli/src/main.rs`.

use std::collections::HashSet;
use thiserror::Error;

use crate::assembly::Assembly;
use crate::ids::{PortId, SlotId, Twist, VariantId};
use crate::solve::{SolveResult, state};

/// One way a result breaks the rules.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum Violation {
    /// Two neighbouring choices are not an allowed pair.
    #[error(
        "slot {slot} ({variant}) and slot {neighbour} ({neighbour_variant}) are not allowed across port {port} twist {twist}"
    )]
    Pair {
        /// The slot.
        slot: SlotId,
        /// Its variant.
        variant: VariantId,
        /// Its port on the edge.
        port: PortId,
        /// The edge's twist.
        twist: Twist,
        /// The neighbour.
        neighbour: SlotId,
        /// The neighbour's variant.
        neighbour_variant: VariantId,
    },
    /// A solved slot holds a variant outside its initial domain.
    #[error("slot {slot} holds {variant}, which is not in its initial domain")]
    Domain {
        /// The slot.
        slot: SlotId,
        /// Its variant.
        variant: VariantId,
    },
    /// A solved slot does not hold its fixed variant.
    #[error("slot {slot} holds {variant} but was fixed to {fixed}")]
    Fixed {
        /// The slot.
        slot: SlotId,
        /// Its variant.
        variant: VariantId,
        /// The required variant.
        fixed: VariantId,
    },
    /// A boundary port's variant does not carry the required connector.
    #[error("slot {slot} port {port} needs connector {connector:?} but holds {variant}")]
    Boundary {
        /// The slot.
        slot: SlotId,
        /// The exposed port.
        port: PortId,
        /// The connector name required there.
        connector: String,
        /// The variant found.
        variant: VariantId,
    },
    /// A multi-cell part lacks its sibling across an internal port.
    #[error("slot {slot} holds part {variant} but port {port} does not hold sibling part {part}")]
    Incomplete {
        /// The slot.
        slot: SlotId,
        /// The part variant.
        variant: VariantId,
        /// The internal port.
        port: PortId,
        /// The sibling part index expected across it.
        part: u32,
    },
    /// The result arrays do not have one entry per slot.
    #[error("result has {found} entries, assembly has {expected} slots")]
    Shape {
        /// Entries found.
        found: usize,
        /// Slots in the assembly.
        expected: usize,
    },
}

/// Checks `result` against `assembly`. Open and contradicted slots are skipped:
/// only solved slots make claims that can be false.
///
/// Cost: O(allowed pairs) to index the rules, then O(edges + slots + boundary ports).
///
/// # Errors
/// Every violation found, in slot order; empty means valid.
///
/// # Examples
/// ```
/// use lamda_core::assembly::{Assembly, examples};
/// use lamda_core::progress::Progress;
/// use lamda_core::solve::Solver;
/// use lamda_core::validate::validate;
/// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
/// let solver = Solver::new(&assembly).unwrap();
/// let result = solver.run_attempt(0, &Progress::new()).unwrap();
/// assert!(validate(&assembly, &result).is_ok());
/// ```
pub fn validate(assembly: &Assembly, result: &SolveResult) -> Result<(), Vec<Violation>> {
    let slots = assembly.slot_count();
    if result.variant.len() != slots || result.state.len() != slots {
        return Err(vec![Violation::Shape {
            found: result.variant.len(),
            expected: slots,
        }]);
    }
    // RUST: `HashSet<(u8, u8, u32, u32)>`: a set of tuples keyed by
    // (port, twist, v, w). Tuples of hashable types are hashable.
    let mut allowed: HashSet<(u8, u8, u32, u32)> = HashSet::new();
    for table in &assembly.allowed {
        for (v, w) in &table.pairs {
            allowed.insert((table.port.0, table.twist.0, v.0, w.0));
        }
    }
    // RUST: a closure that reads the result; returns `Option<VariantId>`.
    let chosen = |slot: SlotId| -> Option<VariantId> {
        let index = slot.index();
        if result.state[index] == state::SOLVED && result.variant[index] >= 0 {
            Some(VariantId(result.variant[index] as u32))
        } else {
            None
        }
    };
    let mut violations = Vec::new();
    for index in 0..slots {
        let slot = SlotId(index as u32);
        let Some(variant) = chosen(slot) else {
            continue;
        };
        check_domain(assembly, slot, variant, &mut violations);
        check_edges(assembly, &allowed, slot, variant, &chosen, &mut violations);
        check_internal(assembly, slot, variant, &chosen, &mut violations);
    }
    check_boundary(assembly, &chosen, &mut violations);
    if violations.is_empty() {
        Ok(())
    } else {
        Err(violations)
    }
}

/// The variant must be in the slot's domain class and equal its fixed variant.
fn check_domain(assembly: &Assembly, slot: SlotId, variant: VariantId, out: &mut Vec<Violation>) {
    let class = &assembly.domain_classes[assembly.slot_domain[slot.index()] as usize];
    // RUST: `binary_search` on a sorted slice returns `Ok(position)` or
    // `Err(insertion point)`; only membership matters here.
    if class.binary_search(&variant).is_err() {
        out.push(Violation::Domain { slot, variant });
    }
    if let Some(fixed) = &assembly.fixed {
        let value = fixed[slot.index()];
        if value >= 0 && value as u32 != variant.0 {
            out.push(Violation::Fixed {
                slot,
                variant,
                fixed: VariantId(value as u32),
            });
        }
    }
}

/// Every solved neighbour must form an allowed pair across the edge.
fn check_edges(
    assembly: &Assembly,
    allowed: &HashSet<(u8, u8, u32, u32)>,
    slot: SlotId,
    variant: VariantId,
    chosen: &dyn Fn(SlotId) -> Option<VariantId>,
    out: &mut Vec<Violation>,
) {
    // RUST: `&dyn Fn(..)` is a reference to any closure with that signature,
    // resolved at run time ("trait object"); fine here, this is not a hot loop.
    let csr = &assembly.edges;
    let start = csr.offsets[slot.index()] as usize;
    let end = csr.offsets[slot.index() + 1] as usize;
    for e in start..end {
        let neighbour = csr.neighbour[e];
        let Some(neighbour_variant) = chosen(neighbour) else {
            continue;
        };
        let key = (
            csr.port[e].0,
            csr.twist[e].0,
            variant.0,
            neighbour_variant.0,
        );
        if !allowed.contains(&key) {
            out.push(Violation::Pair {
                slot,
                variant,
                port: csr.port[e],
                twist: csr.twist[e],
                neighbour,
                neighbour_variant,
            });
        }
    }
}

/// Each internal port of a multi-cell part must hold the sibling part of the
/// same module and rotation.
fn check_internal(
    assembly: &Assembly,
    slot: SlotId,
    variant: VariantId,
    chosen: &dyn Fn(SlotId) -> Option<VariantId>,
    out: &mut Vec<Violation>,
) {
    let this = &assembly.variants[variant.index()];
    let csr = &assembly.edges;
    for (port, part) in &this.internal {
        let start = csr.offsets[slot.index()] as usize;
        let end = csr.offsets[slot.index() + 1] as usize;
        // RUST: `find` returns the first edge index whose port matches, if any.
        let across = (start..end)
            .find(|e| csr.port[*e] == *port)
            .map(|e| csr.neighbour[e]);
        let sibling_ok = across
            .and_then(chosen)
            .map(|v| &assembly.variants[v.index()])
            .is_some_and(|other| {
                other.module == this.module
                    && other.rotation == this.rotation
                    && other.part == *part
            });
        if !sibling_ok {
            out.push(Violation::Incomplete {
                slot,
                variant,
                port: *port,
                part: *part,
            });
        }
    }
}

/// Every listed boundary port must hold a variant of its class.
fn check_boundary(
    assembly: &Assembly,
    chosen: &dyn Fn(SlotId) -> Option<VariantId>,
    out: &mut Vec<Violation>,
) {
    for port in &assembly.boundary {
        let Some(variant) = chosen(port.slot) else {
            continue;
        };
        let class = &assembly.boundary_classes[port.class as usize];
        if class.variants.binary_search(&variant).is_err() {
            out.push(Violation::Boundary {
                slot: port.slot,
                port: port.port,
                connector: class.connector.clone(),
                variant,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::{BoundaryClass, BoundaryPort, examples};
    use crate::progress::Progress;
    use crate::solve::Solver;

    /// Produces a valid result for the two-slot example.
    fn solved() -> (Assembly, SolveResult) {
        let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let result = Solver::new(&assembly)
            .unwrap()
            .run_attempt(0, &Progress::new())
            .unwrap();
        (assembly, result)
    }

    /// Swapping the two variants breaks the only rule and must be caught on
    /// both edges (each direction is checked from its own slot).
    #[test]
    fn detects_disallowed_pair() {
        let (assembly, mut result) = solved();
        result.variant.swap(0, 1);
        let errors = validate(&assembly, &result).unwrap_err();
        assert_eq!(errors.len(), 2);
        assert!(matches!(
            errors[0],
            Violation::Pair {
                slot: SlotId(0),
                ..
            }
        ));
    }

    /// A variant outside the slot's class is caught even if the pair is allowed.
    #[test]
    fn detects_domain_violation() {
        let (mut assembly, result) = solved();
        assembly.domain_classes = vec![vec![VariantId(0)], vec![VariantId(1)]];
        assembly.slot_domain = vec![1, 0];
        let errors = validate(&assembly, &result).unwrap_err();
        assert!(errors.iter().all(|e| matches!(e, Violation::Domain { .. })));
        assert_eq!(errors.len(), 2);
    }

    /// Fixed and boundary requirements are checked independently of the solver.
    #[test]
    fn detects_fixed_and_boundary_violations() {
        let (mut assembly, result) = solved();
        assembly.fixed = Some(vec![1, -1]);
        assembly.boundary_classes = vec![BoundaryClass {
            connector: "0".into(),
            variants: vec![VariantId(0)],
        }];
        assembly.boundary = vec![BoundaryPort {
            slot: SlotId(1),
            port: PortId(0),
            class: 0,
        }];
        let errors = validate(&assembly, &result).unwrap_err();
        assert!(errors.iter().any(|e| matches!(
            e,
            Violation::Fixed {
                slot: SlotId(0),
                ..
            }
        )));
        assert!(errors.iter().any(|e| matches!(
            e,
            Violation::Boundary {
                slot: SlotId(1),
                ..
            }
        )));
    }

    /// A multi-cell part whose sibling is missing is reported.
    #[test]
    fn detects_incomplete_module() {
        let (mut assembly, result) = solved();
        // Claim variant 0 needs sibling part 1 across +X; slot 1 holds variant 1
        // of a different module, so the claim fails.
        assembly.variants[0].internal = vec![(PortId(0), 1)];
        let errors = validate(&assembly, &result).unwrap_err();
        assert_eq!(
            errors,
            vec![Violation::Incomplete {
                slot: SlotId(0),
                variant: VariantId(0),
                port: PortId(0),
                part: 1
            }]
        );
    }

    /// Open slots make no claims and never produce violations.
    #[test]
    fn open_slots_are_skipped() {
        let (assembly, mut result) = solved();
        result.state[1] = state::OPEN;
        result.variant[1] = -1;
        assert!(validate(&assembly, &result).is_ok());
    }
}
