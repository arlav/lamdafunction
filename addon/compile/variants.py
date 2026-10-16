# SPDX-License-Identifier: GPL-3.0-or-later
"""Expand modules into solver variants: module × rotation, deduplicated.

Build order step 2 (``docs/PLAN.md``, "Rule compiler"). For every module and
every rotation its freedom allows, the connectors are moved to their world
faces with :func:`frames.variant_face`. Two rotations of one module are the
same variant when their world-face signatures match *and* their geometry
hashes match; the lowest rotation index is kept. The signature ignores the
offset of a connector that pairs at every twist, because no rule can tell
such offsets apart. Without geometry hashes nothing is merged, so a rotation
is never lost silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.kit import Connector, Kit, Module
from . import frames
from .rotations import freedom_rotations


@dataclass(frozen=True)
class VariantInfo:
    """One solver variant: a module in one rotation, with its faces in world order.

    ``faces[d]`` is the connector on world face ``d`` (name and effective
    offset); ``terminator[d]`` whether that world face may sit at a closed
    boundary; ``authored_face[d]`` which authored face landed on world face
    ``d``, for evaluating rules that name authored faces.
    """

    index: int
    module: int
    rotation: int
    faces: tuple[Connector, ...]
    terminator: tuple[bool, ...]
    authored_face: tuple[int, ...]

    def world_face_of(self, authored: int) -> int:
        """The world face an authored face occupies in this variant."""
        return self.authored_face.index(authored)

    def signature(self) -> tuple[Connector, ...]:
        """The world-face connector signature used for deduplication."""
        return self.faces


@dataclass(frozen=True)
class Expansion:
    """All variants of a kit, with lookups by module and by (module, rotation)."""

    variants: tuple[VariantInfo, ...]
    by_module: tuple[tuple[int, ...], ...]
    representative: tuple[dict[int, int], ...]

    def variant_for(self, module: int, rotation: int) -> int | None:
        """The variant index representing ``(module, rotation)``, or ``None`` if the freedom excludes the rotation."""
        return self.representative[module].get(rotation)


def rotate_module(module: Module, rotation: int) -> tuple[tuple[Connector, ...], tuple[bool, ...], tuple[int, ...]]:
    """World faces, terminator flags and authored-face map of ``module`` under ``rotation``."""
    faces: list[Connector] = [Connector()] * 6
    terminator = [False] * 6
    authored = [0] * 6
    for face, connector in enumerate(module.faces):
        world, offset = frames.variant_face(rotation, face, connector.offset)
        faces[world] = Connector(connector.name, offset if connector.covered else 0)
        terminator[world] = module.terminator[face]
        authored[world] = face
    return tuple(faces), tuple(terminator), tuple(authored)


def keyed_connectors(kit: Kit) -> frozenset[str]:
    """Connector names whose offset matters: they appear in a pair that does not allow all four twists.

    A connector that pairs at every twist (a round tube, say) behaves the
    same whatever its offset, so two rotations that differ only in such
    offsets are the same variant.
    """
    return frozenset(name for pair in kit.pairs if not all(pair.twists) for name in (pair.a, pair.b))


def signature(faces: tuple[Connector, ...], keyed: frozenset[str]) -> tuple[Connector, ...]:
    """The world faces with offsets kept only on keyed connectors."""
    return tuple(c if c.name in keyed else Connector(c.name, 0) for c in faces)


def expand_variants(kit: Kit) -> Expansion:
    """Build the variant list of a kit in library order, rotations ascending, duplicates merged."""
    variants: list[VariantInfo] = []
    by_module: list[tuple[int, ...]] = []
    representative: list[dict[int, int]] = []
    keyed = keyed_connectors(kit)
    for module_index, module in enumerate(kit.modules):
        seen: dict[tuple[object, ...], int] = {}
        mine: list[int] = []
        reps: dict[int, int] = {}
        for rotation in freedom_rotations(module.freedom):
            faces, terminator, authored = rotate_module(module, rotation)
            # Without geometry hashes the rotation itself is part of the key, so
            # nothing merges: a rotated asymmetric mesh is a different part.
            shape = module.geometry_hash[rotation] if module.geometry_hash is not None else rotation
            key: tuple[object, ...] = (signature(faces, keyed), terminator, shape)
            if key in seen:
                reps[rotation] = seen[key]
                continue
            index = len(variants)
            variants.append(VariantInfo(index, module_index, rotation, faces, terminator, authored))
            seen[key] = index
            reps[rotation] = index
            mine.append(index)
        by_module.append(tuple(mine))
        representative.append(reps)
    return Expansion(tuple(variants), tuple(by_module), tuple(representative))
