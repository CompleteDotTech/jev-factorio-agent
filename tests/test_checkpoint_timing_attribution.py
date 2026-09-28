"""Checkpoint phase accounting without changing authoritative write barriers."""

from jev_factorio.memory import CampaignMemory
from jev_factorio.performance import PerformanceCounters
import jev_factorio.checkpoint_io as checkpoint


def test_capture_and_json_encoding_are_separate_with_inclusive_legacy_clock(tmp_path, monkeypatch):
    clock = [0]
    capture, dumps = checkpoint.asdict, checkpoint.json.dumps

    def capture_with_cost(value):
        clock[0] += 40_000_000
        return capture(value)

    def encode_with_cost(*args, **kwargs):
        clock[0] += 7_000_000
        return dumps(*args, **kwargs)

    monkeypatch.setattr(checkpoint.time, 'perf_counter_ns', lambda: clock[0])
    monkeypatch.setattr(checkpoint, 'asdict', capture_with_cost)
    monkeypatch.setattr(checkpoint.json, 'dumps', encode_with_cost)
    memory = CampaignMemory('timing-fixture', 'rocket_launch')
    memory.save(tmp_path / 'checkpoint.json')
    metrics = memory._checkpoint_metrics
    assert metrics['status'] == 'written'
    assert metrics['capture_ns'] == 40_000_000
    assert metrics['json_encode_ns'] == 7_000_000
    assert metrics['serialize_ns'] == 47_000_000
    assert metrics['file_sync_calls'] == 1
    assert metrics['directory_sync_calls'] == 1
    assert metrics['verification_read_calls'] == 1


def test_aggregation_keeps_legacy_inclusive_timing_distinct_from_new_phases():
    counters = PerformanceCounters()
    counters.checkpoint({'status': 'written', 'bytes': 5, 'serialize_ns': 47})
    legacy = counters.snapshot()
    assert legacy['checkpoint_ns'] == {'serialize_ns': 47}
    assert 'phase_fields_present_calls' not in legacy['checkpoint_operations']

    counters.checkpoint({'status': 'written', 'bytes': 5, 'serialize_ns': 47,
                         'capture_ns': 40, 'json_encode_ns': 7})
    current = counters.snapshot()
    assert current['checkpoint_ns'] == {
        'serialize_ns': 94, 'capture_ns': 40, 'json_encode_ns': 7}
    assert current['checkpoint_operations']['phase_fields_present_calls'] == 1

    counters.checkpoint({'status': 'unchanged', 'capture_ns': 0, 'json_encode_ns': 0})
    assert counters.snapshot()['checkpoint_operations']['phase_fields_present_calls'] == 2
