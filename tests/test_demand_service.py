"""Bounded horizon accounting and real-controller service-visit regressions."""
from copy import deepcopy

import pytest

from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning.demand import SupplyLedger, horizon_demands
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.service_visits import service_visit
from jev_factorio.skills import Plan
from test_craft_jobs import admitted
from test_factory import catalog, machine, recipe, snapshot
from test_output_buffer_integration import setup


def test_machine_inputs_are_forecast_products_not_free_raw_materials():
    data = catalog()
    state = snapshot(inventory={'iron-ore': 7})
    state.factory['entities']['recipe:iron-plate'] = machine(
        recipe='iron-plate', input={'iron-ore': 9}, output={'iron-plate': 2}, crafting=True)
    ledger = SupplyLedger.capture(state, data, reserved={'iron-ore': 2})
    assert ledger.carried == {'iron-ore': 5}
    assert ledger.machine_inputs == {'iron-ore': 9}
    assert ledger.queued_output == {'iron-plate': 9}
    assert ledger.in_flight_output == {'iron-plate': 1}
    assert ledger.forecast_stock() == {'iron-ore': 5, 'iron-plate': 12}
    assert ledger.summary()['reserved'] == {'iron-ore': 2}


def test_aliases_cannot_multiply_one_native_inventory_or_one_running_batch():
    data = catalog()
    state = snapshot()
    entity = machine(recipe='iron-plate', input={'iron-ore': 4},
                     output={'iron-plate': 3}, crafting=True)
    state.factory['entities'] = {'recipe:iron-plate': entity, 'alias': deepcopy(entity)}
    assert SupplyLedger.capture(state, data).forecast_stock() == {'iron-plate': 8}


def test_partial_craft_output_is_locked_and_forecast_once():
    state = snapshot(inventory={'science': 5, 'coal': 2})
    job = admitted()  # baseline 3 + ten outputs; two already completed
    ledger = SupplyLedger.capture(state, catalog(), job=job)
    assert ledger.locked_inventory == {'science': 5}
    assert ledger.forecast_stock() == {'coal': 2, 'science': 13}
    assert state.inventory['science'] == 5
    # Reconstructing after a verified completion credits the actual stock only.
    state.inventory['science'] = 13
    assert SupplyLedger.capture(state, catalog()).forecast_stock()['science'] == 13


@pytest.mark.parametrize('quantity', [-1, float('nan'), True, 11])
def test_invalid_or_excessive_reservation_rejected(quantity):
    with pytest.raises(ValueError):
        SupplyLedger.capture(snapshot(inventory={'coal': 10}), catalog(), reserved={'coal': quantity})


def test_unsupported_fluid_recipe_does_not_forecast_free_solid_output():
    data = catalog()
    data.recipes['wet'] = recipe('wet', {'iron-ore': 2})
    data.recipes['wet']['ingredients'][0]['type'] = 'fluid'
    state = snapshot()
    state.factory['entities']['wet'] = machine(recipe='wet', input={'iron-ore': 10}, crafting=True)
    ledger = SupplyLedger.capture(state, data)
    assert not ledger.queued_output and not ledger.in_flight_output


def science_state():
    data = catalog()
    for item in ('red', 'green'):
        data.recipes[item] = recipe(item, {'iron-plate': 1})
    data.technologies['study'] = {'enabled': True, 'count': 100, 'effects': [],
        'prerequisites': [], 'ingredients': [{'name': 'red', 'amount': 1},
                                             {'name': 'green', 'amount': 1}]}
    state = snapshot(inventory={})
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['utility:lab'] = machine('lab', input={'red': 20})
    return state, data


def test_horizon_aggregates_shared_inputs_without_spending_lab_packs_twice():
    state, data = science_state()
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('green', 20)
    assert planner.demands == {'green': 40, 'red': 40}
    assert planner.raw_targets['iron-ore'] == 80
    state.factory['research_progress'] = 0.9
    demands = horizon_demands(state, data, 'rocket_launch', 'green', 1)
    assert 'red' not in demands
    assert 1 <= demands['green'] <= 11
    assert horizon_demands(state, data, 'automation_science', 'green', 10) == {'green': 10}


def test_paid_supply_reduces_horizon_gather_target():
    state, data = science_state()
    state.factory['entities']['recipe:iron-plate'] = machine(
        recipe='iron-plate', unit_number=18, input={'iron-ore': 39}, crafting=True)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('green', 20)
    assert planner.raw_targets['iron-ore'] == 40


