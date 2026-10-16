# SPDX-License-Identifier: GPL-3.0-or-later
"""The rule compiler: kits and envelopes in, a compiled assembly out.

Nothing in this package imports ``bpy``; ``addon/bl/`` reads Blender data
into the records of ``addon/model/`` and hands them here. Modules, in reading
order:

- :mod:`rotations`: the 24 proper rotations of the cube as integer matrices,
  with a fixed numbering shared with ``lamda-core`` (``variants[i].rotation``).
- :mod:`frames`: the fixed in-plane frame of each face, connector arrows,
  how a rotation moves faces and arrows, and the twist between two touching
  faces (spike 2, ``docs/spikes/02-face-frames-twist.md``).
- :mod:`variants`: modules × rotations, deduplicated into solver variants.
- :mod:`rules`: connector pairs, Indifference and explicit rules into
  symmetric allowed pairs per port.
- :mod:`graph`: the slot graph of an envelope in CSR form, and its exposed ports.
- :mod:`domains`: per-slot domains, weights, priorities and boundary classes.
- :mod:`audit`: what the author should know before solving.
- :mod:`assemble`: :func:`assemble.compile_assembly`, the entry point that
  writes the compiled assembly of ``schema/compiled-assembly-v1.json``.
"""
