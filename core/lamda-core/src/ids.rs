// SPDX-License-Identifier: MIT OR Apache-2.0

//! Index newtypes `SlotId`, `VariantId`, `PortId`, `Twist`, and `EdgeType`.
//!
//! Where it sits: every other file uses these to say which slot, variant, port
//! or twist it means. The compiled assembly stores plain integers; `assembly.rs`
//! wraps them on load and the result arrays unwrap them at the end. `EdgeType`
//! names the kind of an edge, (port, neighbour port, twist), which picks the
//! adjacency table the solver reads for it.
//!
//! Rust in this file:
//! - newtypes: a tuple struct with one field, `struct SlotId(pub u32)`, is a
//!   distinct type to the compiler although it costs nothing at run time.
//!   <https://doc.rust-lang.org/rust-by-example/generics/new_types.html>
//! - `Copy`: types that are duplicated bit for bit on assignment instead of
//!   moved; all four newtypes are, like the integers they wrap.
//!   <https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html#stack-only-data-copy>
//! - `From` / `Into`: the standard conversion traits; `u32::from(id)` and
//!   `let n: u32 = id.into()` both work once `From` is implemented.
//!   <https://doc.rust-lang.org/rust-by-example/conversion/from_into.html>
//! - `PartialOrd` / `Ord` and `Hash`: derived so ids can be sorted, used as map
//!   keys and compared in the entropy heap.
//!   <https://doc.rust-lang.org/book/appendix-03-derivable-traits.html>
//! - `impl Display`: the trait behind `{}` formatting.
//!   <https://doc.rust-lang.org/rust-by-example/hello/print/print_display.html>
//! - `serde(transparent)`: (de)serialise the newtype as the bare integer.
//! - a struct with named fields (`EdgeType`) and field init shorthand
//!   (`EdgeType { port, .. }` when a variable is named like the field).
//!   <https://doc.rust-lang.org/book/ch05-01-defining-structs.html>
//!
//! Read next: `assembly.rs`.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Index of a slot in the slot graph.
///
/// WHY a newtype: `SlotId` and `VariantId` are both "a u32", but passing a slot
/// index where a variant index belongs is a silent bug in Python and NumPy.
/// Here the compiler rejects it: `domains[slot]` compiles, `domains[variant]`
/// does not. WHY `u32` and not `usize`: it halves the memory of every index
/// array (the graph stores millions of them) and 4 billion slots is plenty.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
// RUST: `Copy` (which needs `Clone`) makes `let b = a;` a bit copy that leaves
// `a` usable. `PartialOrd, Ord` give `<` and sorting; `Hash` allows use as a
// HashMap key; `Serialize, Deserialize` come from serde's derive macros.
#[serde(transparent)]
// RUST: without `transparent`, serde would write `{"0": 5}` instead of `5`.
pub struct SlotId(pub u32);
// RUST: a tuple struct: one unnamed field reached as `.0`. The field is `pub`,
// so `SlotId(3)` and `id.0` work everywhere.

/// Index of a solver variant (module × rotation × part).
///
/// `u32` for the same reasons as `SlotId`; see there.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct VariantId(pub u32);

/// A port: one face of a slot. On a box grid the ports are numbered
/// 0 `+X`, 1 `-X`, 2 `+Y`, 3 `-Y`, 4 `+Z`, 5 `-Z` (`docs/PLAN.md`, "Face frames").
///
/// `u8` because a slot has a handful of ports and the edge arrays store one per
/// edge: a byte each keeps them small.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct PortId(pub u8);

/// Quarter turns between two face frames across an edge, in `0..=3`
/// (`.claude/rules/solver-core.md`, "Twist convention"). Zero on box grids.
///
/// `u8` because only four values exist.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Twist(pub u8);

/// Number of distinct twists; tables are keyed by `port * TWISTS + twist`.
pub const TWISTS: usize = 4;

impl SlotId {
    /// This id as a `usize`, for indexing a `Vec`.
    ///
    /// Cost: O(1), a widening cast that cannot lose bits.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::SlotId;
    /// let names = ["a", "b", "c"];
    /// assert_eq!(names[SlotId(2).index()], "c");
    /// ```
    pub fn index(self) -> usize {
        // RUST: `self` by value is fine because SlotId is `Copy`. `u32 as usize`
        // never truncates on the 64-bit platforms Blender runs on.
        self.0 as usize
    }
}

impl VariantId {
    /// This id as a `usize`, for indexing a `Vec` or a `Bitset`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::VariantId;
    /// use lamda_core::bitset::Bitset;
    /// let domain = Bitset::from_bits(8, &[5]);
    /// assert!(domain.contains(VariantId(5).index()));
    /// ```
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

impl PortId {
    /// This port as a `usize`, for indexing.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::PortId;
    /// assert_eq!(PortId(4).index(), 4);
    /// ```
    pub fn index(self) -> usize {
        // RUST: `u8 as usize` widens; nothing can be lost.
        self.0 as usize
    }

