# SPDX-License-Identifier: GPL-3.0-or-later
"""Cell envelopes and shells (phase 2, S3).

The central check is equivalence: a flat quad grid grown into layers is the
same set of cells as a box envelope, so it must give the same cell envelope
and compile to the same assembly. With a random labelling turn ``k`` per
face it must equal a box whose frames are those quarter turns about Z; the
shell works out ports and twists from shared mesh edges, the box from world
directions, so agreement checks one against the other.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from addon.compile.assemble import compile_assembly
from addon.compile.graph import envelope_cells, graph_from_cells, relative_rotation
from addon.compile.rotations import IDENTITY, compose, quarter_turn, rotations_about
from addon.compile.test_tube import tube_kit
from addon.model import Envelope, Module, SolveSettings
from addon.model import cells as cells_module
from addon.model.cells import CellEnvelope, corner_jacobians
from addon.model.field import FieldSettings, seam_turns, topology
from addon.model.sample_meshes import barrel_vault, quad_sphere, saddle
from addon.model.shell import build_shell, orient
from reference import load_assembly, solve, validate

SETTINGS = SolveSettings(seed=1, attempts=4, closed_by="0")
SettingsFree = SolveSettings(seed=3, attempts=4)
TURN_Z = quarter_turn((0, 0, 1))


def flat_grid(nx: int, ny: int) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Vertices at integer (x, y, 0) and quads in x-then-y order, counter-clockwise from +Z."""
    vertices = np.array([(x, y, 0.0) for y in range(ny + 1) for x in range(nx + 1)], dtype=np.float64)

    def vid(x: int, y: int) -> int:
        return y * (nx + 1) + x

    polygons = [(vid(x, y), vid(x + 1, y), vid(x + 1, y + 1), vid(x, y + 1)) for y in range(ny) for x in range(nx)]
    return vertices, polygons


def z_turns(k: int) -> int:
    """The rotation of ``k`` counter-clockwise quarter turns about +Z."""
    r = IDENTITY
    for _ in range(k % 4):
        r = compose(TURN_Z, r)
    return r


def same_cells(a: CellEnvelope, b: CellEnvelope) -> None:
    """Corners, neighbours and exposed (cell, port) agree; groups and sources may differ."""
    assert a.neighbours == b.neighbours
    assert [(x.cell, x.port) for x in a.exposed] == [(x.cell, x.port) for x in b.exposed]
    assert np.array_equal(a.corners, b.corners)
    assert np.array_equal(a.frames, b.frames)


def without_hash(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "compile_hash"}


@pytest.mark.parametrize("size", [(3, 3, 1), (4, 2, 3)])
def test_flat_grid_equals_box_envelope(size: tuple[int, int, int]) -> None:
    """Same cells as a box, and the same compiled assembly with the tube kit."""
    nx, ny, layers = size
    vertices, polygons = flat_grid(nx, ny)
    shell = build_shell(vertices, polygons, thickness=float(layers), layers=layers)
    box = Envelope.box(size)
    same_cells(shell, envelope_cells(box))
    assert shell.notes == []
    assert {x.group for x in shell.exposed} == {"top", "bottom", "rim"}
    from_shell = compile_assembly(tube_kit(), shell, SETTINGS)
    from_box = compile_assembly(tube_kit(), box, SETTINGS)
    assert from_shell.document["schema_version"] == "1.0.0"
    assert without_hash(from_shell.document) == without_hash(from_box.document)


@pytest.mark.parametrize("seed", range(1, 6))
def test_turned_labelling_equals_box_with_turned_frames(seed: int) -> None:
    """A random k per face gives the box envelope whose frames are k quarter turns about Z."""
    rng = random.Random(seed)
    nx, ny, layers = 4, 3, 2
    vertices, polygons = flat_grid(nx, ny)
    k = [rng.randrange(4) for _ in polygons]
    shell = build_shell(vertices, polygons, thickness=2.0, layers=2, k=k)
    box = Envelope.box((nx, ny, layers))
    box = Envelope(box.cells, frames=[z_turns(k[i % len(polygons)]) for i in range(len(box.cells))])
    same_cells(shell, envelope_cells(box))
    from_shell = compile_assembly(tube_kit(), shell, SETTINGS)
    from_box = compile_assembly(tube_kit(), box, SETTINGS)
    assert from_shell.document["schema_version"] == "1.1.0"
    assert without_hash(from_shell.document) == without_hash(from_box.document)


