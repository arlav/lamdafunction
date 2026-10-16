# SPDX-License-Identifier: GPL-3.0-or-later
"""Spike 1: in which order does Collection Info with Separate Children emit instances?

The Materialise node group picks an instance by ``wfc_module`` (the library
index). That only works if separated children come out in a known order. This
script links child collections in an order that differs from their name order,
instances them through Collection Info (Separate Children, Reset Children),
stamps each instance with its index, realises, and reads the index back next
to a vertex that identifies the child. It then states which order Blender used.

Runs headless: ``blender -b --factory-startup --python-exit-code 1 --python
tests/blender/test_collection_order.py``.
"""

from __future__ import annotations

import re

import bpy

# Link order on purpose not alphabetical, with the traps we care about: plain
# names ("b" before "a"), unpadded numbers ("10" versus "2") and padded ones
# ("0012" versus "10"). Plain string order and natural order disagree on them,
# so the result tells the two apart. No two names are numerically equal.
LINK_ORDER = ["b", "a", "c", "10", "2", "9", "0012", "0003", "0001"]


def natural_key(name: str) -> list[tuple[int, int | str]]:
    """Sort key for Blender's natural name order: digit runs compare as numbers, letters case-insensitively."""
    key: list[tuple[int, int | str]] = []
    for part in re.split(r"(\d+)", name):
        if part.isdigit():
            key.append((0, int(part)))
        elif part:
            key.append((1, part.lower()))
    return key


def build_library() -> bpy.types.Collection:
    """A library collection with one child collection per name, each holding a one-vertex mesh at x = link index."""
    library = bpy.data.collections.new("LamdaFunction Library")
    bpy.context.scene.collection.children.link(library)
    for link_index, name in enumerate(LINK_ORDER):
        child = bpy.data.collections.new(name)
        library.children.link(child)
        mesh = bpy.data.meshes.new(name)
        # One vertex at x = link index: after Realize Instances, the x
        # coordinate tells us which child a point came from.
        mesh.from_pydata([(float(link_index), 0.0, 0.0)], [], [])
        obj = bpy.data.objects.new(name, mesh)
        child.objects.link(obj)
    return library


def value_input(node: bpy.types.Node, socket_type: str) -> bpy.types.NodeSocket:
    """The enabled "Value" input of the given socket type.

    Blender 4.3 gives Store Named Attribute one "Value" socket that changes
    type with ``data_type``; older versions kept one hidden socket per type
    (identifiers ``Value_Int`` and so on), so match on type, not identifier.
    """
    for socket in node.inputs:
        if socket.name == "Value" and socket.enabled and socket.type == socket_type:
            return socket
    raise KeyError(f"no enabled Value input of type {socket_type}")


def build_host(library: bpy.types.Collection) -> bpy.types.Object:
    """A mesh object whose Geometry Nodes modifier realises the separated children with their instance index stored."""
    tree = bpy.data.node_groups.new("spike1", "GeometryNodeTree")
    tree.is_modifier = True
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    output = tree.nodes.new("NodeGroupOutput")
    info = tree.nodes.new("GeometryNodeCollectionInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Collection"].default_value = library
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    index = tree.nodes.new("GeometryNodeInputIndex")
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "INT"
    store.domain = "INSTANCE"
    store.inputs["Name"].default_value = "inst_index"
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    tree.links.new(info.outputs["Instances"], store.inputs["Geometry"])
    tree.links.new(index.outputs["Index"], value_input(store, "INT"))
    tree.links.new(store.outputs["Geometry"], realize.inputs["Geometry"])
    tree.links.new(realize.outputs["Geometry"], output.inputs["Geometry"])

    mesh = bpy.data.meshes.new("host")
    host = bpy.data.objects.new("host", mesh)
    bpy.context.scene.collection.objects.link(host)
    modifier = host.modifiers.new("spike1", "NODES")
    modifier.node_group = tree
    return host


def instance_order(host: bpy.types.Object) -> list[str]:
    """Child names in instance-index order, read from the evaluated mesh."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = host.evaluated_get(depsgraph)
    mesh = evaluated.data
    by_index: dict[int, str] = {}
    values = mesh.attributes["inst_index"].data
    for vertex in mesh.vertices:
        link_index = round(vertex.co.x)
        by_index[values[vertex.index].value] = LINK_ORDER[link_index]
    assert len(by_index) == len(LINK_ORDER), by_index
    return [by_index[i] for i in range(len(LINK_ORDER))]


def main() -> None:
    """Build the scene, evaluate, report, and assert the documented behaviour."""
    library = build_library()
    host = build_host(library)
    rna_order = [child.name for child in library.children]
    gn_order = instance_order(host)
    print("blender", bpy.app.version_string)
    print("link order:  ", LINK_ORDER)
    print("children RNA:", rna_order)
    print("GN instances:", gn_order)
    print("natural:     ", sorted(LINK_ORDER, key=natural_key))
    print("plain sorted:", sorted(LINK_ORDER))

    # Finding: separated children come out in *natural* name order (digit runs
    # compared as numbers, case-insensitive), not link order and not plain
    # string order. collection.children (RNA) keeps link order.
    assert gn_order == sorted(LINK_ORDER, key=natural_key), "Separate Children did not use natural name order"
    assert gn_order != sorted(LINK_ORDER), "names chosen for this test no longer separate natural from plain order"
    assert rna_order == LINK_ORDER, "collection.children is no longer link order"

    # Renaming re-sorts on the next evaluation: instancer collection names
    # must be stable once compiled, so rename only through the add-on.
    bpy.data.collections["c"].name = "0000"
    LINK_ORDER[LINK_ORDER.index("c")] = "0000"
    host.update_tag()
    bpy.context.view_layer.update()
    assert instance_order(host) == sorted(LINK_ORDER, key=natural_key), "rename did not re-sort"

    # Zero-padded index names give library order under natural order and
    # under plain string order alike, so the design survives either.
    padded = [f"{i:04d}" for i in range(12)]
    assert sorted(padded, key=natural_key) == padded == sorted(padded)
    print("spike 1: Separate Children uses natural name order; zero-padded index names give library order")


if __name__ == "__main__":
    main()
