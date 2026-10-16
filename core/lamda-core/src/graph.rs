// SPDX-License-Identifier: MIT OR Apache-2.0

//! The slot graph: which slots touch, through which ports, with what twist.
//!
//! Where it sits: built once from the compiled assembly's CSR arrays
//! (`assembly.rs`), read by propagation (`propagate.rs`) on every queue pop and
//! by the validator. A 3D grid is just a graph we never had to store as a grid
//! (`docs/PLAN.md`, "Graphs, grids and the SpaceTope + TopologicPy integration").
//!
//! Rust in this file:
//! - lifetimes on methods returning borrowed data: `fn edges(&self) -> &[Edge]`
//!   says the slice lives as long as the graph; written explicitly once here as
//!   `fn edges<'a>(&'a self, ..) -> &'a [Edge]` so the rule is visible.
//!   <https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html>
//! - slices of structs, `&[Edge]`, and `#[repr]`-free compact structs: `Edge`
//!   is 8 bytes because its fields are `u32 + u8 + u8 + u8`.
//! - `Vec::with_capacity`: reserve once when the final size is known.
//!   <https://doc.rust-lang.org/std/vec/struct.Vec.html#method.with_capacity>
//!
//! Read next: `adjacency.rs`.

use thiserror::Error;

use crate::assembly::Assembly;
use crate::ids::{PortId, SlotId, Twist};

/// One directed edge, stored on the slot it leaves.
///
/// WHY array-of-structs here rather than the file's struct-of-arrays: the
/// propagation loop reads all four fields of each edge together, so keeping
/// them adjacent in memory is one cache line instead of four.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Edge {
    /// Port on the owning slot. `u8` inside, as in `ids.rs`.
    pub port: PortId,
    /// The slot on the other side.
    pub neighbour: SlotId,
    /// Port on the neighbour that this edge enters.
    pub neighbour_port: PortId,
    /// Quarter turns between the two face frames (0 on box grids).
    pub twist: Twist,
}

/// Structural problems the assembly's range checks cannot see.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum GraphError {
    /// A slot has an edge to itself.
    #[error("slot {slot} has an edge to itself on port {port}")]
    SelfLoop {
        /// The slot.
        slot: SlotId,
        /// Its port.
        port: PortId,
    },
    /// An edge `s -> n` has no matching edge `n -> s` with swapped ports and the
    /// same twist, so propagation would be one-directional.
    #[error("edge from slot {slot} port {port} to slot {neighbour} has no reverse edge")]
    MissingReverse {
        /// The slot the edge leaves.
        slot: SlotId,
        /// Its port.
        port: PortId,
        /// The slot it enters.
        neighbour: SlotId,
    },
    /// Two edges leave the same slot through the same port.
    #[error("slot {slot} uses port {port} twice")]
    DuplicatePort {
        /// The slot.
        slot: SlotId,
        /// The port used twice.
        port: PortId,
    },
}

/// The slot graph in CSR form.
///
/// Invariant: for every edge `(s, p) -> (n, q, t)` there is an edge
/// `(n, q) -> (s, p, t)`; no slot uses a port twice; no self loops.
///
/// Cost: `8 × edges + 4 × (slots + 1)` bytes. A 30×30×21 box grid has about
/// 19,000 slots and 110,000 directed edges: under 1 MB.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Graph {
    /// `offsets[s]..offsets[s + 1]` are slot `s`'s edges in `edges`. `u32`
    /// halves the memory of `usize` and 4 billion edges is enough.
    offsets: Vec<u32>,
    /// All edges, grouped by owning slot, in the file's order.
    edges: Vec<Edge>,
}

impl Graph {
    /// Builds and checks the graph from a loaded assembly.
    ///
    /// Cost: O(slots + edges × degree) for the reverse-edge check, where degree
    /// is the ports per slot (6 on a box grid).
    ///
    /// # Errors
    /// A [`GraphError`] for the first self loop, duplicate port or missing
    /// reverse edge found.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::graph::Graph;
    /// use lamda_core::ids::SlotId;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let graph = Graph::from_assembly(&assembly).unwrap();
    /// assert_eq!(graph.slot_count(), 2);
    /// assert_eq!(graph.edges(SlotId(0))[0].neighbour, SlotId(1));
    /// ```
    pub fn from_assembly(assembly: &Assembly) -> Result<Graph, GraphError> {
        let csr = &assembly.edges;
        let count = csr.port.len();
        // RUST: `with_capacity` allocates once; pushes up to `count` never reallocate.
        let mut edges = Vec::with_capacity(count);
        for i in 0..count {
            edges.push(Edge {
                port: csr.port[i],
                neighbour: csr.neighbour[i],
                neighbour_port: csr.neighbour_port[i],
                twist: csr.twist[i],
            });
        }
        let graph = Graph {
            // RUST: `.clone()` copies the offsets Vec; the assembly keeps its own
            // because the validator reads it later, independently of this graph.
            offsets: csr.offsets.clone(),
            edges,
        };
        graph.check()?;
        Ok(graph)
    }