    /// The port on the opposite face of a box slot: `+X` ↔ `-X` and so on.
    ///
    /// WFC: a box-grid edge through `p` arrives at `opposite(p)`, which is how
    /// schema 1.0 tables are read ([`EdgeType::box_grid`]). Only meaningful for
    /// the six box ports; edges on surfaces and cell complexes carry the
    /// neighbour's port themselves (schema 1.1).
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::PortId;
    /// assert_eq!(PortId(0).opposite(), PortId(1)); // +X <-> -X
    /// assert_eq!(PortId(5).opposite(), PortId(4)); // -Z <-> +Z
    /// ```
    pub fn opposite(self) -> PortId {
        // WHY: faces are numbered in (+, -) pairs, so flipping the lowest bit
        // swaps a face with its opposite: 0^1 = 1, 1^1 = 0, 2^1 = 3, ...
        PortId(self.0 ^ 1)
    }
}

impl Twist {
    /// This twist as a `usize`, for indexing.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::Twist;
    /// assert_eq!(Twist(3).index(), 3);
    /// ```
    pub fn index(self) -> usize {
        self.0 as usize
    }

    /// Whether this twist is in the legal range `0..=3`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::Twist;
    /// assert!(Twist(3).is_valid());
    /// assert!(!Twist(4).is_valid());
    /// ```
    pub fn is_valid(self) -> bool {
        (self.0 as usize) < TWISTS
    }
}

/// The kind of an edge: port `port` of a slot meets port `neighbour_port` of
/// its neighbour, at `twist`. Every edge of one kind shares one adjacency table.
///
/// WFC: on a box grid `+X` always meets `-X` with twist 0, so six kinds cover
/// every edge. On a surface shell the slot frames turn from face to face, so
/// `+X` may meet `+Y`; the kind must name both ports (`docs/PLAN.md`,
/// "Contract change: edge types (schema 1.1)").
///
/// WHY a struct with named fields and not a tuple `(PortId, PortId, Twist)`:
/// the two ports have the same type, so a tuple would let `(p, q, t)` and
/// `(q, p, t)` be swapped silently; field names make every use say which is which.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
// RUST: the same derives as the newtypes above. `Copy` works because every
// field is `Copy`, so an `EdgeType` is passed by value like an integer.
pub struct EdgeType {
    /// The port on the slot being looked at.
    pub port: PortId,
    /// The port on the neighbour that touches it.
    pub neighbour_port: PortId,
    /// The twist of the edge, the same seen from either side.
    pub twist: Twist,
}

impl EdgeType {
    /// The box-grid kind: `port` meets its opposite port.
    ///
    /// WHY: schema 1.0 files name only `(port, twist)` per table; this is how
    /// they are read, so every 1.0 file keeps its exact meaning.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{EdgeType, PortId, Twist};
    /// let kind = EdgeType::box_grid(PortId(0), Twist(0));
    /// assert_eq!(kind.neighbour_port, PortId(1)); // +X meets -X
    /// ```
    pub fn box_grid(port: PortId, twist: Twist) -> EdgeType {
        EdgeType {
            port,
            neighbour_port: port.opposite(),
            twist,
        }
        // RUST: `port,` is shorthand for `port: port,` when a variable has the
        // field's name ("field init shorthand").
    }

    /// The same edge seen from the neighbour: the ports swap, the twist stays.
    ///
    /// WFC: the symmetry invariant pairs `table(e)` with `table(e.reverse())`
    /// (`.claude/rules/solver-core.md`, "Data model").
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{EdgeType, PortId, Twist};
    /// let kind = EdgeType { port: PortId(0), neighbour_port: PortId(2), twist: Twist(1) };
    /// let back = kind.reverse();
    /// assert_eq!((back.port, back.neighbour_port, back.twist), (PortId(2), PortId(0), Twist(1)));
    /// assert_eq!(back.reverse(), kind);
    /// ```
    pub fn reverse(self) -> EdgeType {
        // RUST: `self` by value: EdgeType is `Copy`, so the caller keeps its own copy.
        EdgeType {
            port: self.neighbour_port,
            neighbour_port: self.port,
            twist: self.twist,
        }
    }

