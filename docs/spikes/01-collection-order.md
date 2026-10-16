# Spike 1: order of separated collection children

**Question.** The Materialise node group picks a module by `wfc_module` (its library index) with Collection Info set to Separate Children. In which order does Blender emit the children: link order, plain string order of the names, or something else? Does the planned naming scheme (a hidden instancer collection whose children are named `0000`, `0001`, …) give library order?

**Method.** `tests/blender/test_collection_order.py`, run headless. It links nine child collections into a library in the order `b, a, c, 10, 2, 9, 0012, 0003, 0001`, each holding a one-vertex mesh at x = link index. A Geometry Nodes modifier does Collection Info (Separate Children, Reset Children) → Store Named Attribute (instance domain, the instance index) → Realize Instances. Reading the realised points gives, for each instance index, the child it came from. The test then renames a child and re-evaluates.

Blender available here: 4.3.2 (the plan targets 5.2 LTS, which is not installed yet). The test is part of `tests/blender/run_all.py`, so it re-runs on 5.2 the moment that is available.

**Numbers.**

| Source | Order |
| --- | --- |
| Link order | b, a, c, 10, 2, 9, 0012, 0003, 0001 |
| `collection.children` (RNA) | b, a, c, 10, 2, 9, 0012, 0003, 0001 (link order) |
| Separate Children instances | 0001, 2, 0003, 9, 10, 0012, a, b, c |
| Python `sorted()` | 0001, 0003, 0012, 10, 2, 9, a, b, c |

Separate Children uses Blender's natural string order: runs of digits compare as numbers (so `2 < 0003 < 9 < 10 < 0012`), letters compare case-insensitively, digits before letters. It is not link order and not plain string order. Renaming a child re-sorts on the next evaluation.

**Decision.** The naming scheme stands: zero-padded index names (`0000`, `0001`, …) are in library order under natural order and under plain string order alike, so a future change of Blender's comparison would not break it. Two rules follow for the add-on:

- The instancer collection is hidden and its children are named only by the add-on, because any rename re-orders the instances. Rename the user's module collections freely; the instancer is regenerated from the library order on every compile.
- Never rely on `collection.children` matching the node's order; it is link order.

Open: confirm on 5.2 LTS (`blender -b --factory-startup --python-exit-code 1 --python tests/blender/run_all.py`).