    /// Verifies the invariant: no self loops, no duplicate ports, every edge
    /// has its reverse with the same twist.
    ///
    /// Cost: O(edges × degree).
    fn check(&self) -> Result<(), GraphError> {
        for slot_index in 0..self.slot_count() {
            let slot = SlotId(slot_index as u32);
            // RUST: `as u32` from usize could truncate above 4 billion; `slots`
            // came from a u32 in the file, so it cannot here.
            let edges = self.edges(slot);
            for (i, edge) in edges.iter().enumerate() {
                if edge.neighbour == slot {
                    return Err(GraphError::SelfLoop {
                        slot,
                        port: edge.port,
                    });
                }
                // WFC: a port is one face; two edges on one face make no sense.
                if edges[..i].iter().any(|other| other.port == edge.port) {
                    // RUST: `edges[..i]` is the sub-slice of the edges before `i`.
                    return Err(GraphError::DuplicatePort {
                        slot,
                        port: edge.port,
                    });
                }
                if !self.has_reverse(slot, edge) {
                    return Err(GraphError::MissingReverse {
                        slot,
                        port: edge.port,
                        neighbour: edge.neighbour,
                    });
                }
            }
        }
        Ok(())
    }

    /// Whether the neighbour of `edge` has an edge back to `slot` through the
    /// matching ports with the same twist (`.claude/rules/solver-core.md`,
    /// "Twist convention": the reverse edge carries the same twist).
    ///
    /// Cost: O(degree of the neighbour).
    fn has_reverse(&self, slot: SlotId, edge: &Edge) -> bool {
        self.edges(edge.neighbour).iter().any(|back| {
            back.neighbour == slot
                && back.port == edge.neighbour_port
                && back.neighbour_port == edge.port
                && back.twist == edge.twist
        })
    }

    /// Number of slots.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::graph::Graph;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Graph::from_assembly(&assembly).unwrap().slot_count(), 2);
    /// ```
    pub fn slot_count(&self) -> usize {
        // RUST: `saturating_sub` returns 0 instead of panicking if the Vec were
        // empty; `check()` in assembly.rs guarantees it is not, but this keeps
        // the method total.
        self.offsets.len().saturating_sub(1)
    }

    /// Number of directed edges (twice the number of touching slot pairs).
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::graph::Graph;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Graph::from_assembly(&assembly).unwrap().edge_count(), 2);
    /// ```
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// The edges leaving `slot`, in file order.
    ///
    /// WFC: propagation visits these in this order, in both languages, so the
    /// trail and the queue evolve identically.
    ///
    /// Cost: O(1): two offset reads and a slice.
    ///
    /// # Panics
    /// If `slot` is not a slot of this graph.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::graph::Graph;
    /// use lamda_core::ids::{PortId, SlotId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let graph = Graph::from_assembly(&assembly).unwrap();
    /// let edges = graph.edges(SlotId(1));
    /// assert_eq!(edges.len(), 1);
    /// assert_eq!(edges[0].port, PortId(1)); // -X: slot 0 is west of slot 1
    /// ```
    #[allow(clippy::needless_lifetimes)]
    // RUST: clippy would rather we let the compiler infer this lifetime; the
    // attribute silences that one lint here because the annotation is the lesson.
    pub fn edges<'a>(&'a self, slot: SlotId) -> &'a [Edge] {
        // RUST: `'a` is a lifetime parameter. It says: the returned slice borrows
        // from `self`, so it is valid exactly as long as the `&'a self` borrow.
        // The compiler infers this ("lifetime elision") when a method has `&self`
        // and returns one reference, so `fn edges(&self, slot: SlotId) -> &[Edge]`
        // means the same; it is spelled out once so the rule is visible. While
        // the caller holds the slice it cannot mutate the graph, which is what
        // makes the slice safe to read. `all_edges` below uses the elided form.
        let start = self.offsets[slot.index()] as usize;
        let end = self.offsets[slot.index() + 1] as usize;
        // RUST: `&self.edges[start..end]` is a sub-slice: a pointer and a length,
        // no copy. PY: `edges[start:end]`, but Python would copy the list.
        &self.edges[start..end]
    }

    /// Every edge of every slot, for the validator and for tests.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::graph::Graph;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let graph = Graph::from_assembly(&assembly).unwrap();
    /// assert_eq!(graph.all_edges().len(), graph.edge_count());
    /// ```
    pub fn all_edges(&self) -> &[Edge] {
        &self.edges
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::examples;

    /// A missing reverse edge must be rejected: propagation only pushes
    /// constraints along stored edges, so a one-way edge would let one side
    /// ignore the other.
    #[test]
    fn rejects_missing_reverse_edge() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        // Remove slot 1's edge by shrinking its range.
        assembly.edges.offsets[2] = 1;
        assembly.edges.port.pop();
        assembly.edges.neighbour.pop();
        assembly.edges.neighbour_port.pop();
        assembly.edges.twist.pop();
        assembly.check().unwrap();
        let err = Graph::from_assembly(&assembly).unwrap_err();
        assert_eq!(
            err,
            GraphError::MissingReverse {
                slot: SlotId(0),
                port: PortId(0),
                neighbour: SlotId(1)
            }
        );
    }

    /// A reverse edge with a different twist is not a reverse edge: both sides
    /// must measure the same quarter turns.
    #[test]
    fn rejects_twist_mismatch() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.edges.twist[1] = Twist(2);
        assert!(matches!(
            Graph::from_assembly(&assembly),
            Err(GraphError::MissingReverse { .. })
        ));
    }

    /// Self loops are rejected: a slot cannot be its own neighbour.
    #[test]
    fn rejects_self_loop() {
        let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assembly.edges.neighbour = vec![SlotId(0), SlotId(1)];
        assert!(matches!(
            Graph::from_assembly(&assembly),
            Err(GraphError::SelfLoop { .. })
        ));
    }
}
