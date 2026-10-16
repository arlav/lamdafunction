# Rust concepts index

One row per Rust concept used in this codebase, pointing to the first place its teaching comment explains it. Claude Code adds a row whenever a file introduces a new concept. Sorted by the order they appear in the Rust learning path (`CLAUDE.md`). Line numbers are approximate; grep the `RUST:` tag nearby.

| Concept | First explained at | The Rust Book | Notes |
| --- | --- | --- | --- |
| `struct` and `impl` | `core/lamda-core/src/rng.rs` (Pcg32) | ch. 5.1, 5.3 | Private fields keep the odd-increment invariant |
| Fixed-width integers, `wrapping_*` | `rng.rs` (`next_u32`) | ch. 3.2 | Plain `+` panics on overflow in debug builds |
| `as` casts and truncation | `rng.rs` (`next_u32`) | ch. 3.2 | Comment says what is lost at every cast |
| `#[derive(..)]` | `rng.rs` (Pcg32) | appendix C | No `Copy` on the RNG, on purpose |
| Unit tests and doc tests | `rng.rs` (`mod tests`) | ch. 11 | Every `pub fn` has a `# Examples` block |
| Ownership and moves | `bitset.rs` (`from_bits`, `difference`) | ch. 4.1 | `Vec<u64>` owns heap memory |
| `&` and `&mut` borrows | `bitset.rs` (`intersect_with`) | ch. 4.2 | The mutable borrow guarantees `support` is a different set |
| Slices `&[T]` | `bitset.rs` (`words`) | ch. 4.3 | A view, no copy; lifetime elided |
| Iterators and adaptors | `bitset.rs` (`ones`, `count`) | ch. 13.2 | `impl Iterator<Item = usize> + '_` |
| `impl Iterator for` | `bitset.rs` (WordOnes) | ch. 13.2 | One `next`, everything else for free |
| Operator traits | `bitset.rs` (`BitAnd for &Bitset`) | ch. 19.3 | Implemented on references so operands survive |
| `debug_assert!` | `bitset.rs` (`intersect_with`) | std docs | Free in release builds |
| Newtypes | `ids.rs` (SlotId) | RBE generics/new_types | Slot index cannot be passed as a variant index |
| `Copy` | `ids.rs` (SlotId) | ch. 4.1 | Bit copy, original stays usable |
| `From` / `Into` | `ids.rs` (macro) | RBE conversion | Written once by a `macro_rules!` |
| `macro_rules!` | `ids.rs` (newtype_conversions) | ch. 20.5 | Declarative, pattern-based |
| serde derive | `assembly.rs` (Assembly) | serde.rs | `#[serde(default)]`, `transparent`, `rename_all` |
| `Result` and `?` | `assembly.rs` (`from_json`) | ch. 9.2 | Each `?` names the `From` it uses |
| `thiserror` and `#[from]` | `assembly.rs` (LoadError) | docs.rs/thiserror | `#[error("..")]` is the `Display` text |
| `Option<T>` | `assembly.rs` (optional fields), `bitset.rs` (`single`) | ch. 6.1 | JSON null or missing key |
| `match` and `matches!` | `assembly.rs` (tests), `attempts.rs` (`is_final`) | ch. 6.2 | Exhaustive |
| `impl AsRef<Path>` | `assembly.rs` (`from_path`) | ch. 10.2 | Accept `&str`, `String`, `PathBuf` |
| Lifetimes on methods | `graph.rs` (`edges<'a>`) | ch. 10.3 | Written explicitly once; elided elsewhere |
| `Vec::with_capacity` | `graph.rs` (`from_assembly`) | std docs | Allocate once |
| Nested `Vec<Option<Vec<T>>>` | `adjacency.rs` (Adjacency) | ch. 8.1 | `as_deref` borrows inside the Option |
| `let .. else` | `adjacency.rs` (`check_symmetry`) | reference | Bind or diverge |
| `cfg!(debug_assertions)` | `adjacency.rs` (`from_assembly`) | reference | Compile-time boolean |
| Atomics, interior mutability | `progress.rs` (Progress) | ch. 16.4, 15.5 | `Relaxed` is enough for counters |
| `VecDeque` | `propagate.rs` (Propagator) | ch. 8.1 | FIFO, matches the Python reference |
| Closures as `FnMut` parameters | `propagate.rs` (`propagate`) | ch. 13.1 | Generic `F` with a `where` bound |
| `mem::replace` | `propagate.rs` (`shrink`) | std docs | Old value out, no clone |
| `while let` | `propagate.rs` (`propagate`) | ch. 6.3 | Loop until `pop_front` is None |
| `f64` and summation order | `entropy.rs` (`recompute`) | ch. 3.2 | Plain left-to-right loop, ascending |
| `libm::log` | `entropy.rs` (`recompute`) | docs.rs/libm | Same bits on every platform |
| Derived `Ord` field order | `entropy.rs` (SlotKey) | std `Ord` docs | Lexicographic in declaration order |
| `BinaryHeap` and `Reverse` | `observe.rs` (Agenda) | ch. 8 / std docs | Lazy deletion by version |
| `loop` | `observe.rs` (`next_open`) | ch. 3.5 | Every path returns |
| Moves out of a `Vec` (`pop`) | `trail.rs` (`backtrack`) | ch. 4.1 | Restored Bitset moves back, no clone |
| `mem::take` | `trail.rs` (`take_changes`) | std docs | Leaves `Default` behind |
| Traits | `solve.rs` (Domain) | ch. 10.2 | The seam for sparse domains |
| Generics with bounds | `solve.rs` (`all_collapsed<D: Domain>`) | ch. 10.1 | Only trait methods are callable on `D` |
| Struct holding a reference | `solve.rs` (`Solver<'a>`) | ch. 10.3 | Cannot outlive the assembly |
| Destructuring to borrow fields | `solve.rs` (`propagate`) | ch. 5.1, 4.2 | Several `&mut` to different fields at once |
| `std::time::Instant` | `solve.rs` (`run_attempt`) | std docs | Elapsed seconds in the report |
| `rayon` parallel iterators | `attempts.rs` (`solve`) | docs.rs/rayon | `collect` keeps order |
| `Send` and `Sync` | `attempts.rs` (header, tests) | ch. 16.4 | Compile-time checked in a test |
| `Arc` | `attempts.rs` (tests), `lamda-py` | ch. 16.3 | Shared `Progress` across threads |
| `HashSet` with tuple keys | `validate.rs` (`validate`) | ch. 8.3 | O(1) pair membership |
| `&dyn Fn` trait objects | `validate.rs` (`check_edges`) | ch. 17.2 | Runtime dispatch, fine off the hot path |
| `binary_search` | `validate.rs` (`check_domain`) | std docs | On sorted domain classes |
| Builder-style options with `Default` | `synthetic.rs` (BenchSpec) | ch. 5.1 | `..Default::default()` |
| `BTreeSet` | `synthetic.rs` (`generate`) | std docs | Sorted, deduplicated pairs |
| Integration tests | `core/lamda-core/tests/office.rs` | ch. 11.3 | Public API only |
| `std::env::args` | `lamda-cli/src/main.rs` (`main`) | ch. 12.1 | `skip(1)` drops the program name |
| `Box<dyn Error>` | `lamda-cli/src/main.rs` (`run`) | ch. 9.2 | Any error type with `?` |
| Slice patterns | `lamda-cli/src/main.rs` (`cmd_validate`) | reference | `let [a, b] = args else ..` |
| Collecting `Result`s | `lamda-cli/src/main.rs` (`parse_size`) | ch. 9.2 | Stops at the first error |
| PyO3 module, class, function | `lamda-py/src/lib.rs` | pyo3.rs | `frozen` classes, `#[getter]`, `signature` |
| Releasing the GIL | `lamda-py/src/lib.rs` (`solve_json`) | pyo3.rs/parallelism | `allow_threads` with a `Send` closure |
| Workspace manifests | `Cargo.toml` | ch. 14.3 | `key.workspace = true` |
