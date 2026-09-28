"""Current producer metrics survive reporting without inventing legacy costs."""
import json

import pytest

from jev_factorio.latency_report import analyze
from jev_factorio.performance import PerformanceCounters


def report_rows(tmp_path, rows):
    path = tmp_path / 'gameplay.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    return analyze(path)


def checkpoint_row(*, multiplier=1, status='written'):
    counters = PerformanceCounters()
    counters.checkpoint({
        'status': status, 'bytes': 128,
        'capture_calls': 1, 'serialization_calls': 1,
        'capture_ns': 2 * multiplier, 'json_encode_ns': 3 * multiplier,
        'serialize_ns': 5 * multiplier, 'compare_ns': multiplier,
        'file_sync_ns': 7 * multiplier, 'directory_sync_ns': 11 * multiplier,
        'installation_check_ns': 13 * multiplier, 'total_ns': 40 * multiplier,
        'file_sync_calls': 1, 'directory_sync_calls': 1,
        'parent_directory_sync_calls': 0,
        'verification_read_calls': 1, 'verification_read_bytes': 128,
    })
    return {'performance': counters.snapshot()}


def test_report_preserves_current_producer_io_and_phase_coverage(tmp_path):
    first, second = checkpoint_row(), checkpoint_row(multiplier=3)
    report = report_rows(tmp_path, [first, second])
    for key, value in first['performance']['checkpoint_operations'].items():
        assert report['counts']['checkpoint:' + key] == 2 * value
    assert report['counts']['checkpoint:bytes_written'] == 256
    for key, value in first['performance']['checkpoint_ns'].items():
        assert report['distributions']['per_record_checkpoint:' + key] == {
            'count': 2, 'median_ns': 2 * value, 'p95_ns': 3 * value, 'total_ns': 4 * value,
        }
    assert 'not_additive' in report['scopes']['checkpoint']
    assert 'serialize_ns_includes_capture_and_json_encode' in report['scopes']['checkpoint']
    assert report['native_acceptance_proven'] is False


def test_legacy_fields_are_unknown_not_zero_or_current_coverage(tmp_path):
    current = checkpoint_row()
    legacy = {'performance': {'schema': 1, 'checkpoint_ns': {'serialize_ns': 15},
                             'checkpoint_operations': {'serialization_calls': 1}}}
    report = report_rows(tmp_path, [current, legacy, {}])
    assert report['counts']['checkpoint:serialization_calls'] == 2
    assert report['counts']['checkpoint:io_measured_calls'] == 1
    assert report['counts']['checkpoint:phase_fields_present_calls'] == 1
    assert report['counts']['checkpoint:verification_read_bytes'] == 128
    assert report['distributions']['per_record_checkpoint:capture_ns']['count'] == 1
    assert report['distributions']['per_record_checkpoint:serialize_ns'] == {
        'count': 2, 'median_ns': 10, 'p95_ns': 15, 'total_ns': 20,
    }
    legacy_only = report_rows(tmp_path, [legacy, {}])
    assert 'checkpoint:verification_read_bytes' not in legacy_only['counts']
    assert 'checkpoint:phase_fields_present_calls' not in legacy_only['counts']
    assert 'per_record_checkpoint:capture_ns' not in legacy_only['distributions']


def test_failed_and_zero_operation_evidence_stays_distinct_from_missing(tmp_path):
    failed = checkpoint_row(status='failed')
    unchanged = checkpoint_row(status='unchanged')
    unchanged['performance']['checkpoint_ns'] = {
        key: 0 for key in unchanged['performance']['checkpoint_ns']}
    unchanged['performance']['checkpoint_operations'] = {
        key: 1 if key in {'measured_calls', 'io_measured_calls', 'phase_fields_present_calls'} else 0
        for key in unchanged['performance']['checkpoint_operations']}
    report = report_rows(tmp_path, [failed, unchanged])
    assert report['counts']['checkpoint:failed'] == 1
    assert report['counts']['checkpoint:unchanged'] == 1
    assert 'checkpoint:bytes_written' not in report['counts']
    assert report['counts']['checkpoint:verification_read_calls'] == 1
    assert report['counts']['checkpoint:io_measured_calls'] == 2
    assert report['distributions']['per_record_checkpoint:capture_ns']['count'] == 2
    assert report['distributions']['per_record_checkpoint:capture_ns']['median_ns'] == 1


@pytest.mark.parametrize('group,key', [
    ('checkpoint_operations', 'verification_read_bytes'),
    ('checkpoint_operations', 'phase_fields_present_calls'),
    ('checkpoint_ns', 'capture_ns'),
    ('checkpoint_ns', 'installation_check_ns'),
])
@pytest.mark.parametrize('value', [-1, True, None, 1.5, 'private-native-payload'])
def test_invalid_checkpoint_numbers_are_rejected(tmp_path, group, key, value):
    row = checkpoint_row()
    row['performance'][group][key] = value
    with pytest.raises(ValueError, match='Invalid latency record at line 1'):
        report_rows(tmp_path, [row])


@pytest.mark.parametrize('group', ['checkpoint_operations', 'checkpoint_ns'])
@pytest.mark.parametrize('value', [None, [], {'private-native-label': 1}])
def test_invalid_checkpoint_tables_and_unbounded_labels_are_rejected(tmp_path, group, value):
    row = checkpoint_row()
    row['performance'][group] = value
    with pytest.raises(ValueError, match='Invalid latency record at line 1'):
        report_rows(tmp_path, [row])
