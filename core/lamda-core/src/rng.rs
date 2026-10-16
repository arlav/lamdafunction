// SPDX-License-Identifier: MIT OR Apache-2.0

//! PCG32: the solver's random number generator.
//!
//! Where it sits: one `Pcg32` per solve attempt. The solver draws from it in a
//! fixed order (`.claude/rules/solver-core.md`, "Random numbers"): one `next()`
//! per slot at start for tie-break noise, then one `unit_f64()` per observation.
//! Because the same algorithm is written by hand in Python (`reference/`), the
//! same seed gives the same choices in both languages and on every OS.
//!
//! The algorithm is PCG-XSH-RR 64/32 by Melissa O'Neill (<https://www.pcg-random.org>):
//! a 64-bit linear congruential generator whose state is scrambled ("xorshift,
//! random rotate") down to 32 output bits.
//!
//! Rust in this file:
//! - `struct` with named fields and `impl` blocks holding its methods.
//!   <https://doc.rust-lang.org/book/ch05-01-defining-structs.html>
//! - integer types: `u32`, `u64` are unsigned 32- and 64-bit integers. Unlike
//!   Python's unbounded `int`, they have a fixed size; arithmetic that leaves
//!   the range *panics* in debug builds and wraps in release builds unless you
//!   say what you want. <https://doc.rust-lang.org/book/ch03-02-data-types.html>
//! - wrapping arithmetic: `wrapping_mul`, `wrapping_add` ask for modulo-2^64
//!   behaviour explicitly, on every build.
//!   <https://doc.rust-lang.org/std/primitive.u64.html#method.wrapping_mul>
//! - `&mut self`: a method that may change the struct it is called on.
//!   <https://doc.rust-lang.org/book/ch05-03-method-syntax.html>
//! - `#[derive(...)]`: ask the compiler to write trait impls for you.
//!   <https://doc.rust-lang.org/book/ch05-02-example-structs.html#adding-useful-functionality-with-derived-traits>
//! - unit tests in `#[cfg(test)] mod tests`, and doc tests in `/// # Examples`.
//!   <https://doc.rust-lang.org/book/ch11-01-writing-tests.html>
//!   <https://doc.rust-lang.org/rustdoc/write-documentation/documentation-tests.html>
//!
//! Read next: `bitset.rs`.

/// The LCG multiplier from the PCG reference implementation. Changing it changes
/// every result, so it is a named constant, not a literal in the code.
///
/// Cost: none; constants are folded into the code at compile time.
const MULTIPLIER: u64 = 6_364_136_223_846_793_005;
// RUST: `const` items need an explicit type. Underscores inside a number literal
// are ignored by the compiler and only help the reader.

/// The stream constant every solve attempt uses as `initseq`: the bytes spell
/// "LAMD". Attempt `k` is seeded with `Pcg32::new(seed + k, STREAM)`.
pub const STREAM: u64 = 0x4C41_4D44;

/// 2^32 as a float, the divisor that maps a `u32` onto `[0, 1)`.
const TWO_POW_32: f64 = 4_294_967_296.0;

/// A PCG32 generator: 64 bits of state and a 64-bit odd increment that selects
/// one of 2^63 independent streams.
///
/// Invariant: `inc` is always odd, which `new` guarantees; an even increment
/// would shorten the period.
///
/// Cost: 16 bytes; copying it (`Clone`) is as cheap as copying two integers.
#[derive(Debug, Clone, PartialEq, Eq)]
// RUST: `derive` writes trait implementations for us. `Debug` lets `{:?}` print
// the struct; `Clone` adds `.clone()` for an explicit copy; `PartialEq` and `Eq`
// give `==`, which the tests use to compare two generators' states.
// There is no `Copy` on purpose: a generator copied by accident would replay the
// same numbers twice, and `Copy` would make such copies silent.
pub struct Pcg32 {
    /// The LCG state. `u64` because the PCG32 recipe is defined on 64-bit words
    /// and depends on wrap-around at exactly 2^64.
    state: u64,
    /// The stream selector, `(initseq << 1) | 1`, so always odd.
    inc: u64,
}
// RUST: fields without `pub` are private to this module. Outside code must go
// through the methods, so the invariant on `inc` cannot be broken from outside.
// PY: Python has no enforced privacy; `_inc` would be a convention only.

