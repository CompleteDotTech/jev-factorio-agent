"""Synthetic display fixtures. No game, provider, checkpoint or release authority."""
from copy import deepcopy
import json
import time

import pytest

from jev_factorio.dashboard import EventWriter, Monitor, project_record, project_state, sanitize
from jev_factorio.dashboard_mission import MILESTONES, launch_summary, milestones


def mission_state():
    return {'session_id': 'test:mission', 'tick': 1000, 'world_kind': 'fle', 'victory': False,
        'victory_source': None, 'inventory': {'raw-fish': 5}, 'factory': {
        'entities': {'recipe:rocket-part': {'name': 'rocket-silo', 'unit_number': 30}},
        'research': 'rocket-silo', 'research_progress': 0.8,
        'launch_readiness': {'schema': 1, 'supported': True, 'version': '2.0.77',
            'session_id': 'test:mission', 'tick': 1000, 'actor_unit': 10, 'surface_index': 1,
            'force_index': 1, 'fault': False, 'pad': {'name': 'cargo-landing-pad',
                'unit_number': 50, 'position': {'x': 2, 'y': 0},
                'accepts': {'raw-fish': True, 'satellite': True}},
            'silo': {'unit_number': 30, 'rocket_unit': 31, 'ready': True, 'automatic': False,
                     'cargo_available': True, 'cargo': {'raw-fish': 1}},
            'attempts': {}, 'receipts': {}}}}


def gates(state):
    return {g['key']: g for g in launch_summary(state)['gates']}


def receipt():
    return {'kind': 'launch', 'actor_unit': 10, 'session_id': 'test:mission',
            'tick': 999, 'silo_unit': 30, 'rocket_unit': 31}


def test_observed_prerequisites_do_not_mean_submitted_or_victory():
    state = mission_state(); before = deepcopy(state)
    result = launch_summary(state)
    assert result['headline'] == 'Launch prerequisites observed'
    assert gates(state)['request']['state'] == 'pending'
    assert gates(state)['victory']['state'] == 'pending'
    assert state == before


def test_submitted_receipt_is_not_native_victory_or_action_verification():
    state = mission_state()
    state['factory']['launch_readiness']['receipts']['launch'] = receipt()
    result = project_record({'state': state, 'action': 'factory_launch', 'verified': True, 'status': 'completed'})
    launch = result['state']['mission']['launch']
    assert launch['headline'] == 'Launch submitted; victory unverified'
    assert next(g for g in launch['gates'] if g['key'] == 'request')['state'] == 'submitted'
    assert next(g for g in launch['gates'] if g['key'] == 'victory')['state'] == 'pending'
    assert result['mission_record']['native_acceptance'] == 'No acceptance report connected'


@pytest.mark.parametrize('world,source,expected', [
    ('fle', 'native:base-game-rocket-launch', 'observed'),
    ('mock', 'native:base-game-rocket-launch', 'unknown'),
    ('fle', 'model_claim', 'unknown'), (None, 'native:base-game-rocket-launch', 'unknown')])
def test_only_declared_native_flag_and_source_are_displayed_as_native_victory(world,source,expected):
    state = mission_state(); state.update(world_kind=world, victory=True, victory_source=source)
    assert gates(state)['victory']['state'] == expected


@pytest.mark.parametrize('key,value', [('schema', True), ('tick', 999), ('tick', True),
    ('session_id', 'other'), ('supported', None), ('version', '2.1'), ('actor_unit', True),
    ('surface_index', 0), ('receipts', []), ('silo', None), ('fault', 0)])
def test_malformed_or_mismatched_evidence_does_not_get_green_prerequisites(key, value):
    state = mission_state(); state['factory']['launch_readiness'][key] = value
    result = launch_summary(state)
    assert not result['evidence_valid']
    assert all(g['state'] == 'unknown' for g in result['gates'] if g['key'] != 'victory')


def test_fault_unknown_data_and_missing_launch_contract_remain_visible():
    state = mission_state(); state['factory']['launch_readiness']['fault'] = True
    assert launch_summary(state)['headline'] == 'Launch evidence fault'
    del state['factory']['launch_readiness']
    assert launch_summary(state)['headline'] == 'Launch evidence unavailable'


