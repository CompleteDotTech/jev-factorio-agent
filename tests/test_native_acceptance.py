"""All data below is synthetic, including deliberately native-shaped gate fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

import pytest

from jev_factorio.acceptance_io import canonical, load_json, sha256, stable_read
from jev_factorio.acceptance_capture import capture, verify
from jev_factorio.dev_preflight import inspect_native, probe
from jev_factorio.memory import CampaignMemory
from jev_factorio.native_acceptance import analyze, experiment

DEV = '00000000-0000-4000-8000-000000000001'
PROD = '00000000-0000-4000-8000-000000000002'
COMMIT = 'a' * 40


def native():
    return {'schema': 1, 'tick': 0, 'speed': 1, 'tick_paused': False, 'marked': True,
        'runtime_present': True, 'session_id': 'synthetic-native-shaped-session', 'player_index': 1,
        'connected': True, 'bound': True, 'campaign_present': True, 'fair_present': True,
        'actor_unit': 9, 'surface_index': 1, 'force_index': 1, 'mods': {'base': '2.0.77'},
        'entities': {}, 'input_routes': {}, 'output_buffers': {}, 'truncated': False}


def checkpoint(path, tick=0):
    value = CampaignMemory('synthetic-native-shaped-session', 'rocket_launch', last_tick=tick)
    value.save(path)
    return json.loads(path.read_text())


def source_files(root, trial_id='trial1', arm='baseline', pair_id='pair1', span=108000, world='fle'):
    root.mkdir()
    initial = root / 'initial.json'; final = root / 'final.json'
    first_cp = checkpoint(initial); checkpoint(final, span)
    save = root / 'save.zip'; save.write_bytes(b'synthetic save bytes, not a Factorio game')
    config = {'factory_scheduling': 'ready-work', 'background_work': True,
        'furnace_output_buffers': True, 'furnace_input_belts': True,
        'mining_outposts': False, 'ore_side_successors': arm != 'baseline'}
    trial = {'schema': 1, 'experiment_id': 'synthetic-experiment', 'trial_id': trial_id,
        'pair_id': pair_id, 'arm': arm, 'expected_commit': COMMIT, 'expected_policy': 'hybrid',
        'expected_model': 'synthetic-model', 'initial_save_sha256': sha256(save.read_bytes()),
        'initial_checkpoint_sha256': sha256(initial.read_bytes()), 'vm_uuid': DEV,
        'production_vm_uuid': PROD, 'goal': 'research:automation', 'configuration': config}
    preflight = {'schema': 'jev-factorio.dev-preflight.v1', 'vm_uuid': DEV, 'production_vm_uuid': PROD,
        'checkpoint_sha256': trial['initial_checkpoint_sha256'], 'native': native(),
        'ready_for_coordinated_validation': True, 'deployment_authorized': False,
        'observed_at_utc': '2026-09-24T00:00:00+00:00', 'query_sha256': 'f' * 64,
        'issues': [], 'gameplay_started': False}
    def state(tick):
        return {'tick': tick, 'session_id': first_cp['session_id'], 'world_kind': world,
            'player_position': [tick / 1800, 0], 'inventory': {}, 'researched': [],
            'victory': False, 'factory': {'tick': tick, 'entities': {}, 'receipts': {},
                'research': 'automation', 'research_progress': 0.1 + 0.7 * tick / span,
                'produced': {'automation-science-pack': tick // 1800},
                'consumed': {'automation-science-pack': tick // 1800},
                'acceptance_runtime': {k: v for k, v in native().items() if k in {
                    'schema','speed','tick_paused','session_id','actor_unit','player_index','surface_index','force_index','mods'}}}}
    rows = []
    start = datetime(2026, 9, 24, tzinfo=timezone.utc)
    for tick in range(0, span, 1800):
        rows.append({'schema_version': 2, 'controller': 'hierarchical', 'session_id': first_cp['session_id'],
            'world_kind': world, 'target': 'rocket_launch', 'policy': 'hybrid', 'requested_model': 'synthetic-model',
            'resolved_model': None, 'run_id': trial_id, 'segment_id': 'one', 'execution_id': trial_id,
            'code_revision': {'commit': COMMIT, 'source_sha256': 'b' * 64},
            'recorded_at_utc': (start + timedelta(seconds=tick/60)).isoformat(),
            'tick': tick, 'state': state(tick), 'after_state': state(tick + 1800), 'action': 'observe',
            'verified': False, 'outcome': 'Synthetic observation', 'status': 'running',
            'model_call': False, 'usage': None, 'phases': [], 'attempt': None, 'attempt_outcomes': [],
            'pending': None, 'performance': {}, 'capacity_evidence': {}, 'planning_diagnostics': {},
            'failure_budgets': {}, 'fair_action_metrics': {'move_requests': tick//1800},
            'completed_goals': {}, 'acceptance_configuration': deepcopy(config)})
    # Include final record to measure the full two-hour wall horizon in soak tests.
    rows.append({**deepcopy(rows[-1]), 'recorded_at_utc': (start + timedelta(seconds=span/60)).isoformat(),
                 'tick': span, 'state': state(span), 'after_state': state(span)})
    for name, value in (('trial.json', trial), ('preflight.json', preflight)):
        (root / name).write_bytes(canonical(value))
    log = root / 'gameplay.jsonl'; log.write_bytes(b''.join(canonical(row) for row in rows))
    return dict(gameplay=log, initial_checkpoint=initial, final_checkpoint=final, save=save,
                trial_path=root/'trial.json', preflight_path=root/'preflight.json', output=root/'capture'), rows


def rewrite(args, rows):
    args['gameplay'].write_bytes(b''.join(canonical(row) for row in rows))


@pytest.fixture
def bundle(tmp_path):
    args, rows = source_files(tmp_path / 'inputs')
    capture(**args, environ={})
    return args['output']


def rehash(directory):
    values = [sha256(p.read_bytes()) + '  ' + p.name for p in sorted(directory.iterdir()) if p.name != 'SHA256SUMS']
    (directory/'SHA256SUMS').write_text('\n'.join(values)+'\n')


def test_native_shaped_fixture_only_passes_measurement_checks_not_deployment(bundle):
    result = analyze(bundle)
    assert result['measurement_checks_passed'], result['issues']
    assert result['production_delta']['automation-science-pack'] == 60
    assert result['consumption_delta']['automation-science-pack'] == 60
    assert result['goal_progress_delta'] == pytest.approx(0.7)
    assert result['native_acceptance'] == 'not_accepted'
    assert not result['deployment_authorized']
    assert result['metrics']['actor_idle_seconds'] is None
    assert result['metrics']['sampled_travel_lower_bound']['actual_path_distance'] is None


@pytest.mark.parametrize('name', ['trial.json','initial-checkpoint.json','preflight.json','gameplay.jsonl.gz'])
def test_hash_tampering_prevents_any_report(bundle, name):
    with (bundle/name).open('ab') as stream: stream.write(b'changed')
    with pytest.raises(ValueError): analyze(bundle)


def test_valid_outer_checksum_cannot_hide_bad_gzip(bundle):
    p=bundle/'gameplay.jsonl.gz'; data=bytearray(p.read_bytes()); data[-8]^=1;p.write_bytes(data);rehash(bundle)
    with pytest.raises((OSError,ValueError)): verify(bundle)


def test_oversized_decompression_manifest_fails_before_expansion(bundle):
    p=bundle/'capture-manifest.json'; value=load_json(p.read_bytes());value['decompressed_bytes']=10**12
    p.write_bytes(canonical(value));rehash(bundle)
    with pytest.raises(ValueError, match='budget'):verify(bundle)


def test_duplicate_checksum_entry_rejected(bundle):
    p=bundle/'SHA256SUMS';p.write_text(p.read_text()+p.read_text().splitlines()[0]+'\n')
    with pytest.raises(ValueError):verify(bundle)


def test_symlink_and_duplicate_json_keys_rejected(tmp_path):
    p=tmp_path/'real';p.write_text('{}');q=tmp_path/'link';q.symlink_to(p)
    with pytest.raises((OSError,ValueError)):stable_read(q)
    with pytest.raises(ValueError):load_json('{"tick":1,"tick":2}')
    with pytest.raises(ValueError):load_json('{"value":1e999}')


def test_partial_or_blank_jsonl_fails_without_creating_capture(tmp_path):
    args,rows=source_files(tmp_path/'inputs');args['gameplay'].write_bytes(canonical(rows[0]).rstrip(b'\n'))
    with pytest.raises(ValueError):capture(**args,environ={})
    assert not args['output'].exists()


def test_existing_destination_is_never_overwritten(bundle, tmp_path):
    args,_=source_files(tmp_path/'other');args['output']=bundle
    before=(bundle/'SHA256SUMS').read_bytes()
    with pytest.raises(ValueError):capture(**args,environ={})
    assert (bundle/'SHA256SUMS').read_bytes()==before


def test_projection_keeps_operational_evidence_not_questions_provider_bodies_or_known_secrets(tmp_path):
    args,rows=source_files(tmp_path/'inputs')
    rows[0].update(prompt='do-not-export', provider_response={'api_key':'secret-value'},
                   outcome='https://private.invalid/?key=secret-value',
                   decision={'plan_id':'keep-plan','source':'jev','state':{'questions':['do-not-export']}})
    rows[0]['planning_diagnostics']={'ranked_plan_ids':['keep-plan']}
    rows[0]['state']['factory']['successors']={'sources':{},'tick':0}
    rewrite(args,rows);capture(**args,environ={'TYPESAFE_API_KEY':'secret-value'})
    result=verify(args['output']);raw=canonical(result['rows'])
    assert b'do-not-export' not in raw and b'secret-value' not in raw and b'private.invalid' not in raw
    assert result['rows'][0]['planning_diagnostics']['ranked_plan_ids']==['keep-plan']
    assert 'successors' in result['rows'][0]['state']['factory']
    assert 'produced' in result['rows'][0]['after_state']['factory']


@pytest.mark.parametrize('case,expected', [
    ('mock','synthetic_or_unknown_world'),('runtime','native_runtime_coverage_missing'),
    ('speed','simulation_speed_or_pause_changed'),('actor','native_actor_mod_or_surface_drift'),
    ('config','configuration_mismatch_or_missing'),('model','model_usage_incomplete'),
    ('counter','consumption_coverage_or_counter_regression'),('source','source_revision_mismatch')])
def test_incomplete_or_incompatible_evidence_is_not_accepted(tmp_path,case,expected):
    args,rows=source_files(tmp_path/'inputs',world='mock' if case=='mock' else 'fle')
    for row in rows:
        if case=='config':row.pop('acceptance_configuration')
        if case=='model':row['model_call']=True
        if case=='source':row['code_revision']['commit']='c'*40
        for label in ('state','after_state'):
            f=row[label]['factory']
            if case=='runtime':f.pop('acceptance_runtime')
            if case=='speed':f['acceptance_runtime']['speed']=2
            if case=='counter':f.pop('consumed')
    if case=='actor':rows[-1]['after_state']['factory']['acceptance_runtime']['actor_unit']=999
    rewrite(args,rows);capture(**args,environ={});report=analyze(args['output'])
    assert not report['measurement_checks_passed'] and expected in report['issues']
    assert not report['deployment_authorized']


def test_duplicate_records_do_not_double_model_cost(tmp_path):
    args,rows=source_files(tmp_path/'inputs');rows.insert(1,deepcopy(rows[0]));rewrite(args,rows)
    capture(**args,environ={})
    with pytest.raises(ValueError,match='Duplicate'):analyze(args['output'])


def test_reusing_a_capture_as_multiple_replicates_is_rejected(bundle):
    with pytest.raises(ValueError,match='Duplicate'):experiment([bundle,bundle],'automation-science-pack')


def test_no_pairs_or_soak_cannot_pass_experiment_gate(bundle):
    result=experiment([bundle],'automation-science-pack')
    assert 'three_valid_matched_pairs_required' in result['issues']
    assert 'complete_treatment_soak_required' in result['issues']
    assert not result['deployment_authorized']


def test_two_hour_soak_requires_both_native_and_wall_horizon(tmp_path):
    args,rows=source_files(tmp_path/'inputs',arm='soak',span=432000)
    capture(**args,environ={});assert analyze(args['output'])['measurement_checks_passed']
    args2,rows2=source_files(tmp_path/'short',arm='soak',span=108000)
    capture(**args2,environ={});report=analyze(args2['output'])
    assert 'trial_horizon_incomplete' in report['issues'] and 'soak_wall_horizon_incomplete' in report['issues']


def probe_inputs(root):
    root.mkdir(); cp=root/'checkpoint.json';checkpoint(cp)
    password=root/'credential';password.write_text('secret-not-for-logs\n');password.chmod(0o600)
    config=root/'config.json';config.write_bytes(canonical({'schema':1,'vm_uuid':DEV,'production_vm_uuid':PROD,
        'session_id':'synthetic-native-shaped-session','port':27015,'password_file':str(password)}));config.chmod(0o600)
    dmi=root/'dmi';dmi.write_text(DEV)
    return config,cp,dmi


def test_probe_is_guest_local_and_sends_one_fixed_query_then_closes(tmp_path):
    config,cp,dmi=probe_inputs(tmp_path/'probe'); calls=[]
    class Client:
        def __init__(self,host,port,password,timeout):
            calls.append(('connect',host,port,timeout))
            assert password=='secret-not-for-logs'
        def send_command(self,command):
            calls.append(('query',command));return canonical(native()).decode()
        def close(self):calls.append(('close',))
    before=cp.read_bytes();result=probe(config,cp,client_factory=Client,dmi_path=dmi)
    assert result['ready_for_coordinated_validation'] and not result['gameplay_started']
    assert cp.read_bytes()==before and calls[0]==('connect','127.0.0.1',27015,10)
    assert len(calls)==3 and calls[-1]==('close',)
    assert 'secret-not-for-logs' not in canonical(result).decode()
    assert 'campaign.observe(' not in calls[1][1]


@pytest.mark.parametrize('case',['wrong_vm','production_vm','public_config','pending_checkpoint','wrong_session'])
def test_invalid_preflight_never_opens_network(tmp_path,case):
    config,cp,dmi=probe_inputs(tmp_path/'probe')
    if case=='wrong_vm':dmi.write_text('00000000-0000-4000-8000-000000000003')
    if case=='production_vm':dmi.write_text(PROD)
    if case=='public_config':config.chmod(0o644)
    if case=='pending_checkpoint':
        value=load_json(cp.read_bytes());value['status']='uncertain';cp.write_bytes(canonical(value))
    if case=='wrong_session':
        value=load_json(config.read_bytes());value['session_id']='different';config.write_bytes(canonical(value))
    def no_network(*args,**kwargs):raise AssertionError('Must not connect')
    with pytest.raises((ValueError,OSError)):probe(config,cp,client_factory=no_network,dmi_path=dmi)


def test_probe_reports_missing_runtime_without_initializing_it(tmp_path):
    cp=checkpoint(tmp_path/'cp');value=native();value.update(runtime_present=False,session_id='',bound=False)
    issues=inspect_native(value,cp,cp['session_id'])
    assert 'native_runtime_present_missing' in issues and 'session_mismatch' in issues


def test_probe_requires_retained_paid_receipts_not_just_a_live_furnace(tmp_path):
    cp=checkpoint(tmp_path/'cp');cp['input_commitments']={'recipe:iron-plate':{
        'source_unit':17,'layout':'input:17','parts':{'drill':{'role':'drill:17','unit_number':18,'receipt':'paid:1','paid':1}}}}
    value=native();value['entities']={'recipe:iron-plate':{'unit_number':17},'drill:17':{'unit_number':18}}
    assert 'owned_input_routes_mismatch' in inspect_native(value,cp,cp['session_id'])
