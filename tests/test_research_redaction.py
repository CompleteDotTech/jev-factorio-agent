"""Redaction equivalence and traversal bounds; no timing-dependent assertions."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import math

import pytest

from jev_factorio import research_log as rl


def legacy_clean(redactor, value):
    """Prior algorithm retained only as an output/benchmark reference."""
    rl._json_value(value)
    if type(value) is str:
        return redactor.text(value)
    if type(value) is list:
        return [legacy_clean(redactor, item) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            safe_key = redactor.text(key)
            if safe_key in result:
                raise ValueError('Redaction produced duplicate evidence keys')
            result[safe_key] = (rl.REDACTED if rl._SENSITIVE.search(key)
                                else legacy_clean(redactor, item))
        return result
    return value


def legacy_safe_payload(snapshot, secrets):
    """Reproduce the prior snapshot asdict -> normalize -> clean sequence."""
    def normalize(item):
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float:
            return item if math.isfinite(item) else {'invalid_numeric': repr(item)}
        if type(item) in (list, tuple):
            return [normalize(child) for child in item]
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('Causal evidence keys must be strings')
            return {key: normalize(child) for key, child in item.items()}
        return '[unsupported value]'

    redactor = rl.Redactor({f'SECRET_{index}': secret for index, secret in enumerate(secrets)
                            if isinstance(secret, str) and secret})
    return redactor.clean(normalize(asdict(snapshot)))


def synthetic_snapshot(count=128):
    return {'snapshot': {'factory': {'entities': {
        f'recipe:fixture-{index}': {
            'unit_number': index + 1, 'position': {'x': index, 'y': -index},
            'input': {'iron-plate': 20, 'copper-plate': 5},
            'output': {'fixture': 3}, 'crafting': True,
        } for index in range(count)
    }}, 'inventory': {'iron-plate': 200}, 'tick': 123456}}


def test_redaction_preserves_canonical_bytes_and_detaches_nested_payload():
    redactor = rl.Redactor({'API_KEY': 'fixture-secret'})
    value = synthetic_snapshot()
    value['details'] = [None, True, 1.5, 'fixture-secret',
                        {'token': ['private'], 'url': 'https://example.invalid/private',
                         'message': 'Bearer abcDEF123', 'fixture-secret-key': 'safe'}]
    before = deepcopy(value)
    cleaned = redactor.clean(value)
    assert rl.canonical_bytes(cleaned) == rl.canonical_bytes(legacy_clean(redactor, value))
    assert rl.digest(cleaned) == rl.digest(legacy_clean(redactor, value))
    assert 'fixture-secret' not in rl.canonical_bytes(cleaned).decode()
    assert 'https://' not in rl.canonical_bytes(cleaned).decode()
    cleaned['snapshot']['factory']['entities']['recipe:fixture-0']['input'].clear()
    assert value == before


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), {1: 'value'},
                                    object(), ('tuple',)])
def test_redaction_still_rejects_invalid_values_even_under_secret_key(invalid):
    with pytest.raises(ValueError):
        rl.Redactor({}).clean({'nested': [{'token': invalid}]})


def test_redaction_still_rejects_colliding_redacted_keys():
    with pytest.raises(ValueError, match='duplicate evidence keys'):
        rl.Redactor({'API_KEY': 'fixture-secret'}).clean(
            {'nested': {'fixture-secret': 1, rl.REDACTED: 2}})


def test_validation_visits_each_value_once(monkeypatch):
    original = rl._json_value
    visited = []

    def counted(value):
        visited.append(value)
        return original(value)

    monkeypatch.setattr(rl, '_json_value', counted)
    for depth in (8, 32, 128):
        value = 'leaf'
        for _ in range(depth):
            value = {'child': [value]}
        visited.clear()
        rl.Redactor({}).clean(value)
        assert len(visited) == 2 * depth + 1


def test_safe_payload_matches_two_pass_reference_for_nested_and_sensitive_data():
    @dataclass
    class NestedEvidence:
        token: str
        values: tuple

    @dataclass
    class SnapshotFixture:
        snapshot: dict

    snapshot = SnapshotFixture({
        **synthetic_snapshot(32),
        'extra': {
            'record': NestedEvidence('fixture-secret', (1, {'url': 'https://example.invalid/private'})),
            'api_token': {'deep': {1: 'invalid but redacted'}},
            'not_finite': float('nan'),
            'unsupported': object(),
            'authorization': 'Bearer abcDEF123',
        },
    })
    secrets = ['fixture-secret', 'abcDEF123']

    with pytest.raises(ValueError, match='keys must be strings'):
        legacy_safe_payload(snapshot, secrets)
    with pytest.raises(ValueError, match='keys must be strings'):
        rl._safe_observation_payload({'snapshot': snapshot.snapshot}, secrets)

    snapshot.snapshot['extra']['api_token'] = {'deep': {'field': 'safe'}}
    expected = legacy_safe_payload(snapshot, secrets)
    actual = rl._safe_observation_payload({'snapshot': snapshot.snapshot}, secrets)
    assert rl.canonical_bytes(actual) == rl.canonical_bytes(expected)
    assert rl.digest(actual) == rl.digest(expected)
    assert 'fixture-secret' not in rl.canonical_bytes(actual).decode()
    assert 'https://' not in rl.canonical_bytes(actual).decode()


def test_unrelated_event_dataclass_remains_unsupported_without_leaking_fields():
    from dataclasses import make_dataclass

    InternalNote = make_dataclass('InternalNote', [('internal_note', str)])
    captured = rl.safe_payload({'extra': InternalNote('unlisted private text')})
    assert captured == {'extra': '[unsupported value]'}
    assert 'unlisted private text' not in rl.canonical_bytes(captured).decode()
