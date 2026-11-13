// SPDX-License-Identifier: MIT OR Apache-2.0

//! Adjacency tables: for each edge kind, which variants may sit next to which.
//!
//! Where it sits: built once from the assembly's `allowed` pair lists, then read
//! by propagation on every edge visit: `support = OR of table(kind)[v]` for each
//! `v` still in the slot (`docs/PLAN.md`, "Solver, step by step"). An edge kind
//! is an [`EdgeType`]: (port, neighbour port, twist). On a box grid the
//! neighbour port is always the opposite one; on a surface shell it need not be
//! (`docs/PLAN.md`, "Contract change: edge types (schema 1.1)"). The symmetry
//! invariant `w ∈ table(e)[v] ⇔ v ∈ table(e.reverse())[w]` is checked here
//! (`.claude/rules/solver-core.md`, "Data model").
//!
//! Rust in this file:
//! - nested `Vec`s: `Vec<Option<Vec<Bitset>>>` is a list (per table key) of
//!   maybe-present lists (per variant) of bitsets; indexing is `tables[key]`,
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
use crate::ids::{EdgeType, PortId, TWISTS, Twist, VariantId};

/// Problems in the allowed-pair tables.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum AdjacencyError {
    /// The same edge kind appears twice in `allowed`.
    #[error("allowed has two tables for {edge}")]
    DuplicateTable {
        /// The edge kind.
        edge: EdgeType,
    },
    /// `w` is allowed across `edge` from `v`, but `v` is not allowed across the
    /// reverse edge from `w`: the compiler emitted a one-sided rule.
    #[error("asymmetric rule: {w} may sit across {edge} from {v}, but not the other way round")]
    Asymmetric {
        /// The edge kind on which the rule was found.
        edge: EdgeType,
        /// The variant looked at.
        v: VariantId,
        /// The neighbour it allows.
        w: VariantId,
    },
    /// A table exists for `edge` but none for `edge.reverse()`.
    #[error("table for {edge} has no table for the reverse edge")]
    MissingReverse {
        /// The edge kind whose reverse is missing.
        edge: EdgeType,
    },
}

/// The adjacency tables, one row of bitsets per edge kind in use.
///
/// Invariant: every present table has `variants` rows of `variants` bits and
/// satisfies the symmetry invariant with its reverse table.
///
/// Cost: `tables × variants² / 8` bytes: a box grid's 6 kinds × 400 variants is
/// 120 KB; at 16,000 variants it is 192 MB, which is what spike 4 measures. A
/// one-layer shell uses up to 18 kinds, three times the box grid.
#[derive(Debug, Clone)]
pub struct Adjacency {
    /// Number of variants, the width of every row.
    variants: usize,
    /// Port count of the assembly, needed to turn an [`EdgeType`] into a key.
    /// WHY `usize`: it is only ever used in index arithmetic.
    ports: usize,
    /// Indexed by `EdgeType::key(ports)`; `None` where the graph has no such
    /// edge kind. Row `v` of a table is the set of `w` allowed across the edge.
    /// WHY a flat `Vec` and not a `HashMap<EdgeType, _>`: lookups happen on
    /// every edge visit, and indexing a Vec is one multiply-add, no hashing.
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
    /// An [`AdjacencyError`] for duplicate tables, a missing reverse table, or
    /// (debug builds) an asymmetric pair.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{EdgeType, PortId, Twist, VariantId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // Variant 1 (b) may sit across +X (meeting -X) from variant 0 (a):
    /// let east = EdgeType::box_grid(PortId(0), Twist(0));
    /// assert!(adjacency.row(east, VariantId(0)).unwrap().contains(1));
    /// ```
    pub fn from_assembly(assembly: &Assembly) -> Result<Adjacency, AdjacencyError> {
        let variants = assembly.variant_count();
        let ports = assembly.ports as usize;
        // RUST: `u8 as usize` widens; nothing can be lost.
        let keys = ports * ports * TWISTS;
        // WFC: one possible table per (port, neighbour port, twist): 144 for boxes.
        // RUST: `vec![None; keys]` needs `Option<Vec<Bitset>>: Clone`, which holds
        // because Bitset is Clone. Each slot starts as "no table".
        let mut tables: Vec<Option<Vec<Bitset>>> = vec![None; keys];
        for table in &assembly.allowed {
            let edge = table.edge_type();
            // WFC: a 1.0 table has no neighbour port; `edge_type` reads it as the
            // opposite port, so box grids behave exactly as before.
            let key = edge.key(ports);
            if tables[key].is_some() {
                return Err(AdjacencyError::DuplicateTable { edge });
                // RUST: `{ edge }` is field init shorthand, explained in ids.rs.
            }
            // WFC: start every row empty; each allowed pair sets one bit.
            let mut rows = vec![Bitset::new(variants); variants];
            for (v, w) in &table.pairs {
                rows[v.index()].insert(w.index());
            }
            tables[key] = Some(rows);
            // RUST: `rows` moves into the Option; the name `rows` is dead after this.
        }
        let adjacency = Adjacency {
            variants,
            ports,
            tables,
        };
        adjacency.check_reverses()?;
        // WHY: the full symmetry check is quadratic in variants. The rules ask for
        // it in debug builds and tests; release builds trust the compiler's own
        // assertion and the audit.
        if cfg!(debug_assertions) {
            adjacency.check_symmetry()?;
        }
        Ok(adjacency)
    }

