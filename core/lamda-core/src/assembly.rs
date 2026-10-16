// SPDX-License-Identifier: MIT OR Apache-2.0

//! The compiled assembly: the contract between the Python compiler and the solver.
//!
//! Where it sits: first stage of the pipeline. `addon/compile/` writes a JSON
//! file (`schema/compiled-assembly-v1.json`); this module reads it into plain
//! structs and checks that every index is in range. `graph.rs` and
//! `adjacency.rs` then build the solver's data structures from it. The Python
//! reference solver reads the same file, so both solvers start from identical
//! inputs (`.claude/rules/solver-core.md`, "Compiled assembly, schema v1").
//!
//! Rust in this file:
//! - `serde` derive: `#[derive(Deserialize)]` writes a JSON parser for a struct
//!   from its field names and types. <https://serde.rs/derive.html>
//! - `Result<T, E>` and `?`: a function that can fail returns `Ok(value)` or
//!   `Err(error)`; `?` unwraps an `Ok` or returns the `Err` to the caller at once.
//!   <https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html>
//! - `thiserror`: derives `std::error::Error` and `Display` for an error enum, and
//!   `#[from]` writes the `From` impl that `?` uses to convert one error type
//!   into another. <https://docs.rs/thiserror>
//! - `enum` with data and `match`: each variant can carry its own fields, and
//!   `match` must handle every variant.
//!   <https://doc.rust-lang.org/book/ch06-01-defining-an-enum.html>
//! - `Option<T>` fields: JSON `null` or a missing key becomes `None`.
//!   <https://doc.rust-lang.org/book/ch06-01-defining-an-enum.html#the-option-enum-and-its-advantages-over-null-values>
//! - `String` versus `&str`: owned text versus borrowed text.
//!   <https://doc.rust-lang.org/book/ch08-02-strings.html>
//! - `std::fs::read_to_string` and `std::path::Path` for file input.
//!
//! Read next: `graph.rs`.

use serde::{Deserialize, Serialize};
use std::path::Path;
use thiserror::Error;

use crate::ids::{PortId, SlotId, Twist, VariantId};
// RUST: `crate::` is the root of this library, so this imports from `ids.rs`.

/// The only schema version this crate reads.
pub const SCHEMA_VERSION: &str = "1.0.0";
// RUST: `&str` is a borrowed string slice; a `&'static str` literal lives in
// the binary for the whole program, so it can be a constant.