impl Pcg32 {
    // RUST: an `impl` block attaches functions to the type. Functions that take
    // `self` are methods; those that do not (like `new`) are called on the type
    // itself: `Pcg32::new(..)`, like a Python `@staticmethod` used as a constructor.

    /// Creates a generator seeded with `initstate` on stream `initseq`, following
    /// the PCG reference `pcg32_srandom_r` exactly.
    ///
    /// Cost: O(1), two `next()` calls.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::rng::Pcg32;
    /// let mut rng = Pcg32::new(42, 54);
    /// assert_eq!(rng.next_u32(), 0xa15c_02b7);
    /// ```
    pub fn new(initstate: u64, initseq: u64) -> Pcg32 {
        // RUST: `let mut` because we change `rng` below; without `mut` the
        // compiler rejects the `rng.next_u32()` calls.
        let mut rng = Pcg32 {
            state: 0,
            // WHY: shift left one and set the low bit so the increment is odd.
            // `<<` on u64 drops the top bit; that is intended, the reference
            // implementation does the same.
            inc: (initseq << 1) | 1,
        };
        // WFC: the warm-up sequence from the reference implementation. Both
        // languages must do exactly this, or seeds will not match.
        rng.next_u32();
        // RUST: `wrapping_add` adds modulo 2^64 and never panics. Plain `+` would
        // panic in debug builds if the sum overflowed.
        rng.state = rng.state.wrapping_add(initstate);
        rng.next_u32();
        rng
        // RUST: the last expression without a semicolon is the return value.
        // Ownership of `rng` moves to the caller.
    }

    /// Creates the generator for solve attempt `attempt` of a run seeded with
    /// `seed`: `new(seed + attempt, STREAM)`, as the solver contract requires.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::rng::Pcg32;
    /// // Attempt 1 of seed 7 is the same generator as attempt 0 of seed 8.
    /// assert_eq!(Pcg32::for_attempt(7, 1), Pcg32::for_attempt(8, 0));
    /// ```
    pub fn for_attempt(seed: u64, attempt: u64) -> Pcg32 {
        // RUST: wrapping again, so a seed near u64::MAX cannot panic.
        Pcg32::new(seed.wrapping_add(attempt), STREAM)
    }

    /// Advances the generator and returns the next 32 random bits.
    ///
    /// Cost: O(1): one multiply, one add, a few shifts.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::rng::Pcg32;
    /// let mut rng = Pcg32::new(42, 54);
    /// let first = rng.next_u32();
    /// let second = rng.next_u32();
    /// assert_ne!(first, second);
    /// ```
    pub fn next_u32(&mut self) -> u32 {
        // RUST: `&mut self` is a mutable borrow of the generator for the length of
        // this call: we may change `self.state`, and nothing else can touch the
        // generator meanwhile. The caller keeps ownership.
        let old = self.state;
        // RUST: `u64` is `Copy`, so `old` is an independent copy of the value, not
        // a reference to the field.

        // WFC: the LCG step, state = state * a + c mod 2^64.
        // RUST: `wrapping_mul` and `wrapping_add` give modulo-2^64 arithmetic in
        // every build. Plain `*` would panic in debug builds the first time the
        // product exceeded u64::MAX, which it does almost immediately.
        // PY: `(old * MULTIPLIER + inc) & 0xFFFF_FFFF_FFFF_FFFF`.
        self.state = old.wrapping_mul(MULTIPLIER).wrapping_add(self.inc);

        // WFC: output function XSH-RR: xorshift the high bits down, then rotate
        // by the top five bits of the old state.
        // RUST: `as u32` keeps the low 32 bits and drops the rest. That truncation
        // is exactly what the algorithm wants here.
        let xorshifted = (((old >> 18) ^ old) >> 27) as u32;
        // RUST: `old >> 59` leaves 5 bits (0..=31), so this `as u32` loses nothing.
        let rot = (old >> 59) as u32;
        // RUST: `rotate_right` is a bit rotation, not a shift: bits that fall off
        // the right re-enter on the left. Standard-library method, no cast needed.
        xorshifted.rotate_right(rot)
    }

