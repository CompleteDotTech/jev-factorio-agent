"""Funding reconciliation uses observations and actual controller boundaries."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event, plan_event, close_funding, assert_rejected
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['missing', 'rebound'])
def test_retained_route_cannot_disappear_before_action_and_return_after(change):
    data = funded_evidence()
    records, _, initial, _ = data
    routes = records[3]['state']['factory']['solid_routes']['routes']
    route = initial['solid_funding']['route']
    if change == 'missing':
        routes.pop(route)
    else:
        routes[route]['layout'] = 'changed-before-action'
    assert_rejected(data)


@pytest.mark.parametrize('action', ['factory_insert', 'factory_solid_build', 'factory_wait', 'idle'])
def test_kit_commit_cannot_accompany_an_unrelated_action(action):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] += 1
    tick = records[3]['state']['tick']
    records[3].update(action=action, history=[event('solid_kit_committed', proof, tick),
                                            plan_event(proof, tick)])
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('action', ['observe', 'verify', 'factory_craft'])
def test_commit_requires_the_corresponding_selected_plan(action):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] += 1
    records[3].update(action=action, history=[event('solid_kit_committed', proof, records[3]['state']['tick'])])
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('action', ['observe', 'verify', 'factory_craft'])
def test_deadline_crossing_requires_pending_or_completed_dispatch_evidence(action):
    data = funded_evidence()
    records, _, initial, final = data
    deadline = records[3]['state']['tick'] + 1
    initial['solid_funding']['deadline_tick'] = deadline
    final['solid_funding']['deadline_tick'] = deadline
    for record in records:
        record['solid_funding']['deadline_tick'] = deadline
    records[3]['action'] = action
    close_funding(data, index=4)
    assert_rejected(data)


def test_abandonment_cannot_precede_current_observation():
    data = funded_evidence()
    records, _, _, _ = data
    close_funding(data)
    before = records[3]['state']
    old_tick = before['tick']
    before['tick'] += 100
    before['factory']['tick'] = before['factory']['solid_routes']['tick'] = before['tick']
    for route in before['factory']['solid_routes']['routes'].values():
        if route['flow']:
            route['flow']['last_tick'] = before['tick']
    records[3]['history'][0]['tick'] = old_tick + 50
    assert_rejected(data)


@pytest.mark.parametrize('corruption', [None, 'missing', 'stale', 'malformed', 'unverified'])
def test_real_successful_dispatch_can_cross_funding_deadline(tmp_path, corruption):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    def advance():
        backend.state.tick += solid_funding.MAX_TICKS + 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    backend.kit_after = advance
    records = [loop.step()]
    assert records[0]['verified'] and records[0]['pending'] is None
    assert loop.memory.solid_funding is not None
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    if corruption is not None:
        altered = deepcopy(records)
        if corruption == 'missing':
            altered[0]['attempt_outcomes'] = []
        elif corruption == 'stale':
            altered[0]['attempt_outcomes'][-1]['started_tick'] = altered[0]['state']['tick'] - 1
        elif corruption == 'malformed':
            altered[0]['attempt_outcomes'][-1].pop('step_sha256')
        else:
            altered[0]['verified'] = False
        assert funding_history_issues(initial, altered, asdict(loop.memory))
    records.append(loop.step())
    assert loop.memory.solid_funding is None
    assert not funding_history_issues(initial, records, asdict(loop.memory))


def test_real_already_satisfied_kit_plan_records_verify_without_dispatch(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    def satisfy():
        if backend.observations == 3:
            step = loop.memory.active_plan['steps'][0]
            backend.state.inventory[step['item']] = step['threshold']
    backend.before_observe = satisfy
    record = loop.step()
    assert record['action'] == 'verify' and record['verified']
    assert not backend.calls
    assert not funding_history_issues(initial, [record], asdict(loop.memory))


def test_real_ordinary_production_can_cross_deadline_while_funding_is_held(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    initial = asdict(loop.memory)
    proof = deepcopy(loop.memory.solid_funding)
    plan = Plan('ordinary-gear-production', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel',
             backend.state.inventory.get('iron-gear-wheel', 0) + 1,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    # Supply an ordinary frontier plan; execution, pending state, and evidence
    # still go through the composed production controller.
    loop._compile_candidates = lambda snapshot: ([plan], '')
    def advance():
        backend.state.tick = proof['deadline_tick'] + 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    backend.kit_after = advance
    record = loop.step()
    assert record['action'] == 'factory_craft' and record['verified']
    assert record['solid_funding'] == proof
    assert not funding_history_issues(initial, [record], asdict(loop.memory))


@pytest.mark.parametrize('missing', [False, True])
def test_initially_unbound_project_can_be_abandoned_at_observation(missing):
    from jev_factorio.integration_evidence import analyze_rows
    data = funded_evidence()
    records, _, initial, _ = data
    close_funding(data)
    records[3]['history'][0]['tick'] = records[3]['state']['tick']
    records[3]['history'][0]['reason'] = 'kit_endpoint_or_layout_changed'
    for record in records[4:]: record['history'] = deepcopy(records[3]['history'])
    routes = records[3]['state']['factory']['solid_routes']['routes']
    if missing:
        routes.pop(initial['solid_funding']['route'])
    else:
        routes[initial['solid_funding']['route']]['layout'] = 'replacement-layout'
    result = analyze_rows(*data)
    assert result['measurement_checks_passed'], result['issues']


def test_abandonment_may_follow_an_intermediate_pre_dispatch_observation():
    from jev_factorio.integration_evidence import analyze_rows
    data = funded_evidence()
    close_funding(data)
    data[0][3]['history'][0]['tick'] = data[0][3]['state']['tick'] + 1
    deadline = data[0][3]['history'][0]['tick']
    data[2]['solid_funding']['deadline_tick'] = deadline
    for record in data[0][:3]:
        record['solid_funding']['deadline_tick'] = deadline
    data[0][3]['history'][0]['funding']['deadline_tick'] = deadline
    for record in data[0][4:]: record['history'] = deepcopy(data[0][3]['history'])
    result = analyze_rows(*data)
    assert result['measurement_checks_passed'], result['issues']