    /// Index of this kind's table in a flat list: `(port × ports + neighbour_port) × 4 + twist`.
    ///
    /// `ports` is the assembly's port count (6 for boxes), so a box assembly has
    /// 6 × 6 × 4 = 144 possible keys, of which a grid uses 6 and a shell up to 18.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::ids::{EdgeType, PortId, Twist};
    /// let kind = EdgeType { port: PortId(2), neighbour_port: PortId(3), twist: Twist(1) };
    /// assert_eq!(kind.key(6), (2 * 6 + 3) * 4 + 1);
    /// ```
    pub fn key(self, ports: usize) -> usize {
        // RUST: plain `*` and `+` on usize. They cannot overflow here: ports is at
        // most 255 (a u8), so the largest key is about 260,000.
        (self.port.index() * ports + self.neighbour_port.index()) * TWISTS + self.twist.index()
    }
}

impl fmt::Display for EdgeType {
    /// Writes `port 0 to 2 twist 1`, for error messages.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // RUST: `'_` is an elided lifetime: the Formatter borrows something for a
        // while and we do not need to name how long. `write!` returns
        // `fmt::Result`, which this function passes on as its own result.
        write!(
            f,
            "port {} to {} twist {}",
            self.port, self.neighbour_port, self.twist
        )
    }
}

/// Writes the same `From` and `Display` impls for each newtype, so the four
/// types cannot drift apart.
///
/// RUST: a declarative macro (`macro_rules!`) is a pattern-matching code
/// template. `$name` and `$inner` are substituted for each `Name => inner,`
/// line in the invocation below. <https://doc.rust-lang.org/book/ch20-05-macros.html>
macro_rules! newtype_conversions {
    ($($name:ident => $inner:ty),* $(,)?) => {
        $(
            impl From<$name> for $inner {
                /// Unwraps the id to its integer.
                fn from(id: $name) -> $inner {
                    id.0
                }
            }

            impl From<$inner> for $name {
                /// Wraps an integer as an id. No range check: the integer is the id.
                fn from(raw: $inner) -> $name {
                    $name(raw)
                }
            }

            impl fmt::Display for $name {
                /// Prints the bare integer, so `{}` on an id reads naturally.
                fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                    // RUST: `write!` into the formatter; `fmt::Result` is
                    // `Result<(), fmt::Error>`.
                    write!(f, "{}", self.0)
                }
            }
        )*
    };
}

newtype_conversions! {
    SlotId => u32,
    VariantId => u32,
    PortId => u8,
    Twist => u8,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Opposite must be an involution (applying it twice gives the start) and
    /// must pair every box face with the other face on its axis.
    #[test]
    fn opposite_pairs_faces() {
        for p in 0..6u8 {
            let port = PortId(p);
            assert_eq!(port.opposite().opposite(), port);
            assert_ne!(port.opposite(), port);
            // Same axis: integer division by 2 gives the axis index.
            assert_eq!(port.opposite().0 / 2, p / 2);
        }
    }

    /// Ordering derives from the integer, so sorting ids sorts by index; the
    /// solver relies on that for its total order of slots.
    #[test]
    fn ids_order_by_value() {
        let mut ids = vec![SlotId(5), SlotId(1), SlotId(3)];
        ids.sort();
        assert_eq!(ids, vec![SlotId(1), SlotId(3), SlotId(5)]);
    }

    /// Conversions and display round-trip through the bare integer.
    #[test]
    fn conversions_roundtrip() {
        let id: VariantId = 7u32.into();
        assert_eq!(u32::from(id), 7);
        assert_eq!(id.to_string(), "7");
        assert_eq!(Twist::from(2u8).index(), 2);
    }

    /// The JSON form is the bare integer, matching the compiled assembly schema.
    #[test]
    fn serde_is_transparent() {
        let json = serde_json::to_string(&SlotId(9)).unwrap();
        // `unwrap` is fine in tests: a failure here is a test failure.
        assert_eq!(json, "9");
        let back: SlotId = serde_json::from_str("9").unwrap();
        assert_eq!(back, SlotId(9));
    }

    /// Table keys are unique over every (port, neighbour port, twist) in range
    /// and stay below `ports² × 4`: two edge kinds sharing a key would make
    /// the solver read the wrong table.
    #[test]
    fn edge_type_keys_are_unique() {
        let mut keys: Vec<usize> = Vec::new();
        for port in 0..6u8 {
            for neighbour_port in 0..6u8 {
                for twist in 0..4u8 {
                    let kind = EdgeType {
                        port: PortId(port),
                        neighbour_port: PortId(neighbour_port),
                        twist: Twist(twist),
                    };
                    keys.push(kind.key(6));
                }
            }
        }
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(keys.len(), 144);
        assert_eq!(keys.last(), Some(&143));
    }

    /// Reversing twice gives the edge back, and a box-grid kind reversed is
    /// the box-grid kind of the opposite port: the 1.0 reading stays consistent.
    #[test]
    fn reverse_is_an_involution() {
        for port in 0..6u8 {
            let kind = EdgeType::box_grid(PortId(port), Twist(0));
            assert_eq!(kind.reverse().reverse(), kind);
            assert_eq!(
                kind.reverse(),
                EdgeType::box_grid(PortId(port).opposite(), Twist(0))
            );
        }
    }
}
