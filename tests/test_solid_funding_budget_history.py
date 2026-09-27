"""Checkpoint history and funding budgets retain the controller's causal limits."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.solid_routes import MAX_BELTS, remaining
from jev_factorio.integration_evidence import analyze_rows
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop
from test_solid_funding_evidence import funded_evidence, close_funding, event, plan_event, assert_rejected


@pytest.mark.parametrize('action,verified', [('observe', True), ('observe', False), ('reconcile', False)])
def test_plan_failed_label_alone_cannot_release_funding(action, verified):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = records[3]['after_state']['tick']
    history = [{'kind': 'plan_failed', 'plan': proof['key'] + ':kit', 'tick': tick,
                'reason': 'Plan precondition changed'},
               event('solid_kit_abandoned', proof, tick, reason='kit_failure_budget')]
    records[3].update(action=action, verified=verified)
    for record in records[3:]:
        record.update(history=deepcopy(history), solid_funding=None)
        record['failure_budgets'][proof['key'] + ':kit'] = 2
    final.update(solid_funding=None, failures=deepcopy(records[-1]['failure_budgets']))
    assert_rejected(data)


def test_reordered_short_history_cannot_invent_a_new_plan_occurrence():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding']); proof['actions'] = 2
    tick = records[3]['state']['tick']
    plan = plan_event(proof, tick)
    records[2]['history'] = [deepcopy(plan)]
    history = [event('solid_kit_committed', proof, tick), plan]
    for record in records[3:]:
        record.update(history=deepcopy(history), solid_funding=deepcopy(proof))
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('count', [3, 999])
def test_deadline_abandonment_budget_is_exact(count):
    data = funded_evidence()
    close_funding(data)
    key = data[2]['solid_funding']['key'] + ':kit'
    for record in data[0][3:]:
        record['failure_budgets'][key] = count
    data[3]['failures'][key] = count
    assert_rejected(data)


@pytest.mark.parametrize('change', ['future', 'proof', 'key', 'kind', 'extra', 'reason'])
def test_initial_funding_events_require_valid_transition_schema(change):
    data = funded_evidence()
    initial = data[2]
    value = event('solid_kit_committed', initial['solid_funding'], initial['last_tick'])
    if change == 'future': value['tick'] += 1
    elif change == 'proof': value['funding'] = None
    elif change == 'key': value['key'] = 'wrong'
    elif change == 'kind': value['kind'] = 'solid_kit_invented'
    elif change == 'extra': value['extra'] = True
    else:
        value.update(kind='solid_kit_abandoned', reason='invented')
    initial['history'] = [value]
    for record in data[0]: record['history'] = [deepcopy(value)]
    assert_rejected(data)


def test_maxed_incomplete_kit_cannot_remain_held_without_an_active_plan():
    data = funded_evidence()
    data[2]['solid_funding']['actions'] = solid_funding.MAX_ACTIONS
    data[3]['solid_funding']['actions'] = solid_funding.MAX_ACTIONS
    for record in data[0]:
        record['solid_funding']['actions'] = solid_funding.MAX_ACTIONS
        for label in ('state', 'after_state'):
            record[label]['inventory'].update(inserter=0, **{'transport-belt': 0})
    assert_rejected(data)


@pytest.mark.parametrize('part', ['receive', 'send', 'belt:1', f'belt:{MAX_BELTS}'])
def test_project_component_failure_exhaustion_prevents_kit_commit(part):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding']); proof['actions'] = 2
    key = proof['key'] + ':' + part
    initial['failures'][key] = final['failures'][key] = 2
    for record in records: record['failure_budgets'][key] = 2
    tick = records[3]['state']['tick']
    history = [event('solid_kit_committed', proof, tick), plan_event(proof, tick)]
    for record in records[3:]:
        record.update(solid_funding=deepcopy(proof), history=deepcopy(history))
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def test_complete_carried_kit_does_not_require_release_at_action_limit():
    data = funded_evidence()
    route = data[2]['solid_funding']['route']
    for checkpoint in data[2:]: checkpoint['solid_funding']['actions'] = solid_funding.MAX_ACTIONS
    for record in data[0]:
        record['solid_funding']['actions'] = solid_funding.MAX_ACTIONS
        for label in ('state', 'after_state'):
            snapshot = record[label]
            snapshot['inventory'].update(remaining(snapshot['factory']['solid_routes']['routes'][route]))
    result = analyze_rows(*data)
    assert result['measurement_checks_passed'], result['issues']


def test_retained_budget_cannot_increase_without_proven_failure():
    data = funded_evidence()
    key = data[2]['solid_funding']['key'] + ':kit'
    for record in data[0][3:]: record['failure_budgets'][key] = 1
    data[3]['failures'][key] = 1
    assert_rejected(data)


@pytest.mark.parametrize('ordinary,kit_count', [(2, 0), (1, 1)])
def test_unbound_ordinary_acquisition_budget_cannot_authorize_a_commit(ordinary, kit_count):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding']); proof['actions'] = 2
    counts = {proof['key'] + ':kit': kit_count, 'factory:factory_craft:inserter': ordinary}
    for checkpoint in (initial, final): checkpoint['failures'].update(counts)
    for record in records: record['failure_budgets'].update(counts)
    tick = records[3]['state']['tick']
    history = [event('solid_kit_committed', proof, tick), plan_event(proof, tick)]
    for record in records[3:]:
        record.update(history=deepcopy(history), solid_funding=deepcopy(proof))
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def test_last_allowed_actual_kit_action_finishes_before_budget_reconciliation(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    # Seed the retained boundary immediately before the last allowed commit.
    loop.memory.solid_funding['actions'] = solid_funding.MAX_ACTIONS - 1
    initial = asdict(loop.memory)
    records = [loop.step()]
    assert records[0]['verified']
    assert loop.memory.solid_funding['actions'] == solid_funding.MAX_ACTIONS
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    records.append(loop.step())
    assert loop.memory.solid_funding is None
    assert not funding_history_issues(initial, records, asdict(loop.memory))
