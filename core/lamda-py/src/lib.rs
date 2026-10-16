// SPDX-License-Identifier: MIT OR Apache-2.0

//! `lamda_core` for Python: PyO3 bindings over `lamda-core`.
//!
//! Where it sits: the host boundary. Blender (through the addon) and the test
//! suite call `solve_json`, `canonicalise_json` and `validate_json` with the
//! compiled-assembly text; results come back as NumPy arrays and dicts. The
//! solve runs with the GIL released, so Blender's UI thread keeps drawing, and a
//! `Progress` object lets it poll counters and cancel
//! (`.claude/rules/solver-core.md`, "Progress and cancel").
//!
//! Rust in this file:
//! - `#[pymodule]`: the function that builds the Python module when it is
//!   imported; it registers classes and functions.
//!   <https://pyo3.rs/latest/module.html>
//! - `#[pyfunction]` and `#[pyclass]` / `#[pymethods]`: expose a Rust function
//!   or struct to Python; PyO3 writes the glue (argument conversion, refcounts).
//!   <https://pyo3.rs/latest/function.html>, <https://pyo3.rs/latest/class.html>
//! - `PyResult<T>` and `PyErr`: Rust errors become Python exceptions; here every
//!   failure is a `ValueError` carrying the Rust error's `Display` text.
//! - `Python<'py>` and `Bound<'py, T>`: a token proving the GIL is held, and a
//!   Python object reference tied to that token's lifetime.
//! - `py.allow_threads(|| ..)`: release the GIL while the closure runs; the
//!   closure must be `Send` and may not touch Python objects.
//!   <https://pyo3.rs/latest/parallelism.html>
//! - `Arc<Progress>`: the `Progress` pyclass and the solve share the counters;
//!   the Arc keeps them alive while either side holds a reference.
//! - `numpy::PyArray1::from_vec`: a NumPy array from a `Vec`, one copy.
//!
//! No `unsafe` is written by hand here; the PyO3 macros expand to the unsafe
//! glue, which is why this crate cannot `forbid(unsafe_code)`.
//!
//! Read next: `../../lamda-cli/src/main.rs`.

#![deny(missing_docs)]
#![warn(
    clippy::missing_docs_in_private_items,
    clippy::missing_errors_doc,
    clippy::missing_panics_doc
)]
#![cfg_attr(not(test), deny(clippy::unwrap_used, clippy::expect_used))]

use std::sync::Arc;

use numpy::{PyArray1, PyArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use lamda_core::assembly::Assembly;
use lamda_core::solve::{SolveResult, Solver};

/// Live counters and the cancel flag of a running solve.
///
/// Create one, pass it to `solve_json`, and poll `snapshot()` or call
/// `cancel()` from the UI thread while the solve runs on a worker thread.
#[pyclass(name = "Progress", frozen)]
// RUST: `frozen` tells PyO3 the struct is never mutated through `&mut`, so
// Python may share it between threads freely; all mutation is through atomics.
struct PyProgress {
    /// The shared counters.
    inner: Arc<lamda_core::progress::Progress>,
}

#[pymethods]
impl PyProgress {
    /// `Progress()`: zeroed counters, cancel flag down.
    #[new]
    // RUST: `#[new]` marks the Python constructor (`__init__`).
    fn new() -> PyProgress {
        PyProgress {
            inner: Arc::new(lamda_core::progress::Progress::new()),
        }
    }

    /// Asks the running solve to stop; it returns with outcome `"cancelled"`.
    fn cancel(&self) {
        self.inner.cancel();
    }

    /// Lowers the cancel flag and zeroes the counters, for the next solve.
    fn reset(&self) {
        self.inner.reset();
    }

    /// The counters as a dict: observations, collapsed, backtracks, attempt, cancelled.
    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let snap = self.inner.snapshot();
        let dict = PyDict::new(py);
        dict.set_item("observations", snap.observations)?;
        dict.set_item("collapsed", snap.collapsed)?;
        dict.set_item("backtracks", snap.backtracks)?;
        dict.set_item("attempt", snap.attempt)?;
        dict.set_item("cancelled", snap.cancelled)?;
        Ok(dict)
    }
}

