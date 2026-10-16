# SPDX-License-Identifier: GPL-3.0-or-later
"""The tube kit golden numbers (``.claude/rules/solver-core.md``, "Golden fixtures").

Modules straight, elbow, tee, cross, cap and empty; connector "t" where a
tube meets a face, "0" where blank; Full rotation. The compiler must produce
37 variants, the 4 x 3 x 2 minus 4 envelope has 20 slots with initial domains
7, 13 and 23 at three named slots, and two fix-and-propagate steps give the
pinned domain sizes.
"""

from __future__ import annotations

import json
from pathlib import Path

from addon.compile import frames
from addon.compile.assemble import Compiled, compile_assembly
from addon.compile.variants import rotate_module
from addon.model import Connector, ConnectorPair, Envelope, Kit, Module, SolveSettings
from reference import Solver, load_assembly, solve, validate

T = Connector("t")
Z = Connector("0")


def tube_module(name: str, open_faces: tuple[int, ...], weight: float = 1.0) -> Module:
    """A tube module with "t" on ``open_faces`` and "0" elsewhere; the geometry hash follows the faces."""
    faces = tuple(T if f in open_faces else Z for f in range(6))
    base = Module(name, "FULL", weight, (), faces)
    # The geometry of a tube piece is determined by which faces it opens to, so
    # the hash of the rotated geometry is the set of open world faces.
    hashes = tuple(str([c.name for c in rotate_module(base, r)[0]]) for r in range(24))
    return Module(name, "FULL", weight, (), faces, geometry_hash=hashes)


def tube_kit() -> Kit:
    """The six tube modules with self-pairs for "t" and "0" at every twist."""
    return Kit(
        modules=[
            tube_module("straight", (0, 1)),
            tube_module("elbow", (0, 2)),
            tube_module("tee", (0, 1, 2)),
            tube_module("cross", (0, 1, 2, 3)),
            tube_module("cap", (0,)),
            tube_module("empty", ()),
        ],
        pairs=[ConnectorPair("t", "t"), ConnectorPair("0", "0")],
    )


def tube_envelope() -> Envelope:
    """A 4 x 3 x 2 block minus the cells with x >= 2 and y = 2: 20 slots."""
    cells = [(x, y, z) for z in range(2) for y in range(3) for x in range(4) if not (x >= 2 and y == 2)]
    return Envelope(cells)


def compiled() -> Compiled:
    """The compiled tube assembly with a closed boundary ("0" everywhere)."""
    return compile_assembly(tube_kit(), tube_envelope(), SolveSettings(seed=1, attempts=4, closed_by="0"))


def variant_named(result: Compiled, module: str, open_faces: set[str]) -> int:
    """The variant of ``module`` whose "t" faces are exactly ``open_faces`` (world face names)."""
    kit = tube_kit()
    for v in result.expansion.variants:
        if kit.modules[v.module].name != module:
            continue
        opens = {frames.FACE_NAMES[d] for d, c in enumerate(v.faces) if c.name == "t"}
        if opens == open_faces:
            return v.index
    raise AssertionError(f"no {module} open to {open_faces}")


def initial_domain(doc: dict, slot: int) -> set[int]:
    """Domain class ∩ boundary classes of a slot, before propagation."""
    domain = set(doc["domain_classes"][doc["slot_domain"][slot]])
    for entry in doc["boundary"]:
        if entry["slot"] == slot:
            domain &= set(doc["boundary_classes"][entry["class"]]["variants"])
    return domain


def test_tube_kit_has_37_variants() -> None:
    """straight 3, elbow 12, tee 12, cross 3, cap 6, empty 1: rotations deduplicated by face signature and geometry."""
    result = compiled()
    counts = [len(v) for v in result.expansion.by_module]
    assert counts == [3, 12, 12, 3, 6, 1]
    assert len(result.document["variants"]) == 37
    assert not result.has_errors, [item.message for item in result.audit]


def test_tube_envelope_slots_and_initial_domains() -> None:
    """20 slots; corner (0,0,1) has 7 variants, edge (1,0,1) 13, top-face (1,1,1) 23."""
    result = compiled()
    doc = result.document
    assert doc["slots"] == 20
    cells = tube_envelope().cells
    assert len(initial_domain(doc, cells.index((0, 0, 1)))) == 7
    assert len(initial_domain(doc, cells.index((1, 0, 1)))) == 13
    assert len(initial_domain(doc, cells.index((1, 1, 1)))) == 23


def test_tube_propagation_golden() -> None:
    """Fix three pieces, propagate: (1,1,1) 6, (1,0,1) 7, (2,1,1) 13, (0,2,1) 2; then fix (1,1,1): (1,0,1) 4, (2,1,1) 6."""
    result = compiled()
    doc = json.loads(result.text)
    cells = tube_envelope().cells
    fixed = [-1] * 20
    fixed[cells.index((0, 0, 1))] = variant_named(result, "cap", {"+Y"})
    fixed[cells.index((0, 1, 1))] = variant_named(result, "elbow", {"-Y", "-Z"})
    fixed[cells.index((1, 2, 1))] = variant_named(result, "elbow", {"-Y", "-Z"})
    doc["fixed"] = fixed
    solver = Solver(load_assembly(doc))
    sizes = {cell: solver.canonical[cells.index(cell)].bit_count() for cell in [(1, 1, 1), (1, 0, 1), (2, 1, 1), (0, 2, 1)]}
    assert sizes == {(1, 1, 1): 6, (1, 0, 1): 7, (2, 1, 1): 13, (0, 2, 1): 2}

    fixed[cells.index((1, 1, 1))] = variant_named(result, "elbow", {"+X", "+Y"})
    doc["fixed"] = fixed
    solver = Solver(load_assembly(doc))
    assert solver.canonical[cells.index((1, 0, 1))].bit_count() == 4
    assert solver.canonical[cells.index((2, 1, 1))].bit_count() == 6


def test_tube_solves_and_validates() -> None:
    """The compiled tube assembly solves with the reference solver and passes the validator and the audit."""
    result = compiled()
    assembly = load_assembly(result.text)
    outcome = solve(assembly)
    assert outcome.report["outcome"] == "solved"
    assert validate(assembly, outcome.variant, outcome.state) == []
    # Every exposed face of the solution carries "0" (the boundary policy) and every touch matches t-t or 0-0.
    for entry in result.document["boundary"]:
        v = int(outcome.variant[entry["slot"]])
        assert result.expansion.variants[v].faces[entry["port"]].name == "0"


def test_tube_audit_with_canonicalisation() -> None:
    """The audit takes the core's canonicalisation report and reports the variant count and decided slots."""
    def canonicalise(text: str) -> dict:
        return Solver(load_assembly(text)).report.as_dict()

    result = compile_assembly(tube_kit(), tube_envelope(), SolveSettings(closed_by="0"), canonicalise)
    messages = [item.message for item in result.audit]
    assert any("37 variants" in m for m in messages)
    assert not result.has_errors, messages


def test_tube_manifest_fixture_matches(tmp_path: Path) -> None:
    """The kit survives the manifest format and compiles to the same document."""
    from addon.model.io import kit_from_dict, kit_to_dict

    kit = kit_from_dict(json.loads(json.dumps(kit_to_dict(tube_kit()))))
    again = compile_assembly(kit, tube_envelope(), SolveSettings(seed=1, attempts=4, closed_by="0"))
    assert again.document == compiled().document
