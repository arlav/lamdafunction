# Tutorial: surfaces

This walks through the three surface examples of the phase 2 gate, using only the LamdaFunction sidebar. About 30 minutes. Do `docs/pipes-tutorial.md` first: you need the installed extension, and example A reuses the pipes kit you built there.

What you make:

- **A. Quad sphere.** The pipes kit wrapped onto a sphere, bent into its cells so every connector is watertight.
- **B. Two-layer freeform.** The same kit on a freeform Subdivision Surface shape, two cells thick, with painted controls.
- **C. Ribs on a barrel vault.** A rib module with no rotation freedom that follows the arches, because the direction field puts each slot's +X along the curvature.

How it works in one paragraph: in **Surface** mode every quad of the envelope mesh grows into one cell per layer through a thickness, and each cell is a slot with the usual six ports. Neighbours are found through shared mesh edges, a direction field picks which mesh direction is each slot's +X, and **Deform to cells** bends each module into its cell in Geometry Nodes. Neighbouring cells share their corners and the deformation of a shared face depends only on those corners, so geometry that matches on the unit cell matches after deformation: watertight by construction.

## 0. Surface mode

Open your pipes-kit file (or `examples/pipes.blend`) and look at the **Envelope** panel. Under the envelope object there is now a **Points / Surface** switch. Points is phase 1: every mesh vertex is a slot. Surface is this tutorial: the mesh is a skin the shell follows.

In Surface mode the panel grows:

- **Thickness**, **Layers**, **Offset**: how deep the shell is, how many cells through it, and whether it grows outward from the surface, inward, or centred on it.
- **Direction field**: *Follow* (Mesh is the default; Field is experimental), *Curvature* weight, *Guide* (None / Axis / UV) with its weight, and *Show field*, which draws each slot's +X arrow and the seams in the viewport overlay.
- **Boundary per side**: the boundary policy per group — *Top* (the outer side of the shell), *Bottom* (the inner side) and *Rim* (the sides along open mesh edges). Each follows the **Closed boundary** setting below, or overrides it: Free, or Closed by its own connector.

Only quads become slots. Triangles and n-gons are skipped and listed in the audit — a Subdivision Surface modifier at level 1 or higher gives all quads, which is why every example here uses one.

## A. Pipes on a quad sphere

1. Hide or delete the old box envelope so it stays out of the way.
2. Add → Mesh → Cube, size 8. In the Modifier Properties, add **Subdivision Surface** with Levels 2 and Render 2 (that is 96 quads; the shipped `examples/surface-pipes.blend` uses 3, giving 384), then add **Cast** with Factor 1.0. The cube is now a quad sphere.
3. In **Envelope**, pick the sphere and switch the mode to **Surface**. Set **Thickness** 0.7, **Layers** 1, **Offset** Outward.
4. Leave the field at its defaults: Follow **Mesh**, Curvature 1, Guide **None**, **Show field** on. Compile once and look at the viewport: every face has a +X arrow, and the seams run from the cube's eight corners — a quad sphere has eight three-valent vertices, and a field cannot be seam-free around one. The audit lists them.
5. Leave **Closed boundary** on with connector `0`, seed 1, and click **Compile**, then **Solve**. The report says 37 variants from 6 modules; the audit lists the eight singularities and no errors.
6. In **Results and Audit**, leave **Deform to cells** on and click **Materialise**. The pipes bend into their cells; across the seams and around the corners the pipe ends still meet (within about a millionth of the cell size — float32 rounding). Turn **Realize** on to get real mesh you can export.

Try: **Deform to cells** off places each module rigidly at its cell centre instead — fast, but you will see wedge-shaped gaps on curved surfaces. Seed 2 gives a different network.

Why the pipes cross the seams: their rotation freedom is **Full**, so the compiler can turn them by the quarter that each seam needs. A module with freedom **None** cannot cross a seam; when one is blocked the audit names the seam and the module, and example C shows the freedom to use instead.

## B. A two-layer freeform

