# SPDX-License-Identifier: GPL-3.0-or-later
"""Read the library collection into a :class:`Kit`, and create modules.

The library is a collection whose child collections, in link order, are the
modules; the library index of a module is its position there. Each child
collection carries :class:`ModuleProps`. The module's geometry is every mesh
object inside the child collection, placed relative to the collection's
instance offset, which is where the Materialise node group instances it.
"""

from __future__ import annotations

import hashlib

import bpy
from mathutils import Vector

from ..compile.rotations import ROTATIONS
from ..model.kit import Connector, ConnectorPair, Kit, Module, Rule
from .properties import SceneProps

LIBRARY_NAME = "LamdaFunction Library"
ROUNDING = 3


def module_collections(library: bpy.types.Collection) -> list[bpy.types.Collection]:
    """The modules in library order (``collection.children`` is link order, spike 1)."""
    return list(library.children)


def module_points(collection: bpy.types.Collection, depsgraph: bpy.types.Depsgraph) -> list[tuple[float, float, float]]:
    """All mesh vertices of the module in collection space, rounded, as a sorted list.

    Evaluated objects are used because ``matrix_world`` and modifier results
    of freshly edited objects are only current after a depsgraph update.
    """
    offset = Vector(collection.instance_offset)
    points: list[tuple[float, float, float]] = []
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        matrix = evaluated.matrix_world
        for vertex in evaluated.data.vertices:
            world = matrix @ vertex.co - offset
            points.append((round(world.x, ROUNDING), round(world.y, ROUNDING), round(world.z, ROUNDING)))
    points.sort()
    return points


def geometry_hashes(collection: bpy.types.Collection, depsgraph: bpy.types.Depsgraph) -> tuple[str, ...]:
    """One hash per rotation index of the module's rotated point cloud.

    Two rotations with equal hashes rotate the mesh onto itself (to three
    decimals), so the compiler may merge them when their faces agree too.
    """
    points = module_points(collection, depsgraph)
    hashes: list[str] = []
    for matrix in ROTATIONS:
        rotated = sorted(
            (
                round(matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z, ROUNDING) + 0.0,
                round(matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z, ROUNDING) + 0.0,
                round(matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z, ROUNDING) + 0.0,
            )
            for x, y, z in points
        )
        hashes.append(hashlib.sha1(repr(rotated).encode("utf-8")).hexdigest())
    return tuple(hashes)


def read_module(collection: bpy.types.Collection, depsgraph: bpy.types.Depsgraph) -> Module:
    """A :class:`Module` from a library child collection."""
    props = collection.lamdafunction
    faces = tuple(Connector(getattr(props, f"connector_{i}").strip(), getattr(props, f"offset_{i}")) for i in range(6))
    terminator = tuple(bool(getattr(props, f"terminator_{i}")) for i in range(6))
    tags = tuple(t.strip() for t in props.tags.split(",") if t.strip())
    return Module(
        name=collection.name,
        freedom=props.freedom,
        weight=props.weight,
        tags=tags,
        faces=faces,
        terminator=terminator,
        geometry_hash=geometry_hashes(collection, depsgraph),
    )


def read_kit(scene_props: SceneProps, depsgraph: bpy.types.Depsgraph) -> Kit:
    """The whole kit: modules from the library, pairs and rules from the scene."""
    library = scene_props.library
    if library is None:
        raise ValueError("no library collection is set")
    kit = Kit(
        modules=[read_module(c, depsgraph) for c in module_collections(library)],
        pairs=[
            ConnectorPair(p.a.strip(), p.b.strip(), (p.t0, p.t1, p.t2, p.t3))
            for p in scene_props.pairs
            if p.a.strip() and p.b.strip()
        ],
        rules=[Rule.parse(r.kind, r.text) for r in scene_props.rules if r.text.strip()],
        indifference=scene_props.indifference,
    )
    kit.check()
    return kit


def ensure_library(scene: bpy.types.Scene) -> bpy.types.Collection:
    """The scene's library collection, created and linked if missing."""
    props = scene.lamdafunction
    if props.library is None:
        library = bpy.data.collections.get(LIBRARY_NAME) or bpy.data.collections.new(LIBRARY_NAME)
        if library.name not in scene.collection.children:
            scene.collection.children.link(library)
        props.library = library
    return props.library


def add_module(scene: bpy.types.Scene, name: str, cell_size: float, with_cube: bool = True) -> bpy.types.Collection:
    """A new module collection in the library, optionally holding a cube of ``cell_size``."""
    library = ensure_library(scene)
    collection = bpy.data.collections.new(name)
    library.children.link(collection)
    if with_cube:
        mesh = bpy.data.meshes.new(name)
        h = cell_size / 2.0
        verts = [(x, y, z) for x in (-h, h) for y in (-h, h) for z in (-h, h)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        mesh.from_pydata(verts, [], faces)
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
    return collection
