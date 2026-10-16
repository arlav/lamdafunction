// SPDX-License-Identifier: MIT OR Apache-2.0

//! Synthetic benchmark kits: a box grid with a planted solution.
//!
//! Where it sits: outside the solving pipeline. Spike 4 needs assemblies of a
//! chosen size (`lamda-cli bench-gen --slots 30x30x21 --variants 400 --seed 1`)
//! whose solvability is guaranteed. The trick: first draw a random variant for
//! every slot (the planted solution), then make every planted neighbour pair
//! allowed, plus `extra` random pairs per variant and port so the solver has
//! real choices to make. The planted assignment is always a valid solution.
//!
//! Rust in this file:
//! - a builder-style options struct with `Default`, so callers set only the
//!   fields they care about: `BenchSpec { variants: 400, ..Default::default() }`.
//!   <https://doc.rust-lang.org/book/ch05-01-defining-structs.html#creating-instances-from-other-instances-with-struct-update-syntax>
//! - `BTreeSet` for deduplicated, sorted pairs: iteration order is the sort
//!   order, so the output file is deterministic.
//!   <https://doc.rust-lang.org/std/collections/struct.BTreeSet.html>
//! - nested loops over a 3D index with a flat slot number
//!   `x + width * (y + height * z)`.
//!
//! Read next: `../../lamda-cli/src/main.rs`.

use std::collections::BTreeSet;

use crate::assembly::{AllowedTable, Assembly, Edges, Mode, Module, Settings, Variant};
use crate::ids::{PortId, SlotId, Twist, VariantId};
use crate::rng::Pcg32;

/// What to generate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BenchSpec {
    /// Grid size in slots along X, Y and Z.
    pub size: [u32; 3],
    /// Number of variants.
    pub variants: u32,
    /// Seed for the generator (independent of the solve seed).
    pub seed: u64,
    /// Extra random allowed neighbours per variant and port, beyond the planted
    /// pairs. 0 makes the solve a pure propagation from any fixed slot.
    pub extra: u32,
}

impl Default for BenchSpec {
    /// The building-study size of `docs/PLAN.md`: 30 × 30 × 21 slots, 400 variants.
    fn default() -> BenchSpec {
        BenchSpec {
            size: [30, 30, 21],
            variants: 400,
            seed: 1,
            extra: 8,
        }
    }
}

/// Builds the assembly for `spec`. Every slot gets the full domain; the
/// returned assembly solves with any seed because the planted solution exists.
///
/// Cost: O(slots × 6 + variants × extra) pairs and edges.
///
/// # Examples
/// ```
/// use lamda_core::synthetic::{BenchSpec, generate};
/// let spec = BenchSpec { size: [3, 2, 1], variants: 5, seed: 7, extra: 1 };
/// let assembly = generate(&spec);
/// assert_eq!(assembly.slots, 6);
/// assert!(assembly.check().is_ok());
/// ```
pub fn generate(spec: &BenchSpec) -> Assembly {
    let [width, height, depth] = spec.size;
    let slots = (width * height * depth) as usize;
    let variants = spec.variants as usize;
    let mut rng = Pcg32::new(spec.seed, 0x4245_4E43); // "BENC"
    // WFC: the planted solution, one random variant per slot.
    let planted: Vec<u32> = (0..slots).map(|_| rng.next_u32() % spec.variants).collect();
    // RUST: a BTreeSet per (port, twist 0): inserting the same pair twice is a no-op.
    let mut pairs: Vec<BTreeSet<(u32, u32)>> = vec![BTreeSet::new(); 6];
    let edges = grid_edges(spec.size, &planted, &mut pairs);
    // WFC: extra random pairs, added symmetrically to the opposite table.
    for port in 0..6u8 {
        for v in 0..spec.variants {
            for _ in 0..spec.extra {
                let w = rng.next_u32() % spec.variants;
                insert_symmetric(&mut pairs, port, v, w);
            }
        }
    }
    let allowed = (0..6u8)
        .map(|port| AllowedTable {
            port: PortId(port),
            twist: Twist(0),
            pairs: pairs[port as usize]
                .iter()
                .map(|(v, w)| (VariantId(*v), VariantId(*w)))
                .collect(),
        })
        .collect();
    Assembly {
        schema_version: crate::assembly::SCHEMA_VERSION.to_string(),
        compile_hash: format!(
            "bench-{}x{}x{}-v{}-s{}-e{}",
            width, height, depth, spec.variants, spec.seed, spec.extra
        ),
        modules: (0..spec.variants)
            .map(|i| Module {
                name: format!("m{i}"),
                library_index: i,
                parts: 1,
            })
            .collect(),
        variants: (0..spec.variants)
            .map(|i| Variant {
                module: i,
                rotation: 0,
                part: 0,
                anchor: true,
                internal: Vec::new(),
            })
            .collect(),
        ports: 6,
        allowed,
        slots: slots as u32,
        edges,
        boundary_classes: Vec::new(),
        boundary: Vec::new(),
        domain_classes: vec![(0..spec.variants).map(VariantId).collect()],
        slot_domain: vec![0; slots],
        variant_weight: vec![1.0; variants],
        slot_module_weight: None,
        priority: None,
        fixed: None,
        settings: Settings {
            seed: 1,
            attempts: 4,
            threads: 1,
            backtrack_budget: 1000,
            max_observations: 0,
            mode: Mode::Collapse,
            supply_caps: None,
        },
    }
}

