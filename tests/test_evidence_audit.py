"""Synthetic capture validation. No production bundle is embedded in this repo."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jev_factorio.evidence_audit import audit_bundle, measurements


def encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def records():
    receipt = {'role': 'furnace', 'item': 'iron-ore', 'quantity': 3,
               'unit_number': 17, 'extracting': False}
    def state(tick, x, output, receipts, researched):
        return {'tick': tick, 'player_position': [x, 0], 'inventory': {},
                'researched': researched, 'factory': {'receipts': receipts,
                    'produced': {'iron-plate': output}, 'research': 'automation',
                    'research_progress': 0.5, 'entities': {}}}
    base = {'session_id': 's', 'policy': 'hybrid', 'code_revision': {
                'commit': 'a' * 40, 'source_sha256': 'b' * 64},
            'run_id': 'run', 'segment_id': 'segment', 'verified': True,
            'outcome': 'synthetic', 'model_call': False}
    return [{**deepcopy(base), 'recorded_at_utc': '2026-09-23T20:00:00+00:00',
                'tick': 10, 'action': 'factory_insert',
                'state': state(10, 0, 3, {'old': receipt}, []),
                'after_state': state(11, 3, 4, {'old': receipt, 'new': receipt}, [])},
            {**deepcopy(base), 'recorded_at_utc': '2026-09-23T20:00:01+00:00',
                'tick': 12, 'action': 'observe',
                'state': state(12, 3, 4, {'old': receipt, 'new': receipt}, []),
                'after_state': state(13, 4, 5, {'old': receipt, 'new': receipt}, ['automation'])}]


def rehash(root):
    lines = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name
             for p in sorted(root.iterdir()) if p.name != 'SHA256SUMS']
    (root / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    root.mkdir()
    rows = records()
    raw = b''.join(encoded(row) for row in rows)
    files = {'gameplay-recent.jsonl.gz': gzip.compress(raw, mtime=0),
        'gameplay-timeline.jsonl': b''.join(encoded({k: row[k] for k in
            ('recorded_at_utc', 'tick', 'action', 'outcome', 'verified')}) for row in rows),
        'controller-snapshot.json': encoded({'source_commit': 'a' * 40}),
        'supervisor-events-recent.jsonl': encoded({'event': 'process_started', 'at': 'historical'}),
        'README.md': b'Synthetic fixture.\n', 'NOTE.md': b'Historical synthetic note.\n',
        'capture-manifest.json': encoded({'stored_gameplay': {
            'uncompressed_bytes': len(raw), 'uncompressed_sha256': hashlib.sha256(raw).hexdigest()},
            'gameplay_records': 2, 'supervisor_events': 1,
            'first_record_utc': rows[0]['recorded_at_utc'],
            'last_record_utc': rows[-1]['recorded_at_utc']})}
    for name, value in files.items():
        (root / name).write_bytes(value)
    rehash(root)
    return root


def test_complete_bundle_hash_gzip_counts_and_projection_are_verified(bundle):
    result = audit_bundle(bundle)
    assert result['gzip_integrity_checked'] and result['timeline_matches_gameplay']
    assert len(result['bundle_files_verified']) == 7
    assert not result['source_range_hashes_independently_verified']
    assert not result['snapshot_is_atomic'] and not result['hashes_prove_external_authenticity']
    metrics = result['metrics']
    assert metrics['newly_observed_transfer_receipts'] == {'insert': 1}
    assert metrics['small_transfer_receipts_le_5'] == 1
    assert metrics['native_production_counter_delta'] == {'iron-plate': 2}
    assert metrics['new_researched_names'] == ['automation']
    assert metrics['sampled_travel_lower_bound']['tiles'] == 4
    assert metrics['actor_idle_seconds'] is None and metrics['billed_model_cost'] is None
    assert metrics['useful_downstream_production_verified'] is None
    assert metrics['field_coverage']['mining_outposts'] == {'present': 0, 'missing': 2}
    assert result['controller_overhead']['legacy_records'] == 2


def test_changed_even_non_log_file_fails_hash_verification(bundle):
    (bundle / 'NOTE.md').write_bytes(b'Tampered note\n')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        audit_bundle(bundle)


@pytest.mark.parametrize('variant', ['traversal', 'duplicate', 'missing'])
def test_manifest_entries_fail_closed(bundle, variant):
    p = bundle / 'SHA256SUMS'
    text = p.read_text()
    if variant == 'traversal': text += 'a' * 64 + '  ../outside\n'
    if variant == 'duplicate': text += text.splitlines()[0] + '\n'
    if variant == 'missing': text = '\n'.join(x for x in text.splitlines() if 'NOTE.md' not in x) + '\n'
    p.write_text(text)
    with pytest.raises(ValueError): audit_bundle(bundle)


def test_symlink_input_is_rejected(bundle, tmp_path):
    outside = tmp_path / 'outside'
    outside.write_bytes((bundle / 'NOTE.md').read_bytes())
    (bundle / 'NOTE.md').unlink()
    (bundle / 'NOTE.md').symlink_to(outside)
    with pytest.raises(ValueError, match='regular files'): audit_bundle(bundle)


@pytest.mark.parametrize('variant', ['crc', 'uncompressed_hash', 'size', 'count', 'timeline', 'partial'])
def test_valid_outer_hash_cannot_hide_invalid_inner_evidence(bundle, variant):
    path = bundle / 'capture-manifest.json'
    manifest = json.loads(path.read_text())
    if variant == 'crc':
        p = bundle / 'gameplay-recent.jsonl.gz'
        data = bytearray(p.read_bytes()); data[-8] ^= 1; p.write_bytes(data)
    elif variant == 'uncompressed_hash': manifest['stored_gameplay']['uncompressed_sha256'] = '0' * 64
    elif variant == 'size': manifest['stored_gameplay']['uncompressed_bytes'] = 1
    elif variant == 'count': manifest['gameplay_records'] = 3
    elif variant == 'timeline':
        p = bundle / 'gameplay-timeline.jsonl'
        rows = [json.loads(x) for x in p.read_text().splitlines()]
        rows[0]['tick'] += 1; p.write_bytes(b''.join(encoded(x) for x in rows))
    elif variant == 'partial':
        p = bundle / 'supervisor-events-recent.jsonl'
        p.write_bytes(p.read_bytes().rstrip(b'\n'))
    path.write_bytes(encoded(manifest)); rehash(bundle)
    with pytest.raises((ValueError, OSError, EOFError)): audit_bundle(bundle)


@pytest.mark.parametrize('key,value', [('session_id', 'different'), ('policy', 'jev'),
    ('code_revision', {'commit': 'c' * 40}), ('segment_id', 'new-segment')])
def test_mixed_treatments_are_not_aggregated(key, value):
    rows = records(); rows[-1][key] = value
    with pytest.raises(ValueError, match='Partition'): measurements(rows)


def test_unknown_usage_and_production_are_not_reported_as_zero():
    rows = records()
    for row in rows:
        row.pop('model_call')
        for key in ('state', 'after_state'):
            row[key]['factory'].pop('produced')
    result = measurements(rows)
    assert result['model_usage']['input_tokens']['total'] is None
    assert result['native_production_counter_delta'] is None
    assert result['model_call_flag_missing_records'] == 2


def test_receipts_are_deduplicated_and_identity_changes_fail():
    rows = records()
    rows[-1]['after_state']['factory']['receipts'] = deepcopy(rows[-1]['after_state']['factory']['receipts'])
    rows[-1]['after_state']['factory']['receipts']['new']['quantity'] += 1
    with pytest.raises(ValueError, match='Conflicting receipt'): measurements(rows)


def test_regressing_tick_rejected():
    rows = records(); rows[-1]['tick'] = 1
    with pytest.raises(ValueError, match='regressed'): measurements(rows)


def test_missing_initial_receipts_do_not_count_late_history_as_new_work():
    rows = records()
    del rows[0]['state']['factory']['receipts']
    result = measurements(rows)
    assert result['newly_observed_transfer_receipts'] is None
    assert result['small_transfer_receipts_le_5'] is None
    assert not result['transfer_receipt_coverage']['baseline_known']


def test_production_site_rejections_live_in_sources_not_diagnostics():
    rows = records()
    rows[0]['after_state']['factory']['production_sites'] = {'sources': {
        'recipe:iron-plate': {'state': 'rejected', 'reason': 'existing_manual_cell'}}}
    result = measurements(rows)
    assert result['automation_rejection_observations'] == {
        'production_sites:recipe:iron-plate:existing_manual_cell': 1}


def test_duplicate_records_fail_instead_of_doubling_costs():
    rows = records()
    with pytest.raises(ValueError, match='Duplicate gameplay'):
        measurements([rows[0], deepcopy(rows[0]), rows[1]])