@pytest.mark.parametrize('change,key', [('automatic','rocket'), ('cargo','cargo'), ('capacity','cargo'), ('attempt','request')])
def test_explicit_obstructions_are_not_repaired_or_hidden(change,key):
    state = mission_state(); row = state['factory']['launch_readiness']
    if change == 'automatic': row['silo']['automatic'] = True
    if change == 'cargo': row['silo']['cargo'] = {'raw-fish': 2}
    if change == 'capacity': row['pad']['accepts']['raw-fish'] = False
    if change == 'attempt': row['attempts']['launch'] = {'tick': 999}
    assert gates(state)[key]['state'] == 'blocked'


@pytest.mark.parametrize('field,value', [('actor_unit', 12), ('session_id', 'other'), ('tick', 1001), ('silo_unit', 99)])
def test_wrong_or_future_receipt_cannot_imply_a_submitted_launch(field,value):
    state = mission_state(); rec = receipt(); rec[field] = value
    state['factory']['launch_readiness']['receipts']['launch'] = rec
    assert gates(state)['request']['state'] == 'unknown'


def test_loaded_payload_is_distinct_from_carried_payload_and_unrecorded_reservation():
    state = mission_state(); state['factory']['launch_readiness']['silo']['cargo'] = {}
    g = gates(state)
    assert g['payload']['state'] == 'observed' and 'reservation not independently captured' in g['payload']['detail']
    assert g['cargo']['state'] == 'pending'


def test_recorded_false_features_are_not_unknown_and_missing_failures_are_not_zero():
    record = project_record({'state': mission_state(), 'acceptance_configuration': {
        'ore_side_successors': False}, 'failure_budgets': {'old': 2}, 'code_revision': {'commit': 'a'*40}})
    info = record['mission_record']
    assert info['features']['ore_side_successors'] is False
    assert info['features']['mining_outposts'] is None and info['failure_count'] == 2
    assert project_record({'state': {}})['mission_record']['failure_count'] is None
    assert 'merge' in info['deployment_status']


def test_projection_is_bounded_and_redacted_even_with_large_factory():
    state = mission_state()
    state['factory']['input_routes'] = {'diagnostics': {f'role:{n}': {'reason': 'private-value https://example.invalid', 'survey_tick': 900, 'cached': True} for n in range(5000)}}
    state['factory']['unrelated'] = {'password': 'must-not-copy', 'raw_model_response': 'must-not-copy'}
    before = deepcopy(state)
    result = sanitize(project_state(state), ('private-value',))
    assert len(result['mission']['automation']) == 6
    assert result['mission']['automation'][0]['survey_tick'] == 900
    assert 'private-value' not in json.dumps(result) and 'must-not-copy' not in json.dumps(result)
    assert result['mission']['launch']['headline'] == 'Launch prerequisites observed'
    assert state == before


def test_legacy_and_sidecar_projection_agree_and_new_snapshot_clears_old_readiness(tmp_path):
    state = mission_state()
    legacy = tmp_path/'legacy.jsonl'; legacy.write_text(json.dumps({'action':'observe','state':state})+'\n')
    a = Monitor(legacy, legacy=True); a.poll()
    sidecar = tmp_path/'events.jsonl'
    with EventWriter(sidecar) as writer:
        writer.emit('observation', 2, state=project_state(state))
        b = Monitor(sidecar); b.poll()
        assert a.view['state']['mission'] == b.view['state']['mission']
        writer.emit('observation', 2, state=project_state({'session_id':'test:mission','tick':1001}))
        b.poll()
        assert b.view['state']['mission']['launch']['headline'] == 'Launch evidence unavailable'


def test_legacy_timestamp_not_copy_mtime_and_heartbeat_not_observation_freshness(tmp_path):
    state = mission_state(); path = tmp_path/'legacy'
    path.write_text(json.dumps({'action':'observe','state':state,'recorded_at_utc':'2020-01-01T00:00:00+00:00'})+'\n')
    monitor = Monitor(path, legacy=True); monitor.poll()
    assert monitor.view['state_observed_time'] == 1577836800
    assert monitor.view['legacy_record_timestamp'] is True
    event_path = tmp_path/'events'
    with EventWriter(event_path) as writer:
        writer.emit('observation',2,state=project_state(state))
        other=Monitor(event_path);other.poll();observed=other.view['state_observed_time']
        writer.emit('model_started',5);other.poll()
        assert other.view['state_observed_time'] == observed
        assert other.view['last_event_time'] >= observed


