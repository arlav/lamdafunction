// SPDX-License-Identifier: MIT OR Apache-2.0

//! `lamda-cli`: headless solves, validation and benchmark generation on
//! compiled-assembly files.
//!
//! ```text
//! lamda-cli solve <assembly.json> [--seed N] [--attempts N] [--threads N] [--out result.json]
//! lamda-cli validate <assembly.json> <result.json>
//! lamda-cli bench-gen --slots WxHxD --variants N [--seed N] [--extra N] --out <assembly.json>
//! lamda-cli bench <assembly.json> [--repeat N]
//! ```
//!
//! Rust in this file:
//! - `std::env::args()`: the command line as an iterator of `String`s; the
//!   first is the program name. <https://doc.rust-lang.org/book/ch12-01-accepting-command-line-arguments.html>
//! - `std::process::exit` and returning `Result` from `main`: a non-zero exit
//!   code on error, with the error printed through `Display`.
//!   <https://doc.rust-lang.org/book/ch12-03-improving-error-handling-and-modularity.html>
//! - `Box<dyn std::error::Error>`: "any error type", so `?` works on every error
//!   in the crate without a custom enum. <https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html#a-shortcut-for-propagating-errors-the--operator>
//! - `std::fs::write` and `serde_json::to_string_pretty` for output files.
//! - `str::parse::<u32>()` and `split('x')` for parsing option values.
//!
//! Read next: `../../lamda-core/src/synthetic.rs`.

use std::error::Error;
use std::time::Instant;

use lamda_core::assembly::Assembly;
use lamda_core::attempts::solve;
use lamda_core::progress::Progress;
use lamda_core::solve::{SolveResult, Solver};
use lamda_core::synthetic::{BenchSpec, generate};
use lamda_core::validate::validate;

/// Usage text printed on bad arguments.
const USAGE: &str = "usage:
  lamda-cli solve <assembly.json> [--seed N] [--attempts N] [--threads N] [--out result.json]
  lamda-cli validate <assembly.json> <result.json>
  lamda-cli bench-gen --slots WxHxD --variants N [--seed N] [--extra N] --out <assembly.json>
  lamda-cli bench <assembly.json> [--repeat N]";

