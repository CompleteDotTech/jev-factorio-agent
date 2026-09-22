"""Synthetic deadline/cadence checks; estimates cannot establish game success."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio.planning.background_work import independent_candidates, research_demands
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.scheduling import (
    replenishment_ticks, research_schedule, future_research_demands,
    next_technology, poll_delay, ready_research_work,
)
from test_factory import catalog, machine, recipe, snapshot


def scenario(available=15, energy=60):
    data = catalog()
    data.recipes['red'] = recipe('red', {'iron-plate': 1})
    data.recipes['red']['energy'] = 5
    data.recipes['green'] = recipe('green', {'iron-plate': 2})
    data.technologies['study'] = {'enabled': True, 'effects': [], 'prerequisites': [],
        'ingredients': [{'name': 'red', 'amount': 1}], 'count': 100, 'energy_ticks': energy}
    data.technologies['rocket-silo'] = {'enabled': True, 'effects': [], 'prerequisites': ['study'],
        'ingredients': [{'name': 'green', 'amount': 1}], 'count': 50, 'energy_ticks': 60}
    state = snapshot(tick=1000)
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['utility:lab'] = machine('lab', unit_number=18, input={'red': available})
    state.factory['entities']['recipe:iron-plate'] = machine(recipe='iron-plate', fuel={'coal': 50})
    return state, data


def test_long_refill_starts_above_the_old_five_pack_threshold():
    state, data = scenario()
    row = research_schedule(state, data)[0]
    assert row['available'] == 15 and row['amount'] == 5 and row['due']
    assert row['coverage_ticks'] < row['lead_ticks'] + 600
    assert row['basis'] == 'catalog-and-policy-estimate'
    assert research_demands(state, data) == [('red', 5)]


def test_slow_research_keeps_sufficient_supply_without_overfill():
    state, data = scenario(available=15, energy=3600)
    assert research_demands(state, data) == []
    assert research_demands(state, data, early=True) == [('red', 5)]
    state.factory['entities']['utility:lab']['input']['red'] = 20
    assert research_demands(state, data, early=True) == []


def test_unknown_raw_site_or_locked_recipe_is_not_zero_lead_time():
    state, data = scenario()
    state.nearby_resources.pop('iron-ore')
    assert replenishment_ticks(state, data, 'red', 5) is None
    data.recipes['red']['enabled'] = False
    assert replenishment_ticks(state, data, 'red', 5) is None


def test_carried_packs_need_only_service_and_travel_time():
    state, data = scenario()
    before = replenishment_ticks(state, data, 'red', 5)
    state.inventory['red'] = 5
    assert 0 < replenishment_ticks(state, data, 'red', 5) < before


def test_refill_priority_reaches_foreground_planner_without_background_controller():
    state, data = scenario()
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    passive = planner._wait('research_progress', 'study', 0.01, timeout=3600, identity='study:epoch')
    before = deepcopy(state)
    chosen = ready_research_work(planner, passive)
    assert chosen.steps[0].action == 'factory_gather'
    assert chosen.materials['scheduling']['kind'] == 'resupply'
    assert state == before


def test_fully_supplied_current_research_prepares_next_batch_without_switching():
    state, data = scenario(available=100)
    before = deepcopy(state)
    assert future_research_demands(state, data) == [('green', 20)]
    plans = independent_candidates('rocket_launch', state, data)
    assert plans and plans[0].steps[0].action == 'factory_gather'
    assert all(p.steps[0].action != 'factory_research' for p in plans)
    assert state == before


def test_future_work_cannot_consume_current_lab_commitment_or_assume_unlocks():
    state, data = scenario(available=99)
    assert future_research_demands(state, data) == []
    state.factory['entities']['utility:lab']['input']['red'] = 100
    data.recipes['green']['enabled'] = False
    assert future_research_demands(state, data) == []


def test_preview_honors_dependencies_and_rejects_cycles():
    state, data = scenario()
    assert next_technology(data, [], 'study') == 'rocket-silo'
    assert next_technology(data, [], '') == 'study'
    data.technologies['study']['prerequisites'] = ['rocket-silo']
    assert next_technology(data, [], '') is None


def test_wait_is_coalesced_but_elapsed_time_does_not_verify_progress():
    state, data = scenario(available=100)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    wait = planner._wait('research_progress', 'study', 0.01, timeout=3600, identity='study:epoch')
    assert wait.steps[0].threshold > 0.01
    assert wait.materials['scheduling']['next_check_tick'] > state.tick
    state.tick += 2000
    assert not wait.steps[0].satisfied(state)
    state.factory['research_progress'] = wait.steps[0].threshold
    assert wait.steps[0].satisfied(state)


def waiting_loop():
    state, data = scenario(available=100)
    wait = ReadyWorkPlanner(data, state, 'rocket_launch')._wait(
        'research_progress', 'study', 0.01, timeout=3600, identity='study:epoch')
    memory = SimpleNamespace(status='running', last_tick=state.tick,
        pending={'dispatch': 'returned', 'action': 'factory_wait'}, active_plan=wait.to_dict())
    return SimpleNamespace(tick_seconds=2.0, factory_scheduling='ready-work', memory=memory)


def test_poll_backoff_is_bounded_and_only_for_acknowledged_passive_research():
    loop = waiting_loop()
    assert 2 < poll_delay(loop) <= 15
    for phase in ('prepared', 'ambiguous'):
        loop.memory.pending['dispatch'] = phase
        assert poll_delay(loop) == 2
    loop.memory.pending.update(dispatch='returned', action='factory_insert')
    assert poll_delay(loop) == 2
    loop.memory.pending['action'] = 'factory_wait'
    loop.memory.status = 'uncertain'
    assert poll_delay(loop) == 2
    loop.memory.status = 'running'
    loop.tick_seconds = 0
    assert poll_delay(loop) == 0


def test_passive_schedule_does_not_backoff_after_expected_check_time():
    loop = waiting_loop()
    loop.memory.last_tick += 100000
    assert poll_delay(loop) == 2
