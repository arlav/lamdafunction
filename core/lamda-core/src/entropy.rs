// SPDX-License-Identifier: MIT OR Apache-2.0

//! Weights, running entropy sums and the integer entropy key.
//!
//! Where it sits: between propagation and observation. Each slot keeps
//! `W = Σ w` and `S = Σ w·ln w` over its remaining variants, so its weighted
//! Shannon entropy `H = ln W − S / W` is a constant-time read after each
//! removal (`docs/PLAN.md`, "Solver, step by step", step 3). The key the heap
//! orders by is `floor(H × 1e6 + 0.5)` as an integer, so platforms whose `ln`
//! differs in the last bit still agree on the order
//! (`.claude/rules/solver-core.md`, "Entropy and observation").
//!
//! Rust in this file:
//! - `f64` arithmetic and why the order of additions matters for bit-identical
//!   results. <https://doc.rust-lang.org/book/ch03-02-data-types.html#floating-point-types>
//! - `libm::log`: a pure-Rust `ln` that gives the same bits on every platform,
//!   unlike `f64::ln`, which calls the system maths library.
//! - `#[derive(PartialOrd, Ord)]` on a struct compares fields in declaration
//!   order, which is how `SlotKey` gets its lexicographic order for free.
//!   <https://doc.rust-lang.org/std/cmp/trait.Ord.html#derivable>
//! - `as i64` from `f64`: saturating conversion; NaN becomes 0.
//!
//! Read next: `observe.rs`.

use crate::assembly::Assembly;
use crate::bitset::Bitset;
use crate::ids::{SlotId, VariantId};

/// Scale for the entropy key: six decimal places.
const KEY_SCALE: f64 = 1e6;

/// Effective weights: `variant_weight[v] × slot_module_weight[s][module(v)]`.
///
/// Invariant: lengths match the assembly's variant, slot and module counts.
#[derive(Debug, Clone)]
pub struct Weights {
    /// Base weight per variant (module weight split across its variants).
    variant: Vec<f64>,
    /// Module index per variant, to look up the per-slot multiplier.
    module_of: Vec<u32>,
    /// Optional multiplier per slot and module; `None` means 1 everywhere.
    slot_module: Option<Vec<Vec<f64>>>,
}

impl Weights {
    /// Copies the weight tables out of the assembly.
    ///
    /// Cost: O(variants + slots × modules).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::entropy::Weights;
    /// use lamda_core::ids::{SlotId, VariantId};
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// let weights = Weights::from_assembly(&assembly);
    /// assert_eq!(weights.effective(SlotId(0), VariantId(1)), 1.0);
    /// ```
    pub fn from_assembly(assembly: &Assembly) -> Weights {
        Weights {
            variant: assembly.variant_weight.clone(),
            // RUST: `.clone()` because `Weights` must own its tables; the
            // assembly may be dropped after setup. O(variants) copy, once.
            module_of: assembly.variants.iter().map(|v| v.module).collect(),
            slot_module: assembly.slot_module_weight.clone(),
        }
    }

    /// The effective weight of `variant` in `slot`: one multiplication, in `f64`,
    /// exactly as the Python reference does it.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::entropy::Weights;
    /// use lamda_core::ids::{SlotId, VariantId};
    /// let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assembly.variant_weight = vec![2.0, 3.0];
    /// assembly.slot_module_weight = Some(vec![vec![1.0, 0.5], vec![1.0, 1.0]]);
    /// let weights = Weights::from_assembly(&assembly);
    /// assert_eq!(weights.effective(SlotId(0), VariantId(1)), 1.5);
    /// assert_eq!(weights.effective(SlotId(1), VariantId(1)), 3.0);
    /// ```
    pub fn effective(&self, slot: SlotId, variant: VariantId) -> f64 {
        let base = self.variant[variant.index()];
        // RUST: `match` on an `Option<&Vec<Vec<f64>>>`; `as_ref()` borrows the
        // Vec inside the Option instead of moving it out of `self`.
        match self.slot_module.as_ref() {
            Some(rows) => base * rows[slot.index()][self.module_of[variant.index()] as usize],
            None => base,
        }
    }