/// The result of a solve: one NumPy array per output column plus a report dict.
#[pyclass(name = "SolveResult", frozen)]
struct PySolveResult {
    /// The Rust result, kept whole; getters copy columns out on demand.
    inner: SolveResult,
}

#[pymethods]
impl PySolveResult {
    /// Variant id per slot, −1 if unsolved (`int32`).
    #[getter]
    // RUST: `#[getter]` makes this a read-only Python property `result.variant`.
    fn variant<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<i32>> {
        // RUST: `.clone()` copies the Vec into the new NumPy array; the Rust side
        // keeps its own so repeated reads work.
        PyArray1::from_vec(py, self.inner.variant.clone())
    }

    /// Module index per slot, −1 if unsolved (`int32`).
    #[getter]
    fn module<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<i32>> {
        PyArray1::from_vec(py, self.inner.module.clone())
    }

    /// Rotation index per slot in `0..24`, −1 if unsolved (`int32`).
    #[getter]
    fn rotation<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<i32>> {
        PyArray1::from_vec(py, self.inner.rotation.clone())
    }

    /// Part index per slot, −1 if unsolved (`int32`).
    #[getter]
    fn part<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<i32>> {
        PyArray1::from_vec(py, self.inner.part.clone())
    }

    /// Anchor flag per slot (`bool`).
    #[getter]
    fn anchor<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<bool>> {
        PyArray1::from_vec(py, self.inner.anchor.clone())
    }

    /// State per slot: 0 solved, 1 open, 2 contradiction (`uint8`).
    #[getter]
    fn state<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<u8>> {
        PyArray1::from_vec(py, self.inner.state.clone())
    }

    /// Variants still allowed per slot (`uint32`).
    #[getter]
    fn options_left<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<u32>> {
        PyArray1::from_vec(py, self.inner.options_left.clone())
    }

    /// Observation at which each slot collapsed, 0 for canonicalisation, −1 if open (`int64`).
    #[getter]
    fn collapse_step<'py>(&self, py: Python<'py>) -> Bound<'py, PyArray1<i64>> {
        PyArray1::from_vec(py, self.inner.collapse_step.clone())
    }

    /// The report as a dict: seed, attempt, observations, backtracks, elapsed, outcome.
    #[getter]
    fn report<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let r = &self.inner.report;
        let dict = PyDict::new(py);
        dict.set_item("seed", r.seed)?;
        dict.set_item("attempt", r.attempt)?;
        dict.set_item("observations", r.observations)?;
        dict.set_item("backtracks", r.backtracks)?;
        dict.set_item("elapsed", r.elapsed)?;
        dict.set_item("outcome", format!("{:?}", r.outcome).to_lowercase())?;
        Ok(dict)
    }
}

/// Turns any Rust error into a Python `ValueError` with its message.
fn value_error<E: std::fmt::Display>(error: E) -> PyErr {
    // RUST: `E: Display` accepts every error type in lamda-core.
    PyValueError::new_err(error.to_string())
}

/// `solve_json(assembly_json, progress=None) -> SolveResult`
///
/// Parses the compiled assembly, sets up the solver and runs the attempts with
/// the GIL released.
///
/// # Errors
/// `ValueError` for a malformed assembly, a canonicalisation contradiction, or
/// a solver error.
#[pyfunction]
#[pyo3(signature = (assembly_json, progress = None))]
// RUST: the `signature` attribute gives `progress` a Python default of None.
fn solve_json(
    py: Python<'_>,
    assembly_json: &str,
    progress: Option<&PyProgress>,
) -> PyResult<PySolveResult> {
    let assembly = Assembly::from_json(assembly_json).map_err(value_error)?;
    // RUST: `Arc::clone` shares the counters with the Python `Progress` object;
    // a fresh one is made when the caller passed None.
    let progress = match progress {
        Some(p) => Arc::clone(&p.inner),
        None => Arc::new(lamda_core::progress::Progress::new()),
    };
    // RUST: `allow_threads` releases the GIL for the closure. The closure moves
    // `assembly` and `progress` in and touches no Python object, which is what
    // makes it `Send`. Other Python threads (Blender's UI) run meanwhile.
    let result = py.allow_threads(
        move || -> Result<SolveResult, lamda_core::solve::SolveError> {
            let solver = Solver::new(&assembly)?;
            lamda_core::attempts::solve(&solver, &progress)
        },
    );
    Ok(PySolveResult {
        inner: result.map_err(value_error)?,
    })
}