/// Everything that can go wrong while reading a compiled assembly.
///
/// Each variant names the field and the offending value, so the Python audit can
/// show the user exactly where the compiler went wrong.
#[derive(Debug, Error)]
// RUST: `Error` here is thiserror's derive macro (imported above). The
// `#[error("...")]` line on each variant becomes its `Display` text; `{field}`
// interpolates the variant's fields.
pub enum LoadError {
    /// The file could not be read from disk.
    #[error("cannot read compiled assembly: {0}")]
    Io(#[from] std::io::Error),
    // RUST: `#[from]` generates `impl From<std::io::Error> for LoadError`, which
    // is what lets `?` turn a failed `read_to_string` into `LoadError::Io`.
    // `{0}` refers to the first (unnamed) field of this tuple variant.
    /// The text is not valid JSON, or does not match the schema's shape.
    #[error("compiled assembly is not valid JSON for schema {SCHEMA_VERSION}: {0}")]
    Json(#[from] serde_json::Error),

    /// The file declares a schema version this crate does not read.
    #[error("unsupported schema version {found}, expected {SCHEMA_VERSION}")]
    SchemaVersion {
        /// The version string found in the file.
        found: String,
    },

    /// An array whose length must equal another count has the wrong length.
    #[error("{field} has {found} entries, expected {expected}")]
    Length {
        /// Name of the JSON field.
        field: &'static str,
        /// Entries found.
        found: usize,
        /// Entries required.
        expected: usize,
    },

    /// An index points past the end of the array it indexes.
    #[error("{field}: index {index} is out of range (limit {limit})")]
    Index {
        /// Name of the JSON field holding the index.
        field: &'static str,
        /// The offending index.
        index: usize,
        /// One past the largest legal index.
        limit: usize,
    },

    /// A twist outside `0..=3`.
    #[error("{field}: twist {twist} is not in 0..=3")]
    Twist {
        /// Name of the JSON field holding the twist.
        field: &'static str,
        /// The offending twist.
        twist: u8,
    },

    /// A domain class is not strictly ascending, so it is not a set.
    #[error("domain_classes[{class}] is not sorted ascending without duplicates")]
    UnsortedDomain {
        /// Index of the class.
        class: usize,
    },
}

/// One authored module, as the solver needs to know it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
// RUST: `Serialize` too, so the CLI benchmark generator can write assemblies
// with the same structs that read them: one definition, no drift.
pub struct Module {
    /// Display name, for reports only.
    pub name: String,
    /// Index of the module in the Blender library collection, which the addon
    /// uses to instance the right object.
    pub library_index: u32,
    /// How many cells (parts) the module covers; 1 for single-cell modules.
    #[serde(default = "one")]
    // RUST: when the key is missing, serde calls the function `one` for a value.
    pub parts: u32,
}

/// Default for `Module::parts`.
fn one() -> u32 {
    1
}

/// One solver variant: a module in one rotation, one part of it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Variant {
    /// Index into `Assembly::modules`.
    pub module: u32,
    /// Rotation index in `0..24` (the 24 proper rotations of the cube); the
    /// addon turns it into a quaternion.
    pub rotation: u8,
    /// Which cell of a multi-cell module this is; 0 for single-cell modules.
    pub part: u32,
    /// Whether this part is the one that gets materialised.
    pub anchor: bool,
    /// For multi-cell modules: `(port, part)` pairs saying which sibling part of
    /// the same module and rotation must sit across `port`. The validator checks
    /// these; the solver relies on the compiler's exclusive internal rules.
    #[serde(default)]
    // RUST: `default` with no argument uses `Default::default()`, an empty Vec.
    pub internal: Vec<(PortId, u32)>,
}

/// The allowed pairs for one (port, twist): `w` may sit across `port` from `v`
/// when the edge's twist is `twist`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AllowedTable {
    /// Port on the slot being looked at.
    pub port: PortId,
    /// Twist of the edge.
    pub twist: Twist,
    /// Allowed `[v, w]` pairs, any order, duplicates tolerated.
    pub pairs: Vec<(VariantId, VariantId)>,
    // RUST: serde reads a JSON two-element array as a tuple `(a, b)`.
}

/// The slot graph in compressed sparse row (CSR) form: the edges of slot `s` are
/// entries `offsets[s]..offsets[s + 1]` of the four parallel arrays.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Edges {
    /// `slots + 1` entries, ascending; `offsets[slots]` is the edge count.
    pub offsets: Vec<u32>,
    /// Port on the slot, per edge.
    pub port: Vec<PortId>,
    /// The slot across the edge, per edge.
    pub neighbour: Vec<SlotId>,
    /// Port on the neighbour that touches this one, per edge.
    pub neighbour_port: Vec<PortId>,
    /// Twist of the edge, per edge.
    pub twist: Vec<Twist>,
}

/// A boundary requirement: the variants allowed to show a given connector on an
/// exposed face. "Free" boundary ports are simply not listed in `boundary`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BoundaryClass {
    /// The connector name the face must carry, for reports.
    pub connector: String,
    /// Variants whose face at that port carries the connector, sorted ascending.
    pub variants: Vec<VariantId>,
}

/// One exposed port that must satisfy a boundary class.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BoundaryPort {
    /// The slot with the exposed face.
    pub slot: SlotId,
    /// The exposed port.
    pub port: PortId,
    /// Index into `Assembly::boundary_classes`.
    pub class: u32,
}

/// How the solver should run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Settings {
    /// Seed of attempt 0; attempt `k` uses `seed + k`.
    pub seed: u64,
    /// Number of attempts before giving up; at least 1.
    pub attempts: u32,
    /// Worker threads for attempts 1 and up; 0 means "all cores".
    pub threads: u32,
    /// Backtracks allowed per attempt before it fails.
    pub backtrack_budget: u32,
    /// Stop after this many observations and return a partial result; 0 = no limit.
    pub max_observations: u64,
    /// `collapse` fills every slot; `grow` is phase 2 (growth mode).
    pub mode: Mode,
    /// Per module, how many instances may be placed; `None` means unlimited.
    #[serde(default)]
    pub supply_caps: Option<Vec<i64>>,
}

/// Solve mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
// RUST: JSON `"collapse"` maps to `Mode::Collapse`.
pub enum Mode {
    /// Fill every slot with one variant.
    Collapse,
    /// Grow from placed modules, allowing "empty" (phase 2).
    Grow,
}

