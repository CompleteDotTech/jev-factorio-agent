"""Successor kit protection is identical in runtime and read-only replay."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import _dispatch_admitted, funding_history_issues
from jev_factorio import successors
from test_successors import empty_state, site_offer, GROWTH, KIND, Backend
from test_solid_kit_acquisition import kit_loop


def project_for(state):
    site = site_offer(state)
    return {'anchor': site['anchor'], 'predecessor_unit': 500, 'source_unit': 0,
            'started_tick': state.tick, 'deadline_tick': state.tick + successors.MAX_PROJECT_TICKS, 'status': 'active'}


@pytest.mark.parametrize('surplus', [False, True])
def test_successor_kit_gate_matches_runtime_admission(surplus):
    state = empty_state(); project = project_for(state)
    state.factory['solid_routes'] = dict(protocol=1, session_id=state.session_id, tick=state.tick,
        actor_index=1, surface_index=1, force_index=1, routes={}, diagnostics=[])
    state.inventory['transport-belt'] = 4 if surplus else 3
    step = Step('factory_insert', 'transfer', costs={'transport-belt': 1}, parameters={
        'role': 'recipe:iron-plate', 'item': 'transport-belt', 'quantity': 1, 'receipt': 'urgent-belt'})
    materials = {}
    plan = Plan('urgent-belt', 'rocket_launch', 'Insert carried belt', (step,), materials=materials)
    loop = KIND(Backend(state), policy='deterministic', target='rocket_launch', factory_scheduling='ready-work')
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', last_tick=state.tick)
    loop.memory.successor_projects[GROWTH] = project
    actual = loop._investment_step_allowed(plan, step, state)
    assert actual is surplus
    record = {'state': state.for_jev(), 'ore_side_successors': True, 'successor_projects': {GROWTH: project}}
    assert _dispatch_admitted(step, record, plan.id, {}) is actual


@pytest.mark.parametrize('change', ['drop', 'pause', 'anchor'])
def test_record_cannot_release_the_initial_successor_lock(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    state = empty_state(); project = project_for(state)
    for captured in (record['state'], record['after_state']):
        site_data = deepcopy(state.factory['production_sites'])
        site_data.update(session_id=captured['session_id'], tick=captured['tick'])
        captured['factory']['production_sites'] = site_data
        captured['factory']['successors'] = dict(protocol=1, session_id=captured['session_id'],
            tick=captured['tick'], sources={})
    for checkpoint in (initial, final):
        checkpoint.update(successor_schema=1, successor_projects={GROWTH: deepcopy(project)})
    record.update(ore_side_successors=True, successor_projects={GROWTH: deepcopy(project)})
    assert not funding_history_issues(initial, [record], final)
    if change == 'drop': record['successor_projects'] = {}
    elif change == 'pause': record['successor_projects'][GROWTH]['status'] = 'paused'
    else: record['successor_projects'][GROWTH]['anchor'] = 'cell-site:forged'
    final['successor_projects'] = deepcopy(record['successor_projects'])
    assert funding_history_issues(initial, [record], final)
