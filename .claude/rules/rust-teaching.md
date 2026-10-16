---
paths:
  - "**/*.rs"
  - "**/Cargo.toml"
  - "**/build.rs"
---

# Rust teaching standard

Theo is fluent in Python and computational design and is learning Rust from this codebase. Write every Rust file so that someone who knows Python but not Rust can read it top to bottom and understand both **what the Rust means** and **what the algorithm does**. The comments are part of the deliverable: a file without them is unfinished, and comments must be updated in the same change as the code they describe.

## Three layers of comments

**1. File level, `//!` at the top of every file.**
- What this module does, in plain words, and where it sits in the pipeline: compiled assembly → slot graph → adjacency tables → propagate and observe → result arrays.
- A "Rust in this file" list: every Rust concept this file uses for the first time in the codebase, each with a one-line meaning and a link to the relevant chapter of The Rust Book (https://doc.rust-lang.org/book/) or Rust by Example (https://doc.rust-lang.org/rust-by-example/).
- A "Read next" line naming the file to read after this one.

**2. Item level, `///` on every item, public or private:** structs, enums, every field, functions, methods, traits, impls, constants, type aliases, macros.
- What it is for; each parameter and the return value; invariants it relies on and keeps; `# Errors` and `# Panics` sections where they apply.
- Cost in big-O, saying what `n` is (slots, variants, edges, words per mask).
- For struct fields: why the field has its type (why `u32` and not `usize`, why `Vec<u64>` and not `Vec<bool>`, why a newtype).
- A runnable `# Examples` doc test on every public function. `cargo test` runs them, so they double as tests and as lessons.

**3. Line level, `//` inside bodies, tagged so Theo can grep for one kind:**
- `// RUST:` what the language is doing at this line.
- `// WFC:` which step of the algorithm this is and what it means, with a pointer to the `docs/PLAN.md` section.
- `// PERF:` why this is fast, or what it costs.
- `// WHY:` the design decision and the alternative that was rejected.
- `// PY:` the closest Python equivalent, when that makes the Rust click.

## Density

- In the algorithm files of `lamda-core` (`rng`, `bitset`, `graph`, `adjacency`, `propagate`, `entropy`, `observe`, `trail`, `solve`, `attempts`, `validate`), comment nearly every statement. A reader should never meet a line whose Rust meaning or algorithmic role is unexplained.
- The first time a concept appears in a file, explain it fully in 2 to 6 lines. Later in the same file, a short tag is enough: `// RUST: another shared borrow, see the note on line 42`.
- For a concept already explained in an earlier file, give a one-line reminder and point to where it was first explained, for example `// RUST: slices, explained in bitset.rs ("RUST: &[u64]")`.
- Name the types the compiler infers whenever they are not obvious: `// RUST: words is &[u64], a borrowed slice: a view into the Vec, no copy`.

These get a `// RUST:` note at every occurrence: the full explanation the first time in a file, a short tag with a pointer after that.
- ownership moves, and why a value can no longer be used after one;
- `&` versus `&mut` borrows, and what the borrow checker is preventing at that line;
- every lifetime annotation, said in words ("the returned slice lives as long as `self`");
- every `?`: which error type converts into which, and through which `From` impl;
- every `.clone()`: why it is needed and what it copies;
- every `as` cast: what can be lost (truncation, sign, precision);
- integer overflow: `wrapping_*` versus `checked_*` versus plain arithmetic, and that plain arithmetic panics on overflow in debug builds;
- `unwrap()` and `expect()`: allowed only in tests and examples, with a comment on why it cannot fail;
- each `#[derive(...)]` trait the first time it is used in a file;
- closures: what they capture and whether by reference or by move;
- `impl Trait`, generics and trait bounds: what the caller may pass and what the function may do with it;
- `Send`, `Sync`, `Arc` and atomics in parallel code: what would go wrong without them;
- every PyO3 macro and attribute in `lamda-py`, and what Python sees as a result.

In `Cargo.toml`, comment every dependency (what it does, why this project needs it, its licence), every feature flag and every profile setting.

## Code style that helps learning

- Explicit over clever. Use named intermediate variables. Use a plain `for` loop when an iterator chain would hide the logic; when a chain is the right tool, put each adaptor on its own line with its own comment.
- Functions stay under about 40 lines of code, not counting comments.
- Newtype indices (`SlotId(u32)`, `VariantId(u32)`, `PortId(u8)`), with a `WHY:` comment the first time explaining how newtypes stop a slot index being passed where a variant index belongs.
- Errors are `thiserror` enums. Library code returns `Result`; it does not panic on bad input.
- Algorithm modules are declared `pub mod` in `lib.rs`, so their doc tests run (doc tests only reach public items).
- Tests sit in a `#[cfg(test)] mod tests` block at the bottom of each file. Every test begins with a comment saying which property it checks and why that property matters. Property tests (`proptest`) explain what their generators produce.
- Never water code down to avoid a concept the design needs; explain the concept instead. Never add a feature just to have something to teach.

## Enforcement

`lib.rs` of every crate starts with:

```rust
#![forbid(unsafe_code)]
#![deny(missing_docs)]
#![warn(
    clippy::missing_docs_in_private_items,
    clippy::missing_errors_doc,
    clippy::missing_panics_doc
)]
#![cfg_attr(not(test), deny(clippy::unwrap_used, clippy::expect_used))]
```

(`lamda-py` leaves out the `unsafe_code` line, because PyO3's macros expand to unsafe code; never write `unsafe` by hand there.) Clippy runs with `-D warnings`, so these warnings fail the build.

`scripts/check_teaching.py` fails if any `.rs` file under `core/` lacks a `//!` header, a "Rust in this file" list, a "Read next" line, or if a public function in `lamda-core` has no `# Examples` doc test. Keep the script in step with this standard.

## The learning loop

- Before writing a new file, tell Theo in chat which Rust concepts it will introduce.
- After each task, append to `docs/rust-journal.md`: date, files changed, the concepts introduced with `file:line` pointers, every compiler error met along the way (the message, what it meant, the fix), and one small exercise that the compiler or a test can check.
- Keep `docs/rust-concepts.md` up to date: one row per concept, with the first `file:line` where it is explained and the Rust Book chapter.
- When Theo asks "why", answer in chat and then improve the comment at that line so the next reader does not need to ask.

## Example of the expected density

This is the level of commenting expected in `lamda-core` algorithm files. The code compiles and its doc test passes with Rust 1.97, edition 2024.

```rust
/// Intersects this domain with `support`, in place, and reports whether anything was removed.
///
/// WFC: this is the heart of propagation. A neighbour keeps only the variants that
/// some variant in this slot allows across the shared port (`docs/PLAN.md`, "Propagation").
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
```
