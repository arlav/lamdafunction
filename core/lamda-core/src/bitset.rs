// SPDX-License-Identifier: MIT OR Apache-2.0

//! Bitsets: a slot's domain as a row of 64-bit words.
//!
//! Where it sits: every slot owns one `Bitset` over the variants (bit `v` set
//! means "variant `v` is still allowed here"). The adjacency tables
//! (`adjacency.rs`) are rows of bitsets too, so propagation is a handful of
//! word-wise AND and OR operations (`docs/PLAN.md`, "Solver, step by step").
//!
//! Rust in this file:
//! - ownership and moves: a `Vec<u64>` owns heap memory; assigning or passing it
//!   moves it, and the old name can no longer be used.
//!   <https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html>
//! - borrowing: `&T` is a shared, read-only borrow; `&mut T` is an exclusive,
//!   writable one. The compiler proves that no `&mut` overlaps any other borrow.
//!   <https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html>
//! - slices `&[u64]`: a view into part or all of a `Vec` (or array), no copy.
//!   <https://doc.rust-lang.org/book/ch04-03-slices.html>
//! - iterators and adaptors (`iter`, `iter_mut`, `zip`, `map`, `enumerate`), and
//!   `impl Iterator` in a return type.
//!   <https://doc.rust-lang.org/book/ch13-02-iterators.html>
//! - `usize`: the pointer-sized unsigned integer every index into a `Vec` or
//!   slice must have. <https://doc.rust-lang.org/book/ch03-02-data-types.html>
//! - operator traits (`BitAnd`, `BitOr`) so `&a & &b` works like Python's
//!   `a & b` on sets. <https://doc.rust-lang.org/book/ch19-03-advanced-traits.html>
//! - `debug_assert!`: a check that runs in debug and test builds only.
//!   <https://doc.rust-lang.org/std/macro.debug_assert.html>
//!
//! Read next: `ids.rs`.

use std::ops::{BitAnd, BitOr};
// RUST: `use` brings names into scope, like a Python `from ... import`. These
// two traits are what the `&` and `|` operators call.

/// Bits per word. `u64` holds 64 variants; `usize` because it is used in index
/// arithmetic.
const WORD_BITS: usize = 64;

/// A fixed-size set of variant indices `0..len`, stored one bit per variant.
///
/// Invariant: bits at positions `>= len` in the last word are always zero, so
/// `count()` and `==` can look at whole words without masking.
///
/// Cost: `ceil(len / 64)` words of 8 bytes; 400 variants fit in 7 words.
#[derive(Debug, Clone, PartialEq, Eq)]
// RUST: `derive` as in rng.rs. `Clone` here copies the heap words, which costs
// O(words); the trail (`trail.rs`) clones domains to remember old states.
pub struct Bitset {
    /// Number of variants this set ranges over. `usize` because it is compared
    /// with indices.
    len: usize,
    /// The bits, least-significant bit of `words[0]` being variant 0.
    /// WHY `Vec<u64>` and not `Vec<bool>`: a `bool` takes a whole byte and
    /// intersecting two domains would be one operation per variant; packed words
    /// handle 64 variants per AND. PY: like a Python `int` used as a bitmask,
    /// but with a fixed width.
    words: Vec<u64>,
}

impl Bitset {
    /// Creates an empty set over `len` variants.
    ///
    /// Cost: O(len / 64).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let empty = Bitset::new(100);
    /// assert!(empty.is_empty());
    /// assert_eq!(empty.len(), 100);
    /// ```
    pub fn new(len: usize) -> Bitset {
        // RUST: `vec![value; n]` builds a Vec of n copies of value.
        // PY: `[0] * n`.
        Bitset {
            len,
            // RUST: `len` alone is shorthand for `len: len` (field init shorthand).
            words: vec![0; Bitset::word_count(len)],
        }
    }

    /// Creates a set over `len` variants with every variant present.
    ///
    /// Cost: O(len / 64).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let all = Bitset::full(70);
    /// assert_eq!(all.count(), 70);
    /// assert!(all.contains(69));
    /// ```
    pub fn full(len: usize) -> Bitset {
        let mut set = Bitset {
            len,
            words: vec![u64::MAX; Bitset::word_count(len)],
        };
        // WFC/WHY: keep the invariant that bits past `len` are zero, so counts
        // and equality stay honest when `len` is not a multiple of 64.
        set.clear_tail();
        set
    }

