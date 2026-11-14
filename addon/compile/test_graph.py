# SPDX-License-Identifier: GPL-3.0-or-later
"""Slot graph with frames: twist and edge kinds, exhaustively over the 24 rotations."""

from __future__ import annotations

import random

from addon.compile import frames as faces
from addon.compile.graph import build_graph, edge_twist, local_face
from addon.compile.rotations import IDENTITY, ROTATIONS, apply, compose, inverse


def test_twist_is_the_same_from_both_sides() -> None:
    """For every pair of frames and every direction, both ends of an edge compute the same twist.

    The core requires the reverse edge to carry the same twist; a mismatch would make the
    graph check reject a correct shell.
    """
    for frame_a in range(24):
        for frame_b in range(24):
            for world in range(6):
                p = local_face(frame_a, world)
                q = local_face(frame_b, faces.opposite(world))
                r_ab = compose(inverse(frame_a), frame_b)
                r_ba = compose(inverse(frame_b), frame_a)
                assert edge_twist(p, q, r_ab) == edge_twist(q, p, r_ba), (frame_a, frame_b, world)


def test_identical_frames_give_the_box_grid_kind() -> None:
    """Same frame on both sides: opposite ports and twist 0, as the phase 1 tables assume."""
    for frame in range(24):
        for world in range(6):
            p = local_face(frame, world)
            q = local_face(frame, faces.opposite(world))
            assert q == faces.opposite(p)
            assert edge_twist(p, q, IDENTITY) == 0


def test_edge_kind_determines_the_relative_rotation() -> None:
    """For each port, the 24 relative rotations give 24 distinct (neighbour port, twist): one table per rotation.

    If two rotations shared a kind, one adjacency table would have to serve two different
    relations and the compiler could not build it.
    """
    for p in range(6):
        seen: dict[tuple[int, int], int] = {}
        for r in range(len(ROTATIONS)):
            # The neighbour port is the face that r turns to face back across p.
            q = next(f for f in range(6) if faces.face_of_normal(_turn(r, f)) == faces.opposite(p))
            kind = (q, edge_twist(p, q, r))
            assert kind not in seen, (p, r, seen.get(kind))
            seen[kind] = r
        assert len(seen) == 24


def _turn(rotation: int, face: int) -> tuple[int, int, int]:
    """The normal of ``face`` after ``rotation``."""
    return apply(rotation, faces.FACE_NORMAL[face])


def test_scrambled_graph_is_consistent() -> None:
    """Random frames on a block: every edge has its reverse with swapped ports and the same twist, and inverse rotations."""
    rng = random.Random(7)
    cells = [(x, y, z) for z in range(3) for y in range(3) for x in range(3)]
    frames = [rng.randrange(24) for _ in cells]
    graph = build_graph(cells, frames)
    edges = {}
    for slot in range(graph.slot_count):
        for i in range(graph.offsets[slot], graph.offsets[slot + 1]):
            edges[(slot, graph.neighbour[i])] = (graph.port[i], graph.neighbour_port[i], graph.twist[i], graph.relative[i])
    for (a, b), (p, q, t, r) in edges.items():
        bp, bq, bt, br = edges[(b, a)]
        assert (bp, bq, bt) == (q, p, t)
        assert br == inverse(r)
    assert len(graph.exposed) == 6 * 9
    graph.kinds()  # raises if one kind stood for two rotations
