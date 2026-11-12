# Spike 7: curvature, direction field and seams on quad meshes

**Question.** Phase 2 turns each quad's frame so +X follows the surface's curvature (`docs/PLAN.md`, "Surfaces (phase 2)", "Direction field"). Three questions:
- Can that be done robustly and identically on every platform?
- How many seams does it leave? A seam is an edge where neighbouring frames differ by a quarter or half turn, so a module with no rotation freedom cannot cross it.
- Does the relative rotation across an edge worked out from shared vertices always match the geometry?

**Method.** `addon/model/field.py` (NumPy only, no `bpy`), driven by `tests/python/spike_07_field.py`.

It estimates a shape operator per face, by least squares from the corner normals along the four sides. The direction of largest absolute curvature becomes a line field, weighted by how strongly the two principal curvatures differ (`2·min(|H|, |k1 − k2|/2)`, which is zero on spheres, planes and where `k1 = −k2`). Each face is then given `k`, the quarter turn of its corner labelling, in one of two ways:

- **follow = "field":** smooth the line field across edges (damped Jacobi, 200 iterations, each neighbour's basis carried over the shared edge using both face normals). Each face then snaps to the pair of opposite sides closest to its own line, and breadth-first combing picks the sign.
- **follow = "mesh":** comb along the mesh's edge flow, integers only. A neighbour takes the `k` that makes the edge to its parent regular. Then each connected piece turns as a whole to the family the curvature (or a guide) prefers. A whole-piece turn changes no seam.

Seams are counted from the labelling alone (`seam_turns`) and checked against the turn measured from the transported axes (`seam_turns_geometric`).

The test meshes are in `addon/model/sample_meshes.py`:
- quad spheres at levels 1 to 4 and about 10,000 faces;
- a bumpy quad sphere;
- a barrel vault, and the same vault meshed with grid lines sheared about 13° off the generators;
- a saddle, a torus and a bumpy height field.

Every run is repeated to check `k` is identical. macOS arm64, NumPy 2.4.6.

**Numbers.** "Aligned" is the share of faces with a clear curvature preference (|k1| and |k2| more than 20 % apart) whose +X lies within 15° of the direction of largest |curvature|.

| Mesh | Faces | Irregular vertices | Aligned, mesh / field | Seams (quarter + half), mesh | Seams, field | Time, mesh / field |
| --- | --- | --- | --- | --- | --- | --- |
| Barrel vault | 768 | 0 | 100 % / 100 % | 0 | 0 | 2 ms / 13 ms |
| Vault, sheared grid | 768 | 0 | 100 % / 100 % (worst 3.6°) | 0 | 0 | 2 ms / 13 ms |
| Torus | 1,152 | 0 | 100 % / 100 % | 0 | 0 | 3 ms / 15 ms |
| Saddle | 900 | 0 | 49 % / 82 % | 0 | 89 + 26 | 2 ms / 14 ms |
| Bumpy height field | 900 | 0 | 18 % / 30 % | 0 | 164 + 117 | 2 ms / 14 ms |
| Quad sphere, level 4 | 1,536 | 8 | (no preference) | 64 + 32 | 280 + 240 | 4 ms / 18 ms |
| Quad sphere, 10,086 faces | 10,086 | 8 | (no preference) | 164 + 82 of 20,172 edges | 908 + 878 | 22 ms / 75 ms |
| Bumpy sphere | 1,536 | 8 | 21 % / 42 % | 64 + 48 | 268 + 169 | 4 ms / 19 ms |

On the 10,086-face sphere with world Z as the guide, the mesh mode puts 56 % of the faces within 15° of "up" (the cube-sphere grid does not follow meridians), with the same 246 seams. `k` was identical on every repeat. Five meshes are frozen in `tests/fixtures/field/` with exact coordinates, and `addon/model/test_field.py` pins `k` for eight (mesh, mode) cases so CI on Linux must reproduce the macOS result.

**What the numbers say.**

1. **A quad mesh carries its own cross field, its edge flow.** Frames can only be turned by quarter turns, so modules always run along the mesh's edges. Where the edges follow the curvature lines (the vault, even sheared by 13°, and the torus), both modes align perfectly with no seams, and ribs follow the arches exactly: +X has no component along the vault's length on any face.
2. **Where the edges do not follow curvature, following curvature face by face costs seams.** On the bumpy field and the saddle, every face snapping to its own curvature adds 115 to 281 seams on meshes that have no irregular vertex at all. Each seam blocks modules with no rotation freedom and breaks patterns. The mesh mode has zero seams there, at the price of lower alignment.
3. **Seams in mesh mode are only the ones the mesh forces.** On the quad sphere they run from its eight three-valent corners: 1.2 % of the edges at 10,086 faces, against 8.9 % for the field mode.
4. **Shared vertices, never geometry, must decide the relative rotation.** The two seam measures agree on every edge of the regular meshes. On the spheres they disagree on 16 to 112 edges, all where the transported axis is 47° to 59° from the combinatorial one, past the 45° at which rounding to a quarter turn flips. Those edges lie along the cube-edge lines of the projected sphere, where quads meet strongly skewed. The combinatorial answer is the one the cells actually share, so it is the only one used.
5. **Plain Jacobi smoothing oscillates on quad meshes.** It flipped sign every step, because the faces split into two alternating sets. Keeping each face's own value with weight 4 fixed it on the small meshes. On 10,000 faces it is still not converged after 200 steps (last change 0.014), so the field mode would need a better solver (multigrid, or the sparse solve of Knöppel et al. 2013, which needs a sparse-matrix package).
6. **Determinism holds by construction.** Complex numbers are pairs of real arrays multiplied one rounding at a time, sums run in a fixed order, and there is no BLAS or LAPACK and no trigonometry. NumPy's own complex multiply was avoided because it may be compiled into fused multiply-adds on one platform and not another.

**Decision.** Pass, with a change to the plan:

- **Mesh mode is the default.** Comb along the edge flow, then turn each piece to the family the curvature prefers, or the guide when one is set. It has the fewest seams possible for the mesh, it is exact, and it takes 22 ms for 10,000 faces.
- **The field mode stays experimental**, behind an option, with no new package. Its smoothing would need a better solver before it could be relied on.
- **Layouts that truly follow curvature need a curvature-aligned quad mesh.** The tutorial will say so: model with the curvature (vaults, lofts, swept surfaces, subdivision of a cage drawn along the main directions), or remesh with a field-aligned remesher. Curvature then only has to pick the family, which it does perfectly.
- **The audit lists seams and the irregular vertices that cause them**, and the overlay draws them. The guide is the user's way to say which family is "+X" where curvature has no opinion (spheres, planes).
- `k` is now pinned on frozen meshes in CI (Linux). Windows is checked when the field runs inside the Blender tests (S5).
