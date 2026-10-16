// SPDX-License-Identifier: MIT OR Apache-2.0

//! Adjacency tables: for each (port, twist), which variants may sit next to which.
//!
//! Where it sits: built once from the assembly's `allowed` pair lists, then read
//! by propagation on every edge visit: `support = OR of table(port, twist)[v]`
//! for each `v` still in the slot (`docs/PLAN.md`, "Solver, step by step").
//! The symmetry invariant `w ∈ table(p, t)[v] ⇔ v ∈ table(opposite(p), t)[w]`
//! is checked here (`.claude/rules/solver-core.md`, "Data model").
//!
//! Rust in this file:
//! - nested `Vec`s: `Vec<Option<Vec<Bitset>>>` is a list (per table key) of
//!   maybe-present lists (per variant) of bitsets; indexing is `tables[k]`,
//!   then `.as_ref()` to borrow inside the Option, then `[v]`.
//! - `Option::as_ref` and `as_deref`: borrow the contents of an Option without
//!   moving them out. <https://doc.rust-lang.org/std/option/enum.Option.html#method.as_deref>
//! - `debug_assert!` for internal invariants that are too costly to check in
//!   release builds. <https://doc.rust-lang.org/std/macro.debug_assert.html>
//! - `cfg!(debug_assertions)`: a compile-time boolean, true in debug builds.
//!
//! Read next: `progress.rs`.

use thiserror::Error;

use crate::assembly::Assembly;
use crate::bitset::Bitset;
use crate::ids::{PortId, TWISTS, Twist, VariantId, table_key};

/// Problems in the allowed-pair tables.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum AdjacencyError {
    /// The same (port, twist) appears twice in `allowed`.
    #[error("allowed has two tables for port {port} twist {twist}")]
    DuplicateTable {
        /// The port.
        port: PortId,
        /// The twist.
        twist: Twist,
    },
    /// `w` is allowed across `port` from `v`, but `v` is not allowed across the
    /// opposite port from `w`: the compiler emitted a one-sided rule.
    #[error(
        "asymmetric rule: {w} may sit across port {port} twist {twist} from {v}, \
         but not the other way round"
    )]
    Asymmetric {
        /// The port on which the rule was found.
        port: PortId,
        /// The twist.
        twist: Twist,
        /// The variant looked at.
        v: VariantId,
        /// The neighbour it allows.
        w: VariantId,
    },
    /// A table exists for `port` but none for `opposite(port)` with the same twist.
    #[error("table for port {port} twist {twist} has no opposite table")]
    MissingOpposite {
        /// The port.
        port: PortId,
        /// The twist.
        twist: Twist,
    },
}

/// The adjacency tables, one row of bitsets per (port, twist) in use.
///
/// Invariant: every present table has `variants` rows of `variants` bits and
/// satisfies the symmetry invariant with its opposite table.
///
/// Cost: `tables × variants² / 8` bytes: 6 box ports × 400 variants is 120 KB;
/// at 16,000 variants it is 192 MB, which is what spike 4 measures.
#[derive(Debug, Clone)]
pub struct Adjacency {
    /// Number of variants, the width of every row.
    variants: usize,
    /// Indexed by `table_key(port, twist)`; `None` where the graph has no such
    /// edge type. Row `v` of a table is the set of `w` allowed across the port.
    tables: Vec<Option<Vec<Bitset>>>,
}