def test_only_speculative_one_unit_trip_is_suppressed():
    state = snapshot()
    primary = ReadyWorkPlanner(catalog(), state, 'rocket_launch')
    assert primary._need('iron-ore', 1).steps[0].parameters['quantity'] == 1
    speculative = ReadyWorkPlanner(catalog(), state, 'rocket_launch')
    speculative.speculative = True
    assert speculative._need('iron-ore', 1) is None
    assert speculative._need('iron-ore', 20).steps[0].parameters['quantity'] == 20


def visit_fixture():
    state, data, row = setup(True)
    state.inventory = {'coal': 50, 'iron-ore': 20}
    state.factory['entities'][row['source']].update(fuel={'coal': 1}, input={}, crafting=False)
    state.factory['entities'][row['chest_role']]['output'] = {'iron-plate': 20}
    planner = OutputBufferPlanner(data, state, 'rocket_launch')
    planner._set_focus('iron-plate', 20)
    first = planner._transfer(row['chest_role'], 'iron-plate', 20, extracting=True)
    return state, data, row, planner, first


def test_service_groups_same_cell_transfers_with_native_receipts_and_paid_inputs():
    state, data, row, planner, first = visit_fixture()
    before = deepcopy(state)
    visit = service_visit(planner, first)
    assert len(visit.steps) == 3
    assert [s.action for s in visit.steps] == ['factory_extract', 'factory_insert', 'factory_insert']
    assert [s.parameters['item'] for s in visit.steps] == ['iron-plate', 'coal', 'iron-ore']
    assert len({s.parameters['receipt'] for s in visit.steps}) == 3
    assert Plan.from_dict(visit.to_dict()) == visit
    assert state == before


def test_service_keeps_background_single_step_and_never_spends_collection_forecast():
    state, data, row, planner, first = visit_fixture()
    state.factory['crafting_queue'] = 1
    assert service_visit(planner, first) == first
    state.factory['crafting_queue'] = 0
    state.inventory = {}
    assert service_visit(planner, first) == first
    planner.allow_service_visits = False
    assert service_visit(planner, first) == first


def test_service_partial_plan_resume_does_not_repeat_lost_acknowledgment(tmp_path):
    state, data, row, planner, first = visit_fixture()
    visit = service_visit(planner, first)
    class Backend:
        output_buffers_supported = True
        def __init__(self):
            self.calls = []
        def enable_factory(self):
            return data
        def observe(self):
            return deepcopy(state)
        def execute(self, action, parameters):
            self.calls.append((action, deepcopy(parameters)))
            p = parameters
            entity = state.factory['entities'][p['role']]
            section = 'output' if action == 'factory_extract' else ('fuel' if p['item'] == 'coal' else 'input')
            amount = p['quantity']
            sign = 1 if action == 'factory_extract' else -1
            state.inventory[p['item']] = state.inventory.get(p['item'], 0) + sign * amount
            entity[section][p['item']] = entity[section].get(p['item'], 0) - sign * amount
            state.factory['receipts'][p['receipt']] = dict(p, unit_number=entity['unit_number'],
                                                         extracting=action == 'factory_extract')
            state.tick += 1
            state.factory['output_buffers']['tick'] = state.tick
            if len(self.calls) == 1:
                raise TimeoutError('synthetic lost acknowledgment after native transfer')
            return 'synthetic receipt returned'
    backend = Backend()
    kind = buffered_loop_type(HierarchicalLoop)
    def make(resume):
        loop = kind(backend, policy='deterministic', target='rocket_launch',
                    factory_scheduling='ready-work', checkpoint=str(tmp_path / 'state.json'),
                    resume_controller=resume, tick_seconds=0)
        if not resume:
            loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
                completed_goals={g: 1 for g in loop.order[:-1]}, last_tick=state.tick)
        loop._compile_candidates = lambda current: ([visit], '')
        return loop
    loop = make(False)
    assert not loop.step()['verified']
    assert loop.memory.pending['dispatch'] == 'ambiguous'
    resumed = make(True)
    for _ in range(6):
        resumed.step()
        if len(backend.calls) == 3:
            break
    assert len(backend.calls) == 3
    assert [p['receipt'] for _, p in backend.calls] == [s.parameters['receipt'] for s in visit.steps]
    assert state.inventory['iron-plate'] == 20
    assert state.inventory['iron-ore'] == 0
