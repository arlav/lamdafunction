// SPDX-License-Identifier: MIT OR Apache-2.0

//! Criterion benchmarks for spike 4: setup (which includes the canonicalising
//! propagation pass) and one full attempt on synthetic kits.
//!
//! Run with `cargo bench -p lamda-core`. The spike-4 target: 19,000 slots ×
//! 400 variants fully propagates and solves in under 1 s single-threaded.
//!
//! Rust in this file:
//! - `criterion_group!` / `criterion_main!`: macros that build the benchmark
//!   binary's `main`. <https://bheisler.github.io/criterion.rs/book/>
//! - `b.iter(|| ..)`: criterion runs the closure many times and reports
//!   statistics; `black_box` stops the optimiser from deleting unused results.
//!
//! Read next: `../src/synthetic.rs`.

use criterion::{BenchmarkId, Criterion, criterion_group, criterion_main};
use std::hint::black_box;

use lamda_core::progress::Progress;
use lamda_core::solve::Solver;
use lamda_core::synthetic::{BenchSpec, generate};

/// Setup plus one attempt, for a few sizes.
fn attempts(c: &mut Criterion) {
    let mut group = c.benchmark_group("attempt");
    group.sample_size(10);
    let specs = [
        (
            "small-500x50",
            BenchSpec {
                size: [10, 10, 5],
                variants: 50,
                seed: 1,
                extra: 4,
            },
        ),
        (
            "study-19k-400",
            BenchSpec {
                size: [30, 30, 21],
                variants: 400,
                seed: 1,
                extra: 8,
            },
        ),
    ];
    for (name, spec) in specs {
        let assembly = generate(&spec);
        group.bench_with_input(BenchmarkId::new("setup", name), &assembly, |b, assembly| {
            b.iter(|| black_box(Solver::new(assembly).unwrap()));
        });
        let solver = Solver::new(&assembly).unwrap();
        group.bench_with_input(BenchmarkId::new("solve", name), &solver, |b, solver| {
            b.iter(|| black_box(solver.run_attempt(0, &Progress::new()).unwrap()));
        });
    }
    group.finish();
}

criterion_group!(benches, attempts);
criterion_main!(benches);