    /// Number of variants.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::entropy::Weights;
    /// let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assert_eq!(Weights::from_assembly(&assembly).variant_count(), 2);
    /// ```
    pub fn variant_count(&self) -> usize {
        self.variant.len()
    }
}

/// Running sums per slot: `W = Σ w` and `S = Σ w·ln w` over the domain.
///
/// Invariant (kept by the solver): after every domain change the sums equal
/// what `recompute` would produce, up to the floating-point differences that the
/// specification allows (incremental subtraction during propagation, recompute
/// after backtracking), identically in both implementations.
#[derive(Debug, Clone, PartialEq)]
pub struct EntropySums {
    /// `W` per slot.
    weight_sum: Vec<f64>,
    /// `S` per slot.
    wlogw_sum: Vec<f64>,
}

impl EntropySums {
    /// Creates zeroed sums for `slots` slots.
    ///
    /// Cost: O(slots).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::entropy::EntropySums;
    /// use lamda_core::ids::SlotId;
    /// let sums = EntropySums::new(3);
    /// assert_eq!(sums.weight_sum(SlotId(2)), 0.0);
    /// ```
    pub fn new(slots: usize) -> EntropySums {
        EntropySums {
            weight_sum: vec![0.0; slots],
            wlogw_sum: vec![0.0; slots],
        }
    }

    /// Recomputes both sums for `slot` from scratch over `domain`, adding in
    /// ascending variant order with a plain left-to-right loop.
    ///
    /// WFC: used at setup and after backtracking restores a domain.
    ///
    /// Cost: O(count(domain)) with one `log` per variant.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::entropy::{EntropySums, Weights};
    /// use lamda_core::ids::SlotId;
    /// let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assembly.variant_weight = vec![1.0, 2.0];
    /// let weights = Weights::from_assembly(&assembly);
    /// let mut sums = EntropySums::new(2);
    /// sums.recompute(SlotId(0), &Bitset::full(2), &weights);
    /// assert_eq!(sums.weight_sum(SlotId(0)), 3.0);
    /// ```
    pub fn recompute(&mut self, slot: SlotId, domain: &Bitset, weights: &Weights) {
        let mut weight_sum = 0.0;
        let mut wlogw_sum = 0.0;
        // WFC: ascending order, plain `+=`: never a vectorised or pairwise sum,
        // so Rust and NumPy-free Python add the same numbers in the same order.
        for v in domain.ones() {
            let w = weights.effective(slot, VariantId(v as u32));
            // RUST: `as u32` cannot truncate: v < variants, which fits in u32.
            weight_sum += w;
            wlogw_sum += w * libm::log(w);
            // RUST: `libm::log` is the natural logarithm with platform-independent
            // bits; `w.ln()` would call the C library and may differ by one ulp.
        }
        self.weight_sum[slot.index()] = weight_sum;
        self.wlogw_sum[slot.index()] = wlogw_sum;
    }

    /// Subtracts the variants in `removed` from `slot`'s sums, in ascending order.
    ///
    /// WFC: called from the propagation callback for every shrink
    /// (`.claude/rules/solver-core.md`, "Entropy and observation").
    ///
    /// Cost: O(count(removed)).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::entropy::{EntropySums, Weights};
    /// use lamda_core::ids::SlotId;
    /// let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assembly.variant_weight = vec![1.0, 2.0];
    /// let weights = Weights::from_assembly(&assembly);
    /// let mut sums = EntropySums::new(2);
    /// sums.recompute(SlotId(0), &Bitset::full(2), &weights);
    /// sums.remove(SlotId(0), &Bitset::from_bits(2, &[1]), &weights);
    /// assert_eq!(sums.weight_sum(SlotId(0)), 1.0);
    /// ```
    pub fn remove(&mut self, slot: SlotId, removed: &Bitset, weights: &Weights) {
        for v in removed.ones() {
            let w = weights.effective(slot, VariantId(v as u32));
            self.weight_sum[slot.index()] -= w;
            self.wlogw_sum[slot.index()] -= w * libm::log(w);
        }
    }

