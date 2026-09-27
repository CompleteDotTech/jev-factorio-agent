"""Controller traces for durable catalog, dispatch, and ordinary plan continuity."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.solid_funding_evidence import funding_history_issues
from jev_factorio.skills import Plan, Step
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['unlock', 'research'])
def test_research_progress_does_not_rebind_static_catalog(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop.catalog.technologies['unlock-kit'] = {'effects': [{'type': 'unlock-recipe', 'recipe': 'inserter'}]}
    loop.catalog.technologies['next-study'] = deepcopy(loop.catalog.technologies['study'])
    if change == 'unlock': loop.catalog.recipes['inserter']['enabled'] = False
    loop._observe(); initial = asdict(loop.memory)
    if change == 'unlock': backend.state.researched.append('unlock-kit')
    else: backend.state.factory['research'] = 'next-study'
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'factory_craft', record
    assert not funding_history_issues(initial, [record], final)
    assert initial['solid_funding_catalogs'] == final['solid_funding_catalogs']


@pytest.mark.parametrize('action', ['craft', 'extract'])
@pytest.mark.parametrize('field', ['receipt', 'expected_unit_number'])
def test_kit_attempt_detached_endpoint_fields_are_bound(tmp_path, action, field):
    loop, backend = kit_loop(tmp_path)
    if action == 'extract':
        backend.state.inventory['copper-plate'] = 0
        backend.state.player_position = (0, 0)
        backend.state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
            output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'factory_' + action
    assert not funding_history_issues(initial, [record], final)
    record['attempt_outcomes'][-1][field] = 'unrelated-receipt' if field == 'receipt' else 8888
    final['attempt_outcomes'] = deepcopy(record['attempt_outcomes'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('change', ['erase', 'replace', 'tick'])
def test_final_catalog_declarations_preserve_initial_prefix(tmp_path, change):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    if change == 'erase': final['solid_funding_catalogs'] = {}
    else:
        declaration = next(iter(final['solid_funding_catalogs'].values()))
        if change == 'replace': declaration['acquisition_sha256'] = '1' * 64
        else: declaration['observed_tick'] += 1
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('source,called', [('deterministic', False), ('deterministic-singleton', False),
    ('deterministic-fallback', True), ('jev', False), ('mock', False)])
def test_strict_jev_rejects_impossible_selection_provenance(tmp_path, source, called):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    record['policy'] = 'jev'
    record['decision'].update(source=source, model_called=called)
    for event in record['history']:
        if event['kind'] == 'plan_committed': event['source'] = source
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


def test_service_outcomes_cannot_appear_in_an_observation(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    forged = deepcopy(initial['attempt_outcomes'][0])
    forged['id'] = 'e' * 32
    record['attempt_outcomes'].append(forged)
    final['attempt_outcomes'] = deepcopy(record['attempt_outcomes'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('definition', [False, True])
@pytest.mark.parametrize('name', ['ordinary-retained', 'ordinary:kit'])
def test_observed_ordinary_plan_cannot_be_replaced_by_a_kit(tmp_path, definition, name):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    ordinary = Plan(name, 'rocket_launch', 'Finish prior work', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', 999,
            parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    first = deepcopy(record)
    first.update(action='observe', verified=False, decision=None, attempt=None, pending=None,
        attempt_outcomes=deepcopy(initial['attempt_outcomes']), solid_funding=None,
        after_state=deepcopy(record['state']), history=deepcopy(initial['history']))
    event = {'kind': 'plan_committed', 'plan': ordinary.id, 'source': 'deterministic', 'tick': first['state']['tick']}
    if definition: event['definition'] = ordinary.to_dict()
    first['history'].append(event)
    record['history'].insert(0, event)
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [first, record], final)


def test_retained_ordinary_plan_cannot_disappear_from_final_checkpoint(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop.step()
    ordinary = Plan('ordinary-retained', 'rocket_launch', 'Finish prior work', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', 999,
            parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop.memory.active_plan = ordinary.to_dict()
    loop.memory.step_index = 0
    initial = asdict(loop.memory)
    loop._execution_barrier = lambda snapshot: True
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'observe'
    assert not funding_history_issues(initial, [record], final)
    final['active_plan'] = None
    assert funding_history_issues(initial, [record], final)


def test_genuine_ordinary_work_clears_before_the_next_kit(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop.step()  # Hold funding while the ordinary frontier does useful work.
    initial = asdict(loop.memory)
    ordinary = Plan('ordinary-gear', 'rocket_launch', 'Make one gear', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
            costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    compile_candidates = loop._compile_candidates
    loop._compile_candidates = lambda snapshot: ([ordinary], '')
    first = loop.step()
    assert first['verified'] and loop.memory.active_plan is None
    loop._compile_candidates = compile_candidates
    second = loop.step()
    assert second['verified'] and second['solid_funding']['actions'] == initial['solid_funding']['actions'] + 1
    assert not funding_history_issues(initial, [first, second], asdict(loop.memory))


@pytest.mark.parametrize('reconstruct', [False, True])
def test_verified_retained_acquisition_reconciles_after_lost_ack(reconstruct, tmp_path):
    from solid_routes_fixtures import INTENTS
    loop, backend = kit_loop(tmp_path)
    backend.lose_kit_ack = True
    assert not loop.step()['verified']
    initial = asdict(loop.memory)
    assert initial['pending'] and initial['active_plan'] and initial['solid_funding']
    backend.lose_kit_ack = False
    if reconstruct:
        loop = type(loop)(backend, target='rocket_launch', policy='deterministic',
            factory_scheduling='ready-work', tick_seconds=0, checkpoint=str(backend.checkpoint),
            resume_controller=True, solid_intents=INTENTS, solid_science_policy=True)
    recovered = loop.step()
    assert recovered['verified'] and len(backend.calls) == 1
    assert loop.memory.pending is None and loop.memory.failures == initial['failures']
    assert loop.memory.attempt_outcomes[-1]['id'] == initial['attempt']['id']
    assert not funding_history_issues(initial, [recovered], asdict(loop.memory))