/// A compiled assembly, exactly as the JSON file describes it, after range checks.
///
/// Invariant after `check()`: every index in every field is in range, lengths
/// agree with `slots`, `variants.len()` and `modules.len()`, and every twist is
/// in `0..=3`. Later stages rely on this and index without further checks.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Assembly {
    /// Must equal [`SCHEMA_VERSION`].
    pub schema_version: String,
    /// Hash of the compiler's inputs, for caching and reports; not interpreted.
    pub compile_hash: String,
    /// Authored modules.
    pub modules: Vec<Module>,
    /// Solver variants.
    pub variants: Vec<Variant>,
    /// Ports per slot; 6 on a box grid.
    pub ports: u8,
    /// Allowed pairs per (port, twist) that the graph uses.
    pub allowed: Vec<AllowedTable>,
    /// Number of slots.
    pub slots: u32,
    /// The slot graph.
    pub edges: Edges,
    /// Boundary requirement classes.
    #[serde(default)]
    pub boundary_classes: Vec<BoundaryClass>,
    /// Exposed ports with a requirement.
    #[serde(default)]
    pub boundary: Vec<BoundaryPort>,
    /// Distinct initial domains, each a sorted list of variants.
    pub domain_classes: Vec<Vec<VariantId>>,
    /// One domain class index per slot.
    pub slot_domain: Vec<u32>,
    /// Base weight per variant (the module weight split across its variants).
    pub variant_weight: Vec<f64>,
    /// Optional per-slot, per-module multiplier, already combined by the compiler.
    #[serde(default)]
    pub slot_module_weight: Option<Vec<Vec<f64>>>,
    /// Optional per-slot priority; higher is observed first.
    #[serde(default)]
    pub priority: Option<Vec<i32>>,
    /// Optional fixed variant per slot, `-1` for free.
    #[serde(default)]
    pub fixed: Option<Vec<i32>>,
    /// Run settings.
    pub settings: Settings,
}

impl Assembly {
    /// Parses an assembly from JSON text and checks it.
    ///
    /// Cost: O(size of the file) to parse, then the cost of [`Assembly::check`].
    ///
    /// # Errors
    /// [`LoadError::Json`] for malformed JSON, or any error from `check`.
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::Assembly;
    /// let text = lamda_core::assembly::examples::two_slots_json();
    /// let assembly = Assembly::from_json(&text).unwrap();
    /// assert_eq!(assembly.slots, 2);
    /// ```
    pub fn from_json(text: &str) -> Result<Assembly, LoadError> {
        // RUST: `serde_json::from_str` returns `Result<Assembly, serde_json::Error>`.
        // The `?` converts the error through `From<serde_json::Error> for LoadError`
        // (written by `#[from]` above) and returns early; on success `assembly`
        // is the parsed value.
        let assembly: Assembly = serde_json::from_str(text)?;
        assembly.check()?;
        Ok(assembly)
        // RUST: ownership of `assembly` moves into the `Ok` and on to the caller.
    }

    /// Reads and parses an assembly file.
    ///
    /// Cost: as `from_json` plus the file read.
    ///
    /// # Errors
    /// [`LoadError::Io`] if the file cannot be read, then as `from_json`.
    ///
    /// # Examples
    /// ```no_run
    /// use lamda_core::assembly::Assembly;
    /// let assembly = Assembly::from_path("tests/fixtures/office_south_strip.json").unwrap();
    /// assert!(assembly.slots > 0);
    /// ```
    pub fn from_path(path: impl AsRef<Path>) -> Result<Assembly, LoadError> {
        // RUST: `impl AsRef<Path>` accepts anything that can be viewed as a Path:
        // `&str`, `String`, `PathBuf`, `&Path`. The function may only call
        // `.as_ref()` on it.
        let text = std::fs::read_to_string(path)?;
        // RUST: this `?` converts `std::io::Error` into `LoadError::Io`.
        Assembly::from_json(&text)
        // RUST: `&text` borrows the String as a `&str` (deref coercion).
    }

