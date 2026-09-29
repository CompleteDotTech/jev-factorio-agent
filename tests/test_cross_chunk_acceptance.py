"""Offline chain/progress fixtures; they do not synthesize native acceptance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from jev_factorio.cross_chunk_acceptance import SCHEMA, analyze


def put(root, name, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    (root / name).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def fixture(root):
    h = lambda data: hashlib.sha256(data).hexdigest()
    identity = {key: 'campaign' for key in ('session_id', 'policy',
                                            'requested_model', 'controller')}
    identity.update(save_sha256='a' * 64, source_commit='1' * 40, source_tree='2' * 40,
                    treatment_sha256='c' * 64, witness_sha256='d' * 64,
                    attachment_receipt_sha256='e' * 64, actor_unit=2543)
    identity.update(policy='jev', requested_model='jev-1.13.0', controller='hierarchical')
    base = datetime(2026, 9, 29, tzinfo=timezone.utc)
    rows = []
    for i in range(5):
        tick = i * 36000 + 1
        rows.append({'session_id': 'campaign', 'world_kind': 'fle', 'controller': 'hierarchical',
                     'policy': 'jev', 'requested_model': 'jev-1.13.0',
                     'code_revision': {'commit': '1' * 40, 'dirty': False},
                     'model_call': i % 2 == 0,
                     'status': 'running', 'recorded_at_utc': (base + timedelta(seconds=600*i)).isoformat(),
                     'after_state': {'session_id': 'campaign', 'tick': tick, 'researched': [],
                                     'factory': {'acceptance_runtime': {'session_id': 'campaign',
                                                 'actor_unit': 2543, 'speed': 1, 'tick_paused': False},
                                                 'research': 'automation', 'research_progress': i / 5,
                                                 'consumed': {'automation-science-pack': i},
                                                 'entities': {'utility:lab': {'name': 'lab', 'unit_number': 77}},
                                                 'force_entity_counts': {'lab': 1},
                                                 'receipts': ({'paid-lab-1': {'role': 'utility:lab',
                                                     'item': 'automation-science-pack', 'extracting': False,
                                                     'quantity': 1, 'tick': 36001, 'unit_number': 77}}
                                                     if i >= 1 else {})}}})
    chunks = []
    checkpoint = put(root, 'cp0.json', {'session_id': 'campaign', 'status': 'running', 'last_tick': 0})
    for index, subset in enumerate((rows[:3], rows[3:])):
        gameplay = f'game{index}.jsonl'
        span = b''.join((json.dumps(row, sort_keys=True) + '\n').encode() for row in subset)
        (root / gameplay).write_bytes(span)
        next_checkpoint = put(root, f'cp{index + 1}.json',
                              {'session_id': 'campaign', 'status': 'running', 'last_tick': subset[-1]['after_state']['tick']})
        result = put(root, f'result{index}.json', {'status': 'verified', 'chunk': index})
        chunks.append({'gameplay': gameplay, 'start_byte': 0, 'end_byte': len(span), 'span_sha256': h(span),
                       'before_checkpoint': f'cp{index}.json', 'before_sha256': checkpoint,
                       'after_checkpoint': f'cp{index + 1}.json', 'after_sha256': next_checkpoint,
                       'owner_result': f'result{index}.json', 'owner_result_sha256': result,
                       'owner_status': 'verified'})
        checkpoint = next_checkpoint
    manifest = {'schema': SCHEMA, 'identity': identity, 'max_observation_gap_seconds': 600,
                'max_science_stall_seconds': 600, 'chunks': chunks}
    put(root, 'manifest.json', manifest)
    return manifest


def test_cross_chunk_science_is_bounded_and_not_native_acceptance(tmp_path):
    fixture(tmp_path)
    report = analyze(tmp_path / 'manifest.json')
    assert report['records'] == 5
    assert report['useful_seconds_lower_bound'] == 1800
    assert report['science_consumed_delta'] == 4
    assert report['lab_delivered_by_new_receipts'] == 1
    assert report['native_acceptance_proven'] is False
    assert 'native_paid_coal_and_downstream_rollup_required' in report['issues']


@pytest.mark.parametrize('damage,reason', [
    ('span', 'Gameplay span digest mismatch'),
    ('checkpoint', 'Checkpoint chain gap'),
    ('outcome', 'Ambiguous or repeated owner outcome'),
    ('time', 'Observation gap'),
])
def test_cross_chunk_rejects_evidence_breaks(tmp_path, damage, reason):
    manifest = fixture(tmp_path)
    if damage == 'span':
        manifest['chunks'][1]['span_sha256'] = '0' * 64
    elif damage == 'checkpoint':
        manifest['chunks'][1]['before_sha256'] = '0' * 64
    elif damage == 'outcome':
        manifest['chunks'][1]['owner_status'] = 'uncertain'
    else:
        manifest['max_observation_gap_seconds'] = 599
    put(tmp_path, 'manifest.json', manifest)
    with pytest.raises(ValueError, match=reason):
        analyze(tmp_path / 'manifest.json')


def test_cross_chunk_rejects_reused_log_overlap(tmp_path):
    manifest = fixture(tmp_path)
    first = (tmp_path / 'game0.jsonl').read_bytes()
    second = (tmp_path / 'game1.jsonl').read_bytes()
    (tmp_path / 'joined.jsonl').write_bytes(first + second)
    manifest['chunks'][0]['gameplay'] = manifest['chunks'][1]['gameplay'] = 'joined.jsonl'
    manifest['chunks'][1]['start_byte'] = len(first) - len(first.splitlines()[-1]) - 1
    manifest['chunks'][1]['end_byte'] = len(first + second)
    manifest['chunks'][1]['span_sha256'] = hashlib.sha256((first + second)[
        manifest['chunks'][1]['start_byte']:]).hexdigest()
    put(tmp_path, 'manifest.json', manifest)
    with pytest.raises(ValueError, match='Gap or overlap'):
        analyze(tmp_path / 'manifest.json')


def rewrite_rows(root, manifest, chunk_index, change):
    chunk = manifest['chunks'][chunk_index]
    rows = [json.loads(line) for line in (root / chunk['gameplay']).read_bytes().splitlines()]
    change(rows)
    span = b''.join((json.dumps(row, sort_keys=True) + '\n').encode() for row in rows)
    (root / chunk['gameplay']).write_bytes(span)
    chunk['end_byte'] = len(span)
    chunk['span_sha256'] = hashlib.sha256(span).hexdigest()
    put(root, 'manifest.json', manifest)


def test_idle_wall_time_never_earns_useful_seconds(tmp_path):
    manifest = fixture(tmp_path)
    rewrite_rows(tmp_path, manifest, 0, lambda rows: [row['after_state']['factory'].update(
        consumed={'automation-science-pack': 0}, research_progress=0) for row in rows])
    rewrite_rows(tmp_path, manifest, 1, lambda rows: [row['after_state']['factory'].update(
        consumed={'automation-science-pack': 0}, research_progress=0) for row in rows])
    report = analyze(tmp_path / 'manifest.json')
    assert report['useful_seconds_lower_bound'] == 0
    assert 'continuous_useful_1800_seconds_missing' in report['issues']


def test_source_drift_and_counter_regression_fail_closed(tmp_path):
    manifest = fixture(tmp_path)
    rewrite_rows(tmp_path, manifest, 1, lambda rows: rows[0]['code_revision'].update(commit='f' * 40))
    with pytest.raises(ValueError, match='Gameplay source drift'):
        analyze(tmp_path / 'manifest.json')
    manifest = fixture(tmp_path)
    rewrite_rows(tmp_path, manifest, 1, lambda rows: rows[0]['after_state']['factory'].update(
        consumed={'automation-science-pack': 0}))
    with pytest.raises(ValueError, match='Science counter regression'):
        analyze(tmp_path / 'manifest.json')


def test_unowned_lab_receipt_rejected(tmp_path):
    manifest = fixture(tmp_path)
    rewrite_rows(tmp_path, manifest, 0, lambda rows: rows[1]['after_state']['factory']['receipts'][
        'paid-lab-1'].update(unit_number=999))
    with pytest.raises(ValueError, match='Unbound lab delivery'):
        analyze(tmp_path / 'manifest.json')
