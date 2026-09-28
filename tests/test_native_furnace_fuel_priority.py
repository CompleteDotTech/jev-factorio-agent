"""Native-shaped ready-work fuel priority and due-service regressions."""
from copy import deepcopy

import pytest

from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.fuel_failure_budget import acquisition_failures
from jev_factorio.planning.ready_work import ReadyWorkPlanner

from test_factory import catalog, machine, snapshot


def owned_furnace(*, fuel=3, carried=0):
    data = catalog()
    state = snapshot(inventory={'coal': carried},
                     nearby_resources={'coal': 2, 'iron-ore': 5})
    state.factory['entities']['recipe:iron-plate'] = machine(
        unit_number=2547, recipe='iron-plate', fuel={'coal': fuel},
        products_finished=20, input={'iron-ore': 0}, output={})
    state.factory['production_sites'] = {'protocol': 1, 'session_id': state.session_id,
                                         'tick': state.tick, 'sources': {
        'recipe:iron-plate': {'state': 'owned', 'source_unit': 2547}}}
    return state, data


def next_iron_step(state, data):
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    plan = planner._need('iron-plate', 10)
    return planner, plan


def test_stocked_owned_furnace_offers_current_ore_instead_of_47_coal():
    state, data = owned_furnace(fuel=3)
    _, plan = next_iron_step(state, data)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters == {'resource': 'iron-ore', 'quantity': 10}
    assert plan.steps[0].threshold == 10
    assert 'fuel_service' not in (plan.materials or {})
    # This advances only an ore prerequisite. It is no output or fuel guarantee.
    assert state.factory['entities']['recipe:iron-plate']['fuel'] == {'coal': 3}


def test_lab_dependency_keeps_exact_iron_ore_prerequisite_provenance():
    state, data = owned_furnace(fuel=3)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('lab', 1)
    plan = planner._need('iron-plate', 10, ('item:lab', 'item:electronic-circuit'))
    assert plan.steps[0].parameters == {'resource': 'iron-ore', 'quantity': 10}
    assert plan.materials['local_objective']['item'] == 'lab'
    assert plan.materials['raw_prerequisite']['planner_item_path'] == [
        'lab', 'electronic-circuit', 'iron-plate', 'iron-ore']


def test_exhausted_owned_furnace_uses_due_group_not_50_coal_bulk_trip():
    state, data = owned_furnace(fuel=0)
    _, plan = next_iron_step(state, data)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters == {'resource': 'coal', 'quantity': 5}
    service = plan.materials['fuel_service']
    assert service['combined_deficit'] == service['acquisition_target'] == 5
    assert service['reserve'] == 0
    assert service['reserve_basis'] == 'unknown_burn_rate_combined_due_deficits_only'
    assert service['consumers'][0]['role'] == 'recipe:iron-plate'


def test_partial_carried_coal_transfers_without_bulk_mining():
    state, data = owned_furnace(fuel=0, carried=2)
    _, plan = next_iron_step(state, data)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['role'] == 'recipe:iron-plate'
    assert plan.steps[0].parameters['item'] == 'coal'
    assert plan.steps[0].parameters['quantity'] == 2
    assert plan.steps[0].costs == {'coal': 2}
    assert plan.materials['fuel_service']['carried_spendable'] == 2


def test_unrelated_copper_furnace_is_not_counted_as_due():
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['recipe:copper-plate'] = machine(
        unit_number=2546, recipe='copper-plate', fuel={}, products_finished=20)
    state.factory['production_sites']['sources']['recipe:copper-plate'] = {
        'state': 'owned', 'source_unit': 2546}
    _, plan = next_iron_step(state, data)
    assert plan.materials['fuel_service']['consumer_count'] == 1
    assert plan.materials['fuel_service']['combined_deficit'] == 5


def test_due_output_arm_groups_with_exhausted_furnace_without_extra_trip():
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['output:inserter'] = machine(
        'burner-inserter', unit_number=2550, fuel={'coal': 1},
        position={'x': 2, 'y': 0})
    state.factory['output_buffers'] = {'sources': {'recipe:iron-plate': {
        'item': 'iron-plate', 'chest_role': 'output:chest',
        'state': 'ready', 'topology': True,
        'parts': {'inserter': {'role': 'output:inserter'}}}}}
    _, plan = next_iron_step(state, data)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters['quantity'] == 9
    service = plan.materials['fuel_service']
    assert service['consumer_count'] == 2
    assert service['combined_deficit'] == 9
    assert service['reserve'] == 0


def test_imminent_science_deadline_defers_optional_burner(monkeypatch):
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['output:inserter'] = machine(
        'burner-inserter', unit_number=2550, fuel={'coal': 1},
        position={'x': 2, 'y': 0})
    state.factory['output_buffers'] = {'sources': {'recipe:iron-plate': {
        'item': 'iron-plate', 'chest_role': 'output:chest',
        'state': 'ready', 'topology': True,
        'parts': {'inserter': {'role': 'output:inserter'}}}}}
    monkeypatch.setattr('jev_factorio.planning.scheduling.research_schedule',
                        lambda snapshot, catalog: [{'amount': 1,
                            'deadline_tick': snapshot.tick + 1100}])
    _, plan = next_iron_step(state, data)
    service = plan.materials['fuel_service']
    assert plan.steps[0].parameters['quantity'] == 5
    assert service['consumer_count'] == 1
    assert service['deferred']['science_deadline_or_unknown_lead'] == 1