    /// Checks every length and index; see the invariant on [`Assembly`].
    ///
    /// Cost: O(variants + edges + allowed pairs + domain entries).
    ///
    /// # Errors
    /// The first problem found, as a [`LoadError`].
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::Assembly;
    /// let text = lamda_core::assembly::examples::two_slots_json();
    /// let mut assembly = Assembly::from_json(&text).unwrap();
    /// assembly.slot_domain[0] = 99; // no such class
    /// assert!(assembly.check().is_err());
    /// ```
    pub fn check(&self) -> Result<(), LoadError> {
        // RUST: `Result<(), E>`: success carries no value, only "it was fine".
        if self.schema_version != SCHEMA_VERSION {
            return Err(LoadError::SchemaVersion {
                found: self.schema_version.clone(),
                // RUST: `.clone()` copies the String because the error must own
                // its text; `self` is only borrowed here.
            });
        }
        self.check_variants()?;
        self.check_allowed()?;
        self.check_edges()?;
        self.check_domains()?;
        self.check_weights_and_slots()?;
        self.check_boundary()
        // WHY: one small function per field group keeps each under 40 lines and
        // makes the error messages easy to find.
    }

    /// Variants point at real modules; rotations are in `0..24`.
    fn check_variants(&self) -> Result<(), LoadError> {
        let modules = self.modules.len();
        for variant in &self.variants {
            // RUST: `&self.variants` iterates by shared reference: `variant: &Variant`.
            check_index("variants.module", variant.module as usize, modules)?;
            check_index("variants.rotation", variant.rotation as usize, 24)?;
            for (port, part) in &variant.internal {
                check_index("variants.internal.port", port.index(), self.ports as usize)?;
                check_index("variants.internal.part", *part as usize, u32::MAX as usize)?;
            }
        }
        if let Some(caps) = &self.settings.supply_caps {
            check_len("settings.supply_caps", caps.len(), modules)?;
        }
        Ok(())
    }

    /// Allowed tables name real ports, legal twists and real variants.
    fn check_allowed(&self) -> Result<(), LoadError> {
        let variants = self.variants.len();
        for table in &self.allowed {
            check_index("allowed.port", table.port.index(), self.ports as usize)?;
            check_twist("allowed.twist", table.twist)?;
            for (v, w) in &table.pairs {
                check_index("allowed.pairs", v.index(), variants)?;
                check_index("allowed.pairs", w.index(), variants)?;
            }
        }
        Ok(())
    }

    /// CSR arrays agree in length, offsets ascend, and every entry is in range.
    fn check_edges(&self) -> Result<(), LoadError> {
        let slots = self.slots as usize;
        let edges = &self.edges;
        check_len("edges.offsets", edges.offsets.len(), slots + 1)?;
        // RUST: `windows(2)` yields overlapping pairs `[a, b]` of consecutive
        // elements, the natural way to check "ascending".
        for pair in edges.offsets.windows(2) {
            if pair[0] > pair[1] {
                return Err(LoadError::Index {
                    field: "edges.offsets",
                    index: pair[1] as usize,
                    limit: pair[0] as usize,
                });
            }
        }
        // RUST: `last()` is `Option<&u32>`; `copied()` turns `&u32` into `u32`;
        // `unwrap_or(0)` handles the (already rejected) empty case without panicking.
        let count = edges.offsets.last().copied().unwrap_or(0) as usize;
        check_len("edges.port", edges.port.len(), count)?;
        check_len("edges.neighbour", edges.neighbour.len(), count)?;
        check_len("edges.neighbour_port", edges.neighbour_port.len(), count)?;
        check_len("edges.twist", edges.twist.len(), count)?;
        for i in 0..count {
            check_index("edges.port", edges.port[i].index(), self.ports as usize)?;
            check_index("edges.neighbour", edges.neighbour[i].index(), slots)?;
            check_index(
                "edges.neighbour_port",
                edges.neighbour_port[i].index(),
                self.ports as usize,
            )?;
            check_twist("edges.twist", edges.twist[i])?;
        }
        Ok(())
    }

    /// Domain classes are sorted sets of real variants; every slot has a class.
    fn check_domains(&self) -> Result<(), LoadError> {
        let variants = self.variants.len();
        for (class, domain) in self.domain_classes.iter().enumerate() {
            for v in domain {
                check_index("domain_classes", v.index(), variants)?;
            }
            // RUST: `is_sorted_by` takes a closure returning bool; `<` makes it
            // "strictly ascending", which rejects duplicates too.
            if !domain.is_sorted_by(|a, b| a < b) {
                return Err(LoadError::UnsortedDomain { class });
            }
        }
        check_len("slot_domain", self.slot_domain.len(), self.slots as usize)?;
        for class in &self.slot_domain {
            check_index("slot_domain", *class as usize, self.domain_classes.len())?;
        }
        Ok(())
    }

