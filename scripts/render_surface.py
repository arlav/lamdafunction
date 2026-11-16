# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README's surface figure from ``examples/surface-pipes.blend``, headless.

Usage, from the repository root::

    blender -b examples/surface-pipes.blend --python scripts/render_surface.py -- docs/figures

Writes ``surface-pipes.png``: the pipes kit deformed onto the quad sphere, over
the envelope in dark grey so the far side does not show through. The
Materialise node group is saved in the file, so the add-on need not be
installed. The .blend is not saved: materials, lights and camera are set up
for the render only.
"""

import math
import sys

import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "docs/figures"
KIT = ["straight", "elbow", "tee", "cross", "cap", "empty"]

scene = bpy.context.scene

# The same copper as the box figures, on every module.
pipe = bpy.data.materials.new("pipe")
pipe.use_nodes = True
bsdf = pipe.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.80, 0.30, 0.08, 1)
bsdf.inputs["Metallic"].default_value = 0.6
bsdf.inputs["Roughness"].default_value = 0.35
for name in KIT:
    mesh = bpy.data.objects[name].data
    mesh.materials.clear()
    mesh.materials.append(pipe)

# Only the assembly renders (the object carrying the Materialise modifier; without the
# add-on there is no scene property naming it); the originals and the envelopes stay hidden.
for obj in bpy.data.objects:
    if obj.type == "MESH" and obj.name not in KIT:
        obj.hide_render = obj.modifiers.get("LamdaFunction Materialise") is None
bpy.data.collections["LamdaFunction Library"].hide_render = True
assembly = next(o for o in bpy.data.objects if o.modifiers.get("LamdaFunction Materialise") is not None)

# The envelope, matte and dark, behind the pipes.
sphere = bpy.data.objects["Quad Sphere"]
sphere.hide_render = False
sphere.display_type = "SOLID"
core = bpy.data.materials.new("core")
core.use_nodes = True
core_bsdf = core.node_tree.nodes["Principled BSDF"]
core_bsdf.inputs["Base Color"].default_value = (0.08, 0.08, 0.09, 1)
core_bsdf.inputs["Roughness"].default_value = 0.8
sphere.data.materials.clear()
sphere.data.materials.append(core)
for polygon in sphere.data.polygons:
    polygon.use_smooth = True

scene.render.engine = "BLENDER_EEVEE"
world = scene.world or bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.93, 0.93, 0.95, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0

sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 5
sun.rotation_euler = (math.radians(40), math.radians(10), math.radians(30))
scene.collection.objects.link(sun)
camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
camera.data.lens = 50
scene.collection.objects.link(camera)
scene.camera = camera

# Frame the deformed pipes: fit the camera to the evaluated mesh's bounding box.
# The fit reads the render resolution, so set it first.
scene.render.resolution_x, scene.render.resolution_y = 1200, 1100
bpy.context.view_layer.update()
evaluated = assembly.evaluated_get(bpy.context.evaluated_depsgraph_get())
corners = [assembly.matrix_world @ Vector(c) for c in evaluated.bound_box]
centre = sum(corners, Vector()) / 8
direction = Vector((1.0, -1.2, 0.8)).normalized()
camera.location = centre + direction * 60
camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()
coords = [c for p in corners for c in p]
location, _scale = camera.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), coords)
camera.location = location + (location - centre) * 0.05

scene.render.filepath = f"{OUT}/surface-pipes.png"
bpy.ops.render.render(write_still=True)
