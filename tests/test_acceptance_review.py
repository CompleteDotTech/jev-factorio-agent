"""Adversarial measurement fixtures, not real-world acceptance trials."""
from copy import deepcopy
from pathlib import Path

import pytest

from jev_factorio.acceptance_capture import capture
from jev_factorio.acceptance_io import canonical, load_json
from jev_factorio.native_acceptance import analyze, counter_delta, experiment, goal_observed
from test_native_acceptance import source_files, rewrite


@pytest.mark.parametrize('date', ['2020-01-01T00:00:00+00:00', '2030-01-01T00:00:00+00:00', '2026-09-24T00:00:00'])
def test_stale_future_or_timezone_free_preflight_cannot_pass(tmp_path,date):
    args,_=source_files(tmp_path/'inputs')
    value=load_json(args['preflight_path'].read_bytes());value['observed_at_utc']=date
    args['preflight_path'].write_bytes(canonical(value));capture(**args,environ={})
    result=analyze(args['output']);assert 'preflight_time_not_fresh' in result['issues']


def test_post_action_milestone_not_backdated_to_before_state():
    record={'completed_goals':{'steam_power':200}}
    assert not goal_observed('milestone:steam_power',{'tick':100},record)
    assert goal_observed('milestone:steam_power',{'tick':200},record)


@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),-1])
def test_invalid_counter_values_do_not_become_progress(value):
    assert counter_delta({'item':0},{'item':value}) is None


def test_redescribed_trial_identity_is_rejected(tmp_path):
    args,rows=source_files(tmp_path/'inputs')
    for row in rows: row['run_id']='not-declared-trial'
    rewrite(args,rows);capture(**args,environ={})
    assert 'trial_run_identity_mismatch' in analyze(args['output'])['issues']


def reports():
    base_config={'factory_scheduling':'ready-work','background_work':True,'furnace_output_buffers':True,
                 'furnace_input_belts':True,'mining_outposts':False,'ore_side_successors':False}
    result={}
    for i in range(3):
        for arm in ('baseline','treatment'):
            name=f'{arm}{i}'
            result[name]={'bundle_sha256':name,'gameplay_sha256':name,'measurement_checks_passed':True,
                'trial':{'experiment_id':'x','trial_id':name,'pair_id':str(i),'arm':arm,
                         'initial_save_sha256':'save','initial_checkpoint_sha256':'checkpoint',
                         'expected_policy':'hybrid','expected_model':'test','goal':'research:x',
                         'production_vm_uuid':'production','expected_commit':'baseline' if arm=='baseline' else 'treatment',
                         'configuration':{**base_config,'ore_side_successors':arm!='baseline'}},
                'runtime_identity':{'session':'same'},'resolved_models':['test'],
                'native_ticks':108000,'production_delta':{'pack':60},'consumption_delta':{'pack':60},
                'goal_first_observed_elapsed_ticks':1000,'goal_progress_delta':None,
                'newly_qualified_sources':['growth:iron-plate'] if arm=='treatment' else [],
                'qualified_sources_observed':['growth:iron-plate'] if arm=='treatment' else [],
                'new_successor_use_witnesses':[{'job_id':'witness'}] if arm=='treatment' else []}
    soak=deepcopy(result['treatment0']);soak.update(bundle_sha256='soak',gameplay_sha256='soak',native_ticks=432000)
    soak['trial'].update(arm='soak',trial_id='soak');result['soak']=soak
    return result


def compare(monkeypatch, data):
    monkeypatch.setattr('jev_factorio.native_acceptance.analyze',lambda path:deepcopy(data[str(path)]))
    return experiment([Path(key) for key in data],'pack')


def test_three_consistent_pairs_and_soak_still_cannot_authorize_deployment(monkeypatch):
    result=compare(monkeypatch,reports())
    assert result['measurement_checks_passed'],result['issues']
    assert not result['deployment_authorized'] and result['native_acceptance']=='not_accepted'
    assert result['outstanding_review_gates']


@pytest.mark.parametrize('field',['initial_save_sha256','initial_checkpoint_sha256','expected_model','goal'])
def test_pair_boundary_drift_is_rejected(monkeypatch,field):
    data=reports();data['treatment1']['trial'][field]='different'
    result=compare(monkeypatch,data)
    assert not result['measurement_checks_passed']
    assert any('pair_mismatch:'+field in p['issues'] for p in result['pairs'])


def test_baseline_code_drift_cannot_be_hidden_under_same_condition(monkeypatch):
    data=reports();data['baseline1']['trial']['expected_commit']='different'
    result=compare(monkeypatch,data)
    assert 'baseline_drift:expected_commit' in result['issues']


def test_flag_enabled_without_exercising_successor_is_not_success(monkeypatch):
    data=reports();data['treatment0']['newly_qualified_sources']=[]
    result=compare(monkeypatch,data)
    assert not result['measurement_checks_passed']
    assert 'successor_lifecycle_not_exercised' in result['pairs'][0]['issues']


def test_zero_tick_failed_run_is_visible_not_divided_or_dropped(monkeypatch):
    data=reports();data['treatment1'].update(native_ticks=0,measurement_checks_passed=False)
    result=compare(monkeypatch,data)
    assert not result['measurement_checks_passed'] and len(result['trials'])==7
    assert 'zero_simulation_horizon' in result['pairs'][1]['issues']


def test_slower_goal_and_consumption_regression_fail_pair(monkeypatch):
    data=reports();data['treatment1']['goal_first_observed_elapsed_ticks']=1001
    data['treatment1']['consumption_delta']['pack']=59
    result=compare(monkeypatch,data)
    assert 'next_goal_slower_or_unobserved' in result['pairs'][1]['issues']
    assert 'consumption_delta_rate_regressed' in result['pairs'][1]['issues']