    /// Per-variant and per-slot arrays have the right lengths and contents.
    fn check_weights_and_slots(&self) -> Result<(), LoadError> {
        let slots = self.slots as usize;
        let variants = self.variants.len();
        check_len("variant_weight", self.variant_weight.len(), variants)?;
        // RUST: `if let Some(x) = &option` borrows the contents when present.
        if let Some(rows) = &self.slot_module_weight {
            check_len("slot_module_weight", rows.len(), slots)?;
            for row in rows {
                check_len("slot_module_weight[s]", row.len(), self.modules.len())?;
            }
        }
        if let Some(priority) = &self.priority {
            check_len("priority", priority.len(), slots)?;
        }
        if let Some(fixed) = &self.fixed {
            check_len("fixed", fixed.len(), slots)?;
            for value in fixed {
                // WHY: -1 means free; any other negative value is a compiler bug.
                if *value < -1 {
                    return Err(LoadError::Index {
                        field: "fixed",
                        index: usize::MAX,
                        limit: variants,
                    });
                }
                if *value >= 0 {
                    check_index("fixed", *value as usize, variants)?;
                }
            }
        }
        Ok(())
    }

    /// Boundary ports name real slots, ports and classes; classes are sorted.
    fn check_boundary(&self) -> Result<(), LoadError> {
        let variants = self.variants.len();
        for (class, entry) in self.boundary_classes.iter().enumerate() {
            for v in &entry.variants {
                check_index("boundary_classes.variants", v.index(), variants)?;
            }
            if !entry.variants.is_sorted_by(|a, b| a < b) {
                return Err(LoadError::UnsortedDomain { class });
            }
        }
        for port in &self.boundary {
            check_index("boundary.slot", port.slot.index(), self.slots as usize)?;
            check_index("boundary.port", port.port.index(), self.ports as usize)?;
            check_index(
                "boundary.class",
                port.class as usize,
                self.boundary_classes.len(),
            )?;
        }
        Ok(())
    }

    /// Number of variants, as a `usize` for sizing bitsets.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::Assembly;
    /// let assembly = Assembly::from_json(&lamda_core::assembly::examples::two_slots_json()).unwrap();
    /// assert_eq!(assembly.variant_count(), 2);
    /// ```
    pub fn variant_count(&self) -> usize {
        self.variants.len()
    }

    /// Number of slots, as a `usize` for sizing per-slot arrays.
    ///
    /// Cost: O(1).
    ///
    /// # Examples
    /// ```
    /// use lamda_core::assembly::Assembly;
    /// let assembly = Assembly::from_json(&lamda_core::assembly::examples::two_slots_json()).unwrap();
    /// assert_eq!(assembly.slot_count(), 2);
    /// ```
    pub fn slot_count(&self) -> usize {
        self.slots as usize
    }
}

/// Returns an error unless `index < limit`.
///
/// Cost: O(1).
fn check_index(field: &'static str, index: usize, limit: usize) -> Result<(), LoadError> {
    // RUST: `&'static str`: the field name is a literal that lives forever, so
    // the error can hold it without owning a String.
    if index < limit {
        Ok(())
    } else {
        Err(LoadError::Index {
            field,
            index,
            limit,
        })
    }
}

/// Returns an error unless `found == expected`.
///
/// Cost: O(1).
fn check_len(field: &'static str, found: usize, expected: usize) -> Result<(), LoadError> {
    if found == expected {
        Ok(())
    } else {
        Err(LoadError::Length {
            field,
            found,
            expected,
        })
    }
}

/// Returns an error unless the twist is in `0..=3`.
///
/// Cost: O(1).
fn check_twist(field: &'static str, twist: Twist) -> Result<(), LoadError> {
    if twist.is_valid() {
        Ok(())
    } else {
        Err(LoadError::Twist {
            field,
            twist: twist.0,
        })
    }
}