    /// Creates a set over `len` variants containing exactly `bits`.
    ///
    /// # Panics
    /// If any bit in `bits` is `>= len` (a programming error, not bad input).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let set = Bitset::from_bits(8, &[0, 1, 2]);
    /// assert_eq!(set.ones().collect::<Vec<_>>(), vec![0, 1, 2]);
    /// ```
    pub fn from_bits(len: usize, bits: &[usize]) -> Bitset {
        // RUST: `bits: &[usize]` is a borrowed slice: the caller may pass `&vec`
        // or `&[1, 2, 3]`; we only read it and copy nothing.
        let mut set = Bitset::new(len);
        // RUST: `for bit in bits` iterates the slice by shared reference, so `bit`
        // is `&usize`; `*bit` reads the value. `usize` is `Copy`, so this is free.
        for bit in bits {
            set.insert(*bit);
        }
        set
    }

    /// Number of words needed for `len` bits (ceiling division).
    ///
    /// Cost: O(1).
    fn word_count(len: usize) -> usize {
        // RUST: integer division rounds down, so add `WORD_BITS - 1` first.
        // `len == 0` gives 0 words, which every method below tolerates.
        len.div_ceil(WORD_BITS)
    }

    /// Zeroes the bits past `len` in the last word (the invariant).
    ///
    /// Cost: O(1).
    fn clear_tail(&mut self) {
        let used = self.len % WORD_BITS;
        // RUST: `if let Some(last) = ...` runs the block only when `last_mut()`
        // returns a value, binding `last: &mut u64` to the last word.
        // PY: `if words: last = words[-1]`, but with the borrow checked.
        if used != 0 {
            if let Some(last) = self.words.last_mut() {
                // RUST: `(1u64 << used) - 1` is a mask of `used` ones. `used` is
                // 1..=63 here, so the shift never overflows.
                *last &= (1u64 << used) - 1;
            }
        }
    }

    /// The number of variants this set ranges over (its capacity, not its count).
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// assert_eq!(Bitset::new(37).len(), 37);
    /// ```
    pub fn len(&self) -> usize {
        // RUST: `&self` is a shared borrow: read-only access while the call runs.
        self.len
    }

    /// The words of this set, for callers that need raw access (the validator and
    /// tests). The returned slice borrows `self` and cannot outlive it.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let set = Bitset::from_bits(8, &[0, 3]);
    /// assert_eq!(set.words(), &[0b1001]);
    /// ```
    pub fn words(&self) -> &[u64] {
        // RUST: `&self.words` turns `&Vec<u64>` into `&[u64]` automatically
        // (deref coercion). The lifetime is elided: "lives as long as `self`".
        &self.words
    }

    /// Whether `bit` is in the set. Bits at or beyond `len` are never present.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let set = Bitset::from_bits(8, &[5]);
    /// assert!(set.contains(5));
    /// assert!(!set.contains(4));
    /// assert!(!set.contains(500));
    /// ```
    pub fn contains(&self, bit: usize) -> bool {
        // RUST: `/` and `%` on usize locate the word and the bit inside it.
        // `self.words.get(i)` returns `Option<&u64>`: `None` when out of range,
        // instead of panicking like `self.words[i]` would.
        // RUST: `is_some_and(f)` is true when the Option holds a value and `f`
        // says yes about it. PY: `x is not None and f(x)`.
        self.words
            .get(bit / WORD_BITS)
            .is_some_and(|word| (word >> (bit % WORD_BITS)) & 1 == 1)
    }

    /// Adds `bit` to the set.
    ///
    /// Cost: O(1).
    ///
    /// # Panics
    /// If `bit >= len`.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let mut set = Bitset::new(8);
    /// set.insert(3);
    /// assert!(set.contains(3));
    /// ```
    pub fn insert(&mut self, bit: usize) {
        // RUST: `assert!` runs in every build and panics with the message. This is
        // a contract violation by the caller, so a panic is right.
        assert!(
            bit < self.len,
            "bit {bit} out of range for {} variants",
            self.len
        );
        // RUST: `self.words[i]` indexes the Vec; `|=` ORs in place through `&mut self`.
        self.words[bit / WORD_BITS] |= 1u64 << (bit % WORD_BITS);
    }