    /// Every edge kind with a table, in key order.
    ///
    /// Cost: O(ports² × twists) to scan the keys.
    fn present(&self) -> Vec<EdgeType> {
        let mut found = Vec::new();
        for port in 0..self.ports {
            for neighbour_port in 0..self.ports {
                for twist in 0..TWISTS {
                    let edge = EdgeType {
                        port: PortId(port as u8),
                        neighbour_port: PortId(neighbour_port as u8),
                        twist: Twist(twist as u8),
                    };
                    // RUST: `as u8` cannot truncate: `ports` came from a u8 and
                    // `twist` is below 4.
                    if self.table(edge).is_some() {
                        found.push(edge);
                    }
                }
            }
        }
        found
    }

    /// Every present table must have its reverse table present too, because
    /// propagation pushes constraints across an edge from both ends.
    ///
    /// Cost: O(ports² × twists).
    fn check_reverses(&self) -> Result<(), AdjacencyError> {
        for edge in self.present() {
            if self.table(edge.reverse()).is_none() {
                return Err(AdjacencyError::MissingReverse { edge });
            }
        }
        Ok(())
    }

    /// Checks `w ∈ table(e)[v] ⇔ v ∈ table(e.reverse())[w]` for every present
    /// table. Tests call this always; `from_assembly` only in debug builds.
    ///
    /// Cost: O(tables × variants²) bit tests.
    ///
    /// # Errors
    /// [`AdjacencyError::Asymmetric`] naming the first one-sided pair, or
    /// [`AdjacencyError::MissingReverse`] if a reverse table is absent.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// let assembly = Assembly::from_json(&examples::turned_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// assert!(adjacency.check_symmetry().is_ok());
    /// ```
    pub fn check_symmetry(&self) -> Result<(), AdjacencyError> {
        for edge in self.present() {
            // RUST: `let Some(rows) = ... else { ... }`: bind on success, otherwise
            // run the else block, which must leave the function or loop.
            let Some(rows) = self.table(edge) else {
                continue;
            };
            let Some(back) = self.table(edge.reverse()) else {
                return Err(AdjacencyError::MissingReverse { edge });
            };
            for (v, row) in rows.iter().enumerate() {
                for w in row.ones() {
                    // WFC: w may sit across `edge` from v, so v must be allowed
                    // across the same edge seen from w's side.
                    if !back[w].contains(v) {
                        return Err(AdjacencyError::Asymmetric {
                            edge,
                            v: VariantId(v as u32),
                            w: VariantId(w as u32),
                            // RUST: `as u32` cannot truncate: indices came from u32.
                        });
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

    /// The table for an edge kind, one row per variant, or `None` if the graph
    /// has no such edge kind.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{EdgeType, PortId, Twist};
    /// let assembly = Assembly::from_json(&examples::turned_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // +X meets +Y here, not -X:
    /// let turned = EdgeType { port: PortId(0), neighbour_port: PortId(2), twist: Twist(0) };
    /// assert!(adjacency.table(turned).is_some());
    /// assert!(adjacency.table(EdgeType::box_grid(PortId(0), Twist(0))).is_none());
    /// ```
    pub fn table(&self, edge: EdgeType) -> Option<&[Bitset]> {
        // RUST: the returned slice borrows from `self`: it lives as long as the
        // Adjacency it came from (the elided lifetime ties them together).
        // RUST: `get(key)` is `Option<&Option<Vec<Bitset>>>`; `and_then` flattens
        // the two Options; `as_deref()` turns `&Vec<Bitset>` into `&[Bitset]`.
        // WHY `get` and not `[key]`: a key from a port beyond `ports` returns
        // `None` instead of panicking.
        self.tables
            .get(edge.key(self.ports))
            .and_then(|table| table.as_deref())
    }

    /// Row `v` of an edge kind's table: the variants allowed across that edge
    /// from `v`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::adjacency::Adjacency;
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::ids::{EdgeType, PortId, Twist, VariantId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// let west = EdgeType::box_grid(PortId(1), Twist(0));
    /// let row = adjacency.row(west, VariantId(1)).unwrap();
    /// assert_eq!(row.ones().collect::<Vec<_>>(), vec![0]);
    /// ```
    pub fn row(&self, edge: EdgeType, v: VariantId) -> Option<&Bitset> {
        // RUST: `map` on Option: if there is a table, index it; `&table[i]` borrows
        // one Bitset out of the slice.
        self.table(edge).map(|table| &table[v.index()])
    }

    /// The support mask: the OR of the rows of every variant in `domain`, which
    /// is the set of neighbours allowed across `edge` by at least one of them.
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
    /// use lamda_core::ids::{EdgeType, PortId, Twist};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let adjacency = Adjacency::from_assembly(&assembly).unwrap();
    /// // Across +X from {a, b}: only a allows anything (b), so support = {b}.
    /// let east = EdgeType::box_grid(PortId(0), Twist(0));
    /// let support = adjacency.support(east, &Bitset::full(2)).unwrap();
    /// assert_eq!(support.ones().collect::<Vec<_>>(), vec![1]);
    /// ```
    pub fn support(&self, edge: EdgeType, domain: &Bitset) -> Option<Bitset> {
        debug_assert_eq!(
            domain.len(),
            self.variants,
            "domain over a different variant set"
        );
        let table = self.table(edge)?;
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
        let err = built.check_symmetry().unwrap_err();
        assert_eq!(
            err,
            AdjacencyError::Asymmetric {
                edge: EdgeType::box_grid(PortId(0), Twist(0)),
                v: VariantId(0),
                w: VariantId(1)
            }
        );
        // And `from_assembly` catches it too in this (debug) test build.
        assert!(Adjacency::from_assembly(&assembly).is_err());
    }

    /// A table without its reverse is rejected before any symmetry check.
    #[test]
    fn detects_missing_reverse() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.allowed.pop();
        assert_eq!(
            Adjacency::from_assembly(&assembly).unwrap_err(),
            AdjacencyError::MissingReverse {
                edge: EdgeType::box_grid(PortId(0), Twist(0))
            }
        );
    }

    /// On turned frames the reverse of (+X to +Y) is (+Y to +X), not -X: the
    /// check must pair the right tables, or a correct shell would be rejected
    /// (and an incorrect one accepted).
    #[test]
    fn turned_tables_pair_with_their_reverse() {
        let assembly = Assembly::from_json(&examples::turned_slots_json()).unwrap();
        let adjacency = Adjacency::from_assembly(&assembly).unwrap();
        assert!(adjacency.check_symmetry().is_ok());
        let mut broken = assembly.clone();
        // RUST: `.clone()` deep-copies the assembly so the original stays intact.
        broken.allowed[1].pairs.clear();
        assert_eq!(
            build_unchecked(&broken).check_symmetry().unwrap_err(),
            AdjacencyError::Asymmetric {
                edge: EdgeType {
                    port: PortId(0),
                    neighbour_port: PortId(2),
                    twist: Twist(0)
                },
                v: VariantId(0),
                w: VariantId(1)
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
        let ports = assembly.ports as usize;
        let mut tables = vec![None; ports * ports * TWISTS];
        for table in &assembly.allowed {
            let mut rows = vec![Bitset::new(variants); variants];
            for (v, w) in &table.pairs {
                rows[v.index()].insert(w.index());
            }
            tables[table.edge_type().key(ports)] = Some(rows);
        }
        Adjacency {
            variants,
            ports,
            tables,
        }
    }
}
