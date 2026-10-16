// SPDX-License-Identifier: MIT OR Apache-2.0

//! Index newtypes: `SlotId`, `VariantId`, `PortId`, `Twist`.
//!
//! Where it sits: every other file uses these to say which slot, variant, port
//! or twist it means. The compiled assembly stores plain integers; `assembly.rs`
//! wraps them on load and the result arrays unwrap them at the end.
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
    /// WFC: the symmetry invariant pairs `table(p, t)` with
    /// `table(opposite(p), t)` (`.claude/rules/solver-core.md`, "Data model").
    /// Only meaningful for the six box ports; cell complexes (research track)
    /// will carry the opposite port on the edge instead.
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

/// Key into the per-(port, twist) table list: `port * 4 + twist`.
///
/// Cost: O(1).
///
/// # Examples
/// ```
/// use lamda_core::ids::{PortId, Twist, table_key};
/// assert_eq!(table_key(PortId(2), Twist(1)), 9);
/// ```
pub fn table_key(port: PortId, twist: Twist) -> usize {
    port.index() * TWISTS + twist.index()
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

    /// Table keys are unique over all (port, twist) pairs in range.
    #[test]
    fn table_keys_are_unique() {
        let mut keys: Vec<usize> = Vec::new();
        for port in 0..6u8 {
            for twist in 0..4u8 {
                keys.push(table_key(PortId(port), Twist(twist)));
            }
        }
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(keys.len(), 24);
    }
}
