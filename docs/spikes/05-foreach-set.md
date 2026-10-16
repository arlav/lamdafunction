# Spike 5: attribute write-back with `foreach_set`

**Question.** Writing a solve result into the assembly mesh happens on the main thread, so it must be fast: 200,000 points × 8 attributes in under 0.5 s in total.

**Method.** `tests/blender/spike_05_foreach_set.py`, headless. It builds a 200,000-vertex mesh with `vertices.add` plus one `foreach_set("co")`, then creates and writes the eight attributes of the contract with one `foreach_set` each from contiguous NumPy arrays in the attribute's own dtype: `wfc_cell` (float vector), `wfc_module`, `wfc_part`, `wfc_state`, `wfc_options`, `wfc_step` (int), `wfc_rotation` (quaternion, four float32 per point), `wfc_anchor` (bool). It writes them a second time into the existing attributes, reads all eight back with `foreach_get` and checks every array round-trips bit for bit, then times `mesh.update()` and linking the object into the scene. Blender 4.3.2, numpy 1.24.3, Apple Silicon.

**Numbers.**

| Step | Time |
| --- | --- |
| 8 attributes, create + write | 2.3 ms |
| 8 attributes, rewrite existing | 0.3 ms |
| Mesh build (`vertices.add` + positions) | 3.4 ms |
| Read all 8 back with `foreach_get` | 1.1 ms |
| `mesh.update()` | 0.0 ms |
| Object link + view layer update | 0.5 ms |

The `wfc_rotation` quaternion is the slowest single attribute at 1.3 ms. Everything together is under 10 ms, about 50 times inside the budget, because `foreach_set` with a contiguous array of the right dtype is a single memory copy through the buffer protocol. Passing a Python list or an array of the wrong dtype falls back to per-element conversion and is orders of magnitude slower, so the add-on must always hand over `np.ascontiguousarray(x, dtype=...)`.

**Decision.** Pass, with a large margin; no need for a binary attribute path or chunked writes. The rules that keep it fast: build result arrays in the solver's dtypes (`int32`, `float32`, `bool`), flatten vectors and quaternions to `n × components`, and write with `foreach_set` into attributes created once per solve. The undo step around the write is not measured here (undo is unavailable in background mode); measure it in the modal operator in phase 1.