    /// Removes `bit` from the set and reports whether it was present.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let mut set = Bitset::from_bits(8, &[3]);
    /// assert!(set.remove(3));
    /// assert!(!set.remove(3));
    /// ```
    pub fn remove(&mut self, bit: usize) -> bool {
        if !self.contains(bit) {
            return false;
            // RUST: `return` leaves the function early; the final expression form
            // is only for the last line.
        }
        // RUST: `!` on an integer is bitwise NOT. `&=` clears the bit in place.
        self.words[bit / WORD_BITS] &= !(1u64 << (bit % WORD_BITS));
        true
    }

    /// Whether no variant remains. An empty domain is a contradiction.
    ///
    /// Cost: O(len / 64).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// assert!(Bitset::new(8).is_empty());
    /// assert!(!Bitset::full(8).is_empty());
    /// ```
    pub fn is_empty(&self) -> bool {
        // RUST: `iter()` yields `&u64`; `all` stops at the first false.
        // PY: `all(w == 0 for w in words)`.
        self.words.iter().all(|word| *word == 0)
    }

    /// Number of variants in the set.
    ///
    /// Cost: O(len / 64), one popcount instruction per word.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// assert_eq!(Bitset::from_bits(100, &[0, 64, 99]).count(), 3);
    /// ```
    pub fn count(&self) -> usize {
        // RUST: `count_ones()` returns u32; `as usize` widens it, nothing is lost.
        // `map` turns each word into its count; `sum` adds them up.
        // PERF: the compiler emits the `popcnt` instruction on x86-64 and NEON `cnt`.
        self.words
            .iter()
            .map(|word| word.count_ones() as usize)
            .sum()
    }

    /// If exactly one variant remains, returns it; otherwise `None`.
    ///
    /// WFC: a collapsed slot has exactly one variant; this is how the result
    /// arrays read it off.
    ///
    /// Cost: O(len / 64).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// assert_eq!(Bitset::from_bits(8, &[6]).single(), Some(6));
    /// assert_eq!(Bitset::from_bits(8, &[1, 6]).single(), None);
    /// ```
    pub fn single(&self) -> Option<usize> {
        // RUST: `Option<usize>` is either `Some(value)` or `None`; the caller must
        // handle both, which is how Rust avoids Python's accidental `None` errors.
        if self.count() == 1 {
            self.ones().next()
            // RUST: `next()` on an iterator yields `Option<usize>`, exactly the
            // type we return, so no wrapping is needed.
        } else {
            None
        }
    }

