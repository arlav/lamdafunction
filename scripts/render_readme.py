# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README figures from ``examples/2pipes.blend``, headless.

Usage, from the repository root (the extension must be installed so the
Materialise modifiers evaluate)::

    blender -b examples/2pipes.blend --python scripts/render_readme.py -- docs/figures

Writes ``2pipes-seeds.png`` (three solves side by side), ``2pipes-network.png``
(one solve close up) and ``2pipes-kit.png`` (the six modules). The .blend is
not saved: materials, lights and camera are set up for the render only.
"""

import math
import sys

import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "docs/figures"
KIT = ["straight", "elbow", "tee", "cross", "cap", "empty"]
ASSEMBLIES = ["LamdaFunction Assembly", "LamdaFunction Assembly.001", "LamdaFunction Assembly.002", "LamdaFunction Assembly.003"]
ALWAYS_HIDDEN = ["Cube", "LamdaFunction Envelope", "LamdaFunction Envelope.001"]

scene = bpy.context.scene

# One copper material on every module, so the instances share it.
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

scene.render.engine = "BLENDER_EEVEE"
world = scene.world or bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.93, 0.93, 0.95, 1)
light = bpy.data.objects["Light"]
light.data.type = "SUN"
light.data.energy = 5
light.rotation_euler = (math.radians(40), math.radians(10), math.radians(30))
camera = bpy.data.objects["Camera"]
camera.data.lens = 50


def hide(names):
    """Hide these objects in renders. Module objects stay visible to the instancer;
    hiding the library collection hides the originals without hiding their instances."""
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.name not in KIT:
            obj.hide_render = obj.name in names
    bpy.data.collections["LamdaFunction Library"].hide_render = "straight" in names


def box(x0, y0, z0, x1, y1, z1):
    return [Vector((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def shoot(points, direction, filename, resolution):
    """Aim the camera along ``direction`` at the points, fit them in frame with a margin, render."""
    scene.render.resolution_x, scene.render.resolution_y = resolution
    centre = sum(points, Vector()) / len(points)
    camera.location = centre + Vector(direction).normalized() * 60
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    coords = [c for p in points for c in p]
    location, _scale = camera.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), coords)
    camera.location = location + (location - centre) * 0.12
    scene.render.filepath = f"{OUT}/{filename}"
    bpy.ops.render.render(write_still=True)


def slots(name):
    """Bounds of a 7 x 7 x 5 assembly placed at its object's x."""
    x = bpy.data.objects[name].location.x
    return box(x - 0.5, -0.5, -0.5, x + 6.5, 6.5, 4.5)


# Three seeds side by side (Assembly.003 repeats the seed of Assembly, so it is left out).
hide(ALWAYS_HIDDEN + KIT + ["LamdaFunction Assembly"])
shoot([p for a in ASSEMBLIES[1:] for p in slots(a)], (0.35, -1, 0.7), "2pipes-seeds.png", (1800, 640))

hide(ALWAYS_HIDDEN + KIT + ASSEMBLIES[1:])
shoot(slots(ASSEMBLIES[0]), (1, -1.2, 0.9), "2pipes-network.png", (1200, 1000))

hide(ALWAYS_HIDDEN + ASSEMBLIES)
shoot(box(-0.6, -3.6, -0.6, 10.6, -2.4, 0.6), (0.15, -1, 0.8), "2pipes-kit.png", (1600, 420))