def cube() -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """The unit cube's six quads, counter-clockwise seen from outside."""
    mesh = quad_sphere(1)
    return mesh.vertices, [tuple(int(v) for v in f) for f in mesh.faces]


def test_cube_gives_six_slots_and_24_side_edges() -> None:
    """Six slots; each meets four others; every side edge turns about the normal by the labelling's seam turn."""
    vertices, polygons = cube()
    shell = build_shell(vertices, polygons)
    assert shell.slot_count == 6
    sides = [x for x in shell.neighbours if x.port < 4]
    assert len(sides) == 24 and len(shell.neighbours) == 24
    assert sorted((x.cell, x.port) for x in shell.exposed) == [(c, p) for c in range(6) for p in (4, 5)]
    assert {x.group for x in shell.exposed if x.port == 4} == {"top"}
    topo = topology(orient(quad_sphere(1))[0])
    turns = seam_turns(np.zeros(6, dtype=np.int64), topo)
    z_axis = rotations_about((0, 0, 1))
    for x in sides:
        r = relative_rotation((x.port, x.other_port, x.twist))
        assert r in z_axis
        side = [s for s in range(4) if topo.neighbour[x.cell, s] == x.other][0]
        # The labelling's seam turn is the neighbour's frame turned against this one, counter-clockwise.
        assert r == z_turns(int(turns[x.cell, side]))
    # With k = 0 half of the cube's 12 edges are seams; three-valent corners mean some must be.
    assert sum(relative_rotation((x.port, x.other_port, x.twist)) != IDENTITY for x in sides) == 2 * 6


def test_cube_solves_with_the_tube_kit() -> None:
    """The tube kit (freedom FULL) fills a cube shell; the seams are reported but block nothing."""
    vertices, polygons = cube()
    result = compile_assembly(tube_kit(), build_shell(vertices, polygons), SETTINGS)
    assert not result.has_errors, [i.message for i in result.audit]
    assert any("6 edges are seams" in i.message for i in result.audit)
    assert not any("cannot cross" in i.message for i in result.audit)
    assembly = load_assembly(result.text)
    outcome = solve(assembly)
    assert outcome.report["outcome"] == "solved"
    assert validate(assembly, outcome.variant, outcome.state) == []


def test_fixed_module_cannot_cross_seams() -> None:
    """A module with freedom None is named in the audit with the seams it cannot cross."""
    kit = tube_kit()
    kit.modules = [
        Module(m.name, "NONE" if m.name == "straight" else m.freedom, m.weight, m.tags, m.faces, geometry_hash=m.geometry_hash)
        for m in kit.modules
    ]
    vertices, polygons = cube()
    result = compile_assembly(kit, build_shell(vertices, polygons), SETTINGS)
    warnings = [i for i in result.audit if "cannot cross" in i.message]
    assert [i.target for i in warnings] == [("module", "straight")]
    assert "6 of the 6 seams" in warnings[0].message
    load_assembly(result.text)  # tables stay symmetric


def random_quad_mesh(rng: random.Random) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """A random patch of a bumpy grid: random quads removed, some faces reversed, wavy heights."""
    nx, ny = rng.randrange(2, 7), rng.randrange(2, 7)
    vertices, polygons = flat_grid(nx, ny)
    vertices = vertices.copy()
    phase = rng.random() * 6.0
    vertices[:, 2] = [0.3 * np.sin(0.9 * x + 0.5 * y + phase) for x, y, _ in vertices]
    kept = [p for p in polygons if rng.random() > 0.2] or polygons[:1]
    return vertices, [tuple(reversed(p)) if rng.random() < 0.3 else p for p in kept]


@pytest.mark.parametrize("seed", range(40))
def test_random_quad_meshes_give_symmetric_graphs(seed: int) -> None:
    """Reversed faces are turned back, every touch has its reverse, and the tube kit compiles and solves validly."""
    rng = random.Random(seed)
    vertices, polygons = random_quad_mesh(rng)
    layers = rng.randrange(1, 4)
    k = [rng.randrange(4) for _ in polygons]
    shell = build_shell(vertices, polygons, thickness=0.2 * layers, layers=layers, k=k)
    shell.check()
    assert shell.slot_count == len(polygons) * layers
    assert not [n for n in shell.notes if n.level == "error"]
    graph = graph_from_cells(shell)
    graph.kinds()
    result = compile_assembly(tube_kit(), shell, SettingsFree)
    assembly = load_assembly(result.text)  # the reference checks table symmetry and reverse edges
    outcome = solve(assembly)
    if outcome.report["outcome"] == "solved":
        assert validate(assembly, outcome.variant, outcome.state) == []