impl Adjacency {
    /// Builds the tables from the assembly's pair lists and checks symmetry.
    ///
    /// Cost: O(tables × variants² / 64) for the bitsets plus O(pairs) to fill them,
    /// plus O(tables × variants²) for the symmetry check in debug builds and tests.
    /// In release builds only table presence is checked; the Python audit and
    /// the tests run `check_symmetry` in full.
    ///
    /// # Errors
    /// An [`AdjacencyError`] for duplicate tables, a missing opposite table, or
    /// (debug builds) an asymmetric pair.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{PortId, Twist, VariantId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // Variant 1 (b) may sit across +X from variant 0 (a):
    /// assert!(adjacency.row(PortId(0), Twist(0), VariantId(0)).unwrap().contains(1));
    /// ```
    pub fn from_assembly(assembly: &Assembly) -> Result<Adjacency, AdjacencyError> {
        let variants = assembly.variant_count();
        let keys = assembly.ports as usize * TWISTS;
        // RUST: `vec![None; keys]` needs `Option<Vec<Bitset>>: Clone`, which holds
        // because Bitset is Clone. Each slot starts as "no table".
        let mut tables: Vec<Option<Vec<Bitset>>> = vec![None; keys];
        for table in &assembly.allowed {
            let key = table_key(table.port, table.twist);
            if tables[key].is_some() {
                return Err(AdjacencyError::DuplicateTable {
                    port: table.port,
                    twist: table.twist,
                });
            }
            // WFC: start every row empty; each allowed pair sets one bit.
            let mut rows = vec![Bitset::new(variants); variants];
            for (v, w) in &table.pairs {
                rows[v.index()].insert(w.index());
            }
            tables[key] = Some(rows);
            // RUST: `rows` moves into the Option; the name `rows` is dead after this.
        }
        let adjacency = Adjacency { variants, tables };
        adjacency.check_opposites(assembly.ports)?;
        // WHY: the full symmetry check is quadratic in variants. The rules ask for
        // it in debug builds and tests; release builds trust the compiler's own
        // assertion and the audit.
        if cfg!(debug_assertions) {
            adjacency.check_symmetry(assembly.ports)?;
        }
        Ok(adjacency)
    }

    /// Every present table must have its opposite-port table present too.
    ///
    /// Cost: O(ports × twists).
    fn check_opposites(&self, ports: u8) -> Result<(), AdjacencyError> {
        for port in 0..ports {
            for twist in 0..TWISTS as u8 {
                let (port, twist) = (PortId(port), Twist(twist));
                let present = self.table(port, twist).is_some();
                let opposite_present = self.table(port.opposite(), twist).is_some();
                if present && !opposite_present {
                    return Err(AdjacencyError::MissingOpposite { port, twist });
                }
            }
        }
        Ok(())
    }

    /// Checks `w ∈ table(p, t)[v] ⇔ v ∈ table(opposite(p), t)[w]` for every
    /// present table. Tests call this always; `from_assembly` only in debug builds.
    ///
    /// Cost: O(tables × variants²) bit tests.
    ///
    /// # Errors
    /// [`AdjacencyError::Asymmetric`] naming the first one-sided pair.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// assert!(adjacency.check_symmetry(assembly.ports).is_ok());
    /// ```
    pub fn check_symmetry(&self, ports: u8) -> Result<(), AdjacencyError> {
        for port_index in 0..ports {
            for twist_index in 0..TWISTS as u8 {
                let (port, twist) = (PortId(port_index), Twist(twist_index));
                // RUST: `let Some(rows) = ... else { continue }`: bind on success,
                // otherwise run the else block, which must leave (here: next loop).
                let Some(rows) = self.table(port, twist) else {
                    continue;
                };
                let Some(back) = self.table(port.opposite(), twist) else {
                    continue;
                };
                for (v, row) in rows.iter().enumerate() {
                    for w in row.ones() {
                        if !back[w].contains(v) {
                            return Err(AdjacencyError::Asymmetric {
                                port,
                                twist,
                                v: VariantId(v as u32),
                                w: VariantId(w as u32),
                                // RUST: `as u32` cannot truncate: indices came from u32.
                            });
                        }
                    }
                }
            }
        }
        Ok(())
    }

