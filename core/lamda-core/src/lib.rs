// SPDX-License-Identifier: MIT OR Apache-2.0

//! `lamda-core`: wave function collapse over a slot graph.
//!
//! Pipeline, and the file that owns each stage:
//! compiled assembly (`assembly.rs`) → slot graph (`graph.rs`) → adjacency
//! tables (`adjacency.rs`) → propagate (`propagate.rs`) and observe
//! (`entropy.rs`, `observe.rs`, `trail.rs`, `solve.rs`) → attempts in parallel
//! (`attempts.rs`) → result arrays, checked by `validate.rs`.
//! Supporting files: `rng.rs` (PCG32), `bitset.rs` (domains), `ids.rs`
//! (index newtypes), `progress.rs` (counters and cancel flag), `synthetic.rs`
//! (benchmark kits with a planted solution, for spike 4).
//!
//! Read the files in the order of the Rust learning path in `LEARNING.md` at the repository root:
//! `rng`, `bitset`, `ids`, `assembly`, `graph`, `adjacency`, `progress`,
//! `propagate`, `entropy`, `observe`, `trail`, `solve`, `attempts`, `validate`.
//!
//! Rust in this file:
//! - crate attributes `#![...]`: settings for the whole crate, placed before any
//!   item. `forbid` cannot be overridden anywhere; `deny` turns a lint into an
//!   error; `warn` plus clippy's `-D warnings` on the command line does the same.
//!   <https://doc.rust-lang.org/reference/attributes.html>
//! - `pub mod name;`: declares a module whose body lives in `src/name.rs` and
//!   makes it part of the public API, so its doc tests run.
//!   <https://doc.rust-lang.org/book/ch07-02-defining-modules-to-control-scope-and-privacy.html>
//! - `cfg_attr(not(test), ...)`: apply an attribute only outside test builds.
//!
//! Read next: `rng.rs`.

// Never any `unsafe` in this crate; the compiler rejects it even in dependencies'
// macro expansions inside our code.
#![forbid(unsafe_code)]
// Every public item needs a `///` doc comment, or the build fails.
#![deny(missing_docs)]
// Clippy extends that to private items and to `# Errors` / `# Panics` sections.
#![warn(
    clippy::missing_docs_in_private_items,
    clippy::missing_errors_doc,
    clippy::missing_panics_doc
)]
// Outside tests, `unwrap()` and `expect()` are build errors: library code returns
// errors instead of panicking on bad input.
#![cfg_attr(not(test), deny(clippy::unwrap_used, clippy::expect_used))]

pub mod adjacency;
pub mod assembly;
pub mod attempts;
pub mod bitset;
pub mod entropy;
pub mod graph;
pub mod ids;
pub mod observe;
pub mod progress;
pub mod propagate;
pub mod rng;
pub mod solve;
pub mod synthetic;
pub mod trail;
pub mod validate;
