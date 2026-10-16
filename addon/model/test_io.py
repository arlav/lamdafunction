# SPDX-License-Identifier: GPL-3.0-or-later
"""Round-trip and validation tests for the data model."""

from __future__ import annotations

import json

import pytest

from addon.model import Connector, ConnectorPair, Envelope, Kit, Module, Rule, SolveSettings
from addon.model.io import (
    envelope_from_dict,
    envelope_to_dict,
    kit_from_dict,
    kit_to_dict,
    settings_from_dict,
    settings_to_dict,
)


def sample_kit() -> Kit:
    """A small kit using every feature of the model."""
    return Kit(
        modules=[
            Module("pin", "Z", 2.0, ("a",), (Connector("key", 1),) + (Connector("0"),) * 5, (False,) * 5 + (True,)),
            Module("slot", "FULL", 1.0, (), (Connector(), Connector("key", 3)) + (Connector("0"),) * 4, geometry_hash=tuple(str(i) for i in range(24))),
        ],
        pairs=[ConnectorPair("key", "key", (True, False, False, False)), ConnectorPair("0", "0")],
        rules=[Rule.parse("disallowed", "pin:+X -> slot:-X"), Rule("exclusive", "pin", 4, "slot", 5)],
        indifference=False,
    )


def test_kit_round_trips_through_json() -> None:
    """kit_to_dict and kit_from_dict are inverses, through real JSON text."""
    kit = sample_kit()
    text = json.dumps(kit_to_dict(kit))
    assert kit_from_dict(json.loads(text)) == kit


def test_envelope_and_settings_round_trip() -> None:
    """Envelopes with weights, priority and fixed, and settings, survive JSON."""
    envelope = Envelope.box((2, 1, 1))
    envelope.weights["#a"] = [1.0, 0.0]
    envelope.priority = [0, 3]
    envelope.fixed = [-1, 1]
    assert envelope_from_dict(json.loads(json.dumps(envelope_to_dict(envelope)))) == envelope
    settings = SolveSettings(seed=7, attempts=2, closed_by="0")
    assert settings_from_dict(settings_to_dict(settings)) == settings


def test_rule_parsing() -> None:
    """Rules parse from the a:+X -> b:-X syntax and print back the same."""
    rule = Rule.parse("allowed", " elbow : -z -> cap:+Y ")
    assert (rule.module_a, rule.face_a, rule.module_b, rule.face_b) == ("elbow", 5, "cap", 2)
    assert rule.text() == "elbow:-Z -> cap:+Y"
    with pytest.raises(ValueError):
        Rule.parse("allowed", "elbow -> cap")
    with pytest.raises(ValueError):
        Rule.parse("maybe", "a:+X -> b:-X")


def test_model_rejects_bad_values() -> None:
    """Freedom names, face counts, offsets, empty pair names and duplicate module names are errors."""
    with pytest.raises(ValueError):
        Module("m", freedom="W")
    with pytest.raises(ValueError):
        Module("m", faces=(Connector(),) * 5)
    with pytest.raises(ValueError):
        Module("m", faces=(Connector("a", 4),) + (Connector(),) * 5)
    with pytest.raises(ValueError):
        ConnectorPair("", "a")
    kit = Kit(modules=[Module("m"), Module("m")])
    with pytest.raises(ValueError):
        kit.check()
    envelope = Envelope([(0, 0, 0), (0, 0, 0)])
    with pytest.raises(ValueError):
        envelope.check()
    with pytest.raises(ValueError):
        kit_from_dict({"format": "other"})
