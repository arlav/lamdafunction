---
paths:
  - "addon/**"
  - "tests/blender/**"
  - "examples/**"
---

# Blender extension rules

Target Blender 5.2 LTS (Python 3.13) as the minimum. Every source file starts with `# SPDX-License-Identifier: GPL-3.0-or-later`. Background: `docs/PLAN.md`, "Blender integration" and "Repository, packaging, licensing, CI".

## Structure

- `addon/model/` and `addon/compile/` never import `bpy`, so pytest runs them outside Blender. Everything that touches Blender lives in `addon/bl/`.
- `addon/__init__.py` must not import `bpy` or `addon/bl/` at module level: do those imports inside `register()` and `unregister()`, so importing `addon.model` in pytest never pulls in Blender.
- The compiler (rotation group, face frames, rule expansion, audit) is Python and writes the compiled assembly defined in `schema/`. Adjacency building, canonicalisation and solving happen in `lamda_core` (Rust). `reference/` is the fallback when the wheel cannot load: `scripts/build_extension.py` copies it into `addon/vendor/lamda_reference/` (gitignored) before building, so the shipped zip contains it.
- Python style: type hints everywhere, `ruff` clean, docstrings on every public function. Comment `bpy` behaviour that surprises (evaluated versus original data, undo invalidating references, depsgraph timing).

## Threading and data flow

1. Compile on the main thread. Read Blender data into NumPy arrays with `foreach_get`.
2. Hand plain arrays to `lamda_core` on a worker thread. Never touch `bpy` data from that thread.
3. A modal operator polls the solver's progress counters on a timer, shows progress in the status bar, and cancels on Esc.
4. Write results on the main thread with `foreach_set`, as one undo step.
5. Never keep `bpy` object references across an undo; store names or session UIDs.

## Objects

- **Library:** a "LamdaFunction Library" collection; each child collection is a module and carries a `lamdafunction` property group (name, footprint cells with the anchor first, rotational freedom None/X/Y/Z/Full, default weight, tags, per-face connector name and quarter-turn offset, terminator flags).
- **Envelope:** any mesh object; slots are its evaluated vertices, so Geometry Nodes can generate it.
- **Assembly:** a separate mesh object created by the solver, one vertex per slot, with seed, compile hash and solver report as custom properties. Results are not written into the envelope because its evaluated Geometry Nodes output cannot be written back.

## Attribute contract (point domain)

| Attribute | Type | Direction | Meaning |
| --- | --- | --- | --- |
| `wfc_cell` | float vector holding whole numbers | read from envelope | Grid coordinates; Blender has no 3-component integer attribute, so round on read. Derived from position and cell size if absent |
| `wfc_size` | float vector | read | Cell size, for heterogeneous grids |
| `wfc_w:<module>`, `wfc_w:#<tag>` | float | read | Weight multiplier; ≤ 0 removes the module from the slot |
| `wfc_priority` | int | read | Observation priority, higher first |
| `wfc_fix` | int | read | Force a module index; −1 means free |
| `wfc_module` | int | written to assembly | Library index of the placed module |
| `wfc_rotation` | quaternion | written | Rotation of the placed variant (mapped from the core's rotation index) |
| `wfc_part`, `wfc_anchor` | int, bool | written | Multi-cell part index; true where geometry is instanced |
| `wfc_state` | int | written | 0 solved, 1 open, 2 contradiction |
| `wfc_options` | int | written | Candidates left (partial results) |
| `wfc_step` | int | written | Order of collapse, for playback |

## Node groups, built by `addon/bl/nodes.py`

The groups are built from existing nodes by script at first use (and rebuilt on demand), so they stay in version control as code; an `assets/lamda.blend` export can be added later for users who want them without the add-on.

- **LamdaFunction Materialise:** Instance on Points (selection: anchor and solved), Collection Info on the library with Separate Children and Reset Children, Pick Instance on, Instance Index from `wfc_module`, Rotation from `wfc_rotation`, optional Realize Instances. Separate Children orders children by natural name order (digit runs compare as numbers, case-insensitive; not link order, and `collection.children` is link order), so the instance index only matches `wfc_module` if names sort in library order. Use a hidden instancer collection whose children are named with zero-padded indices (`0000`, `0001`, …) and renamed only by the add-on; spike 1 confirmed this on 4.3.2 (`docs/spikes/01-collection-order.md`), re-check on 5.2.
- **LamdaFunction Inspect:** colour slots by state (red contradiction, green solved, grey ramp by options left) and hide slots whose `wfc_step` is past the current frame, for playback.
- **LamdaFunction Envelope:** voxelise a mesh at a cell size (surface wrap, fill volume, or both) and write `wfc_cell` and `wfc_size`.
- Python add-ons cannot define new Geometry Nodes nodes, so all of this is node groups built from existing nodes. No custom node editor in v1.

## UI

- Sidebar tab "LamdaFunction" with panels: Library, Faces and Connectors, Rules, Envelope, Solve, Results and Audit.
- Viewport overlay: module cages, face arrows coloured by axis (X red, Y green, Z blue), connector arrows showing their offset, slot states.
- Every audit item is clickable and selects its culprit in the viewport.
- Wording: use generic WFC vocabulary from `docs/PLAN.md`. Never mention other WFC products in the UI.

## Packaging

- Manifest: `id = "lamdafunction"`, `name = "LamdaFunction"`, `type = "add-on"`, `blender_version_min = "5.2.0"`, `license = ["SPDX:GPL-3.0-or-later"]`, wheels for Windows x64, macOS arm64 and x64, and manylinux 2.28 x64, `files` permission declared. See the manifest sketch in `docs/PLAN.md`.
- Wheels come unmodified from the PyPI build of `lamda-core`; the extensions platform rejects anything else.
- Build with `python scripts/build_extension.py`: it vendors `reference/`, then runs `blender --command extension build --split-platforms --source-dir addon --output-dir dist`.

## Tests

`tests/blender/` runs with `blender -b --factory-startup --python-exit-code 1 --python tests/blender/run_all.py`; without `--python-exit-code` a failing test still exits with 0. Each example file compiles, solves with a fixed seed, and matches a golden hash of the written attributes; the instance count must equal the number of solved anchors.