    /// `W` for `slot`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::entropy::EntropySums;
    /// use lamda_core::ids::SlotId;
    /// assert_eq!(EntropySums::new(1).weight_sum(SlotId(0)), 0.0);
    /// ```
    pub fn weight_sum(&self, slot: SlotId) -> f64 {
        self.weight_sum[slot.index()]
    }

    /// The weighted Shannon entropy `H = ln W − S / W` of `slot`, given how many
    /// variants it has left. A slot with one variant has entropy exactly 0.
    ///
    /// Cost: O(1), one `log`.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::{Assembly, examples};
    /// use lamda_core::bitset::Bitset;
    /// use lamda_core::entropy::{EntropySums, Weights};
    /// use lamda_core::ids::SlotId;
    /// let mut assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
    /// assembly.variant_weight = vec![1.0, 2.0];
    /// let weights = Weights::from_assembly(&assembly);
    /// let mut sums = EntropySums::new(1);
    /// sums.recompute(SlotId(0), &Bitset::full(2), &weights);
    /// // {1, 2}: ln 3 - (2 ln 2) / 3 = 0.6365...
    /// assert!((sums.entropy(SlotId(0), 2) - 0.6365).abs() < 1e-4);
    /// assert_eq!(sums.entropy(SlotId(0), 1), 0.0);
    /// ```
    pub fn entropy(&self, slot: SlotId, count: usize) -> f64 {
        // WHY: `ln W − (W ln W) / W` is not exactly 0 in floating point, and the
        // Python reference special-cases it too.
        if count <= 1 {
            return 0.0;
        }
        let w = self.weight_sum[slot.index()];
        libm::log(w) - self.wlogw_sum[slot.index()] / w
    }

    /// The integer entropy key: `floor(H × 1e6 + 0.5)`.
    ///
    /// WHY an integer: `ln` can differ by one bit between platforms; after
    /// rounding to six decimals both implementations order slots identically.
    /// Not `f64::round`, which rounds halves away from zero, and not Python's
    /// `round`, which rounds halves to even: the same formula in both languages.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::entropy::entropy_key;
    /// assert_eq!(entropy_key(0.6365141682948128), 636514);
    /// assert_eq!(entropy_key(0.0), 0);
    /// ```
    pub fn key(&self, slot: SlotId, count: usize) -> i64 {
        entropy_key(self.entropy(slot, count))
    }
}

/// `floor(H × 1e6 + 0.5)` as an `i64`; see [`EntropySums::key`].
///
/// Cost: O(1).
///
/// # Examples
/// ```
/// use lamda_core::entropy::entropy_key;
/// assert_eq!(entropy_key(1.1204), 1120400);
/// assert_eq!(entropy_key(0.0000005), 1); // half rounds up, never to even
/// ```
pub fn entropy_key(entropy: f64) -> i64 {
    // RUST: `as i64` from f64 saturates at the i64 range and maps NaN to 0, so
    // it cannot panic; entropies are small positive numbers anyway.
    libm::floor(entropy * KEY_SCALE + 0.5) as i64
}

/// The total order the solver picks the next slot by: smallest first.
///
/// WHY field order matters: `#[derive(Ord)]` compares `neg_priority` first, then
/// `entropy_key`, then `noise`, then `slot`, exactly the tuple
/// `(−priority, entropy_key, noise, slot index)` of the specification. Slot
/// index last makes the order total: no two open slots ever tie.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct SlotKey {
    /// Minus the slot's priority, so higher priority sorts first. `i64` so that
    /// negating `i32::MIN` cannot overflow.
    pub neg_priority: i64,
    /// The rounded entropy.
    pub entropy_key: i64,
    /// Seeded tie-break noise drawn once per slot at the start of the attempt.
    pub noise: u32,
    /// The slot index, the final tie-break.
    pub slot: u32,
}