/// Builds the CSR edges of a box grid and records every planted neighbour pair.
///
/// Cost: O(slots × 6).
fn grid_edges(size: [u32; 3], planted: &[u32], pairs: &mut [BTreeSet<(u32, u32)>]) -> Edges {
    let [width, height, depth] = size;
    // RUST: a closure computing the flat index; captures the sizes by copy.
    let index = |x: u32, y: u32, z: u32| -> u32 { x + width * (y + height * z) };
    let mut edges = Edges {
        offsets: vec![0],
        port: Vec::new(),
        neighbour: Vec::new(),
        neighbour_port: Vec::new(),
        twist: Vec::new(),
    };
    for z in 0..depth {
        for y in 0..height {
            for x in 0..width {
                let slot = index(x, y, z);
                // WFC: ports in face-table order: +X, -X, +Y, -Y, +Z, -Z.
                let neighbours: [Option<u32>; 6] = [
                    (x + 1 < width).then(|| index(x + 1, y, z)),
                    (x > 0).then(|| index(x - 1, y, z)),
                    (y + 1 < height).then(|| index(x, y + 1, z)),
                    (y > 0).then(|| index(x, y - 1, z)),
                    (z + 1 < depth).then(|| index(x, y, z + 1)),
                    (z > 0).then(|| index(x, y, z - 1)),
                ];
                // RUST: `bool::then(|| v)` gives `Some(v)` when true, else `None`.
                for (port, neighbour) in neighbours.iter().enumerate() {
                    let Some(n) = neighbour else { continue };
                    edges.port.push(PortId(port as u8));
                    edges.neighbour.push(SlotId(*n));
                    edges.neighbour_port.push(PortId(port as u8).opposite());
                    edges.twist.push(Twist(0));
                    insert_symmetric(
                        pairs,
                        port as u8,
                        planted[slot as usize],
                        planted[*n as usize],
                    );
                }
                edges.offsets.push(edges.port.len() as u32);
            }
        }
    }
    edges
}

/// Records `(v, w)` on `port` and `(w, v)` on the opposite port.
///
/// Cost: O(log pairs).
fn insert_symmetric(pairs: &mut [BTreeSet<(u32, u32)>], port: u8, v: u32, w: u32) {
    pairs[port as usize].insert((v, w));
    pairs[PortId(port).opposite().index()].insert((w, v));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::Progress;
    use crate::solve::{Outcome, Solver};
    use crate::validate::validate;

    /// A small generated kit must load, be symmetric, and solve: the planted
    /// solution guarantees at least one answer exists.
    #[test]
    fn small_kit_solves() {
        let spec = BenchSpec {
            size: [4, 3, 2],
            variants: 12,
            seed: 3,
            extra: 2,
        };
        let assembly = generate(&spec);
        assembly.check().unwrap();
        let solver = Solver::new(&assembly).unwrap();
        let result = crate::attempts::solve(&solver, &Progress::new()).unwrap();
        assert_eq!(result.report.outcome, Outcome::Solved);
        assert!(validate(&assembly, &result).is_ok());
    }

    /// The same spec gives the same file: the benchmark must be reproducible.
    #[test]
    fn generation_is_deterministic() {
        let spec = BenchSpec {
            size: [3, 3, 3],
            variants: 9,
            seed: 5,
            extra: 1,
        };
        assert_eq!(generate(&spec), generate(&spec));
    }
}
