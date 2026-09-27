"""Admission, release cause, and deadline ownership regression traces."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['catalog', 'demand'])
@pytest.mark.parametrize('advance', [False, True])
def test_first_deferred_release_cause_survives_restored_eligibility(tmp_path, change, advance):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    proof = deepcopy(loop.memory.solid_funding)
    loop.memory.active_plan = Plan(proof['key'] + ':kit', 'rocket_launch', 'Retained kit', (
        Step('factory_craft', 'inventory', 'inserter', 2,
             parameters={'recipe': 'inserter', 'batches': 1}),)).to_dict()
    recipes = deepcopy(loop.catalog.recipes)
    if change == 'catalog': loop.catalog.recipes['inserter']['ingredients'][0]['amount'] += 1
    else: backend.state.factory['research_progress'] = 1
    loop._reconcile_solid_funding(backend.state)
    expected = 'kit_catalog_changed' if change == 'catalog' else 'kit_demand_or_payback_changed'
    assert loop._solid_funding_release[1] == expected
    loop.catalog.recipes.clear()
    loop.catalog.recipes.update(recipes)
    backend.state.factory['research_progress'] = 0
    if advance:
        backend.state.tick += 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    loop.memory.last_tick = backend.state.tick
    loop._reconcile_solid_funding(backend.state)
    loop._clear_plan()
    release = loop.memory.history[-1]
    assert release['kind'] == 'solid_kit_abandoned'
    assert release['reason'] == expected and release['funding'] == proof
    assert release['tick'] == backend.state.tick and loop._solid_funding_release is None


@pytest.mark.parametrize('pending', [False, True])
def test_owned_service_attempt_requires_available_source_before_dispatch(tmp_path, pending):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    backend.state.player_position = (0, 0)
    backend.state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
        output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    plan = Plan('ordinary-extraction', 'rocket_launch', 'Ordinary extraction', (
        Step('factory_extract', 'transfer', parameters={'role': 'copper-source', 'item': 'copper-plate',
            'quantity': 10, 'receipt': f'{backend.state.tick}:factory_extract:copper-source:copper-plate'}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    backend.lose_kit_ack = pending
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'factory_extract' and record['verified'] is (not pending)
    assert not funding_history_issues(initial, [record], final)
    record['state']['factory']['entities']['copper-source']['output']['copper-plate'] = 9
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('retained', [False, True])
def test_deadline_deferral_requires_observed_plan_including_pending(tmp_path, pending, retained):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    proof = deepcopy(loop.memory.solid_funding)
    plan = Plan('ordinary-gears', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    if retained:
        loop.memory.active_plan = plan.to_dict()
        loop.memory.step_index = 0
    else: loop._compile_candidates = lambda snapshot: ([plan], '')
    initial = asdict(loop.memory)
    def advance():
        backend.state.tick = proof['deadline_tick'] + 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    backend.kit_after = advance
    backend.lose_kit_ack = pending
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'factory_craft' and record['verified'] is (not pending)
    assert not funding_history_issues(initial, [record], final)
    if retained:
        initial['active_plan'] = None
    else:
        record['history'] = [value for value in record['history']
            if not (value.get('kind') == 'plan_committed' and value.get('plan') == plan.id)]
        final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)