def test_unknown_science_deadline_does_not_admit_optional_burner(monkeypatch):
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['output:inserter'] = machine(
        'burner-inserter', unit_number=2550, fuel={'coal': 1})
    state.factory['output_buffers'] = {'sources': {'recipe:iron-plate': {
        'item': 'iron-plate', 'chest_role': 'output:chest',
        'state': 'ready', 'topology': True,
        'parts': {'inserter': {'role': 'output:inserter'}}}}}
    monkeypatch.setattr('jev_factorio.planning.scheduling.research_schedule',
                        lambda snapshot, catalog: [{'amount': 1, 'deadline_tick': None}])
    _, plan = next_iron_step(state, data)
    assert plan.materials['fuel_service']['consumer_count'] == 1
    assert plan.materials['fuel_service']['deferred']['science_deadline_or_unknown_lead'] == 1


def test_active_research_without_qualified_schedule_defers_optional_burner():
    state, data = owned_furnace(fuel=0)
    state.factory['research'] = 'unknown-current-technology'
    state.factory['entities']['output:inserter'] = machine(
        'burner-inserter', unit_number=2550, fuel={'coal': 1})
    state.factory['output_buffers'] = {'sources': {'recipe:iron-plate': {
        'item': 'iron-plate', 'chest_role': 'output:chest',
        'state': 'ready', 'topology': True,
        'parts': {'inserter': {'role': 'output:inserter'}}}}}
    _, plan = next_iron_step(state, data)
    assert plan.materials['fuel_service']['consumer_count'] == 1
    assert plan.materials['fuel_service']['deferred']['science_deadline_or_unknown_lead'] == 1


def test_reserved_carried_coal_is_not_spent_and_site_failure_history_survives():
    state, data = owned_furnace(fuel=0, carried=2)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner.ledger = SupplyLedger.capture(state, data, reserved={'coal': 2})
    current = planner._need('iron-plate', 10)
    assert current.steps[0].action == 'factory_gather'
    assert current.steps[0].parameters['quantity'] == 5
    assert current.steps[0].threshold == 7
    assert current.materials['fuel_service']['carried_held'] == 2
    # The older 50-target plan on this same observed site cannot be evaded by
    # the new 5-target ID after a failed acquisition.
    old_state = deepcopy(state)
    old_state.factory.pop('production_sites')
    old = ReadyWorkPlanner(data, old_state, 'rocket_launch')._need('iron-plate', 10)
    assert old.steps[0].parameters['quantity'] == 48
    assert acquisition_failures(current, {old.id: 2}) == 2


@pytest.mark.parametrize('mutate', [
    lambda state: state.factory['production_sites']['sources']['recipe:iron-plate'].update(source_unit=999),
    lambda state: state.factory['production_sites']['sources']['recipe:iron-plate'].update(state='proposed'),
    lambda state: state.factory['production_sites'].update(tick=state.tick - 1),
    lambda state: state.factory['production_sites'].update(session_id='other-session'),
    lambda state: state.factory['entities']['recipe:iron-plate'].update(fuel={'coal': True}),
    lambda state: state.factory['entities']['recipe:iron-plate'].pop('fuel'),
])
def test_stale_ownership_or_invalid_fuel_cannot_defer_fuel(mutate):
    state, data = owned_furnace(fuel=3)
    mutate(state)
    with pytest.raises(ValueError):
        next_iron_step(state, data)


def test_known_zero_actor_coal_capacity_fails_closed_at_due_service():
    state, data = owned_furnace(fuel=0)
    state.factory['inventory_insertable'] = {'coal': 0}
    with pytest.raises(ValueError, match='capacity'):
        next_iron_step(state, data)


@pytest.mark.parametrize('insertable', [{'coal': True}, {'coal': -1}, []])
def test_invalid_native_insertable_hint_fails_before_due_action(insertable):
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['recipe:iron-plate']['fuel_insertable'] = insertable
    with pytest.raises(ValueError):
        next_iron_step(state, data)


def test_aliased_native_unit_cannot_be_grouped_twice():
    state, data = owned_furnace(fuel=0)
    state.factory['entities']['output:inserter'] = machine(
        'burner-inserter', unit_number=2547, fuel={'coal': 1})
    state.factory['output_buffers'] = {'sources': {'recipe:iron-plate': {
        'item': 'iron-plate', 'chest_role': 'output:chest',
        'state': 'ready', 'topology': True,
        'parts': {'inserter': {'role': 'output:inserter'}}}}}
    with pytest.raises(ValueError, match='Aliased'):
        next_iron_step(state, data)
