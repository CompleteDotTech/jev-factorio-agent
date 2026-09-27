"""World, catalog timing, ordinary progress and competing capital admission."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from jev_factorio.integration_evidence import analyze_rows
from jev_factorio.planning import solid_funding
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for, assert_rejected


def increment_fixture():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding']); proof['actions'] = 2
    history = [event('solid_kit_committed', proof, records[3]['state']['tick']),
               plan_event(proof, records[3]['state']['tick'])]
    records[3].update(action='verify', verified=True)
    step = history[0]['step']
    records[3]['after_state']['inventory'][step['item']] = step['threshold']
    decision_for(records[3], proof)
    for record in records[3:]: record.update(solid_funding=deepcopy(proof), history=deepcopy(history))
    final.update(solid_funding=deepcopy(proof), history=deepcopy(history))
    assert analyze_rows(*data)['measurement_checks_passed']
    return data


def test_mock_selection_cannot_authorize_a_live_kit():
    data = increment_fixture()
    records, _, _, final = data
    records[3]['decision']['source'] = 'mock'
    for history in [final['history'], *[record['history'] for record in records[3:]]]:
        for value in history:
            if value['kind'] == 'plan_committed': value['source'] = 'mock'
    assert_rejected(data)


def test_real_mock_selector_remains_valid_in_a_mock_world(tmp_path):
    from jev_factorio.jev_client import MockJevClient
    loop, backend = kit_loop(tmp_path)
    loop.policy = 'jev'
    loop.jev = MockJevClient()
    backend.state.world_kind = 'mock'
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['decision']['source'] == 'mock' and record['solid_funding']
    assert not funding_history_issues(initial, [record], final)


def test_barrier_does_not_advance_an_ordinary_plan(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop.step()
    ordinary = Plan('ordinary-barrier', 'rocket_launch', 'Keep progress behind barrier', (
        Step('factory_craft', 'inventory', 'iron-plate', 1,
            parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),
        Step('factory_craft', 'inventory', 'copper-plate', 1,
            parameters={'recipe': 'copper-cable', 'batches': 1})))
    loop.memory.active_plan = ordinary.to_dict()
    loop.memory.step_index = 0
    initial = asdict(loop.memory)
    loop._execution_barrier = lambda snapshot: True
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'observe' and final['step_index'] == 0
    assert not funding_history_issues(initial, [record], final)
    final['step_index'] = 1
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('retained_commit', [False, True])
def test_initial_catalog_declaration_must_predate_funding(retained_commit):
    data = funded_evidence()
    records, _, initial, final = data
    for proof in [initial['solid_funding'], final['solid_funding'], *[record['solid_funding'] for record in records]]:
        proof['started_tick'] -= 100
        proof['deadline_tick'] -= 100
    if retained_commit:
        history = [event('solid_kit_committed', initial['solid_funding'], initial['solid_funding']['started_tick'])]
        for checkpoint in (initial, final): checkpoint['history'] = deepcopy(history)
        for record in records: record['history'] = deepcopy(history)
    assert_rejected(data)


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('lost_ack', [False, True])
def test_ordinary_plan_skips_an_observed_satisfied_prefix(tmp_path, retained, lost_ack):
    loop, backend = kit_loop(tmp_path)
    loop.step()
    backend.state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
        output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    backend.state.player_position = (0, 0)
    def execute(action, parameters):
        saved = json.loads(backend.checkpoint.read_text())
        assert saved['pending']['dispatch'] == 'prepared' and saved['step_index'] == 1
        assert saved['active_plan']['steps'][1]['parameters'] == parameters
        assert action == 'factory_extract'
        backend.calls.append((action, deepcopy(parameters)))
        entity = backend.state.factory['entities'][parameters['role']]
        entity['output'][parameters['item']] -= parameters['quantity']
        backend.state.inventory[parameters['item']] = backend.state.inventory.get(parameters['item'], 0) + parameters['quantity']
        backend.state.factory['receipts'][parameters['receipt']] = {**{key: parameters[key] for key in ('role', 'item', 'quantity')},
            'extracting': True, 'unit_number': entity['unit_number']}
        backend.state.tick += 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
        if lost_ack: raise TimeoutError('Deliberately lost ordinary acknowledgement')
        return 'fixture ordinary extraction'
    backend.execute = execute
    ordinary = Plan('ordinary-two-step', 'rocket_launch', 'Skip the completed prefix', (
        Step('factory_craft', 'inventory', 'iron-plate', 1,
            parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),
        Step('factory_extract', 'transfer', parameters={'role': 'copper-source', 'item': 'copper-plate',
            'quantity': 1, 'receipt': f'{backend.state.tick}:factory_extract:copper-source:copper-plate'})))
    if retained:
        loop.memory.active_plan = ordinary.to_dict()
        loop.memory.step_index = 0
    else:
        loop._compile_candidates = lambda snapshot: ([ordinary], '')
    initial = asdict(loop.memory)
    records = [loop.step()]
    assert records[0]['action'] == 'factory_extract'
    if lost_ack:
        assert not records[0]['verified'] and loop.memory.step_index == 1
        assert not funding_history_issues(initial, records, asdict(loop.memory))
        records.append(loop.step())
    final = asdict(loop.memory)
    assert records[-1]['verified'] and len(backend.calls) == 2  # Initial kit plus one extraction.
    assert records[-1]['attempt_outcomes'][-1]['step_index'] == 1 and final['active_plan'] is None
    assert not funding_history_issues(initial, records, final)


@pytest.mark.parametrize('change', ['remove', 'zero', 'two', 'reason', 'extra', 'duplicate'])
def test_ordinary_failure_requires_its_exact_counter_and_event(tmp_path, change):
    loop, _ = kit_loop(tmp_path)
    loop.step()
    ordinary = Plan('ordinary-unaffordable', 'rocket_launch', 'No sufficient materials', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', 999,
            costs={'iron-plate': 1000000}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop.memory.active_plan = ordinary.to_dict()
    loop.memory.step_index = 0
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'observe' and record['verified'] is False
    assert record['failure_budgets'][ordinary.id] == 1 and final['active_plan'] is None
    assert not funding_history_issues(initial, [record], final)
    if change in {'remove', 'zero', 'two'}:
        for values in (record['failure_budgets'], final['failures']):
            if change == 'remove': values.pop(ordinary.id)
            else: values[ordinary.id] = 0 if change == 'zero' else 2
    else:
        failed = next(value for value in record['history'] if value.get('kind') == 'plan_failed')
        if change == 'reason': failed['reason'] = ''
        elif change == 'extra': failed['invented'] = True
        else: record['history'].append(deepcopy(failed))
        final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


def test_transient_kit_commit_cannot_coexist_with_active_capital():
    from test_capital_investments import scenario, offer
    from jev_factorio.planning import capital
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['started_tick'] = records[3]['state']['tick']
    proof['deadline_tick'] = proof['started_tick'] + solid_funding.MAX_TICKS
    initial['solid_funding'] = final['solid_funding'] = None
    for record in records: record['solid_funding'] = None
    history = [event('solid_kit_committed', proof, proof['started_tick']), plan_event(proof, proof['started_tick']),
        {'kind': 'plan_failed', 'plan': proof['key'] + ':kit', 'reason': 'Plan precondition changed',
         'tick': records[3]['after_state']['tick']},
        event('solid_kit_abandoned', proof, records[3]['after_state']['tick'], reason='kit_failure_budget')]
    records[3]['verified'] = False
    decision_for(records[3], proof)
    records[3]['after_state']['factory']['solid_routes']['routes'].pop(proof['route'])
    for record in records[3:]:
        record['history'] = deepcopy(history)
        record['failure_budgets'][proof['key'] + ':kit'] = 3
    final.update(history=deepcopy(history), failures=deepcopy(records[-1]['failure_budgets']))
    assert analyze_rows(*data)['measurement_checks_passed']
    catalog, state = scenario()
    spec = offer(catalog, state).materials[capital.MARKER]['spec']
    investment = {'spec': spec, 'stage': 'kit', 'started_tick': initial['last_tick'],
        'deadline_tick': initial['last_tick'] + 7200, 'unit_number': None, 'products_baseline': None}
    for checkpoint in (initial, final):
        checkpoint.update(active_goal='rocket_launch', capital_investment=deepcopy(investment))
    for record in records: record['capital_investment'] = deepcopy(investment)
    assert_rejected(data)


def test_retained_funding_cannot_overlap_transient_capital():
    from test_capital_investments import scenario, offer
    from jev_factorio.planning import capital
    data = funded_evidence()
    records, _, _, final = data
    catalog, state = scenario()
    spec = offer(catalog, state).materials[capital.MARKER]['spec']
    started = records[3]['state']['tick']
    records[3]['capital_investment'] = {'spec': spec, 'stage': 'kit', 'started_tick': started,
        'deadline_tick': started + 7200, 'unit_number': None, 'products_baseline': None}
    committed = {'kind': 'capital_committed', 'spec': spec, 'tick': started}
    records[3]['history'] = [deepcopy(committed)]
    history = [committed, {'kind': 'capital_abandoned', 'key': spec['key'], 'tick': records[4]['state']['tick']}]
    for record in records[4:]:
        record['history'] = deepcopy(history)
        record['failure_budgets'][spec['key']] = 2
    final.update(history=deepcopy(history), failures=deepcopy(records[-1]['failure_budgets']))
    assert_rejected(data)