    /// Iterates the variants in the set in ascending order.
    ///
    /// WFC: every sum over a domain walks it in ascending variant order, so the
    /// floating-point results match the Python reference bit for bit.
    ///
    /// Cost: O(len / 64 + count), skipping zero words whole.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let set = Bitset::from_bits(130, &[129, 2, 64]);
    /// assert_eq!(set.ones().collect::<Vec<_>>(), vec![2, 64, 129]);
    /// ```
    pub fn ones(&self) -> impl Iterator<Item = usize> + '_ {
        // RUST: `impl Iterator<Item = usize> + '_` means "some iterator type that
        // yields usize, borrowing self for as long as the iterator lives" (the `'_`
        // is that elided lifetime). The caller cannot name the concrete type and
        // does not need to.
        // RUST: `enumerate()` pairs each word with its index. `flat_map` turns each
        // word into a sub-iterator of bit positions and chains them.
        // PY: `(i*64 + b for i, w in enumerate(words) for b in bits_of(w))`.
        self.words
            .iter()
            .enumerate()
            .flat_map(|(index, word)| WordOnes::new(index * WORD_BITS, *word))
    }

    /// Intersects this domain with `support`, in place, and reports whether anything
    /// was removed.
    ///
    /// WFC: this is the heart of propagation. A neighbour keeps only the variants that
    /// some variant in this slot allows across the shared port (`docs/PLAN.md`,
    /// "Solver, step by step", step 2).
    ///
    /// Cost: O(len / 64), one AND per 64 variants.
    ///
    /// # Panics
    /// In debug builds, if `support` was built for a different number of variants.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let mut domain = Bitset::from_bits(8, &[0, 1, 2]);
    /// let support = Bitset::from_bits(8, &[1, 2, 5]);
    /// assert!(domain.intersect_with(&support)); // variant 0 was removed
    /// assert_eq!(domain.ones().collect::<Vec<_>>(), vec![1, 2]);
    /// ```
    pub fn intersect_with(&mut self, support: &Bitset) -> bool {
        // RUST: `&mut self` is a mutable borrow. This function may change the Bitset,
        // and while it runs nothing else can read or write it. The caller still owns it.
        // RUST: `support: &Bitset` is a shared, read-only borrow. Because `self` is
        // borrowed mutably, the compiler guarantees `support` is a different Bitset.
        // PY: in Python every name is a shared reference to a mutable object; Rust puts
        //     "may I change this?" into the type and checks it at compile time.
        debug_assert_eq!(self.len, support.len, "domains over different variant sets");
        // RUST: `debug_assert_eq!` runs in debug builds (cargo test, cargo build) and is
        // compiled away in release builds, so the check costs nothing inside Blender.

        let mut changed = false;
        // RUST: bindings are immutable unless declared `mut`.

        for (word, mask) in self.words.iter_mut().zip(support.words.iter()) {
            // RUST: `iter_mut()` yields `&mut u64`, a mutable reference into our Vec;
            // `iter()` yields `&u64`. `zip` pairs them and stops at the shorter one.
            // PY: like `for w, m in zip(words, masks)`, except we can write through `w`.
            let before = *word;
            // RUST: `*word` dereferences the reference to read the u64 it points at.
            // u64 is `Copy`, so `before` is an independent copy, not a borrow.
            *word &= *mask;
            // WFC: keep only the variants the support allows (bitwise AND).
            // PERF: one instruction handles 64 variants.
            changed |= *word != before;
        }
        changed
        // RUST: the last expression, written without a semicolon, is the return value.
    }

    /// Adds every variant of `other` to this set, in place.
    ///
    /// WFC: building a support mask is the OR of one table row per variant still in
    /// the slot (`docs/PLAN.md`, "Propagation").
    ///
    /// Cost: O(len / 64).
    ///
    /// # Panics
    /// In debug builds, if `other` ranges over a different number of variants.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let mut support = Bitset::new(8);
    /// support.union_with(&Bitset::from_bits(8, &[1]));
    /// support.union_with(&Bitset::from_bits(8, &[7]));
    /// assert_eq!(support.ones().collect::<Vec<_>>(), vec![1, 7]);
    /// ```
    pub fn union_with(&mut self, other: &Bitset) {
        debug_assert_eq!(self.len, other.len, "sets over different variant sets");
        // RUST: the same `iter_mut().zip(iter())` shape as `intersect_with`.
        for (word, mask) in self.words.iter_mut().zip(other.words.iter()) {
            *word |= *mask;
        }
    }

    /// Returns the variants in `self` that are not in `other` (set difference).
    ///
    /// WFC: after propagation, `old.difference(&new)` lists the variants a slot lost,
    /// which the entropy sums subtract in ascending order (`entropy.rs`).
    ///
    /// Cost: O(len / 64), allocates one new set.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let old = Bitset::from_bits(8, &[1, 2, 3]);
    /// let new = Bitset::from_bits(8, &[2]);
    /// assert_eq!(old.difference(&new).ones().collect::<Vec<_>>(), vec![1, 3]);
    /// ```
    pub fn difference(&self, other: &Bitset) -> Bitset {
        debug_assert_eq!(self.len, other.len, "sets over different variant sets");
        // RUST: `zip` + `map` + `collect()` builds a new Vec<u64>; `collect` knows
        // the target type from the field it is assigned to.
        let words = self
            .words
            .iter()
            .zip(other.words.iter())
            .map(|(mine, theirs)| mine & !theirs)
            .collect();
        Bitset {
            len: self.len,
            words,
        }
    }

    /// Whether this set and `other` share at least one variant.
    ///
    /// Cost: O(len / 64), stops at the first shared word.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let a = Bitset::from_bits(8, &[1, 2]);
    /// assert!(a.intersects(&Bitset::from_bits(8, &[2, 3])));
    /// assert!(!a.intersects(&Bitset::from_bits(8, &[3])));
    /// ```
    pub fn intersects(&self, other: &Bitset) -> bool {
        debug_assert_eq!(self.len, other.len, "sets over different variant sets");
        self.words
            .iter()
            .zip(other.words.iter())
            .any(|(mine, theirs)| mine & theirs != 0)
    }

    /// Whether every variant of this set is also in `other`.
    ///
    /// WFC: propagation asks "would intersecting with the support change this
    /// domain?" before cloning the old domain for the trail; the answer is
    /// `!domain.is_subset_of(&support)`.
    ///
    /// Cost: O(len / 64), stops at the first word with a bit outside `other`.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let small = Bitset::from_bits(8, &[1]);
    /// let big = Bitset::from_bits(8, &[1, 2]);
    /// assert!(small.is_subset_of(&big));
    /// assert!(!big.is_subset_of(&small));
    /// ```
    pub fn is_subset_of(&self, other: &Bitset) -> bool {
        debug_assert_eq!(self.len, other.len, "sets over different variant sets");
        // RUST: `all` is the dual of `any`: true only if every pair passes.
        self.words
            .iter()
            .zip(other.words.iter())
            .all(|(mine, theirs)| mine & !theirs == 0)
    }

    /// Removes every variant, keeping the capacity.
    ///
    /// Cost: O(len / 64).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::bitset::Bitset;
    /// let mut set = Bitset::full(8);
    /// set.clear();
    /// assert!(set.is_empty());
    /// ```
    pub fn clear(&mut self) {
        // RUST: `fill` writes the value into every element of the slice.
        self.words.fill(0);
    }
}