    /// Number of variants (the width of every row).
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Adjacency::from_assembly(&assembly).unwrap().variant_count(), 2);
    /// ```
    pub fn variant_count(&self) -> usize {
        self.variants
    }

    /// The table for `(port, twist)`, one row per variant, or `None` if the graph
    /// has no such edge type.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{PortId, Twist};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// assert!(adjacency.table(PortId(0), Twist(0)).is_some());
    /// assert!(adjacency.table(PortId(4), Twist(0)).is_none());
    /// ```
    pub fn table(&self, port: PortId, twist: Twist) -> Option<&[Bitset]> {
        // RUST: `get(key)` is `Option<&Option<Vec<Bitset>>>`; `and_then` flattens
        // the two Options; `as_deref()` turns `&Vec<Bitset>` into `&[Bitset]`.
        self.tables
            .get(table_key(port, twist))
            .and_then(|table| table.as_deref())
    }

    /// Row `v` of the `(port, twist)` table: the variants allowed across that
    /// port from `v`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{PortId, Twist, VariantId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// let row = adjacency.row(PortId(1), Twist(0), VariantId(1)).unwrap();
    /// assert_eq!(row.ones().collect::<Vec<_>>(), vec![0]);
    /// ```
    pub fn row(&self, port: PortId, twist: Twist, v: VariantId) -> Option<&Bitset> {
        // RUST: `map` on Option: if there is a table, index it; `&table[i]` borrows
        // one Bitset out of the slice.
        self.table(port, twist).map(|table| &table[v.index()])
    }

    /// The support mask: the OR of the rows of every variant in `domain`, which
    /// is the set of neighbours allowed across `port` by at least one of them.
    ///
    /// WFC: `docs/PLAN.md`, "Solver, step by step", step 2. The neighbour's
    /// domain is then intersected with this mask.
    ///
    /// Cost: O(count(domain) × variants / 64). PERF: this is the inner loop of
    /// the whole solver; the optimisations in `.claude/rules/solver-core.md`
    /// ("Propagation") all target it, but only with benchmark evidence.
    ///
    /// # Panics
    /// In debug builds, if `domain` is not over this table's variant count.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::ids::{PortId, Twist};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // Across +X from {a, b}: only a allows anything (b), so support = {b}.
    /// let support = adjacency.support(PortId(0), Twist(0), &Bitset::full(2)).unwrap();
    /// assert_eq!(support.ones().collect::<Vec<_>>(), vec![1]);
    /// ```
    pub fn support(&self, port: PortId, twist: Twist, domain: &Bitset) -> Option<Bitset> {
        debug_assert_eq!(
            domain.len(),
            self.variants,
            "domain over a different variant set"
        );
        let table = self.table(port, twist)?;
        // RUST: `?` on an Option returns `None` early when there is no table.
        let mut support = Bitset::new(self.variants);
        for v in domain.ones() {
            // WFC: ascending variant order, as every loop over a domain.
            support.union_with(&table[v]);
        }
        Some(support)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::examples;

    /// An asymmetric pair must be caught: the solver pushes constraints in both
    /// directions and a one-sided table would make them disagree.
    #[test]
    fn detects_asymmetry() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        // Drop the reverse pair [1, 0] from the -X table.
        assembly.allowed[1].pairs.clear();
        // Build without the debug check to test `check_symmetry` directly.
        let built = build_unchecked(&assembly);
        let err = built.check_symmetry(6).unwrap_err();
        assert_eq!(
            err,
            AdjacencyError::Asymmetric {
                port: PortId(0),
                twist: Twist(0),
                v: VariantId(0),
                w: VariantId(1)
            }
        );
        // And `from_assembly` catches it too in this (debug) test build.
        assert!(Adjacency::from_assembly(&assembly).is_err());
    }

    /// A table without its opposite is rejected before any symmetry check.
    #[test]
    fn detects_missing_opposite() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.allowed.pop();
        assert_eq!(
            Adjacency::from_assembly(&assembly).unwrap_err(),
            AdjacencyError::MissingOpposite {
                port: PortId(0),
                twist: Twist(0)
            }
        );
    }

    /// Duplicate tables for one (port, twist) are rejected rather than merged,
    /// because the compiler should never emit them.
    #[test]
    fn detects_duplicate_table() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let copy = assembly.allowed[0].clone();
        assembly.allowed.push(copy);
        assert!(matches!(
            Adjacency::from_assembly(&assembly),
            Err(AdjacencyError::DuplicateTable { .. })
        ));
    }

    /// Builds tables without any checks, for tests that exercise the checks.
    fn build_unchecked(assembly: &Assembly) -> Adjacency {
        let variants = assembly.variant_count();
        let mut tables = vec![None; assembly.ports as usize * TWISTS];
        for table in &assembly.allowed {
            let mut rows = vec![Bitset::new(variants); variants];
            for (v, w) in &table.pairs {
                rows[v.index()].insert(w.index());
            }
            tables[table_key(table.port, table.twist)] = Some(rows);
        }
        Adjacency { variants, tables }
    }
}
