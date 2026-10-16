# Spike 2: face frames, rotations and twist

**Question.** Fix, once, what a rotation index means, where a rotation sends each face and its connector arrow, and what the twist between two touching faces is, so that the rule compiler, the overlay and the connector-pair flags all agree. Prove it exhaustively before any other compiler code.

**Method.** Two pure-Python modules in `addon/compile/` with no `bpy`, and 20 pytest tests (`.venv/bin/python -m pytest addon/compile -q`):

- `rotations.py`: the 24 proper rotations of the cube as integer matrices, numbered by the image of +X (over +X, −X, +Y, −Y, +Z, −Z) then the image of +Y (over the perpendicular axes in the same order). Index 0 is the identity. `compose`, `inverse`, `quarter_turn(axis)`, `rotations_about(axis)`, `freedom_rotations("NONE"|"X"|"Y"|"Z"|"FULL")` and `quaternion(index)` for `wfc_rotation`.
- `frames.py`: the face table from `docs/PLAN.md` (outward normal, `u`, `v`; right-handed; opposite faces flip `u` and keep `v`), `arrow(face, offset)`, `rotate_face(rotation, face) -> (new face, offset change)` tabulated as `FACE_TABLE` (24 × 6), `variant_face`, and the twist in two forms: `twist_from_arrows(normal, a, b)` from world-space arrows and `twist(offset_a, offset_b)` from effective offsets.

Tests: 24 distinct proper orthogonal matrices; group closure, inverses and associativity over all 24³ triples; face permutations are bijections that keep opposites opposite; four quarter turns about each axis give the identity and the turn is right-handed; the X/Y/Z subgroups have four elements; quaternions move the basis exactly as the matrices do; the 24 × 6 × 4 table moves every arrow by one offset change per (rotation, face); table rows compose like the rotations; and the keyed pin-and-slot check.

**The pin-and-slot check.** A pin module has a key on its +X face at offset *k_a*; a slot module has a keyway on its −X face at offset *k_b*. Both modules take every one of the 24 rotations and every offset: 24 × 24 × 4 × 4 cases, of which 1,536 bring the two faces opposite each other. The two unit cubes are placed side by side; the key sits 0.4 of a cell from the shared face centre along the pin's world arrow, the keyway likewise along the slot's. The test asserts, for every case: the key lands exactly in the keyway if and only if the computed twist is 0; the frame formula and the world-arrow definition give the same twist; and the twist measured from the slot's side, about the slot's own normal, is the same number. All four twist values occur.

**Numbers.** 20 tests, 0.14 s. The exhaustive loops cover 13,824 composition triples, 576 table entries × 4 offsets, and 1,536 touching pin-and-slot cases.

**Decision, the spec everything downstream uses.**

1. Faces are numbered like the solver's box ports: 0 +X, 1 −X, 2 +Y, 3 −Y, 4 +Z, 5 −Z; `opposite(d) = d ^ 1`.
2. A rotation index is an index into `rotations.ROTATIONS`; the numbering is part of the compiled-assembly contract and never changes. The core stores it in `variants[i].rotation`; the add-on maps it to a quaternion with `rotations.quaternion`.
3. A connector with offset *k* on face *d* points along `u_d` turned *k* quarter turns counter-clockwise (right-hand rule) about the outward normal.
4. Rotating a module by *R* sends face *d* to the face whose normal is *R n_d* and adds `FACE_TABLE[R][d].delta` to every offset on it. The delta does not depend on the offset.
5. **Twist** of a touch between face *d* of A and face *opposite(d)* of B is the number of quarter turns counter-clockwise about A's outward normal that take A's world arrow onto B's. Twist 0 means the arrows coincide: a keyed pin fits. Measured from B's side about B's normal the number is the same, so the four flags on a connector pair (0, 90, 180, 270) have one meaning whichever name is listed first, and a name paired with itself needs no symmetrisation.
6. In the face-frame convention the same twist is `(2 − k_a − k_b) mod 4` from the two effective offsets. The `2` is the mirror between opposite faces' frames: offset 0 on +X (arrow +Y) meets offset 2 on −X (arrow +Y) at twist 0, and offset 0 meets offset 0 at twist 2. The overlay must draw world-space arrows so an author sees this rather than reasons about it; the audit can warn when a self-paired connector allows only twist 0 but its authored offsets never coincide.

Blender's own convention is the same right-hand rule, so `rotations.quaternion` feeds `wfc_rotation` directly.