/// Small assemblies used by doc tests and unit tests across the crate.
pub mod examples {
    /// Two slots side by side on the X axis, two variants `a` and `b`, with the
    /// single rule "a may sit west of b" (and nothing else). Slot 0 is west.
    ///
    /// Cost: builds a short string.
    ///
    /// # Examples
    /// ```
    /// let text = lamda_core::assembly::examples::two_slots_json();
    /// assert!(text.contains("\"slots\": 2"));
    /// ```
    pub fn two_slots_json() -> String {
        // RUST: a raw string `r#"..."#` needs no escaping of quotes inside.
        // `.to_string()` turns the `&'static str` literal into an owned String.
        r#"{
          "schema_version": "1.0.0",
          "compile_hash": "example",
          "modules": [{"name": "a", "library_index": 0}, {"name": "b", "library_index": 1}],
          "variants": [
            {"module": 0, "rotation": 0, "part": 0, "anchor": true},
            {"module": 1, "rotation": 0, "part": 0, "anchor": true}
          ],
          "ports": 6,
          "allowed": [
            {"port": 0, "twist": 0, "pairs": [[0, 1]]},
            {"port": 1, "twist": 0, "pairs": [[1, 0]]}
          ],
          "slots": 2,
          "edges": {
            "offsets": [0, 1, 2],
            "port": [0, 1],
            "neighbour": [1, 0],
            "neighbour_port": [1, 0],
            "twist": [0, 0]
          },
          "domain_classes": [[0, 1]],
          "slot_domain": [0, 0],
          "variant_weight": [1.0, 1.0],
          "settings": {
            "seed": 1, "attempts": 1, "threads": 1, "backtrack_budget": 0,
            "max_observations": 0, "mode": "collapse"
          }
        }"#
        .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The example must load, and its optional fields must default sensibly, so
    /// every other test in the crate can rely on it.
    #[test]
    fn example_loads_with_defaults() {
        let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        assert_eq!(assembly.modules[0].parts, 1);
        assert!(assembly.variants[0].internal.is_empty());
        assert!(assembly.boundary.is_empty());
        assert!(assembly.fixed.is_none());
        assert_eq!(assembly.settings.mode, Mode::Collapse);
    }

    /// Each kind of range error must be caught, because later stages index
    /// without checks and would panic on bad input otherwise.
    #[test]
    fn range_errors_are_reported() {
        let good = Assembly::from_json(&examples::two_slots_json()).unwrap();

        let mut bad = good.clone();
        bad.edges.neighbour[0] = SlotId(7);
        assert!(matches!(
            bad.check(),
            Err(LoadError::Index {
                field: "edges.neighbour",
                ..
            })
        ));
        // RUST: `matches!` tests a value against a pattern; `..` ignores the rest.

        let mut bad = good.clone();
        bad.edges.twist[0] = Twist(4);
        assert!(matches!(bad.check(), Err(LoadError::Twist { .. })));

        let mut bad = good.clone();
        bad.domain_classes[0] = vec![VariantId(1), VariantId(0)];
        assert!(matches!(
            bad.check(),
            Err(LoadError::UnsortedDomain { class: 0 })
        ));

        let mut bad = good.clone();
        bad.variant_weight.pop();
        assert!(matches!(
            bad.check(),
            Err(LoadError::Length {
                field: "variant_weight",
                ..
            })
        ));

        let mut bad = good.clone();
        bad.schema_version = "2.0.0".to_string();
        assert!(matches!(bad.check(), Err(LoadError::SchemaVersion { .. })));

        let mut bad = good;
        bad.fixed = Some(vec![-1, 5]);
        assert!(matches!(
            bad.check(),
            Err(LoadError::Index { field: "fixed", .. })
        ));
    }

    /// Malformed JSON surfaces as `LoadError::Json` through the `From` impl.
    #[test]
    fn malformed_json_is_an_error() {
        assert!(matches!(
            Assembly::from_json("{not json"),
            Err(LoadError::Json(_))
        ));
    }

    /// Writing and reading back must round-trip, since the CLI generator uses
    /// `Serialize` to produce files this crate then reads.
    #[test]
    fn serialize_roundtrip() {
        let assembly = Assembly::from_json(&examples::two_slots_json()).unwrap();
        let text = serde_json::to_string(&assembly).unwrap();
        let back = Assembly::from_json(&text).unwrap();
        assert_eq!(back, assembly);
    }

    /// Error messages must mention the field, which the audit shows to users.
    #[test]
    fn errors_display_the_field() {
        let err = check_index("slot_domain", 9, 3).unwrap_err();
        assert_eq!(
            err.to_string(),
            "slot_domain: index 9 is out of range (limit 3)"
        );
    }
}