def test_skipped_and_non_manifold_faces_are_noted() -> None:
    """A triangle and a pentagon are skipped; three quads on one edge make an error and open sides."""
    vertices, polygons = flat_grid(2, 1)
    vertices = np.vstack([vertices, [[0.5, 0.5, 1.0], [0.5, -0.5, 1.0], [2.0, 2.0, 0.0], [3.0, 2.0, 0.0]]])
    # A third quad on the shared edge 1-4 (vertices (1,0) and (1,1)).
    fin = (1, 4, 6, 7)
    polygons = polygons + [fin, (0, 1, 6), (2, 5, 9, 8, 4)]
    shell = build_shell(vertices, polygons)
    assert shell.slot_count == 3
    assert [s[1] for s in shell.sources] == [0, 1, 2]
    skipped = [n for n in shell.notes if n.target[0] == "mesh_face"]
    assert [n.target for n in skipped] == [("mesh_face", 3), ("mesh_face", 4)]
    errors = [n for n in shell.notes if n.level == "error"]
    assert [n.target for n in errors] == [("mesh_edge", 1, 4)]
    assert all(x.port >= 4 for x in shell.neighbours) and not shell.neighbours
    result = compile_assembly(tube_kit(), shell, SettingsFree)
    assert result.has_errors


def mobius(n: int = 12) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """A Möbius strip of ``n`` quads: it cannot be oriented, so one edge must be cut."""
    vertices = []
    for i in range(n):
        a = 2.0 * np.pi * i / n
        for w in (-0.3, 0.3):
            r = 2.0 + w * np.cos(a / 2.0)
            vertices.append((r * np.cos(a), r * np.sin(a), w * np.sin(a / 2.0)))
    polygons = []
    for i in range(n):
        j = (i + 1) % n
        a0, a1, b0, b1 = 2 * i, 2 * i + 1, 2 * j, 2 * j + 1
        if j == 0:  # the half twist: the far edge comes back reversed
            b0, b1 = b1, b0
        polygons.append((a0, b0, b1, a1))
    return np.array(vertices), polygons


def test_mobius_strip_is_cut_once() -> None:
    """One inconsistent edge is cut and noted; the rest connect, and the graph is still symmetric."""
    vertices, polygons = mobius()
    shell = build_shell(vertices, polygons, thickness=0.1)
    cut = [n for n in shell.notes if "cannot be oriented" in n.message]
    assert len(cut) == 1
    assert len([x for x in shell.neighbours if x.port < 4]) == 2 * (len(polygons) - 1)
    load_assembly(compile_assembly(tube_kit(), shell, SettingsFree).text)


def test_already_oriented_mesh_is_unchanged() -> None:
    """Orientation keeps a consistent mesh as it is, and keeps the majority orientation of a piece."""
    mesh = quad_sphere(2)
    oriented, turned, cut = orient(mesh)
    assert np.array_equal(oriented.faces, mesh.faces) and turned == [] and cut == []


def test_folded_cells_are_errors() -> None:
    """A bow-tie quad (corners out of order) is inside out at two corners; a sphere's thin shell is not.

    The check reads the trilinear Jacobian at the eight corners. A shell grown
    inwards past a sphere's centre is pinched through a point, not inverted,
    and passes it; the thickness control in S7 will bound that case.
    """
    mesh = quad_sphere(2)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    good = build_shell(mesh.vertices, polygons, thickness=0.5, offset="inward")
    assert (corner_jacobians(good.corners) > 0.0).all()
    assert not [n for n in good.notes if n.level == "error"]
    vertices, polygons = flat_grid(1, 1)
    assert polygons == [(0, 1, 3, 2)]
    bowtie = build_shell(vertices, [(0, 1, 2, 3)])  # (1, 0) then (0, 1): the sides cross
    errors = [n for n in bowtie.notes if "folded" in n.message]
    assert [n.target for n in errors] == [("slot", 0)]
    assert compile_assembly(tube_kit(), bowtie, SettingsFree).has_errors


