# SPDX-License-Identifier: GPL-3.0-or-later
"""The Materialise and Inspect node groups, and the hidden instancer collection.

Python add-ons cannot define new Geometry Nodes nodes, so both groups are
built from existing nodes by script, here, and can be rebuilt at any time
(``.claude/rules/blender-addon.md``, "Node groups"). The instancer is a
hidden collection whose children are named ``0000``, ``0001``, … in library
order and link the objects of each module collection; Collection Info with
Separate Children orders children by natural name order (spike 1), so the
instance index equals ``wfc_module``.
"""

from __future__ import annotations

import bpy

MATERIALISE = "LamdaFunction Materialise"
INSPECT = "LamdaFunction Inspect"
INSTANCER = "LamdaFunction Instancer"


def _socket(node: bpy.types.Node, name: str, socket_type: str | None = None, identifier: str | None = None, outputs: bool = False) -> bpy.types.NodeSocket:
    """Find a node socket by identifier, else by name and (optionally) type among enabled sockets.

    Blender versions differ in whether a node has one dynamic socket per
    role or one hidden socket per type, so both are tried.
    """
    sockets = node.outputs if outputs else node.inputs
    if identifier is not None:
        for s in sockets:
            if s.identifier == identifier:
                return s
    for s in sockets:
        if s.name == name and s.enabled and (socket_type is None or s.type == socket_type):
            return s
    for s in sockets:
        if s.name == name:
            return s
    raise KeyError(f"{node.bl_idname} has no socket {name!r}")


def _new_tree(name: str) -> bpy.types.GeometryNodeTree:
    """A fresh modifier-capable geometry node tree, replacing one of the same name."""
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.is_modifier = True
    return tree


def _named(tree: bpy.types.GeometryNodeTree, attribute: str, data_type: str, x: int, y: int) -> bpy.types.Node:
    """An Input Named Attribute node."""
    node = tree.nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = data_type
    node.inputs["Name"].default_value = attribute
    node.location = (x, y)
    return node


def _compare_int(tree: bpy.types.GeometryNodeTree, value: int, operation: str, x: int, y: int) -> bpy.types.Node:
    """A Compare node on integers against a constant."""
    node = tree.nodes.new("FunctionNodeCompare")
    node.data_type = "INT"
    node.operation = operation
    _socket(node, "B", "INT", "B_INT").default_value = value
    node.location = (x, y)
    return node


