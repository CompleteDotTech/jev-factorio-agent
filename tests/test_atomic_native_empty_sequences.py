"""Replay a privacy-scrubbed 2.0.77 envelope; transport remains offline."""
import copy
import hashlib
from pathlib import Path

import pytest

from test_atomic_observation import setup


RAW = Path(__file__).parent / 'fixtures' / 'native_empty_bootstrap_2_0_77_sanitized.txt'
RAW_SHA256 = 'f21e37720c8d58e4df8cf6e79fe86e032bac5585eceab3d524bf630803c8e251'


def test_native_empty_bootstrap_wire_preserves_atomic_inventory_and_identity(monkeypatch):
    # Derived from an isolated 2.0.77 diagnostic before paid gameplay. Identity,
    # coordinates, ticks and profiler durations are synthetic; shapes/framing stay.
    raw = RAW.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == RAW_SHA256
    backend, native, payload, calls = setup(monkeypatch, craft=True)
    def captured(command):
        calls.append(command)
        return raw.decode()
    backend._instance.rcon_client.send_command = captured
    snapshot = backend.observe()
    assert len(calls) == 1 and 'observation_snapshot_v2' in calls[0]
    assert snapshot.session_id == 'native-empty-bootstrap-fixture'
    assert snapshot.tick == 10000
    assert snapshot.placed_entities == []
    assert snapshot.researched == ['automation-science-pack']
    assert snapshot.inventory == {'burner-mining-drill': 1, 'wooden-chest': 1}
    assert snapshot.factory['inventory_insertable'] == {'coal': 3900}
    assert snapshot._coherent_observation_verified == (snapshot.session_id, snapshot.tick)
    assert snapshot._atomic_inventory_verified == (snapshot.session_id, snapshot.tick)
    assert native._coherent_identity == (snapshot.session_id, 17, 2, 3)


@pytest.mark.parametrize('research,placed', [({}, {}), ({}, []), ([], {})])
def test_only_empty_native_sequence_objects_are_normalized(monkeypatch, research, placed):
    backend, _, payload, calls = setup(monkeypatch, craft=True)
    payload['factory']['researched'] = research
    payload['bootstrap']['placed_entities'] = placed
    state = backend.observe()
    assert state.researched == [] and state.placed_entities == []
    assert len(calls) == 1
    # The decoder must not rewrite or fabricate the captured wire payload.
    assert payload['factory']['researched'] == research
    assert payload['bootstrap']['placed_entities'] == placed


@pytest.mark.parametrize('section,key', [('factory', 'researched'), ('bootstrap', 'placed_entities')])
@pytest.mark.parametrize('invalid', [None, False, 0, '', {'1': 'wooden-chest'}, {'unexpected': []}, [False]])
def test_invalid_sequence_shapes_reject_without_fallback_or_cache_publication(
        monkeypatch, section, key, invalid):
    backend, native, payload, calls = setup(monkeypatch, craft=True)
    backend.observe()
    identity, tick = native._coherent_identity, native._coherent_tick
    resources = copy.deepcopy(backend._resources)
    payload[section][key] = invalid
    with pytest.raises(ValueError, match='Invalid atomic (research state|bootstrap entities)'):
        backend.observe()
    assert len(calls) == 2  # Exactly one failed transport; no fallback observation.
    assert native._coherent_identity == identity and native._coherent_tick == tick
    assert backend._resources == resources


def test_empty_bootstrap_does_not_excuse_missing_bound_drill(monkeypatch):
    backend, native, payload, calls = setup(monkeypatch)
    native._coherent_drill = 123
    payload['bootstrap']['placed_entities'] = {}
    with pytest.raises(ValueError, match='Atomic bootstrap drill missing'):
        backend.observe()
    assert len(calls) == 1
