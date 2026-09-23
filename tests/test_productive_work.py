"""Stage 1 regressions over real planners/controllers and synthetic game facts.

These tests prove scheduling and accounting behavior, not native throughput.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.productive_work import (
    MAX_PREPARATION_PROBES, prepare_research_batch, producer_resupply, productive_work,
)
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.telemetry import make_attempt
from test_deadline_scheduling import scenario
from test_economic_production import economic_state
from test_factory import catalog, machine, recipe, snapshot
from test_output_buffer_integration import setup


def refill_state():
    data = catalog()
    data.stack_sizes['iron-ore'] = 50
    state = snapshot(inventory={'iron-ore': 20, 'coal': 50})
    state.factory['entities']['recipe:iron-plate'] = machine(
        recipe='iron-plate', input={}, fuel={'coal': 50}, crafting=False,
        products_finished=100)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner.demands = {'iron-plate': 100}
    planner.targets = {'iron-ore': 300}
    planner.raw_targets = {'iron-ore': 300}
    wait = planner._wait('machine_output', 'iron-plate', 20, 'recipe:iron-plate')
    return data, state, planner, wait


@pytest.mark.parametrize('have,expected', [(0, 20), (5, 15), (19, 1)])
def test_forecast_does_not_inflate_immediate_raw_requirement(have, expected):
    data, state, planner, _ = refill_state()
    state.inventory['iron-ore'] = have
    planner.focus = ('iron-plate', 20)
    step = planner._need('iron-ore', 20).steps[0]
    assert step.parameters == {'resource': 'iron-ore', 'quantity': expected}
    assert step.threshold == 20 and 'site:' in planner._need('iron-ore', 20).id
    assert planner.raw_targets['iron-ore'] == 300


def test_carried_immediate_supply_is_delivered_without_finishing_forecast():
    data, state, planner, _ = refill_state()
    before = deepcopy(state)
    step = planner._production(data.recipes['iron-plate'], 'recipe:iron-plate', 20, ()).steps[0]
    assert step.action == 'factory_insert' and step.parameters['quantity'] == 20
    assert step.costs == {'iron-ore': 20}
    assert state == before


def test_stockpile_remains_a_separate_bounded_optional_task():
    data, state, planner, _ = refill_state()
    state.inventory['iron-ore'] = 0
    planner.focus = ('iron-plate', 20)
    planner.speculative = True
    step = planner._need('iron-ore', 20).steps[0]
    assert step.action == 'factory_gather' and step.parameters['quantity'] == 50
    assert step.threshold == 50


@pytest.mark.parametrize('count', [1, 2, 9, 20])
def test_partial_carried_supply_can_restart_a_complete_batch(count):
    data, state, planner, wait = refill_state()
    state.inventory['iron-ore'] = count
    planner.ledger = SupplyLedger.capture(state, data)
    before = deepcopy(state)
    service = producer_resupply(planner, wait)
    assert service.steps[0].parameters['quantity'] == count
    assert service.steps[0].costs == {'iron-ore': count}
    assert service.materials['scheduling']['reason'] == 'unblocks_complete_batch'
    assert not service.steps[0].satisfied(state)
    assert state == before


def test_refill_preempts_stockpiling_when_producer_is_starved():
    data, state, planner, _ = refill_state()
    gather = planner._plan('factory_gather', 'inventory', 'iron-ore', 70,
                          parameters={'resource': 'iron-ore', 'quantity': 50})
    assert productive_work(planner, gather).steps[0].action == 'factory_insert'


def test_running_furnace_is_refilled_before_its_final_input_is_consumed():
    data, state, planner, wait = refill_state()
    state.factory['entities']['recipe:iron-plate'].update(input={'iron-ore': 4}, crafting=True)
    service = producer_resupply(planner, wait)
    assert service.steps[0].parameters['quantity'] == 15
    evidence = service.materials['scheduling']
    assert evidence['coverage_ticks'] == 300
    assert evidence['lead_ticks'] == 300
    assert evidence['operating_target'] == 20
    assert evidence['forecast_target'] == 300
    assert evidence['basis'] == 'catalog-and-policy-estimate'


def test_watermark_avoids_tiny_repeated_refills():
    data, state, planner, wait = refill_state()
    state.factory['entities']['recipe:iron-plate'].update(input={'iron-ore': 18}, crafting=True)
    assert producer_resupply(planner, wait) is None


@pytest.mark.parametrize('change', ['no_fuel', 'no_stock', 'irrelevant', 'unconfigured', 'no_identity'])
def test_proactive_refill_requires_relevance_and_real_supply(change):
    data, state, planner, wait = refill_state()
    source = state.factory['entities']['recipe:iron-plate']
    if change == 'no_fuel': source['fuel'] = {}
    if change == 'no_stock': state.inventory['iron-ore'] = 0
    if change == 'irrelevant':
        planner.demands = {}
        wait = planner._wait('research_progress', 'none', 0.1)
    if change == 'unconfigured': source['recipe'] = ''
    if change == 'no_identity': source.pop('unit_number')
    planner.ledger = SupplyLedger.capture(state, data)
    assert producer_resupply(planner, wait) is None


@pytest.mark.parametrize('change', ['speed', 'position'])
def test_unknown_estimates_are_not_zero_cost_for_active_production(change):
    data, state, planner, wait = refill_state()
    source = state.factory['entities']['recipe:iron-plate']
    source.update(input={'iron-ore': 1}, crafting=True)
    if change == 'speed': data.machines['stone-furnace'].pop('speed')
    if change == 'position': source['position'] = {}
    assert producer_resupply(planner, wait) is None
    source.update(input={}, crafting=False)
    service = producer_resupply(planner, wait)
    assert service is not None  # An observed complete-batch unblock needs no rate forecast.
    key = 'coverage_ticks' if change == 'speed' else 'lead_ticks'
    assert service.materials['scheduling'][key] is None


def test_delivery_is_bounded_by_actual_free_input_stack():
    data, state, planner, wait = refill_state()
    data.stack_sizes['iron-ore'] = 10
    state.factory['entities']['recipe:iron-plate'].update(input={}, crafting=False)
    assert producer_resupply(planner, wait).steps[0].parameters['quantity'] == 10


def test_reserved_and_locked_materials_are_not_available_for_refill():
    data, state, planner, wait = refill_state()
    planner.ledger = SupplyLedger.capture(state, data, reserved={'iron-ore': 20})
    assert producer_resupply(planner, wait) is None
    planner.ledger = SupplyLedger.capture(state, data, job=SimpleNamespace(
        outputs={'iron-ore': 20}, baseline={'iron-ore': 0}))
    assert producer_resupply(planner, wait) is None


def test_complete_buffer_keeps_ownership_and_refills_before_empty():
    state, data, row = setup(True)
    state.inventory['iron-ore'] = 20
    source = state.factory['entities'][row['source']]
    source.update(input={'iron-ore': 3}, output={}, crafting=True)
    planner = OutputBufferPlanner(data, state, 'rocket_launch')
    planner.demands = {'iron-plate': 100}
    wait = planner._wait('machine_output', 'iron-plate', 10, row['chest_role'])
    service = producer_resupply(planner, wait)
    assert service.steps[0].parameters['role'] == row['source']
    assert service.steps[0].allowed(state)


def test_stale_input_route_evidence_cannot_be_bypassed_by_refill():
    data, state, _, wait = refill_state()
    state.factory['input_routes'] = {'protocol': 1, 'session_id': state.session_id,
                                     'tick': state.tick - 1, 'sources': {}}
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner.demands = {'iron-plate': 100}
    assert producer_resupply(planner, wait) is None


@pytest.mark.parametrize('guard', ['queue', 'boiler', 'buffer', 'disconnected', 'unbound'])
def test_mandatory_barriers_and_maintenance_keep_priority(guard):
    data, state, planner, wait = refill_state()
    if guard == 'queue': state.factory['crafting_queue'] = 1
    if guard == 'boiler': state.factory['entities']['utility:boiler'] = machine('boiler', fuel={})
    if guard == 'buffer': planner._buffer_service = True
    if guard == 'disconnected': state.factory['player_connected'] = False
    if guard == 'unbound': state.factory['player_bound'] = False
    assert productive_work(planner, wait) == wait


def research_fixture():
    old, data = scenario(available=20, energy=3600)
    state = economic_state()  # Connected native power topology in the synthetic fixture.
    state.inventory = {'iron-plate': 40, 'coal': 50}
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['utility:lab']['input'] = {'red': 20}
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    wait = planner._wait('research_progress', 'study', 0.01, timeout=3600)
    return data, state, planner, wait


@pytest.mark.parametrize('kind', [ReadyWorkPlanner, OutputBufferPlanner, InputRoutePlanner])
def test_current_research_preparation_does_not_require_fully_fed_research(kind):
    data, state, _, _ = research_fixture()
    for name in ('output_buffers', 'input_routes'):
        state.factory[name] = {'protocol': 1, 'session_id': state.session_id,
                               'tick': state.tick, 'sources': {}}
    planner = kind(data, state, 'rocket_launch')
    before = deepcopy(state)
    plans = planner.candidates()
    assert plans[0].steps[0].action == 'factory_craft'
    assert plans[0].steps[0].parameters == {'recipe': 'red', 'batches': 20}
    assert plans[0].materials['scheduling']['kind'] == 'research_preparation'
    assert plans[0].steps[0].costs == {'iron-plate': 20}
    assert state == before and state.factory['research'] == 'study'


@pytest.mark.parametrize('supply', ['carried', 'queued', 'in_flight', 'acknowledged'])
def test_preparation_does_not_duplicate_existing_or_forecast_outputs(supply):
    data, state, planner, wait = research_fixture()
    if supply == 'carried': planner.ledger.carried['red'] = 20
    if supply == 'queued': planner.ledger.queued_output['red'] = 20
    if supply == 'in_flight': planner.ledger.in_flight_output['red'] = 20
    if supply == 'acknowledged': planner.ledger.acknowledged_output['red'] = 20
    result = prepare_research_batch(planner, wait)
    assert result.steps == wait.steps
    assert result.materials['productive_work']['reason'] == 'no_ready_independent_work'


def test_research_tail_is_capped_by_remaining_uncommitted_science():
    data, state, planner, wait = research_fixture()
    state.factory['research_progress'] = 0.75  # 25 units remaining, 20 committed in the lab.
    result = prepare_research_batch(planner, wait)
    assert result.steps[0].parameters['batches'] == 5
    assert result.materials['scheduling']['inventory_target'] == 5


def test_elapsed_ticks_do_not_verify_preparation_and_repeated_observation_is_stable():
    data, state, planner, wait = research_fixture()
    first = prepare_research_batch(planner, wait)
    assert prepare_research_batch(planner, wait) == first
    state.tick += 100000
    assert not first.steps[0].satisfied(state)
    assert state.factory['entities']['utility:lab']['input']['red'] == 20


def test_unsupported_preparation_stays_waiting_with_bounded_diagnostic():
    data, state, planner, wait = research_fixture()
    data.recipes['red']['enabled'] = False
    result = prepare_research_batch(planner, wait)
    assert result.steps == wait.steps
    evidence = result.materials['productive_work']
    assert evidence['reason'] == 'no_ready_independent_work'
    assert evidence['preparation_probes'] <= MAX_PREPARATION_PROBES
    assert 'unsupported_preparation' in evidence['rejections']


def test_serial_planner_retains_research_wait():
    data, state, _, _ = research_fixture()
    assert FactoryPlanner(data, state, 'rocket_launch').plan().steps[0].action == 'factory_wait'


@pytest.mark.parametrize('dispatch', ['returned', 'prepared', 'ambiguous'])
def test_real_background_controller_yields_only_acknowledged_research_wait(dispatch):
    data, state, planner, wait = research_fixture()
    state.factory['craft_jobs_protocol'] = 1
    backend = SimpleNamespace(enable_factory=lambda: data, craft_jobs_supported=True, act=lambda action: None)
    loop = BackgroundWorkLoop(backend, policy='deterministic', factory_scheduling='ready-work', tick_seconds=0)
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
        last_tick=state.tick, active_plan=wait.to_dict(), completed_goals={g: 1 for g in loop.order[:-1]})
    loop.memory.pending = {'started_tick': state.tick, 'polls': 0, 'action': 'factory_wait', 'dispatch': dispatch}
    loop.memory.attempt = make_attempt(state.session_id, 'rocket_launch', wait.to_dict(), 0,
                                      loop.memory.pending, process_id=loop._process_id)
    before = deepcopy(state)
    result = loop._verify_pending(state)
    assert not result['verified'] and state == before
    if dispatch == 'returned':
        assert loop.memory.pending is None
        assert any(e['kind'] == 'background_wait_yielded' for e in loop.memory.history)
    else:
        assert loop.memory.pending['dispatch'] == dispatch


def test_real_controller_commits_paid_preparation_and_verifies_actual_inventory():
    data, state, _, _ = research_fixture()
    class Backend:
        calls = []
        def enable_factory(self): return data
        def observe(self): return deepcopy(state)
        def execute(self, action, parameters):
            self.calls.append((action, dict(parameters)))
            assert action == 'factory_craft'
            count = parameters['batches']
            assert state.inventory['iron-plate'] >= count
            state.inventory['iron-plate'] -= count
            state.inventory['red'] = state.inventory.get('red', 0) + count
            state.tick += 1
            return 'synthetic paid craft'
    backend = Backend()
    loop = HierarchicalLoop(backend, policy='deterministic', factory_scheduling='ready-work', tick_seconds=0)
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
        last_tick=state.tick, completed_goals={g: 1 for g in loop.order[:-1]})
    result = loop.step()
    assert result['verified']
    assert backend.calls == [('factory_craft', {'recipe': 'red', 'batches': 20})]
    assert state.inventory['iron-plate'] == 20 and state.inventory['red'] == 20
    assert state.factory['research'] == 'study'
    assert not loop.memory.reservations and loop.memory.pending is None


def test_due_refill_outranks_cheaper_large_stockpile_candidate():
    from jev_factorio.planning.decision_support import scheduling_context
    data, state, planner, wait = refill_state()
    source = state.factory['entities']['recipe:iron-plate']
    source.update(input={'iron-ore': 4}, crafting=True, position={'x': 200, 'y': 0})
    refill = producer_resupply(planner, wait)
    gather = planner._plan('factory_gather', 'inventory', 'iron-ore', 70,
                          parameters={'resource': 'iron-ore', 'quantity': 50})
    context = scheduling_context(state, data, [gather, refill], 'rocket_launch')
    assert context['deterministic_ranking'][0] == refill.id
    evidence = context['candidate_evidence'][refill.id]
    assert evidence['urgency'] == 2
    assert 'due_producer_refill:recipe:iron-plate' in evidence['reasons']
    state.tick += 1
    assert scheduling_context(state, data, [refill], 'rocket_launch')['candidate_evidence'][refill.id]['urgency'] == 0


def test_waiting_first_ingredient_does_not_hide_independent_raw_work():
    data, state, planner, wait = research_fixture()
    data.recipes['red'] = recipe('red', {'iron-plate': 1, 'copper-plate': 1})
    data.recipes['copper-plate'] = recipe('copper-plate', {'copper-ore': 1}, 'smelting')
    state.inventory = {'coal': 50}
    state.nearby_resources['copper-ore'] = 5
    state.factory['fair_resource_targets']['copper-ore'] = {
        'name': 'copper-ore', 'surface_index': 1, 'position': {'x': 5, 'y': 0}}
    state.factory['entities']['recipe:iron-plate'] = machine(
        unit_number=80, recipe='iron-plate', fuel={'coal': 50}, input={'iron-ore': 39},
        output={'iron-plate': 1}, crafting=True)
    state.factory['entities']['recipe:copper-plate'] = machine(
        unit_number=81, recipe='copper-plate', fuel={'coal': 50}, input={}, crafting=False)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    before = deepcopy(state)
    result = prepare_research_batch(planner, wait)
    assert result.steps[0].action == 'factory_gather'
    assert result.steps[0].parameters['resource'] == 'copper-ore'
    assert result.steps[0].parameters['quantity'] <= 50
    assert state == before


@pytest.mark.parametrize('value', [True, float('nan'), -1, None])
def test_unknown_or_invalid_fuel_is_not_proactive_operating_evidence(value):
    data, state, planner, wait = refill_state()
    state.factory['entities']['recipe:iron-plate']['fuel']['coal'] = value
    assert producer_resupply(planner, wait) is None
