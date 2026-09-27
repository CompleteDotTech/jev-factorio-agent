import json
from copy import deepcopy

import pytest

from jev_factorio.latency_report import analyze, distribution
from jev_factorio.observation import ObservationProfile


def write(path, rows):
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    return path


def record():
    profile = ObservationProfile()
    profile.subcall('entities', lambda: profile.rpc('other', lambda:'sensitive'))
    return {'session_id':'private-session','process_id':'private-process','code_revision':{'commit':'a'*40},
            'recorded_at_utc':'2026-09-26T00:00:01+00:00',
            'phases':[{'stage':'observe','status':'started','at_utc':'2026-09-26T00:00:00+00:00',
                       'seconds':None,'error_code':None}],
            'observation_profiles':[profile.summary()]}


def test_nearest_rank_and_even_median_are_explicit():
    result=distribution([1,2,3,100])
    assert result['median_ns']==2.5 and result['p95_ns']==100
    assert distribution([])['median_ns'] is None


def test_sanitized_report_reconciles_and_preserves_legacy_unknown(tmp_path):
    row=record()
    row['observation_profiles'].append({'schema':1,'total_ns':50})
    report=analyze(write(tmp_path/'log', [row]))
    assert report['legacy_profiles_without_cpu_partition']==1
    assert report['reconciled_partitions']==1
    assert report['distributions']['observation_process_cpu']['count']==1
    assert not report['native_acceptance_proven']
    assert 'private-' not in json.dumps(report) and 'sensitive' not in json.dumps(report)
    assert report['source_commit']=='a'*40


def test_gap_is_record_to_next_observation_not_watchdog_delay(tmp_path):
    first=record();second=deepcopy(first)
    second['recorded_at_utc']='2026-09-26T00:00:05+00:00'
    second['phases'][0]['at_utc']='2026-09-26T00:00:03+00:00'
    result=analyze(write(tmp_path/'log',[first,second]))
    assert result['distributions']['record_utc_to_next_observe_utc_gap']['median_ns']==2_000_000_000
    assert 'UTC' in result['scopes']['gap']


@pytest.mark.parametrize('damage', ['partition','negative','label','mixed','truncated','budget','nan'])
def test_bad_captures_fail_closed(tmp_path,damage):
    first=record();second=deepcopy(first)
    first['phases']=[];second['phases']=[]
    if damage=='partition': first['observation_profiles'][0]['wall_partition_ns']['rpc']+=1
    if damage=='negative': first['observation_profiles'][0]['process_cpu_ns']=-1
    if damage=='label': first['observation_profiles'][0]['calls']['private-url']={}
    if damage=='mixed': second['code_revision']['commit']='b'*40
    if damage=='nan': first['observation_profiles'][0]['total_ns']=float('nan')
    path=write(tmp_path/'log',[first,second])
    if damage=='truncated': path.write_text(path.read_text().rstrip())
    with pytest.raises(ValueError): analyze(path,max_records=1 if damage=='budget' else 100)
