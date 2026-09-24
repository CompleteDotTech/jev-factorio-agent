import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.errors import ConnectionPreflightRejected
from jev_factorio.backends.fair_actions import FairActions
from jev_factorio.backends.native_factory import NativeFactory


@pytest.fixture
def positions(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    env.Direction = SimpleNamespace(UP=0)
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    return env.Position


def test_native_ports_use_exact_rotated_pipe_cells(positions):
    factory = object.__new__(NativeFactory)
    commands = []
    factory.command = lambda script: commands.append(script) or json.dumps({
        "points": [{"x": 11.5, "y": 49.5}],
    })
    points = factory.native_fluid_connection_points("recipe:sulfur", "water", output=False)
    assert [(p.x, p.y) for p in points] == [(11.5, 49.5)]
    assert "get_pipe_connections(index)" in commands[0]
    assert "connection.target_position" in commands[0]
    assert "serialize_entity" not in commands[0]


@pytest.mark.parametrize("payload", [{"points": []}, {"points": {}}])
def test_missing_native_ports_are_proven_preflight_rejections(positions, payload):
    factory = object.__new__(NativeFactory)
    factory.command = lambda script: json.dumps(payload)
    with pytest.raises(ConnectionPreflightRejected) as error:
        factory.native_fluid_connection_points("recipe:sulfur", "water", output=False)
    assert error.value.code == "missing_fluid_port"


@pytest.mark.parametrize("payload", [
    {"points": [{"x": 11.5, "y": 53}]}, {"points": "bad"}, {},
])
def test_malformed_port_response_is_not_classified_as_safe_rejection(positions, payload):
    factory = object.__new__(NativeFactory)
    factory.command = lambda script: json.dumps(payload)
    with pytest.raises((ValueError, KeyError)) as error:
        factory.native_fluid_connection_points("recipe:sulfur", "water", output=False)
    assert not isinstance(error.value, ConnectionPreflightRejected)


@pytest.mark.parametrize("cells,count,reason", [
    (set(), 5, "no_connection_route"),
    ({(0.5, 0.5), (1.5, 0.5)}, 1, "insufficient_connection_materials"),
])
def test_connection_preflight_never_places(positions, cells, count, reason):
    fair = object.__new__(FairActions)
    fair._connection_cells = lambda *args: (cells, set())
    fair.command = lambda script: json.dumps({"count": count})
    fair.place_entity = lambda *args, **kwargs: pytest.fail("preflight attempted mutation")
    with pytest.raises(ConnectionPreflightRejected) as error:
        fair.connect(positions(x=0.5, y=0.5), positions(x=1.5, y=0.5),
                     SimpleNamespace(value=("pipe",)), "water")
    assert error.value.code == reason


def test_mutation_failure_remains_ambiguous(positions):
    fair = object.__new__(FairActions)
    fair._connection_cells = lambda *args: ({(0.5, 0.5), (1.5, 0.5)}, set())
    fair.command = lambda script: '{"count":2}'
    def failed(*args, **kwargs):
        raise ValueError("placement response lost")
    fair.place_entity = failed
    with pytest.raises(ValueError) as error:
        fair.connect(positions(x=0.5, y=0.5), positions(x=1.5, y=0.5),
                     SimpleNamespace(value=("pipe",)), "water")
    assert type(error.value) is ValueError


def test_rejection_codes_are_bounded():
    with pytest.raises(ValueError, match="Unknown"):
        ConnectionPreflightRejected("arbitrary potentially sensitive message")