impl SlotKey {
    /// Builds the key for `slot`.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::entropy::SlotKey;
    /// use lamda_core::ids::SlotId;
    /// let urgent = SlotKey::new(SlotId(5), 2, 999, 1);
    /// let later = SlotKey::new(SlotId(0), 0, 0, 0);
    /// assert!(urgent < later); // priority 2 beats priority 0 whatever the entropy
    /// ```
    pub fn new(slot: SlotId, priority: i32, entropy_key: i64, noise: u32) -> SlotKey {
        SlotKey {
            neg_priority: -(priority as i64),
            // RUST: widen to i64 first, then negate: `-(i32::MIN)` would overflow
            // in i32, and plain negation panics on overflow in debug builds.
            entropy_key,
            noise,
            slot: slot.0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Weights for the office strip's panel slots (S 1, WL 2, WR 2) and cells
    /// (E 2, DL 8, DR 8, ST 1), as a 7-variant weight table.
    fn office_weights() -> Weights {
        // Built directly, since the two-slot example has only two variants.
        Weights {
            variant: vec![1.0, 2.0, 2.0, 2.0, 8.0, 8.0, 1.0],
            module_of: vec![0; 7],
            slot_module: None,
        }
    }

    /// The golden entropies from `.claude/rules/solver-core.md` must come out:
    /// {S, WL} and {E, ST} 0.637, {E, DL} 0.500, a full cell 1.120.
    #[test]
    fn golden_office_entropies() {
        let weights = office_weights();
        let cases: [(&[usize], f64); 4] = [
            (&[0, 1], 0.637),
            (&[3, 6], 0.637),
            (&[3, 4], 0.500),
            (&[3, 4, 5, 6], 1.120),
        ];
        for (bits, expected) in cases {
            let domain = Bitset::from_bits(7, bits);
            let mut sums = EntropySums::new(1);
            sums.recompute(SlotId(0), &domain, &weights);
            let h = sums.entropy(SlotId(0), domain.count());
            assert!((h - expected).abs() < 5e-4, "{bits:?}: {h} vs {expected}");
        }
    }

    /// Incremental removal and recomputation must agree closely (they differ at
    /// most by floating-point rounding, which the key absorbs).
    #[test]
    fn remove_matches_recompute() {
        let weights = office_weights();
        let full = Bitset::from_bits(7, &[3, 4, 5, 6]);
        let after = Bitset::from_bits(7, &[3, 6]);
        let mut incremental = EntropySums::new(1);
        incremental.recompute(SlotId(0), &full, &weights);
        incremental.remove(SlotId(0), &full.difference(&after), &weights);
        let mut fresh = EntropySums::new(1);
        fresh.recompute(SlotId(0), &after, &weights);
        assert_eq!(incremental.key(SlotId(0), 2), fresh.key(SlotId(0), 2));
        assert!((incremental.weight_sum(SlotId(0)) - fresh.weight_sum(SlotId(0))).abs() < 1e-12);
    }

    /// Keys order by priority, then entropy, then noise, then slot: the exact
    /// tuple order of the specification.
    #[test]
    fn key_order_is_lexicographic() {
        let a = SlotKey::new(SlotId(9), 0, 100, 5);
        let b = SlotKey::new(SlotId(1), 0, 200, 0);
        let c = SlotKey::new(SlotId(1), 0, 100, 7);
        let d = SlotKey::new(SlotId(0), 0, 100, 5);
        assert!(a < b, "lower entropy first");
        assert!(a < c, "lower noise first at equal entropy");
        assert!(d < a, "lower slot index first at equal everything");
        assert!(
            SlotKey::new(SlotId(0), i32::MIN, 0, 0) > a,
            "lowest priority last, no overflow"
        );
    }
}
