"""Incremental timing/report contracts; no latency targets tied to host speed."""
import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from jev_factorio.causal_trace import CausalTrace
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.performance import PerformanceCounters, summarize
from jev_factorio.telemetry import phase
from test_causal_trace import Backend, Client, Sink


def test_calls_are_incremental_fixed_vocabulary_and_include_failures():
    counters = PerformanceCounters()
    counters.call('observation', 20)
    counters.call('observation', 40, failed=True)
    counters.call('unbounded-user-string', 1)
    captured = counters.snapshot()
    assert captured['calls'] == {'observation': {'count': 2, 'failed': 1, 'total_ns': 60, 'max_ns': 40}}
    captured['calls']['observation']['count'] = 50
    assert counters.snapshot()['calls']['observation']['count'] == 2
    assert captured['durations_are_inclusive']


@pytest.mark.parametrize('enabled', [False, True])
def test_measured_call_does_not_run_operation_twice_or_capture_without_tracing(enabled):
    trace = CausalTrace(Sink() if enabled else None, 'hierarchical')
    trace.metrics = PerformanceCounters()
    calls = []
    def operation(): calls.append('call'); return 3
    def capture(value):
        calls.append('capture')
        return {'value': value}
    assert trace.call('observation', operation, result=capture) == 3
    assert calls == (['call', 'capture'] if enabled else ['call'])
    assert trace.metrics.snapshot()['calls']['observation']['count'] == 1
    with pytest.raises(TimeoutError):
        trace.call('model_response', lambda: (_ for _ in ()).throw(TimeoutError('secret')))
    assert trace.metrics.snapshot()['calls']['model_response']['failed'] == 1


def test_model_boundary_is_measured_even_without_research_log():
    trace = CausalTrace(None, 'hierarchical')
    trace.metrics = PerformanceCounters()
    class Model:
        def evaluate(self, state, questions): return {'answer': 1}
    assert trace.client(Model()).evaluate({}, {}) == {'answer': 1}
    assert trace.metrics.snapshot()['calls']['model_response']['count'] == 1


def test_controller_counters_reset_per_iteration_and_do_not_add_observations(tmp_path):
    backend = Backend()
    loop = HierarchicalLoop(backend, Client(), checkpoint=str(tmp_path/'state.json'),
                            log_file=str(tmp_path/'log.jsonl'), factory_scheduling='ready-work')
    records = [loop.step(), loop.step()]
    assert len([call for call in backend.calls if call[0] == 'observe']) == sum(
        record['performance']['calls']['observation']['count'] for record in records)
    assert all(record['performance']['calls']['observation']['count'] == 3 for record in records)
    result = summarize(tmp_path/'log.jsonl')
    assert result['records'] == result['instrumented_records'] == 2
    assert result['calls']['observation']['count'] == 6
    assert result['checkpoints']['written'] > 0
    assert result['calls']['checkpoint_written']['count'] == sum(
        result['checkpoints'].get(k, 0) for k in ('written', 'unchanged', 'failed', 'disabled'))
    assert not result['wall_time_or_speedup_inferred']


def make_record():
    counters = PerformanceCounters()
    counters.call('observation', 20)
    counters.checkpoint({'status': 'written', 'bytes': 10, 'file_sync_ns': 7})
    events = []
    with phase('observe', events.append): pass
    return {'action': 'observe', 'performance': counters.snapshot(), 'phases': events}


@pytest.mark.parametrize('compressed', [False, True])
def test_plain_and_gzip_legacy_records_remain_explicit(tmp_path, compressed):
    path = tmp_path/('log.jsonl.gz' if compressed else 'log.jsonl')
    opener = gzip.open if compressed else open
    with opener(path, 'wt', encoding='utf-8') as stream:
        stream.write(json.dumps({'action': 'factory_wait'})+'\n')
        stream.write(json.dumps(make_record())+'\n')
    data = summarize(path)
    assert data['records'] == 2 and data['legacy_records'] == 1
    assert data['instrumented_records'] == 1
    assert data['calls']['observation']['total_ns'] == 20
    assert data['calls']['observation']['mean_ns'] == 20
    assert data['checkpoints']['bytes_written'] == 10


@pytest.mark.parametrize('corruption', ['bad_schema', 'negative', 'nan', 'unknown_call', 'unknown_status',
                                        'bad_phase', 'truncated', 'not_object'])
def test_malformed_records_do_not_silently_disappear(tmp_path, corruption):
    path = tmp_path/'log.jsonl'
    record = make_record()
    if corruption == 'bad_schema': record['performance']['schema'] = 9
    elif corruption == 'negative': record['performance']['calls']['observation']['total_ns'] = -1
    elif corruption == 'nan': record['performance']['calls']['observation']['total_ns'] = float('nan')
    elif corruption == 'unknown_call': record['performance']['calls']['private'] = {}
    elif corruption == 'unknown_status': record['performance']['checkpoints']['private'] = 1
    elif corruption == 'bad_phase': record['phases'] = [{'stage': 'bad'}]
    elif corruption == 'not_object': record = []
    text = json.dumps(record)
    if corruption == 'truncated': text = text[:-2]
    path.write_text(text)
    with pytest.raises(ValueError, match='line 1'): summarize(path)


def test_report_does_not_add_nested_durations_as_wall_time(tmp_path):
    record = make_record()
    record['performance']['calls']['observation']['total_ns'] = 100
    record['performance']['calls']['model_response'] = {'count': 1, 'total_ns': 70, 'max_ns': 70}
    path=tmp_path/'log.jsonl';path.write_text(json.dumps(record)+'\n')
    result = summarize(path)
    assert 'total_wall_ns' not in result
    assert result['durations_are_inclusive'] is True


def test_benchmark_preserves_all_changed_writes_and_restores_sync_function():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location('checkpoint_benchmark',
        Path(__file__).parents[1] / 'scripts/benchmark_checkpoint_io.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = os.fsync
    result = module.benchmark(5)
    assert result['cases'][0]['fsync_calls'] == {'file': 1, 'directory': 1}
    assert result['cases'][1]['fsync_calls'] == {'file': 5, 'directory': 5}
    assert os.fsync is original
    with pytest.raises(ValueError): module.benchmark(0)


def test_report_preserves_failed_call_count(tmp_path):
    record = make_record()
    record['performance']['calls']['observation']['failed'] = 1
    path = tmp_path/'log.jsonl'; path.write_text(json.dumps(record)+'\n')
    assert summarize(path)['calls']['observation']['failed'] == 1