def test_large_decision_cannot_consume_display_budget_before_launch_observation():
    row = {'state': mission_state(), 'decision': {'large': {str(i): list(range(128)) for i in range(128)}}}
    projected = sanitize(project_record(row))
    assert projected['state']['mission']['launch']['headline'] == 'Launch prerequisites observed'


@pytest.mark.parametrize('missing_state', [None, [], 'truncated'])
def test_incomplete_decision_record_cannot_reuse_or_rejuvenate_prior_launch_state(tmp_path, missing_state):
    path = tmp_path / 'events'
    with EventWriter(path) as writer:
        writer.emit('observation', 2, state=project_state(mission_state()))
        monitor = Monitor(path); monitor.poll()
        assert monitor.view['state']['mission']['launch']['evidence_valid']
        record = {'action': 'observe', 'mission_record': {'tick': 1001}}
        if missing_state is not None:
            record['state'] = missing_state
        writer.emit('decision_recorded', 7, record=record)
        monitor.poll()
        assert monitor.view['state'] == {}
        assert monitor.view['state_observed_time'] is None


def by_key(rows):
    return {row['key']: row for row in rows}


def test_milestones_mark_only_recorded_facts_and_a_single_next_step():
    rows = milestones({'stockpile_fuel': 10, 'bootstrap_mining': 20},
                      {'steam-power': None, 'automation-science-pack': 500})
    assert [row['key'] for row in rows] == [key for _, key, _ in MILESTONES]
    keyed = by_key(rows)
    assert keyed['bootstrap_mining'] == {'key': 'bootstrap_mining', 'kind': 'goal', 'title': 'bootstrap_mining',
                                         'state': 'done', 'tick': 20}
    assert keyed['steam-power']['state'] == 'done' and keyed['steam-power']['tick'] is None
    assert keyed['automation-science-pack']['tick'] == 500
    assert [row['key'] for row in rows if row['state'] == 'next'] == ['logistic-science-pack']
    assert keyed['rocket_launch']['state'] == 'pending'


@pytest.mark.parametrize('goals,research', [(None, None), ('bad', []), ({'stockpile_fuel': True}, {'steam-power': 'x'}),
                                            ({'stockpile_fuel': -1}, {'steam-power': 1.5})])
def test_malformed_milestone_evidence_is_never_done(goals, research):
    rows = milestones(goals, research)
    assert not [row for row in rows if row['state'] == 'done']
    assert rows[0]['state'] == 'next'


def test_monitor_records_research_first_seen_without_backdating_the_tail_window(tmp_path):
    path = tmp_path / 'events'
    with EventWriter(path) as writer:
        writer.emit('observation', 2, state={'tick': 900, 'researched': ['steam-power', 'electronics']})
        writer.emit('observation', 2, state={'tick': 950, 'researched': ['steam-power', 'automation-science-pack']})
        writer.emit('observation', 2, state={'tick': 990, 'researched': ['steam-power', 'automation-science-pack']})
        writer.emit('goals', 3, completed_goals={'stockpile_fuel': 5}, target='rocket_launch')
        monitor = Monitor(path); monitor.poll()
        keyed = by_key(monitor.snapshot()['view']['milestones'])
        # Research present when observation began has no known tick; later research does.
        assert keyed['steam-power']['state'] == 'done' and keyed['steam-power']['tick'] is None
        assert keyed['automation-science-pack']['tick'] == 950
        assert keyed['stockpile_fuel']['tick'] == 5
        assert keyed['bootstrap_mining']['state'] == 'next'
        writer.emit('observation', 2, state={'tick': 'bad', 'researched': ['oil-processing']})
        monitor.poll()
        assert by_key(monitor.snapshot()['view']['milestones'])['oil-processing']['state'] != 'done'


def test_new_run_clears_research_milestones(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    with EventWriter(first) as writer:
        writer.emit('observation', 2, state={'tick': 1, 'researched': ['steam-power']})
    with EventWriter(second) as writer:
        writer.emit('observation', 2, state={'tick': 2, 'researched': []})
    monitor = Monitor(first); monitor.poll()
    assert by_key(monitor.snapshot()['view']['milestones'])['steam-power']['state'] == 'done'
    for row in second.read_text().splitlines():
        monitor.accept(json.loads(row))
    assert monitor.snapshot()['view']['milestones'][2]['state'] != 'done'
