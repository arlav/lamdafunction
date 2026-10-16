# SPDX-License-Identifier: GPL-3.0-or-later
"""Kits: modules with connectors on their faces, connector pairs, and rules.

Vocabulary (``CLAUDE.md``, "Glossary"): a *module* is an authored part; a
*connector* is a name on one of its six faces plus a quarter-turn offset; a
*connector pair* says two names may touch and at which twists; a *rule*
names two module faces directly. Face indices follow
``addon/compile/frames.py``: 0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z.

Phase 1 covers single-cell modules; the footprint fields for multi-cell
modules arrive in phase 2 without changing what is here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FACE_NAMES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
"""Face names in face-index order."""

FREEDOMS = ("NONE", "X", "Y", "Z", "FULL")
"""Rotational freedom values: identity only; four turns about one axis; all 24."""

RULE_KINDS = ("allowed", "disallowed", "exclusive")
"""What a rule does to the pairs its two faces can form."""


@dataclass(frozen=True)
class Connector:
    """A connector on one face: a name and a quarter-turn offset.

    An empty name means the face is *uncovered*: it carries nothing, and
    under Indifference it may meet any other uncovered face. ``offset`` turns
    the connector's arrow ``offset`` quarter turns counter-clockwise about the
    outward normal, starting from the face's reference axis
    (``docs/spikes/02-face-frames-twist.md``).
    """

    name: str = ""
    offset: int = 0

    @property
    def covered(self) -> bool:
        """True when the face carries a connector."""
        return self.name != ""


UNCOVERED = Connector()
"""The connector of a face that carries nothing."""


@dataclass(frozen=True)
class Module:
    """An authored part: name, rotational freedom, weight, tags, six faces.

    ``faces`` holds one :class:`Connector` per face index. ``terminator`` flags
    faces that may sit at the envelope boundary even when the boundary policy
    asks for a connector. ``geometry_hash`` optionally gives, per rotation
    index (24 entries), a hash of the rotated geometry: two rotations with the
    same face signature *and* the same hash are one variant. Without hashes no
    two rotations are merged, which is always safe.
    """

    name: str
    freedom: str = "NONE"
    weight: float = 1.0
    tags: tuple[str, ...] = ()
    faces: tuple[Connector, ...] = (UNCOVERED,) * 6
    terminator: tuple[bool, ...] = (False,) * 6
    geometry_hash: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Reject malformed modules early, with the field named."""
        if self.freedom not in FREEDOMS:
            raise ValueError(f"module {self.name!r}: freedom {self.freedom!r} is not one of {FREEDOMS}")
        if len(self.faces) != 6:
            raise ValueError(f"module {self.name!r}: needs 6 faces, has {len(self.faces)}")
        if len(self.terminator) != 6:
            raise ValueError(f"module {self.name!r}: needs 6 terminator flags, has {len(self.terminator)}")
        if self.geometry_hash is not None and len(self.geometry_hash) != 24:
            raise ValueError(f"module {self.name!r}: geometry_hash needs 24 entries, has {len(self.geometry_hash)}")
        for face, connector in enumerate(self.faces):
            if not 0 <= connector.offset <= 3:
                raise ValueError(f"module {self.name!r} face {FACE_NAMES[face]}: offset {connector.offset} is not 0 to 3")


@dataclass(frozen=True)
class ConnectorPair:
    """Two connector names that may touch, and at which twists (0, 90, 180, 270 degrees).

    Pairs are unordered: declaring ``("a", "b")`` also allows ``("b", "a")``,
    because the twist of a touch is the same from both sides. A name touches
    itself only when a pair ``("a", "a")`` is declared.
    """

    a: str
    b: str
    twists: tuple[bool, bool, bool, bool] = (True, True, True, True)

    def __post_init__(self) -> None:
        """Both names must be non-empty: uncovered faces are governed by Indifference, not pairs."""
        if not self.a or not self.b:
            raise ValueError("a connector pair needs two non-empty names")
        if len(self.twists) != 4:
            raise ValueError("a connector pair needs four twist flags")


@dataclass(frozen=True)
class Rule:
    """A rule between two module faces, written ``a:+X -> b:-X``.

    ``kind`` is ``"allowed"``, ``"disallowed"`` or ``"exclusive"``. The exact
    meaning under rotation is defined in ``addon/compile/rules.py``.
    """

    kind: str
    module_a: str
    face_a: int
    module_b: str
    face_b: int

    def __post_init__(self) -> None:
        """Check the kind and the face indices."""
        if self.kind not in RULE_KINDS:
            raise ValueError(f"rule kind {self.kind!r} is not one of {RULE_KINDS}")
        for face in (self.face_a, self.face_b):
            if not 0 <= face < 6:
                raise ValueError(f"rule face index {face} is not 0 to 5")

    @classmethod
    def parse(cls, kind: str, text: str) -> Rule:
        """Parse ``"a:+X -> b:-X"``; module names may contain anything but ``:``."""
        try:
            left, right = (side.strip() for side in text.split("->"))
            module_a, face_a = (part.strip() for part in left.rsplit(":", 1))
            module_b, face_b = (part.strip() for part in right.rsplit(":", 1))
        except ValueError:
            raise ValueError(f"cannot parse rule {text!r}; expected 'a:+X -> b:-X'") from None
        return cls(kind, module_a, face_index(face_a), module_b, face_index(face_b))

    def text(self) -> str:
        """The rule as ``a:+X -> b:-X``."""
        return f"{self.module_a}:{FACE_NAMES[self.face_a]} -> {self.module_b}:{FACE_NAMES[self.face_b]}"


def face_index(name: str) -> int:
    """Face index from ``"+X"`` and friends, case-insensitive."""
    key = name.strip().upper()
    if key not in FACE_NAMES:
        raise ValueError(f"unknown face {name!r}; expected one of {FACE_NAMES}")
    return FACE_NAMES.index(key)


@dataclass
class Kit:
    """Everything an author provides: modules in library order, connector pairs, rules, Indifference.

    The library index of a module is its position in ``modules``; it is what
    ``wfc_module`` carries and what the Materialise node group instances.
    ``indifference`` lets any two uncovered faces meet.
    """

    modules: list[Module] = field(default_factory=list)
    pairs: list[ConnectorPair] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    indifference: bool = True

    def module_index(self, name: str) -> int:
        """Library index of a module by name; ``ValueError`` if absent."""
        for index, module in enumerate(self.modules):
            if module.name == name:
                return index
        raise ValueError(f"no module named {name!r}")

    def check(self) -> None:
        """Names must be unique and non-empty."""
        seen: set[str] = set()
        for module in self.modules:
            if not module.name:
                raise ValueError("a module has an empty name")
            if module.name in seen:
                raise ValueError(f"two modules are named {module.name!r}")
            seen.add(module.name)