def test_offsets_place_the_layers() -> None:
    """Outward starts on the surface, inward ends on it, centred straddles it."""
    vertices, polygons = flat_grid(1, 1)
    z = {o: build_shell(vertices, polygons, thickness=2.0, layers=2, offset=o).corners[:, :, 2] for o in ("outward", "inward", "centred")}
    assert z["outward"].min() == 0.0 and z["outward"].max() == 2.0
    assert z["inward"].min() == -2.0 and z["inward"].max() == 0.0
    assert z["centred"].min() == -1.0 and z["centred"].max() == 1.0


def test_face_inputs_reach_every_layer() -> None:
    """Weights, priority and fixes painted per face apply to that face's cells in every layer."""
    vertices, polygons = flat_grid(2, 1)
    polygons = polygons + [(0, 1, 4)]
    shell = build_shell(
        vertices, polygons, layers=2,
        face_inputs={"weights": {"elbow": [0.0, 2.0, 9.0]}, "priority": [1, 5, 9], "fixed": [-1, 3, 9]},
    )
    assert shell.weights == {"elbow": [0.0, 2.0, 0.0, 2.0]}
    assert shell.priority == [1, 5, 1, 5]
    assert shell.fixed == [-1, 3, -1, 3]


def test_json_round_trip() -> None:
    """The JSON form reads back to an equal envelope, which compiles to the same document and hash."""
    mesh = quad_sphere(2, bumps=0.2)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    shell = build_shell(mesh.vertices, polygons, thickness=0.3, layers=2, face_inputs={"priority": list(range(len(polygons)))})
    data = cells_module.to_dict(shell)
    back = cells_module.from_dict(data)
    same_cells(shell, back)
    assert back.exposed == shell.exposed and back.sources == shell.sources and back.priority == shell.priority
    assert compile_assembly(tube_kit(), back, SETTINGS).text == compile_assembly(tube_kit(), shell, SETTINGS).text


def test_check_rejects_a_missing_reverse() -> None:
    """A host that lists only one direction of a touch is refused before compiling."""
    vertices, polygons = flat_grid(2, 1)
    shell = build_shell(vertices, polygons)
    shell.neighbours = shell.neighbours[1:]
    with pytest.raises(ValueError):
        shell.check()


def test_quad_sphere_solves() -> None:
    """A 96-cell quad sphere, two layers, with the tube kit: compiles cleanly, solves, validates."""
    mesh = quad_sphere(4)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    shell = build_shell(mesh.vertices, polygons, thickness=0.2, layers=2)
    assert shell.slot_count == 192
    result = compile_assembly(tube_kit(), shell, SETTINGS)
    assert not result.has_errors, [i.message for i in result.audit]
    assembly = load_assembly(result.text)
    outcome = solve(assembly)
    assert outcome.report["outcome"] == "solved"
    assert validate(assembly, outcome.variant, outcome.state) == []


def test_pieces_touching_at_a_vertex_are_oriented_alike() -> None:
    """Two quads sharing only a corner, one reversed: the second turns to match, so the shared normal stays up."""
    vertices = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 1, 0), (2, 2, 0), (1, 2, 0)], dtype=np.float64)
    shell = build_shell(vertices, [(0, 1, 2, 3), (2, 6, 5, 4)])
    assert [n.target for n in shell.notes] == [("mesh_face", 1)]
    assert (shell.corners[:, 4:, 2] == 1.0).all()
    assert (corner_jacobians(shell.corners) > 0.0).all()


def test_json_form_matches_the_schema_keys() -> None:
    """Every key ``to_dict`` writes is declared in ``schema/cell-envelope-v1.json``, and every required key is written."""
    import json
    from pathlib import Path

    schema = json.loads((Path(__file__).resolve().parents[2] / "schema" / "cell-envelope-v1.json").read_text())
    vertices, polygons = flat_grid(2, 2)
    data = cells_module.to_dict(build_shell(vertices, polygons, face_inputs={"priority": [0] * 4, "fixed": [-1] * 4}))
    assert set(schema["required"]) <= set(data) <= set(schema["properties"])
    assert schema["properties"]["format"]["const"] == cells_module.CELLS_FORMAT


def test_shell_document_agrees_with_the_rust_core() -> None:
    """A bumpy two-layer quad sphere: the Rust core and the reference solve it to the same arrays."""
    lamda_core = pytest.importorskip("lamda_core")
    mesh = quad_sphere(3, bumps=0.2)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    result = compile_assembly(tube_kit(), build_shell(mesh.vertices, polygons, thickness=0.3, layers=2), SETTINGS)
    rust = lamda_core.solve_json(result.text)
    ref = solve(load_assembly(result.text))
    assert list(rust.variant) == list(ref.variant)
    assert lamda_core.validate_json(result.text, rust.variant, rust.state) == []