def ensure_materialise_group() -> bpy.types.GeometryNodeTree:
    """Build (or rebuild) the Materialise group: instance the library on solved anchor slots."""
    tree = _new_tree(MATERIALISE)
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Library", in_out="INPUT", socket_type="NodeSocketCollection")
    realize_socket = tree.interface.new_socket("Realize", in_out="INPUT", socket_type="NodeSocketBool")
    realize_socket.default_value = False
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-900, 0)
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (700, 0)

    anchor = _named(tree, "wfc_anchor", "BOOLEAN", -600, 200)
    state = _named(tree, "wfc_state", "INT", -600, 50)
    solved = _compare_int(tree, 0, "EQUAL", -400, 50)
    both = tree.nodes.new("FunctionNodeBooleanMath")
    both.operation = "AND"
    both.location = (-200, 150)
    module = _named(tree, "wfc_module", "INT", -600, -150)
    rotation = _named(tree, "wfc_rotation", "QUATERNION", -600, -300)
    info = tree.nodes.new("GeometryNodeCollectionInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    info.location = (-400, -450)
    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    instance.inputs["Pick Instance"].default_value = True
    instance.location = (100, 0)
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    realize.location = (300, -200)
    switch = tree.nodes.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    switch.location = (500, 0)

    links = tree.links
    links.new(_socket(state, "Attribute", "INT", outputs=True), _socket(solved, "A", "INT", "A_INT"))
    links.new(_socket(anchor, "Attribute", "BOOLEAN", outputs=True), both.inputs[0])
    links.new(solved.outputs["Result"], both.inputs[1])
    links.new(group_in.outputs["Geometry"], instance.inputs["Points"])
    links.new(both.outputs["Boolean"], instance.inputs["Selection"])
    links.new(group_in.outputs["Library"], info.inputs["Collection"])
    links.new(info.outputs["Instances"], instance.inputs["Instance"])
    links.new(_socket(module, "Attribute", "INT", outputs=True), instance.inputs["Instance Index"])
    links.new(_socket(rotation, "Attribute", "ROTATION", outputs=True), instance.inputs["Rotation"])
    links.new(instance.outputs["Instances"], realize.inputs["Geometry"])
    links.new(group_in.outputs["Realize"], _socket(switch, "Switch"))
    links.new(instance.outputs["Instances"], _socket(switch, "False", "GEOMETRY"))
    links.new(realize.outputs["Geometry"], _socket(switch, "True", "GEOMETRY"))
    links.new(_socket(switch, "Output", outputs=True), group_out.inputs["Geometry"])
    return tree


def ensure_inspect_group(cell_size: float) -> bpy.types.GeometryNodeTree:
    """Build (or rebuild) the Inspect group: a coloured cube per slot, hidden past the current frame in playback."""
    tree = _new_tree(INSPECT)
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    playback = tree.interface.new_socket("Playback", in_out="INPUT", socket_type="NodeSocketBool")
    playback.default_value = False
    size = tree.interface.new_socket("Size", in_out="INPUT", socket_type="NodeSocketFloat")
    size.default_value = cell_size * 0.3
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-1100, 0)
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (900, 0)

    state = _named(tree, "wfc_state", "INT", -900, 300)
    is_contra = _compare_int(tree, 2, "EQUAL", -700, 400)
    is_solved = _compare_int(tree, 0, "EQUAL", -700, 250)
    pick_solved = tree.nodes.new("GeometryNodeSwitch")
    pick_solved.input_type = "RGBA"
    pick_solved.location = (-500, 250)
    _socket(pick_solved, "False", "RGBA").default_value = (0.5, 0.5, 0.5, 1.0)
    _socket(pick_solved, "True", "RGBA").default_value = (0.1, 0.8, 0.2, 1.0)
    pick_contra = tree.nodes.new("GeometryNodeSwitch")
    pick_contra.input_type = "RGBA"
    pick_contra.location = (-300, 300)
    _socket(pick_contra, "True", "RGBA").default_value = (0.9, 0.1, 0.1, 1.0)
    store = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT_COLOR"
    store.domain = "POINT"
    store.inputs["Name"].default_value = "wfc_color"
    store.location = (-100, 100)

    step = _named(tree, "wfc_step", "INT", -900, -200)
    time = tree.nodes.new("GeometryNodeInputSceneTime")
    time.location = (-900, -350)
    later = tree.nodes.new("FunctionNodeCompare")
    later.data_type = "FLOAT"
    later.operation = "GREATER_THAN"
    later.location = (-700, -250)
    hide = tree.nodes.new("FunctionNodeBooleanMath")
    hide.operation = "AND"
    hide.location = (-500, -250)
    delete = tree.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    delete.location = (100, 0)
    cube = tree.nodes.new("GeometryNodeMeshCube")
    cube.location = (100, -300)
    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (400, 0)
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    realize.location = (650, 0)

    links = tree.links
    state_out = _socket(state, "Attribute", "INT", outputs=True)
    links.new(state_out, _socket(is_contra, "A", "INT", "A_INT"))
    links.new(state_out, _socket(is_solved, "A", "INT", "A_INT"))
    links.new(is_solved.outputs["Result"], _socket(pick_solved, "Switch"))
    links.new(is_contra.outputs["Result"], _socket(pick_contra, "Switch"))
    links.new(_socket(pick_solved, "Output", outputs=True), _socket(pick_contra, "False", "RGBA"))
    links.new(group_in.outputs["Geometry"], store.inputs["Geometry"])
    links.new(_socket(pick_contra, "Output", outputs=True), _socket(store, "Value", "RGBA", "Value_Color"))
    links.new(_socket(step, "Attribute", "INT", outputs=True), _socket(later, "A", "VALUE", "A"))
    links.new(time.outputs["Frame"], _socket(later, "B", "VALUE", "B"))
    links.new(group_in.outputs["Playback"], hide.inputs[0])
    links.new(later.outputs["Result"], hide.inputs[1])
    links.new(store.outputs["Geometry"], delete.inputs["Geometry"])
    links.new(hide.outputs["Boolean"], delete.inputs["Selection"])
    links.new(group_in.outputs["Size"], cube.inputs["Size"])
    links.new(delete.outputs["Geometry"], instance.inputs["Points"])
    links.new(cube.outputs["Mesh"], instance.inputs["Instance"])
    links.new(instance.outputs["Instances"], realize.inputs["Geometry"])
    links.new(realize.outputs["Geometry"], group_out.inputs["Geometry"])
    return tree


def ensure_instancer(scene: bpy.types.Scene, library: bpy.types.Collection) -> bpy.types.Collection:
    """Rebuild the hidden instancer collection: child ``0000``… per module, linking the module's objects."""
    instancer = bpy.data.collections.get(INSTANCER)
    if instancer is None:
        instancer = bpy.data.collections.new(INSTANCER)
    for child in list(instancer.children):
        instancer.children.unlink(child)
        if child.users == 0:
            bpy.data.collections.remove(child)
    for index, module in enumerate(library.children):
        child = bpy.data.collections.new(f"{index:04d}")
        child.instance_offset = module.instance_offset
        for obj in module.objects:
            child.objects.link(obj)
        for nested in module.children:
            child.children.link(nested)
        instancer.children.link(child)
    if instancer.name not in scene.collection.children:
        scene.collection.children.link(instancer)
    layer = _find_layer_collection(scene.view_layers[0].layer_collection, instancer.name)
    if layer is not None:
        layer.exclude = True
    instancer.hide_render = True
    return instancer


def _find_layer_collection(root: bpy.types.LayerCollection, name: str) -> bpy.types.LayerCollection | None:
    """Depth-first search of the view layer tree."""
    if root.collection.name == name:
        return root
    for child in root.children:
        found = _find_layer_collection(child, name)
        if found is not None:
            return found
    return None


def attach_group(obj: bpy.types.Object, tree: bpy.types.GeometryNodeTree, name: str) -> bpy.types.NodesModifier:
    """A Geometry Nodes modifier named ``name`` on ``obj`` using ``tree``, created if missing."""
    modifier = obj.modifiers.get(name)
    if modifier is None or modifier.type != "NODES":
        modifier = obj.modifiers.new(name, "NODES")
    modifier.node_group = tree
    return modifier


def set_group_input(modifier: bpy.types.NodesModifier, socket_name: str, value: object) -> None:
    """Set a group input of a Geometry Nodes modifier by its interface name."""
    for item in modifier.node_group.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == socket_name:
            modifier[item.identifier] = value
            return
    raise KeyError(socket_name)
