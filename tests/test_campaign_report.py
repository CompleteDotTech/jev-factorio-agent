"""Offline throughput evidence cannot silently become production acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from jev_factorio.campaign_report import analyze, compare, distribution, main


def records():
    result = []
    for i in range(3):
        result.append({'session_id':'test', 'world_kind':'fle','process_id':'proc',
            'code_revision': {'commit':'a'*40,'source_sha256':'b'*64}, 'policy':'deterministic',
            'target':'rocket_launch','requested_model':'pinned','acceptance_configuration':{'factory_scheduling':'ready-work'},
            'campaign_treatment':{'schema':1,'lead_time_supply':True}, 'status':'running',
            'recorded_at_utc': (datetime(2026,9,25,tzinfo=timezone.utc)+timedelta(seconds=900*i)).isoformat(),
            'after_state': {'tick':54000*i,'researched':[], 'factory':{
                'produced':{'chemical-science-pack':i*20}, 'consumed':{'chemical-science-pack':i*15},
                'research':'study','research_progress':i*.1,
                'acceptance_runtime':{'speed':1,'tick_paused':False,'session_id':'test'}}},
            'phases':[{'stage':'observe','status':'returned','seconds':32},
                      {'stage':'dispatch','status':'returned','seconds':4},
                      {'stage':'approach','status':'returned','seconds':3},
                      {'stage':'transfer_rpc','status':'returned','seconds':1}]})
    return result


def write(path, rows):
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def test_report_separates_phase_timing_and_science_rates_without_authorizing_deployment(tmp_path):
    report = analyze(write(tmp_path/'log.jsonl', records()))
    assert report['measurement_eligible']
    assert report['science_consumed_per_actor_minute'] == 1
    assert report['science_consumed_per_decision'] == 15
    assert report['timing_seconds']['phase:observe']['p95'] == 32
    assert set(report['timing_seconds']) == {'phase:observe','phase:dispatch'}
    assert report['native_acceptance_proven'] is report['deployment_authorized'] is False


@pytest.mark.parametrize('key,value', [('session_id','other'), ('process_id','other'),
    ('code_revision',None), ('campaign_treatment',{}), ('policy','jev')])
def test_report_rejects_mixed_treatments_revisions_sessions_and_processes(tmp_path, key, value):
    rows = records(); rows[-1][key] = value
    with pytest.raises(ValueError, match='Mixed'):
        analyze(write(tmp_path/'log.jsonl', rows))


@pytest.mark.parametrize('case', ['tick','time','naive_time','incomplete','empty'])
def test_report_rejects_invalid_epoch_or_capture(tmp_path, case):
    rows = records()
    if case == 'tick': rows[-1]['after_state']['tick'] = 1
    if case == 'time': rows[-1]['recorded_at_utc'] = '2026-09-24T00:00:00+00:00'
    if case == 'naive_time': rows[-1]['recorded_at_utc'] = '2026-09-25T00:30:00'
    path = write(tmp_path/'log.jsonl', rows if case != 'empty' else [])
    if case == 'incomplete': path.write_text(path.read_text().rstrip())
    with pytest.raises(ValueError): analyze(path)


@pytest.mark.parametrize('case', ['counter_reset','missing','short','mock','speed','revision'])
def test_unknown_or_insufficient_evidence_cannot_qualify_measurement(tmp_path, case):
    rows = records()
    if case == 'counter_reset': rows[-1]['after_state']['factory']['consumed'] = {}
    if case == 'missing': rows[1]['after_state']['factory'].pop('consumed')
    if case == 'short': rows = rows[:2]
    if case == 'mock':
        for row in rows: row['world_kind']='mock'
    if case == 'speed': rows[0]['after_state']['factory']['acceptance_runtime']['speed']=2
    if case == 'revision':
        for row in rows: row['code_revision']=None
    result = analyze(write(tmp_path/'log.jsonl', rows))
    assert not result['measurement_eligible'] and result['issues']
    if case in {'counter_reset','missing'}: assert result['science_consumed_per_actor_minute'] is None


def test_matched_save_comparison_exposes_effects_but_is_not_world_attestation(tmp_path):
    left = analyze(write(tmp_path/'left.jsonl', records()))
    right = deepcopy(left); right['science_consumed_per_actor_minute']=2
    first = tmp_path/'first.zip'; first.write_bytes(b'save')
    second = tmp_path/'second.zip'; second.write_bytes(b'save')
    result = compare(left, right, first, second)
    assert result['comparison_eligible']
    assert result['metrics']['science_consumed_per_actor_minute']['difference'] == 1
    assert not result['deployment_authorized'] and not result['native_acceptance_proven']
    second.write_bytes(b'different')
    assert 'initial_save_mismatch' in compare(left, right, first, second)['issues']


def test_cli_paired_arguments_are_all_required(tmp_path):
    path = write(tmp_path/'log.jsonl', records())
    with pytest.raises(SystemExit) as error:
        main([str(path), '--baseline',str(path)])
    assert error.value.code == 2


@pytest.mark.parametrize('process_id', [None, '', '   '])
def test_missing_process_identity_cannot_qualify_measurement(tmp_path, process_id):
    rows = records()
    for row in rows:
        row['process_id'] = process_id
    result = analyze(write(tmp_path/'log.jsonl', rows))
    assert not result['measurement_eligible']
    assert 'process_identity_required' in result['issues']


@pytest.mark.parametrize('field,value', [('commit', 'c'*40), ('source_sha256', 'd'*64)])
def test_paired_comparison_rejects_changed_source_even_with_matching_save(tmp_path, field, value):
    left = analyze(write(tmp_path/'left.jsonl', records()))
    rows = records()
    for row in rows:
        row['code_revision'][field] = value
    right = analyze(write(tmp_path/'right.jsonl', rows))
    initial = tmp_path/'initial.zip'
    initial.write_bytes(b'same initial save')
    result = compare(left, right, initial, initial)
    assert not result['comparison_eligible']
    assert 'uncontrolled_change:code_revision' in result['issues']


def test_percentile_definition_is_pinned():
    assert distribution([1,2,3,4]) == {'count':4,'p50':2,'p95':3,'sum':10}


def test_acceptance_projection_retains_new_treatment_marker():
    from jev_factorio.acceptance_capture import project_record
    from jev_factorio.research_log import Redactor
    from collections import Counter
    row = records()[0]; row['state'] = deepcopy(row['after_state'])
    projected = project_record(row, Redactor({}), Counter())
    assert projected['campaign_treatment'] == row['campaign_treatment']
