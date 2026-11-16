# SPDX-License-Identifier: GPL-3.0-or-later
"""The Materialise and Inspect node groups, and the hidden instancer collection.

Materialise has two placements. Rigid (the default) instances each module at
its slot, turned by ``wfc_rotation`` and scaled by ``wfc_scale``. **Deform to
cells** (surface assemblies) bends every module into its cell instead: the
recipe of spike 6 (``docs/spikes/06-trilinear-deformation.md``), where each
realised vertex moves to the trilinear blend of its cell's eight corners
``wfc_c0`` … ``wfc_c7``. Neighbours share the corners of their common face, so
connector geometry that matches on the unit cell matches after deformation.

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
    """A fresh modifier-capable geometry node tree, replacing one of the same name.

    Modifiers that used the old tree are pointed at the new one (``user_remap``)
    before it is removed, so rebuilding a group does not strip it from other
    assemblies. The new tree is created first, under a suffixed name, and takes
    the plain name once the old one is gone.
    """
    old = bpy.data.node_groups.get(name)
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.is_modifier = True
    if old is not None:
        old.user_remap(tree)
        bpy.data.node_groups.remove(old)
        tree.name = name
    return tree


def has_input(tree: bpy.types.GeometryNodeTree, socket_name: str) -> bool:
    """Whether a group has an input socket of this name (older files have older groups)."""
    return any(item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == socket_name for item in tree.interface.items_tree)


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


def _mix(tree: bpy.types.GeometryNodeTree, a: bpy.types.NodeSocket, b: bpy.types.NodeSocket, factor: bpy.types.NodeSocket, x: int, y: int) -> bpy.types.NodeSocket:
    """A vector Mix ``a + (b - a) * factor``, unclamped, so a factor outside 0..1 extrapolates."""
    node = tree.nodes.new("ShaderNodeMix")
    node.data_type = "VECTOR"
    node.factor_mode = "UNIFORM"
    node.clamp_factor = False
    node.location = (x, y)
    # Mix has one hidden socket per data type; the identifiers name the vector ones.
    tree.links.new(factor, _socket(node, "Factor", identifier="Factor_Float"))
    tree.links.new(a, _socket(node, "A", identifier="A_Vector"))
    tree.links.new(b, _socket(node, "B", identifier="B_Vector"))
    return _socket(node, "Result", identifier="Result_Vector", outputs=True)


def ensure_materialise_group() -> bpy.types.GeometryNodeTree:
    """Build (or rebuild) the Materialise group: instance the library on solved anchor slots.

    Inputs: Geometry (the assembly), Library (the instancer), Realize, Deform
    and Cell Size (the kit's cell, which Deform maps to the unit cube). Deform
    needs the corners ``wfc_c0`` … ``wfc_c7`` of a surface assembly, and its
    output is always realised.
    """
    tree = _new_tree(MATERIALISE)
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Library", in_out="INPUT", socket_type="NodeSocketCollection")
    realize_socket = tree.interface.new_socket("Realize", in_out="INPUT", socket_type="NodeSocketBool")
    realize_socket.default_value = False
    deform_socket = tree.interface.new_socket("Deform", in_out="INPUT", socket_type="NodeSocketBool")
    deform_socket.default_value = False
    cell_socket = tree.interface.new_socket("Cell Size", in_out="INPUT", socket_type="NodeSocketFloat")
    cell_socket.default_value = 1.0
    cell_socket.min_value = 1e-4
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    group_in = tree.nodes.new("NodeGroupInput")
    group_in.location = (-1300, 0)
    group_out = tree.nodes.new("NodeGroupOutput")
    group_out.location = (1900, 0)
    links = tree.links

    anchor = _named(tree, "wfc_anchor", "BOOLEAN", -900, 200)
    state = _named(tree, "wfc_state", "INT", -900, 50)
    solved = _compare_int(tree, 0, "EQUAL", -700, 50)
    both = tree.nodes.new("FunctionNodeBooleanMath")
    both.operation = "AND"
    both.location = (-500, 150)
    module = _named(tree, "wfc_module", "INT", -900, -150)
    rotation = _named(tree, "wfc_rotation", "QUATERNION", -900, -300)

    # Deform must be a single value: it drives geometry switches, and the Exists
    # output of a Named Attribute is a field, so the group cannot test for the
    # corners itself. The operator turns Deform on only for an assembly that has
    # them; on one without, the corners read as zero and every module collapses.
    deform_on = group_in.outputs["Deform"]

    # Deforming starts from modules at the origin: move the points there first.
    # An unlinked Position input means "the point's own position" (an implicit
    # field), so the origin must come from a linked zero vector (spike 6).
    zero = tree.nodes.new("FunctionNodeInputVector")
    zero.vector = (0.0, 0.0, 0.0)
    zero.location = (-700, 400)
    to_origin = tree.nodes.new("GeometryNodeSetPosition")
    to_origin.location = (-300, 300)
    links.new(group_in.outputs["Geometry"], to_origin.inputs["Geometry"])
    links.new(deform_on, to_origin.inputs["Selection"])
    links.new(zero.outputs["Vector"], to_origin.inputs["Position"])

    # Scale: rigid uses wfc_scale on a surface (a grid assembly has none: fall back
    # to 1). Deform shrinks the kit's cell to the unit cube, so the realised
    # positions are the unit-cell coordinates the blend reads (position + 0.5).
    scale = _named(tree, "wfc_scale", "FLOAT_VECTOR", -900, -600)
    scale_or_one = tree.nodes.new("GeometryNodeSwitch")
    scale_or_one.input_type = "VECTOR"
    _socket(scale_or_one, "False", "VECTOR").default_value = (1.0, 1.0, 1.0)
    scale_or_one.location = (-600, -650)
    unit = tree.nodes.new("ShaderNodeMath")
    unit.operation = "DIVIDE"
    unit.inputs[0].default_value = 1.0
    unit.location = (-600, -500)
    links.new(group_in.outputs["Cell Size"], unit.inputs[1])
    pick_scale = tree.nodes.new("GeometryNodeSwitch")
    pick_scale.input_type = "VECTOR"
    pick_scale.location = (-350, -600)

    info = tree.nodes.new("GeometryNodeCollectionInfo")
    info.transform_space = "ORIGINAL"
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    info.location = (-700, -350)
    instance = tree.nodes.new("GeometryNodeInstanceOnPoints")
    instance.inputs["Pick Instance"].default_value = True
    instance.location = (0, 0)
    realize = tree.nodes.new("GeometryNodeRealizeInstances")
    realize.location = (250, -200)
    realize_switch = tree.nodes.new("GeometryNodeSwitch")
    realize_switch.input_type = "GEOMETRY"
    realize_switch.location = (500, 0)

    links.new(_socket(state, "Attribute", "INT", outputs=True), _socket(solved, "A", "INT", "A_INT"))
    links.new(_socket(anchor, "Attribute", "BOOLEAN", outputs=True), both.inputs[0])
    links.new(solved.outputs["Result"], both.inputs[1])
    links.new(to_origin.outputs["Geometry"], instance.inputs["Points"])
    links.new(both.outputs["Boolean"], instance.inputs["Selection"])
    links.new(group_in.outputs["Library"], info.inputs["Collection"])
    links.new(info.outputs["Instances"], instance.inputs["Instance"])
    links.new(_socket(module, "Attribute", "INT", outputs=True), instance.inputs["Instance Index"])
    # Rotation: rigid placement turns by wfc_rotation, the cell's frame times the
    # variant's turn. Deform works in the cell's own unit cube, where only the
    # variant's turn applies, so it undoes the frame: inverse(wfc_frame) * wfc_rotation.
    frame = _named(tree, "wfc_frame", "QUATERNION", -900, -450)
    unframe = tree.nodes.new("FunctionNodeInvertRotation")
    unframe.location = (-700, -450)
    links.new(_socket(frame, "Attribute", "ROTATION", outputs=True), unframe.inputs[0])
    turn = tree.nodes.new("FunctionNodeRotateRotation")
    turn.rotation_space = "GLOBAL"  # Rotate By applied after Rotation: inverse(frame) * rotation
    turn.location = (-500, -400)
    links.new(_socket(rotation, "Attribute", "ROTATION", outputs=True), _socket(turn, "Rotation"))
    links.new(unframe.outputs[0], _socket(turn, "Rotate By"))
    pick_rotation = tree.nodes.new("GeometryNodeSwitch")
    pick_rotation.input_type = "ROTATION"
    pick_rotation.location = (-300, -300)
    links.new(deform_on, _socket(pick_rotation, "Switch"))
    links.new(_socket(rotation, "Attribute", "ROTATION", outputs=True), _socket(pick_rotation, "False", "ROTATION"))
    links.new(turn.outputs[0], _socket(pick_rotation, "True", "ROTATION"))
    links.new(_socket(pick_rotation, "Output", outputs=True), instance.inputs["Rotation"])
    links.new(_socket(scale, "Exists", outputs=True), _socket(scale_or_one, "Switch"))
    links.new(_socket(scale, "Attribute", "VECTOR", outputs=True), _socket(scale_or_one, "True", "VECTOR"))
    links.new(deform_on, _socket(pick_scale, "Switch"))
    links.new(_socket(scale_or_one, "Output", outputs=True), _socket(pick_scale, "False", "VECTOR"))
    links.new(unit.outputs["Value"], _socket(pick_scale, "True", "VECTOR"))  # a float becomes (s, s, s)
    links.new(_socket(pick_scale, "Output", outputs=True), instance.inputs["Scale"])
    links.new(instance.outputs["Instances"], realize.inputs["Geometry"])
    links.new(group_in.outputs["Realize"], _socket(realize_switch, "Switch"))
    links.new(instance.outputs["Instances"], _socket(realize_switch, "False", "GEOMETRY"))
    links.new(realize.outputs["Geometry"], _socket(realize_switch, "True", "GEOMETRY"))

    # Trilinear blend. Realize Instances copies the assembly's point attributes to
    # every realised vertex, so each vertex reads its own cell's corners. With
    # (u, v, w) = position + 0.5 in the unit cube, corner x + 2y + 4z:
    # mix(mix(c0, c1, u), mix(c2, c3, u), v) on the bottom, the same on the top
    # with c4 ... c7, then mix(bottom, top, w).
    position = tree.nodes.new("GeometryNodeInputPosition")
    position.location = (250, -700)
    offset = tree.nodes.new("ShaderNodeVectorMath")
    offset.operation = "ADD"
    offset.inputs[1].default_value = (0.5, 0.5, 0.5)
    offset.location = (450, -700)
    links.new(position.outputs["Position"], offset.inputs[0])
    uvw = tree.nodes.new("ShaderNodeSeparateXYZ")
    uvw.location = (650, -700)
    links.new(offset.outputs["Vector"], uvw.inputs["Vector"])
    u, v, w = uvw.outputs["X"], uvw.outputs["Y"], uvw.outputs["Z"]
    c = [_socket(_named(tree, f"wfc_c{i}", "FLOAT_VECTOR", 650, -900 - 150 * i), "Attribute", "VECTOR", outputs=True) for i in range(8)]
    bottom = _mix(tree, _mix(tree, c[0], c[1], u, 900, -800), _mix(tree, c[2], c[3], u, 900, -1000), v, 1100, -900)
    top = _mix(tree, _mix(tree, c[4], c[5], u, 900, -1200), _mix(tree, c[6], c[7], u, 900, -1400), v, 1100, -1300)
    blended = _mix(tree, bottom, top, w, 1300, -1100)
    to_cell = tree.nodes.new("GeometryNodeSetPosition")
    to_cell.location = (1300, -300)
    links.new(realize.outputs["Geometry"], to_cell.inputs["Geometry"])
    links.new(blended, to_cell.inputs["Position"])

    # The corners have done their job; drop them so the realised mesh does not
    # carry 96 bytes of corners per vertex.
    tail = to_cell.outputs["Geometry"]
    for i in range(8):
        remove = tree.nodes.new("GeometryNodeRemoveAttribute")
        remove.location = (1500, -300 - 100 * i)
        remove.inputs["Name"].default_value = f"wfc_c{i}"
        links.new(tail, remove.inputs["Geometry"])
        tail = remove.outputs["Geometry"]

    deform_switch = tree.nodes.new("GeometryNodeSwitch")
    deform_switch.input_type = "GEOMETRY"
    deform_switch.location = (1700, 0)
    links.new(deform_on, _socket(deform_switch, "Switch"))
    links.new(_socket(realize_switch, "Output", outputs=True), _socket(deform_switch, "False", "GEOMETRY"))
    links.new(tail, _socket(deform_switch, "True", "GEOMETRY"))
    links.new(_socket(deform_switch, "Output", outputs=True), group_out.inputs["Geometry"])
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


def input_identifier(tree: bpy.types.GeometryNodeTree, socket_name: str) -> str:
    """The socket identifier (``Socket_3``) of a group input, from its interface name."""
    for item in tree.interface.items_tree:
        if item.item_type == "SOCKET" and item.in_out == "INPUT" and item.name == socket_name:
            return item.identifier
    raise KeyError(socket_name)


def set_group_input(modifier: bpy.types.NodesModifier, socket_name: str, value: object) -> None:
    """Set a group input of a Geometry Nodes modifier by its interface name.

    Blender 5.0 moved modifier inputs from ID properties (``modifier["Socket_3"]``)
    to RNA structs: ``modifier.properties.inputs.Socket_3.value``, with an
    ``attribute_name`` next to it for attribute-driven inputs. Item assignment
    raises "id properties not supported for this type" there, and the new
    attribute does not exist before 5.0, so both paths are kept while 4.x is
    used for development.
    """
    identifier = input_identifier(modifier.node_group, socket_name)
    properties = getattr(modifier, "properties", None)
    if properties is not None:
        getattr(properties.inputs, identifier).value = value
    else:
        modifier[identifier] = value