1. Start a new file and rebuild the pipes kit (steps 2–4 of the pipes tutorial take five minutes), or save a copy of your file from example A and delete the sphere.
2. Model a freeform: the shape only has to be a closed quad cage. For example, take the default cube into Edit Mode and move its eight vertices around — say scale it to 6 × 4 × 3 and shear the top (+0.5 in X with Z) — then add a **Subdivision Surface** modifier at level 2. Every evaluated face is a quad, and the evaluated mesh (modifiers applied) is what the shell follows, so you keep editing the cage.
3. In **Envelope**: Surface mode, your freeform as the envelope, **Thickness** 0.5, **Layers** 2, **Offset** Outward. Closed boundary on, `0`.
4. Compile and solve with seed 3: 192 slots, 96 faces × 2 layers. Materialise with **Deform to cells** on: a two-cell-thick pipe sandwich, watertight at every connector, inner layer included.
5. **Boundary per side** now has something to do. A closed shape has no rim, but top and bottom are the shell's two skins: set **Bottom** to *Closed by* `t` and the pipe ends must turn into the inner skin instead of blanking against it. Solve again and look at the difference.
6. Painted controls. These are attributes on the envelope mesh, read per face (they apply to every layer of that face):
   - `wfc_w:<module>`, float, face domain: a weight per module. Paint 0 over a region to keep, say, `cap` out of it.
   - `wfc_priority`, int, face domain: solve these slots first.
   - `wfc_fix`, int, face domain: a module index every slot of that face must take.
   - `wfc_thickness`, float, **point** domain: scales thickness and offset per vertex, so the shell can taper.
   Add them in the mesh's Object Data Properties → Attributes (or paint a float attribute in Weight Paint mode with the attribute selected in the header).

## C. Ribs on a barrel vault

The direction field aligns each slot's +X with the surface's principal curvature, so a module that cannot rotate still runs along the directions the surface bends.

1. New file. In **Library**: New Library, cell size 1, one module named `rib`. Replace its cube with a straight piece: connector `t` on +X and −X, `0` on the other four faces (the pipes kit's `straight` geometry works; so does a plain box). Set its **Rotation** to **None**.
2. In **Rules**: Indifference off, pairs `t`↔`t` and `0`↔`0`, all four twists on. Closed boundary on, `0`.
3. Build the vault: Add → Mesh → Cylinder with 24 vertices and Cap Fill Type **Nothing**, rotate it 90° about Y so its axis runs along X, and in Edit Mode delete the bottom half of the vertices (everything below Z = 0). What is left is a half cylinder: arches across, straight generators along. Make about five loop cuts along its length so the cells stay roughly square (12 arch segments × 6 bands = 72 quads).
4. **Envelope**: the vault, Surface mode, **Thickness** 0.4, **Layers** 1. Under **Boundary per side**, set **Rim** to *Free*: the arches end open at the springing line, and their exposed faces carry `t`, which a closed `0` boundary would forbid.
5. Compile and solve (seed 5, 72 slots, no errors — every vertex of the vault is four-valent, so the field has no singularity and no seam). With **Show field** on, the +X arrows run across the arches, not along the vault: that is the direction of largest curvature.
6. Materialise with Deform: every rib follows its arch. Freedom None means *follow the field*.

Try the guide: set **Guide** to *Axis* with axis (1, 0, 0) and weight 1, and recompile. Curvature votes 1/r per face (here 1/3), so a weight-1 guide outvotes it and turns the whole piece: the ribs now run along the vault's length. Set the guide weight to 0.01 and curvature wins again. On a mesh with a UV map, Guide *UV* points +X where U (or V) grows.

## Reading the audit

- **Skipped N non-quad faces** — triangles and n-gons get no slots. A UV sphere's poles are triangles; use a subdivided cube ("quad sphere") instead.
- **Singularities** (mesh vertices) — three- or five-valent vertices where the field must seam. They are listed per vertex; **Select Culprit** moves the 3D cursor there.
- **Seams** — edges where the field turns by a quarter. Modules whose freedom cannot turn that far are named and cannot cross them; give such modules Z or Full freedom, or model so the seams land where you want them.
- **Folded / pinched cells** — the shell is likely thicker than the local radius of curvature (a sphere grown inward past its centre). Lower the thickness or taper it with `wfc_thickness`.
- **Non-manifold edges**, **faces turned over** — mesh trouble; the builder orients what it can and notes what it did.

## Rules of thumb

- Curvature-aligned layouts come from curvature-aligned meshes. Model with the curvature (vaults, lofts, sweeps, a subdivision cage drawn along the main directions); then the field only has to pick between the two mesh directions, which it does exactly.
- Z freedom (turns about the normal) is the safe default for surface kits with no preferred direction, such as pipes. None means "follow the field" — right for ribs, wrong across seams.
- The evaluated mesh counts: Subdivision Surface, Cast, Solidify previews and Geometry Nodes all shape the shell, and the cage stays editable.
- Rigid placement first to check the solve, Deform to cells for the finished geometry.

If something cannot be solved, the audit says which slot empties first and **Select Culprit** takes you there, exactly as in the pipes tutorial.