/// Entry point: dispatches on the first argument and turns errors into exit code 1.
fn main() {
    // RUST: `collect` turns the args iterator into a Vec; `skip(1)` drops the
    // program name. PY: `sys.argv[1:]`.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(error) = run(&args) {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}

/// Runs one subcommand.
///
/// # Errors
/// Any error from loading, solving, validating or writing, boxed.
fn run(args: &[String]) -> Result<(), Box<dyn Error>> {
    // RUST: `args.first().map(String::as_str)` is `Option<&str>`; matching on it
    // avoids allocating. `Some("solve")` matches a string literal pattern.
    match args.first().map(String::as_str) {
        Some("solve") => cmd_solve(&args[1..]),
        Some("validate") => cmd_validate(&args[1..]),
        Some("bench-gen") => cmd_bench_gen(&args[1..]),
        Some("bench") => cmd_bench(&args[1..]),
        _ => Err(USAGE.into()),
        // RUST: `.into()` converts `&str` into `Box<dyn Error>` through a `From` impl.
    }
}

/// Finds `--name value` in `args` and parses the value.
///
/// # Errors
/// If the option is present without a value or the value does not parse.
fn option<T: std::str::FromStr>(args: &[String], name: &str) -> Result<Option<T>, Box<dyn Error>>
where
    T::Err: Error + 'static,
{
    // RUST: `T: FromStr` lets us call `.parse::<T>()`; the `where` clause says
    // the parse error must be a printable error we can box. `'static` means the
    // error owns its data (no borrowed strings), which `Box<dyn Error>` requires.
    let Some(position) = args.iter().position(|a| a == name) else {
        return Ok(None);
    };
    let value = args
        .get(position + 1)
        .ok_or_else(|| format!("{name} needs a value"))?;
    Ok(Some(value.parse::<T>()?))
}

/// `solve`: load, override settings from options, solve, print the report,
/// optionally write the result arrays.
fn cmd_solve(args: &[String]) -> Result<(), Box<dyn Error>> {
    let path = args.first().ok_or(USAGE)?;
    let mut assembly = Assembly::from_path(path)?;
    if let Some(seed) = option::<u64>(args, "--seed")? {
        assembly.settings.seed = seed;
    }
    if let Some(attempts) = option::<u32>(args, "--attempts")? {
        assembly.settings.attempts = attempts;
    }
    if let Some(threads) = option::<u32>(args, "--threads")? {
        assembly.settings.threads = threads;
    }
    let solver = Solver::new(&assembly)?;
    let report = solver.canonical_report();
    println!(
        "canonical: {} slots collapsed, {} dead variants",
        report.collapsed,
        report.dead_variants.len()
    );
    let result = solve(&solver, &Progress::new())?;
    print_report(&result);
    if let Err(violations) = validate(&assembly, &result) {
        for v in &violations {
            eprintln!("violation: {v}");
        }
        return Err(format!("{} violations", violations.len()).into());
    }
    if let Some(out) = option::<String>(args, "--out")? {
        std::fs::write(&out, serde_json::to_string_pretty(&result)?)?;
        println!("wrote {out}");
    }
    Ok(())
}

/// `validate`: check a saved result against its assembly.
fn cmd_validate(args: &[String]) -> Result<(), Box<dyn Error>> {
    let [assembly_path, result_path] = args else {
        return Err(USAGE.into());
    };
    // RUST: a slice pattern: matches exactly two elements and names them.
    let assembly = Assembly::from_path(assembly_path)?;
    let text = std::fs::read_to_string(result_path)?;
    let result: SolveResult = serde_json::from_str(&text)?;
    match validate(&assembly, &result) {
        Ok(()) => {
            println!("valid");
            Ok(())
        }
        Err(violations) => {
            for v in &violations {
                eprintln!("violation: {v}");
            }
            Err(format!("{} violations", violations.len()).into())
        }
    }
}

/// `bench-gen`: write a synthetic kit with a planted solution.
fn cmd_bench_gen(args: &[String]) -> Result<(), Box<dyn Error>> {
    let slots: String = option(args, "--slots")?.ok_or("--slots WxHxD is required")?;
    let size = parse_size(&slots)?;
    let variants: u32 = option(args, "--variants")?.ok_or("--variants N is required")?;
    let out: String = option(args, "--out")?.ok_or("--out <file> is required")?;
    let spec = BenchSpec {
        size,
        variants,
        seed: option(args, "--seed")?.unwrap_or(1),
        extra: option(args, "--extra")?.unwrap_or(40),
    };
    let assembly = generate(&spec);
    std::fs::write(&out, serde_json::to_string(&assembly)?)?;
    println!(
        "wrote {out}: {} slots, {} variants",
        assembly.slots,
        assembly.variants.len()
    );
    Ok(())
}

/// `bench`: time setup and attempts in this process (use a release build).
fn cmd_bench(args: &[String]) -> Result<(), Box<dyn Error>> {
    let path = args.first().ok_or(USAGE)?;
    let repeat: u32 = option(args, "--repeat")?.unwrap_or(3);
    let assembly = Assembly::from_path(path)?;
    let started = Instant::now();
    let solver = Solver::new(&assembly)?;
    println!(
        "setup (load tables + canonicalise): {:.3} s",
        started.elapsed().as_secs_f64()
    );
    for attempt in 0..u64::from(repeat) {
        let result = solver.run_attempt(attempt, &Progress::new())?;
        print_report(&result);
    }
    Ok(())
}

/// Parses `WxHxD`.
fn parse_size(text: &str) -> Result<[u32; 3], Box<dyn Error>> {
    let parts: Vec<u32> = text.split('x').map(str::parse).collect::<Result<_, _>>()?;
    // RUST: collecting an iterator of `Result`s into `Result<Vec, _>` stops at
    // the first error. `str::parse` here infers `u32` from the Vec's type.
    match parts.as_slice() {
        [w, h, d] => Ok([*w, *h, *d]),
        _ => Err(format!("expected WxHxD, got {text}").into()),
    }
}

/// Prints one attempt's report on one line.
fn print_report(result: &SolveResult) {
    let r = &result.report;
    println!(
        "attempt {}: {:?}, {} observations, {} backtracks, {:.3} s",
        r.attempt, r.outcome, r.observations, r.backtracks, r.elapsed
    );
}
