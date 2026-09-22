"""Production-policy regression fixtures derived from the September 22 trace.

Synthetic observations exercise the real planners; they do not measure gameplay.
"""
from copy import deepcopy

import pytest

from jev_factorio.background import BackgroundMemory, BackgroundWorkLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.input_controller import input_loop_type
from jev_factorio.planning.background_work import independent_candidates
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_craft_jobs import admitted
from test_factory import machine, recipe
from test_output_buffer_integration import setup


def production_fixture(available=2):
    state, catalog, row = setup(True)
    source = state.factory['entities'][row['source']]
    source['output'] = {}
    state.factory['entities'][row['chest_role']]['output'] = {'iron-plate': available}
    state.factory['entities']['utility:lab'] = machine('lab', unit_number=30)
    catalog.recipes['logistic-science-pack'] = recipe('logistic-science-pack', {'iron-plate': 1})
    catalog.technologies['study'] = {
        'enabled': True, 'prerequisites': [], 'effects': [], 'count': 100, 'energy_ticks': 60,
        'ingredients': [{'name': 'logistic-science-pack', 'amount': 1}],
    }
    state.factory.update(research='study', research_progress=0, crafting_queue=1)
    job = admitted()
    job.session_id = state.session_id
    return state, catalog, row, job


@pytest.mark.parametrize('planner_type', [OutputBufferPlanner, InputRoutePlanner])
@pytest.mark.parametrize('available', [1, 2, 3, 5, 9])
def test_background_collection_retains_batch_threshold(planner_type, available):
    state, catalog, row, job = production_fixture(available)
    if planner_type is InputRoutePlanner:
        state.factory['input_routes'] = {'protocol': 1, 'session_id': state.session_id,
                                        'tick': state.tick, 'sources': {}}
    before = deepcopy(state)
    plans = independent_candidates('rocket_launch', state, catalog, job, planner_type)
    assert not any(p.steps[0].action == 'factory_extract' for p in plans)
    assert state == before, 'Planning must not mutate authoritative stock or receipts'


def test_reproduces_generic_planner_regression_then_collects_real_batch():
    state, catalog, row, job = production_fixture()
    legacy = independent_candidates('rocket_launch', state, catalog, job)
    assert any(p.steps[0].action == 'factory_extract'
               and p.steps[0].parameters['quantity'] == 2 for p in legacy)
    state.factory['entities'][row['chest_role']]['output'] = {'iron-plate': 11}
    plans = independent_candidates('rocket_launch', state, catalog, job, OutputBufferPlanner)
    pickups = [p.steps[0] for p in plans if p.steps[0].action == 'factory_extract']
    assert pickups and all(p.parameters['quantity'] == 11 for p in pickups)
    assert all(p.parameters['role'] == row['chest_role'] for p in pickups)


def test_research_prefetch_uses_same_buffer_rules_without_a_job():
    state, catalog, row, _ = production_fixture()
    state.factory['crafting_queue'] = 0
    assert not any(p.steps[0].action == 'factory_extract' for p in
                   independent_candidates('rocket_launch', state, catalog, None, OutputBufferPlanner))


def test_finished_producer_tail_does_not_wait_for_unproducible_batch():
    state, catalog, row, job = production_fixture(2)
    state.factory['entities'][row['source']].update(input={}, output={}, crafting=False)
    plans = independent_candidates('rocket_launch', state, catalog, job, OutputBufferPlanner)
    assert any(p.steps[0].action == 'factory_extract'
               and p.steps[0].parameters['quantity'] == 2 for p in plans)


def test_capability_planner_cannot_spend_locked_outputs_or_build_during_craft():
    state, catalog, row, job = production_fixture(11)
    job.outputs, job.baseline = {'iron-plate': 10}, {'iron-plate': 0}
    plans = independent_candidates('rocket_launch', state, catalog, job, OutputBufferPlanner)
    assert all(job.permits(p.steps[0]) for p in plans)
    assert not any(p.steps[0].action == 'factory_extract' for p in plans)


def test_controller_passes_its_composed_planner_to_independent_work(monkeypatch):
    state, catalog, row, job = production_fixture()
    class Backend:
        output_buffers_supported = True
        input_routes_supported = True
        craft_jobs_supported = True
        def enable_factory(self):
            return catalog
    kind = input_loop_type(buffered_loop_type(BackgroundWorkLoop))
    loop = kind(Backend(), policy='deterministic', target='rocket_launch',
                factory_scheduling='ready-work', tick_seconds=0)
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
                                   background_job=job.to_dict())
    state.factory['input_routes'] = {'protocol': 1, 'session_id': state.session_id,
                                    'tick': state.tick, 'sources': {}}
    captured = []
    def independent(*args):
        captured.append(args[-1])
        return []
    monkeypatch.setattr('jev_factorio.background.independent_candidates', independent)
    plans, _ = loop._compile_candidates(state)
    assert captured == [InputRoutePlanner]
    assert plans and all(p.steps[0].action == 'factory_wait' for p in plans)
    assert BackgroundWorkLoop.planner_type is ReadyWorkPlanner


def test_twenty_plate_demand_is_two_ten_plate_pickups():
    state, catalog, row, job = production_fixture(0)
    quantities = []
    for _ in range(2):
        for available in (2, 3, 5, 9):
            state.factory['entities'][row['chest_role']]['output'] = {'iron-plate': available}
            plans = independent_candidates('rocket_launch', state, catalog, job, OutputBufferPlanner)
            assert not any(p.steps[0].action == 'factory_extract' for p in plans)
        state.factory['entities'][row['chest_role']]['output'] = {'iron-plate': 10}
        plans = independent_candidates('rocket_launch', state, catalog, job, OutputBufferPlanner)
        pickup = next(p.steps[0] for p in plans if p.steps[0].action == 'factory_extract')
        quantities.append(pickup.parameters['quantity'])
        state.inventory['iron-plate'] = state.inventory.get('iron-plate', 0) + pickup.parameters['quantity']
        state.factory['entities'][row['chest_role']]['output'] = {}
    assert quantities == [10, 10]