/// Iterator over the set bits of one word, lowest first, offset by the word's
/// starting variant index. Used by `Bitset::ones`.
///
/// Cost: O(count of set bits), one trailing-zeros instruction per bit.
struct WordOnes {
    /// Variant index of bit 0 of this word.
    base: usize,
    /// Bits not yet yielded; cleared one at a time from the bottom.
    remaining: u64,
}

impl WordOnes {
    /// Starts iterating `word`, whose bit 0 is variant `base`.
    fn new(base: usize, word: u64) -> WordOnes {
        WordOnes {
            base,
            remaining: word,
        }
    }
}

impl Iterator for WordOnes {
    // RUST: implementing the `Iterator` trait needs one associated type (`Item`)
    // and one method (`next`). Everything else (`map`, `collect`, `zip`, ...)
    // then works for free, because the trait provides them.
    // PY: a class with `__next__` raising StopIteration, which `for` understands.

    /// Each item is a variant index.
    type Item = usize;

    /// Yields the next set bit, or `None` when the word is exhausted.
    fn next(&mut self) -> Option<usize> {
        if self.remaining == 0 {
            return None;
        }
        // RUST: `trailing_zeros()` is the index of the lowest set bit, as a u32;
        // `as usize` widens it. PERF: a single `tzcnt`/`rbit` instruction.
        let bit = self.remaining.trailing_zeros() as usize;
        // PERF: `x & (x - 1)` clears the lowest set bit. `remaining` is non-zero
        // here, so the subtraction cannot underflow.
        self.remaining &= self.remaining - 1;
        Some(self.base + bit)
    }
}

impl BitAnd for &Bitset {
    // RUST: implementing `BitAnd` for `&Bitset` makes `&a & &b` legal and leaves
    // both operands usable afterwards (they are only borrowed). Implementing it
    // for `Bitset` itself would consume (move) `a` and `b`.
    // PY: `__and__`.

    /// The operator produces a new, owned set.
    type Output = Bitset;

    /// Set intersection as a new set.
    fn bitand(self, other: &Bitset) -> Bitset {
        // RUST: `self.clone()` copies the words so the result is independent of
        // the operands.
        let mut result = self.clone();
        result.intersect_with(other);
        result
    }
}