def test_field_labels_a_quad_sphere() -> None:
    """The field's k reaches the shell: eight singularities noted, seams as the field says, and it solves."""
    mesh = quad_sphere(4)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    shell = build_shell(mesh.vertices, polygons, thickness=0.2, field=FieldSettings())
    singular = [n for n in shell.notes if n.target[0] == "mesh_vertex"]
    assert len(singular) == 8 and all("3 faces" in n.message for n in singular)
    summary = [n.message for n in shell.notes if "direction field has" in n.message]
    assert len(summary) == 1 and "from 8 irregular vertices" in summary[0]
    result = compile_assembly(tube_kit(), shell, SETTINGS)
    assert not result.has_errors, [i.message for i in result.audit]
    assembly = load_assembly(result.text)
    outcome = solve(assembly)
    assert outcome.report["outcome"] == "solved"
    assert validate(assembly, outcome.variant, outcome.state) == []


@pytest.mark.parametrize(("axis", "k"), [((1.0, 0.0, 0.0), 0), ((0.0, 1.0, 0.0), 1), ((-1.0, 0.0, 0.0), 2), ((0.0, -1.0, 0.0), 3)])
def test_flat_grid_with_a_guide_is_a_turned_box(axis: tuple[float, float, float], k: int) -> None:
    """A world-axis guide on a flat grid turns every cell alike: the same cells as giving that k by hand."""
    vertices, polygons = flat_grid(3, 2)
    guided = build_shell(vertices, polygons, field=FieldSettings(guide=axis))
    same_cells(guided, build_shell(vertices, polygons, k=[k] * len(polygons)))
    assert np.allclose(guided.frames[:, 1], axis)
    assert not any("direction field" in n.message for n in guided.notes)


def test_vault_cells_follow_the_arches() -> None:
    """With the field, every cell's +X lies in its arch's plane (no component along the vault)."""
    mesh = barrel_vault(8, 6)
    shell = build_shell(mesh.vertices, [tuple(int(v) for v in f) for f in mesh.faces], thickness=0.2, field=FieldSettings())
    assert np.abs(shell.frames[:, 1, 0]).max() < 1e-9


def test_per_face_guide_skips_non_quads() -> None:
    """A per-polygon guide is taken per quad, so a skipped triangle does not shift the rows."""
    vertices, polygons = flat_grid(2, 1)
    vertices = np.vstack([vertices, [(5.0, 5.0, 0.0)]])
    polygons = [(0, 1, 6), *polygons]
    rows = np.array([(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1.0, 0.0)])
    shell = build_shell(vertices, polygons, field=FieldSettings(guide=rows))
    assert np.allclose(shell.frames[:, 1], (0.0, 1.0, 0.0))
    with pytest.raises(ValueError):
        build_shell(vertices, polygons, field=FieldSettings(guide=rows[:2]))


def test_field_and_k_are_exclusive() -> None:
    vertices, polygons = flat_grid(1, 1)
    with pytest.raises(ValueError):
        build_shell(vertices, polygons, k=[0], field=FieldSettings())


def test_field_does_not_comb_across_a_cut() -> None:
    """On a Möbius strip the field combs the uncut strip, so it adds no seam: the only open side is the cut."""
    vertices, polygons = mobius()
    shell = build_shell(vertices, polygons, thickness=0.1, field=FieldSettings())
    assert not any("direction field has" in n.message for n in shell.notes)
    assert len([x for x in shell.neighbours if x.port < 4]) == 2 * (len(polygons) - 1)


def test_field_mode_seams_are_compared_with_mesh_mode() -> None:
    """The experimental mode warns when it adds seams the default would not: 19 against 0 on a saddle."""
    mesh = saddle(8)
    polygons = [tuple(int(v) for v in f) for f in mesh.faces]
    shell = build_shell(mesh.vertices, polygons, thickness=0.2, field=FieldSettings(follow="field"))
    assert any("gives 19 seams where 'mesh' gives 0" in n.message for n in shell.notes)
    plain = build_shell(mesh.vertices, polygons, thickness=0.2, field=FieldSettings())
    assert not any("seams" in n.message for n in plain.notes)