    /// Returns a float in `[0, 1)`: the next 32 bits divided by 2^32.
    ///
    /// WFC: the solver multiplies this by a slot's total weight to pick a variant
    /// (`.claude/rules/solver-core.md`, "Variant choice").
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::rng::Pcg32;
    /// let mut rng = Pcg32::new(42, 54);
    /// let x = rng.unit_f64();
    /// assert!((0.0..1.0).contains(&x));
    /// // The first output of this seed is 0xa15c02b7, so (the `_u32` suffix
    /// // matters: without it the literal is an i32, and 0xa15c02b7 does not fit):
    /// assert_eq!(x, 0xa15c_02b7_u32 as f64 / 4_294_967_296.0);
    /// ```
    pub fn unit_f64(&mut self) -> f64 {
        // RUST: `as f64` from u32 is exact: every u32 fits in f64's 53-bit mantissa.
        // Dividing by a power of two is also exact, so both languages get the same
        // bits (`.claude/rules/solver-core.md`, "Floating point").
        // PY: `next_u32() / 4294967296.0`.
        self.next_u32() as f64 / TWO_POW_32
    }
}

#[cfg(test)]
// RUST: this module is compiled only by `cargo test`.
mod tests {
    use super::*;
    // RUST: `super` is the enclosing module (this file); `*` imports everything
    // from it, including private items, because a child module may see its
    // parent's private items.

    /// Checks the shared test vector from `.claude/rules/solver-core.md`. If this
    /// fails, Rust and Python no longer agree on seeds, and nothing downstream is
    /// comparable.
    #[test]
    fn matches_reference_test_vector() {
        let mut rng = Pcg32::new(42, 54);
        let expected: [u32; 6] = [
            0xa15c_02b7,
            0x7b47_f409,
            0xba1d_3330,
            0x83d2_f293,
            0xbfa4_784b,
            0xcbed_606e,
        ];
        // RUST: `[u32; 6]` is a fixed-size array; `expected.iter()` borrows each
        // element in turn as `&u32`.
        for want in expected.iter() {
            assert_eq!(rng.next_u32(), *want);
            // RUST: `*want` reads the u32 behind the reference.
        }
    }

    /// Two generators with the same seed and stream must produce identical
    /// sequences: this is the determinism promise in miniature.
    #[test]
    fn same_seed_same_sequence() {
        let mut a = Pcg32::new(1, 2);
        let mut b = Pcg32::new(1, 2);
        for _ in 0..1000 {
            assert_eq!(a.next_u32(), b.next_u32());
        }
        assert_eq!(a, b);
    }

    /// Different streams with the same state must diverge; otherwise the stream
    /// parameter would be meaningless.
    #[test]
    fn different_streams_differ() {
        let mut a = Pcg32::new(1, 2);
        let mut b = Pcg32::new(1, 3);
        let same = (0..64).filter(|_| a.next_u32() == b.next_u32()).count();
        // RUST: the closure `|_| ...` captures `a` and `b` by mutable reference
        // because it calls `&mut self` methods on them.
        assert!(same < 4, "streams 2 and 3 agreed on {same} of 64 outputs");
    }

    /// `unit_f64` must stay inside [0, 1) for extreme outputs, including the
    /// largest possible u32.
    #[test]
    fn unit_f64_is_below_one() {
        let largest = u32::MAX as f64 / TWO_POW_32;
        assert!(largest < 1.0);
        let mut rng = Pcg32::new(99, 7);
        for _ in 0..10_000 {
            let x = rng.unit_f64();
            assert!((0.0..1.0).contains(&x));
        }
    }

    /// Seeds near `u64::MAX` must not panic: `for_attempt` adds the attempt index
    /// with wrapping arithmetic.
    #[test]
    fn for_attempt_wraps_without_panic() {
        let mut rng = Pcg32::for_attempt(u64::MAX, 5);
        let _ = rng.next_u32();
    }
}
