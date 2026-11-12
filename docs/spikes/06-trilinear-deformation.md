# Spike 6: trilinear deformation in Geometry Nodes

**Question.** Phase 2 bends each placed module into its hexahedral cell (`docs/PLAN.md`, "Surfaces (phase 2)", "Placing geometry"). Can a node group built by script do that in Blender 5.2, without gaps at connectors, in under 1 s for about 10,000 cells?

**Method.** `tests/blender/spike_06_trilinear.py`, headless, Blender 5.2.2 LTS, Apple silicon. It builds a quad sphere (a cube subdivided n × n per face, welded on an integer lattice, pushed to radius 5) and a one-layer shell: inner corners at the vertices, outer corners at vertex + smooth normal × one cell size. The smooth normals are area-weighted averages of the adjacent quad normals. An assembly point cloud has one point per cell with `wfc_c0` … `wfc_c7` (corner `x + 2y + 4z`; `x` along c0→c1, `y` along c0→c3, `z` inner→outer), `wfc_module` and `wfc_rotation` (a random quarter turn about Z per cell, fixed seed).

The library has two modules in the unit cube `[-0.5, 0.5]³`:
- a cross, with four tube stubs whose 16-vertex end rings lie on the ±X and ±Y faces;
- an elbow, with stubs on +X and +Y.

Each module carries a boolean `ring` point attribute on its end-ring vertices. The library goes through a hidden instancer with `0000`, `0001` children, as `addon/bl/nodes.py` builds it.

The script checks three things:
- **Gaps.** Every end-ring vertex of the realised mesh must have a partner from the neighbouring cell. The gap is the second-nearest distance in a KD-tree (the nearest is the vertex itself), divided by the mean edge length.
- **Attribute propagation.** The corner attributes must arrive on the realised vertices unchanged.
- **Rotation.** Each vertex's position before deformation must be the module vertex rotated by its cell's quarter turns.

Evaluation is timed twice. The second run rewrites one input attribute first: `update_tag()` alone does not reliably re-run the modifier, so rewriting an input is the safe way to force a fresh evaluation.

**Numbers.**

| Case | Cells | Realised vertices | Ring vertices checked | First evaluation | Re-evaluation | Max gap / cell size | Rotation error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Quad sphere, n = 41 | 10,086 | 1,291,008 | 645,504 | 0.076 s | 0.028 s | 6.1e-6 | 3.0e-8 |
| Quad sphere, n = 8 | 384 | 49,152 | 24,576 | 0.005 s | 0.002 s | 1.1e-6 | 3.0e-8 |
| Bumpy sphere, n = 24 | 3,456 | 442,368 | 221,184 | 0.011 s | 0.012 s | 3.6e-6 | 3.0e-8 |
| Elbows, n = 8 (rotation check) | 384 | 24,576 | — | 0.003 s | 0.002 s | — | 3.0e-8 |

Every ring vertex found its partner on the closed surfaces. The remaining gap is float32 rounding, not a mapping error. On the 10,000-cell sphere it is 2.3e-6 absolute at coordinates around 5, about five float32 units in the last place. A wrong corner order or rotation gives gaps of the order of the cell size. Evaluation is about 35 times inside the budget.

**Decision.** Pass, so "Deform to cells" will be built in Geometry Nodes. The recipe that works on 5.2.2:

1. **Set Position to the origin.** Link a Vector node set to (0, 0, 0) into Position. An unlinked Position input means "the point's own position" (an implicit field), so setting its default value does nothing.
2. **Instance on Points.** Use Collection Info (Original, Separate Children, Reset Children), with Pick Instance on, Instance Index from `wfc_module` and Rotation from `wfc_rotation` (Named Attribute, Quaternion type, the Rotation output). Leave Scale alone.
3. **Realize Instances.** The assembly's point attributes are copied to the instance domain and from there to every realised vertex, bit for bit. So every vertex sees its own cell's eight corners. Vertices come out in instance order, then in module vertex order.
4. **Optionally store the position as `wfc_local`.** At this point it is exactly the rotated module vertex (error 3e-8, the float32 value of cos 45°).
5. **Trilinear blend.** Vector Math Add (0.5, 0.5, 0.5), then Separate XYZ gives `u, v, w`. Seven Mix nodes do the blend: Vector data type, Uniform factor, Clamp off; socket identifiers `Factor_Float`, `A_Vector`, `B_Vector`, `Result_Vector`. The order is `mix(mix(c0,c1,u), mix(c2,c3,u), v)` for the bottom, the same with c4…c7 for the top, then `mix(bottom, top, w)`. Feed the result into a final Set Position.

What the add-on's Materialise should do:
- Add a **Deform to cells** switch. When it's on, run the recipe above on the anchor-and-solved selection.
- Deformed output is always realised geometry, so it costs memory (about 128 vertices per cell for these stubs; real kits vary). Rigid instancing stays the default for box envelopes.
- The compiler writes `wfc_c0` … `wfc_c7` into the assembly. That is already in the phase 2 attribute contract.

Gotchas to carry into S6:
- **Precision.** Gaps scale with the distance from the object origin, not with the cell size. Keep the assembly object's origin near the envelope, and use object-local corner coordinates. For a welded mesh, offer Merge by Distance with a threshold of about 1e-4 × cell size after realising.
- **Inverted cells.** Trilinear mapping folds inside very skewed or inverted cells, where a corner's Jacobian is negative. The S3 audit should flag those cells before solving.
- **Normals.** Shading normals are recomputed from the deformed positions. Custom split normals on module meshes are not deformed, so the docs should say modules should not rely on them.
- **Untested here:** only quarter turns about Z were tested, but the same quaternion path carries all 24 rotations. Several layers through the thickness were not tested either; they are the same per-cell corners and need no change to the recipe.