impl BitOr for &Bitset {
    /// The operator produces a new, owned set.
    type Output = Bitset;

    /// Set union as a new set.
    fn bitor(self, other: &Bitset) -> Bitset {
        let mut result = self.clone();
        result.union_with(other);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;
    // RUST: proptest's prelude brings in `proptest!`, `any`, `prop::collection`
    // and the strategy types used below.

    /// `full` must respect the tail invariant: bits past `len` stay zero, so
    /// `count` equals `len` even when `len` is not a multiple of 64.
    #[test]
    fn full_count_matches_len_for_odd_sizes() {
        for len in [0usize, 1, 63, 64, 65, 127, 128, 400] {
            assert_eq!(Bitset::full(len).count(), len, "len {len}");
        }
    }

    /// `ones()` must yield ascending order across word boundaries, because the
    /// entropy sums depend on that order for bit-identical floats.
    #[test]
    fn ones_ascend_across_words() {
        let set = Bitset::from_bits(200, &[199, 0, 64, 63, 128, 65]);
        let got: Vec<usize> = set.ones().collect();
        assert_eq!(got, vec![0, 63, 64, 65, 128, 199]);
    }

    /// `intersect_with` must report `true` exactly when something was removed.
    #[test]
    fn intersect_reports_change() {
        let mut domain = Bitset::from_bits(8, &[1, 2]);
        assert!(!domain.intersect_with(&Bitset::full(8)));
        assert!(domain.intersect_with(&Bitset::from_bits(8, &[2, 7])));
        assert_eq!(domain.single(), Some(2));
    }

    /// Operators must leave their operands untouched (they borrow, not move).
    #[test]
    fn operators_borrow_operands() {
        let a = Bitset::from_bits(8, &[1, 2]);
        let b = Bitset::from_bits(8, &[2, 3]);
        let and = &a & &b;
        let or = &a | &b;
        assert_eq!(and.ones().collect::<Vec<_>>(), vec![2]);
        assert_eq!(or.ones().collect::<Vec<_>>(), vec![1, 2, 3]);
        // `a` and `b` are still usable here; a move would not compile.
        assert_eq!(a.count() + b.count(), 4);
    }

    /// Generates a random set over `len` variants as a sorted, deduplicated list
    /// of indices, so the oracle (`Vec` set algebra) is trivial to write.
    fn bits_strategy(len: usize) -> impl Strategy<Value = Vec<usize>> {
        // RUST: `prop::collection::btree_set` produces sorted unique values, which
        // `into_iter().collect()` turns into a Vec.
        prop::collection::btree_set(0..len, 0..=len).prop_map(|set| set.into_iter().collect())
    }

    proptest! {
        /// Property: `from_bits` followed by `ones` is the identity on sorted
        /// unique inputs, for sizes that cross word boundaries.
        #[test]
        fn roundtrip(bits in bits_strategy(150)) {
            let set = Bitset::from_bits(150, &bits);
            prop_assert_eq!(set.ones().collect::<Vec<_>>(), bits.clone());
            prop_assert_eq!(set.count(), bits.len());
        }

        /// Property: set operations agree with an obvious list-based oracle.
        #[test]
        fn algebra_matches_oracle(a in bits_strategy(150), b in bits_strategy(150)) {
            let sa = Bitset::from_bits(150, &a);
            let sb = Bitset::from_bits(150, &b);
            let and_oracle: Vec<usize> = a.iter().copied().filter(|x| b.contains(x)).collect();
            let diff_oracle: Vec<usize> = a.iter().copied().filter(|x| !b.contains(x)).collect();
            let mut or_oracle: Vec<usize> = a.iter().chain(b.iter()).copied().collect();
            or_oracle.sort_unstable();
            or_oracle.dedup();
            prop_assert_eq!((&sa & &sb).ones().collect::<Vec<_>>(), and_oracle.clone());
            prop_assert_eq!((&sa | &sb).ones().collect::<Vec<_>>(), or_oracle);
            prop_assert_eq!(sa.difference(&sb).ones().collect::<Vec<_>>(), diff_oracle);
            prop_assert_eq!(sa.intersects(&sb), !and_oracle.is_empty());
        }
    }
}
