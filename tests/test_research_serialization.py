"""Equivalent canonical evidence with one payload encoding and unchanged barriers."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import itertools
import json
import random

import pytest

from jev_factorio import research_log as rl


def writer_at(path, **options):
    return rl.ResearchLog(path, rl.RunConfiguration(backend='mock', controller='hierarchical',
                          policy='jev', target='bootstrap_mining', mock_model=True),
                          environ={}, monotonic_ns=itertools.count().__next__,
                          utc_now=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc), **options)


def has_fixture(value):
    if type(value) is dict:
        return 'large_serialization_fixture' in value or any(has_fixture(v) for v in value.values())
    if type(value) is list:
        return any(has_fixture(v) for v in value)
    return False


def test_large_payload_encoded_once_before_unchanged_durable_barrier(tmp_path, monkeypatch):
    writer = writer_at(tmp_path / 'run')
    original_dumps = rl.json.dumps
    original_durable = rl._write_durable
    calls = []
    def dumps(value, *args, **kwargs):
        if has_fixture(value):
            calls.append('payload_encoding')
        return original_dumps(value, *args, **kwargs)
    def durable(stream, data):
        calls.append('durable_write')
        return original_durable(stream, data)
    with monkeypatch.context() as patch:
        patch.setattr(rl.json, 'dumps', dumps)
        patch.setattr(rl, '_write_durable', durable)
        event = writer.emit('observation', {'large_serialization_fixture': list(range(500))})
    assert calls == ['payload_encoding', 'durable_write']
    writer.finish()
    assert rl.verify_run(writer.run_dir)['event_count'] == 3
    assert event['sequence'] == 2


def json_value(rng, depth=0):
    scalar = [None, False, True, 0, -10**30, 10**35, 0.0, -0.0, 1.25,
              'a,\\"payload\\":x', 'é😀\\n\x00', '\ud800', 'correlation', 'event_hash']
    kind = rng.randrange(5 if depth < 4 else 3)
    if kind < 3:
        return rng.choice(scalar)
    if kind == 3:
        return [json_value(rng, depth + 1) for _ in range(rng.randrange(5))]
    return {f'{i}:é\\"': json_value(rng, depth + 1) for i in range(rng.randrange(5))}


@pytest.mark.parametrize('seed', range(20))
def test_serialized_events_equal_independent_original_v1_encoding(tmp_path, seed):
    rng = random.Random(seed)
    writer = writer_at(tmp_path / 'run')
    for index in range(3):
        event = writer.emit('observation', {'value': json_value(rng), 'index': index},
                            correlation={'decision_id': 'decision-é-"-id'})
        unsigned = {k: v for k, v in event.items() if k != 'event_hash'}
        options = dict(sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
        assert event['event_hash'] == 'sha256:' + hashlib.sha256(
            json.dumps(unsigned, **options).encode('ascii')).hexdigest()
        raw = (writer.run_dir / 'events.jsonl').read_bytes().splitlines()[-1]
        assert raw == json.dumps(event, **options).encode('ascii')
        assert json.loads(raw) == event
    writer.finish()
    assert rl.verify_run(writer.run_dir)['complete'] is True


def test_nested_caller_and_returned_payload_cannot_change_committed_bytes(tmp_path):
    writer = writer_at(tmp_path / 'run')
    payload = {'x': [{'units': 1}]}
    event = writer.emit('observation', payload)
    committed = (writer.run_dir / 'events.jsonl').read_bytes()
    payload['x'][0]['units'] = 2
    event['payload']['x'][0]['units'] = 3
    assert (writer.run_dir / 'events.jsonl').read_bytes() == committed
    writer.finish()
    assert rl.verify_run(writer.run_dir)['complete'] is True


@pytest.mark.parametrize('failure_point', ['write', 'flush', 'fsync'])
def test_encoded_event_failure_never_advances_identity_or_unseals_writer(tmp_path, monkeypatch, failure_point):
    writer = writer_at(tmp_path / 'run')
    original = writer._stream
    prior = (writer._sequence, writer._previous_hash, writer._last_monotonic_ns)
    def fail(*_):
        raise OSError('synthetic durable failure')
    class Stream:
        def write(self, data):
            return fail() if failure_point == 'write' else original.write(data)
        def flush(self):
            return fail() if failure_point == 'flush' else original.flush()
        def fileno(self):
            return original.fileno()
        def close(self):
            original.close()
    writer._stream = Stream()
    with monkeypatch.context() as patch:
        if failure_point == 'fsync':
            patch.setattr(rl.os, 'fsync', fail)
        with pytest.raises(OSError):
            writer.emit('observation', {'large_serialization_fixture': [1, 2, 3]})
    assert prior == (writer._sequence, writer._previous_hash, writer._last_monotonic_ns)
    assert writer._failed
    with pytest.raises(RuntimeError):
        writer.emit('observation', {})
    writer.close()


def test_oversize_encoding_fails_before_write_and_allows_valid_next_event(tmp_path, monkeypatch):
    writer = writer_at(tmp_path / 'run')
    prior = (writer.run_dir / 'events.jsonl').read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(rl, 'MAX_RECORD_BYTES', 1024)
        with pytest.raises(ValueError, match='size limit'):
            writer.emit('observation', {'large_serialization_fixture': 'x' * 2000})
    assert (writer.run_dir / 'events.jsonl').read_bytes() == prior
    assert writer.emit('observation', {})['sequence'] == 2
    writer.finish()
    assert rl.verify_run(writer.run_dir)['complete']


def test_schema_and_hash_verification_remain_separate_strict_boundaries(tmp_path):
    writer = writer_at(tmp_path / 'run')
    writer.emit('observation', {'large_serialization_fixture': [1, 2]})
    writer.finish()
    path = writer.run_dir / 'events.jsonl'
    rows = [json.loads(v) for v in path.read_bytes().splitlines()]
    forged = deepcopy(rows[1]); forged['payload']['large_serialization_fixture'].append(3)
    # validate_event promises envelope validation; verify_run still checks the hash.
    rl.validate_event(forged)
    rows[1] = forged
    path.write_bytes(b''.join(rl.canonical_bytes(v) + b'\n' for v in rows))
    with pytest.raises(ValueError):
        rl.verify_run(writer.run_dir)