/// `canonicalise_json(assembly_json) -> dict`
///
/// Sets up the solver and reports what one propagation pass found, for the
/// audit: `contradiction` (slot index or None), `dead_variants` (list),
/// `collapsed` (count).
///
/// # Errors
/// `ValueError` for a malformed assembly or graph.
#[pyfunction]
fn canonicalise_json<'py>(py: Python<'py>, assembly_json: &str) -> PyResult<Bound<'py, PyDict>> {
    let assembly = Assembly::from_json(assembly_json).map_err(value_error)?;
    let report =
        match py.allow_threads(|| Solver::new(&assembly).map(|s| s.canonical_report().clone())) {
            Ok(report) => report,
            Err(lamda_core::solve::SolveError::Canonical(report)) => report,
            Err(other) => return Err(value_error(other)),
        };
    let dict = PyDict::new(py);
    dict.set_item("contradiction", report.contradiction.map(|s| s.0))?;
    dict.set_item(
        "dead_variants",
        report
            .dead_variants
            .iter()
            .map(|v| v.0)
            .collect::<Vec<u32>>(),
    )?;
    dict.set_item("collapsed", report.collapsed)?;
    Ok(dict)
}

/// `validate_json(assembly_json, variant, state) -> list[str]`
///
/// Checks a result (as the `variant` and `state` arrays of a `SolveResult`)
/// against the assembly with the independent validator; returns the list of
/// violations, empty when valid.
///
/// # Errors
/// `ValueError` for a malformed assembly.
#[pyfunction]
fn validate_json(
    assembly_json: &str,
    variant: &Bound<'_, PyArray1<i32>>,
    state: &Bound<'_, PyArray1<u8>>,
) -> PyResult<Vec<String>> {
    let assembly = Assembly::from_json(assembly_json).map_err(value_error)?;
    let slots = assembly.slot_count();
    // RUST: `to_vec()` copies the NumPy data into a Vec; it fails on a
    // non-contiguous array, which `?` turns into a Python exception.
    let variant = variant.to_vec()?;
    let state = state.to_vec()?;
    let result = SolveResult {
        variant,
        module: vec![-1; slots],
        rotation: vec![-1; slots],
        part: vec![-1; slots],
        anchor: vec![false; slots],
        state,
        options_left: vec![0; slots],
        collapse_step: vec![-1; slots],
        report: lamda_core::solve::Report {
            seed: 0,
            attempt: 0,
            observations: 0,
            backtracks: 0,
            elapsed: 0.0,
            outcome: lamda_core::solve::Outcome::Solved,
        },
    };
    Ok(match lamda_core::validate::validate(&assembly, &result) {
        Ok(()) => Vec::new(),
        Err(violations) => violations.iter().map(|v| v.to_string()).collect(),
    })
}

/// `version() -> str`: the crate version.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// The module `lamda_core`: registers every class and function above.
#[pymodule(name = "lamda_core")]
// RUST: `name = ..` sets the module name Python imports. Without it the
// function's own name is used, and calling the function `lamda_core` would
// shadow the `lamda_core` crate inside this file (the compiler reported
// "`lamda_core` is ambiguous"). PyO3's expansion also uses the name `module`
// internally, so the function gets a name that clashes with nothing.
fn lamda_py_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyProgress>()?;
    m.add_class::<PySolveResult>()?;
    m.add_function(wrap_pyfunction!(solve_json, m)?)?;
    m.add_function(wrap_pyfunction!(canonicalise_json, m)?)?;
    m.add_function(wrap_pyfunction!(validate_json, m)?)?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    Ok(())
}
