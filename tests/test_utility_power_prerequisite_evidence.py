"""Strict synthetic evidence tests; none of these fixtures are live gameplay."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_factory import machine as native_machine, recipe as native_recipe
from test_economic_production import economic_catalog, economic_state


SESSION = 'utility-power-evidence-test-session'


def nativeize(snapshot, catalog):
    snapshot.world_kind = 'fle'
    snapshot.session_id = SESSION
    snapshot.game_version = catalog.version
    snapshot.factory.update(
        tick=snapshot.tick,
        observation_snapshot_schema=2,
        acceptance_runtime={
            'schema': 1,
            'session_id': SESSION,
            'speed': 1,
            'tick_paused': False,
            'actor_unit': 9001,
            'player_index': 1,
            'surface_index': 1,
            'force_index': 1,
            'mods': {'base': catalog.version, 'core': catalog.version},
        },
        receipts={},
    )
    snapshot._coherent_observation_verified = (SESSION, snapshot.tick)
    snapshot._atomic_inventory_verified = (SESSION, snapshot.tick)
    return snapshot


def fixture(*, goal='steam_power', fuel=1, coal=5):
    catalog = economic_catalog()
    catalog.machines['boiler'] = {
        'categories': {}, 'speed': 0, 'burner': True, 'electric': False,
    }
    snapshot = economic_state()
    snapshot.inventory['coal'] = coal
    snapshot.factory['entities']['utility:boiler']['fuel'] = {'coal': fuel}
    return catalog, nativeize(snapshot, catalog), goal


def evidence(catalog, snapshot, plan):
    return candidate_evidence(snapshot, catalog, [plan])[plan.id][
        'utility_power_prerequisite_start_evidence']


def test_current_connected_boiler_qualifies_only_exact_paid_transfer_start():
    catalog, snapshot, goal = fixture(fuel=1, coal=5)
    plan = ReadyWorkPlanner(catalog, snapshot, goal).plan()

    assert plan.steps[0].action == 'factory_insert'
    row = evidence(catalog, snapshot, plan)

    assert row['next_action_kind'] == 'boiler_fuel_transfer_start'
    assert row['consumer_role'] == 'utility:lab'
    assert row['consumer_unit'] == 20
    assert row['connections_current'] == {
        'water_to_boiler': True,
        'boiler_to_engine_steam': True,
        'engine_to_consumer_electricity': True,
    }
    assert row['utility_units_current'] == {
        'water_pump': 21, 'boiler': 22, 'steam_engine': 23,
    }
    assert row['boiler_catalog_burner_current'] is True
    assert row['boiler_coal_now'] == 1
    assert row['boiler_coal_deficit_to_five'] == 4
    assert row['actor_coal_now'] == 5
    assert row['paid_inventory_sufficient_now'] is True
    assert row['planned_native_receipt'] == '10:factory_insert:utility:boiler:coal'
    assert row['native_transfer_rechecks_reach_capacity_receipt_and_postcondition'] is True
    assert row['does_not_establish_electricity_or_research_completion'] is True


def test_current_lab_research_is_bound_to_the_recursive_technology_path():
    catalog, snapshot, _ = fixture(fuel=1, coal=5)
    plan = ReadyWorkPlanner(catalog, snapshot, 'rocket_launch').plan()

    assert plan.steps[0].action == 'factory_insert'
    row = evidence(catalog, snapshot, plan)

    assert row['research'] == 'study'
    assert row['consumer_demand']['kind'] == 'current_technology_lab_demand'
    assert row['consumer_demand']['technology_not_researched_now'] is True
    assert row['consumer_demand']['technology_prerequisites_satisfied_now'] is True


def test_current_electric_recipe_demand_is_bound_to_the_live_machine_and_output_path():
    catalog, snapshot, _ = fixture(fuel=1, coal=5)
    catalog.machines['assembling-machine-1'] = {
        'categories': {'crafting': True}, 'speed': 0.5,
        'burner': False, 'electric': True,
    }
    snapshot.factory['entities']['recipe:automation-science-pack'] = {
        'name': 'assembling-machine-1', 'unit_number': 30,
        'position': {'x': 4, 'y': 0}, 'recipe': 'automation-science-pack',
        'electric_network_id': 1, 'fuel': {}, 'input': {}, 'output': {},
    }
    planner = FactoryPlanner(catalog, snapshot, 'rocket_launch')
    recipe = catalog.recipes['automation-science-pack']
    plan = planner._production(recipe, 'recipe:automation-science-pack', 1,
                               ('item:automation-science-pack',))

    assert plan.steps[0].action == 'factory_insert'
    row = evidence(catalog, snapshot, plan)
    assert row['consumer_role'] == 'recipe:automation-science-pack'
    assert row['consumer_unit'] == 30
    assert row['consumer_demand']['kind'] == 'current_native_recipe_demand'
    assert row['consumer_demand']['recipe_enabled_now'] is True


def test_paid_transfer_is_not_qualified_without_the_full_observed_chain():
    catalog, snapshot, goal = fixture(fuel=1, coal=5)
    plan = FactoryPlanner(catalog, snapshot, goal).plan()
    for changed in (
        lambda state: state.factory['entities']['utility:boiler'].update(
            fluid_ports=[{'id': 2, 'fluid': 'steam'}]),
        lambda state: state.factory['entities']['utility:engine'].update(
            electric_network_id=None),
        lambda state: state.factory['entities']['utility:lab'].update(
            electric_network_id=2),
    ):
        current = deepcopy(snapshot)
        changed(current)
        assert evidence(catalog, current, plan) is None


@pytest.mark.parametrize('mutate', [
    lambda catalog, state, plan: setattr(state, 'world_kind', 'mock'),
    lambda catalog, state, plan: setattr(state, '_coherent_observation_verified', ('other', state.tick)),
    lambda catalog, state, plan: state.factory['acceptance_runtime'].update(session_id='other'),
    lambda catalog, state, plan: state.factory['acceptance_runtime'].update(mods={'base': '2.0.88'}),
    lambda catalog, state, plan: state.factory['acceptance_runtime'].update(mods={
        'base': '2.0.77', 'space-age': '2.0.77'}),
    lambda catalog, state, plan: catalog.machines['boiler'].update(burner=False),
    lambda catalog, state, plan: state.factory['entities']['utility:boiler'].update(
        name='assembling-machine-1'),
    lambda catalog, state, plan: plan.materials['utility_power_prerequisite'].update(observed_tick=9),
    lambda catalog, state, plan: plan.materials['utility_power_prerequisite'].update(consumer_unit=999),
    lambda catalog, state, plan: state.factory.update(crafting_queue=1),
    lambda catalog, state, plan: state.factory.update(player_bound=False),
    lambda catalog, state, plan: state.inventory.update(coal=3),
    lambda catalog, state, plan: state.factory['receipts'].__setitem__(
        '10:factory_insert:utility:boiler:coal', {'role': 'utility:boiler'}),
])
def test_stale_or_unqualified_power_or_transfer_facts_fail_closed(mutate):
    catalog, snapshot, goal = fixture(fuel=1, coal=5)
    plan = FactoryPlanner(catalog, snapshot, goal).plan()
    mutate(catalog, snapshot, plan)
    assert evidence(catalog, snapshot, plan) is None


def test_unpaid_or_wrong_quantity_transfer_is_not_a_current_paid_start():
    catalog, snapshot, goal = fixture(fuel=1, coal=5)
    plan = FactoryPlanner(catalog, snapshot, goal).plan()
    step = plan.steps[0]
    changed = replace(plan, steps=(replace(
        step, costs={'coal': 3},
        parameters={'role': 'utility:boiler', 'item': 'coal', 'quantity': 3,
                    'receipt': '10:factory_insert:utility:boiler:coal'}),))
    assert evidence(catalog, snapshot, changed) is None


def test_missing_engine_is_a_construction_prerequisite_not_power_evidence():
    catalog, snapshot, goal = fixture(fuel=0, coal=5)
    del snapshot.factory['entities']['utility:engine']
    snapshot.inventory['steam-engine'] = 1
    plan = FactoryPlanner(catalog, snapshot, goal).plan()

    assert plan.steps[0].action == 'factory_place'
    row = evidence(catalog, snapshot, plan)
    assert row['next_action_kind'] == 'utility_entity_construction_start'
    assert row['utility_units_current']['steam_engine'] is None
    assert row['connections_current']['boiler_to_engine_steam'] is False
    assert row['does_not_establish_electricity_or_research_completion'] is True


def test_missing_consumer_network_selects_that_connection_before_boiler_fuel():
    catalog, snapshot, goal = fixture(fuel=0, coal=5)
    snapshot.factory['entities']['utility:lab']['electric_network_id'] = 2
    snapshot.inventory['small-electric-pole'] = 200
    plan = FactoryPlanner(catalog, snapshot, goal).plan()

    assert plan.steps[0].action == 'factory_connect'
    assert plan.steps[0].parameters == {
        'source': 'utility:engine', 'target': 'utility:lab',
        'kind': 'small-electric-pole', 'fluid': 'electricity',
    }
    row = evidence(catalog, snapshot, plan)
    assert row['next_action_kind'] == 'utility_connection_start'
    assert row['connections_current']['engine_to_consumer_electricity'] is False


def test_current_gather_evidence_is_only_a_conditional_boiler_fuel_shortfall():
    catalog, snapshot, goal = fixture(fuel=0, coal=0)
    snapshot.inventory.update(coal=0)
    snapshot.factory['inventory_insertable'] = {'coal': 50}
    snapshot.factory['inventory_insertable_evidence'] = {
        'schema': 1, 'tick': snapshot.tick, 'session_id': snapshot.session_id,
        'actor_unit': 9001, 'surface_index': 1, 'force_index': 1,
        'inventory': 'character_main', 'quality': 'normal',
        'method': 'get_insertable_count', 'items': {'coal': 50},
        'basis': 'native_insertable_count_estimate',
    }
    plan = ReadyWorkPlanner(catalog, snapshot, goal).plan()

    assert plan.steps[0].action == 'factory_gather'
    row = evidence(catalog, snapshot, plan)
    assert row['next_action_kind'] == 'boiler_fuel_gather_start'
    assert row['boiler_coal_now'] == 0
    assert row['boiler_coal_deficit_to_five'] == 5
    assert row['gather_start_evidence']['requested_quantity_equals_current_shortfall'] is True
    assert row['native_transfer_rechecks_reach_capacity_receipt_and_postcondition'] is False


def test_boiler_gather_cannot_claim_a_paid_transfer_or_complete_fuel():
    catalog, snapshot, goal = fixture(fuel=0, coal=0)
    plan = ReadyWorkPlanner(catalog, snapshot, goal).plan()
    assert evidence(catalog, snapshot, plan) is None


def test_boiler_gather_without_same_tick_actor_headroom_is_not_qualified():
    catalog, snapshot, goal = fixture(fuel=0, coal=0)
    plan = ReadyWorkPlanner(catalog, snapshot, goal).plan()
    snapshot.factory['inventory_insertable'] = {'coal': 0}
    snapshot.factory['inventory_insertable_evidence'] = {
        'schema': 1, 'tick': snapshot.tick, 'session_id': snapshot.session_id,
        'actor_unit': 9001, 'surface_index': 1, 'force_index': 1,
        'inventory': 'character_main', 'quality': 'normal',
        'method': 'get_insertable_count', 'items': {'coal': 0},
        'basis': 'native_insertable_count_estimate',
    }
    assert evidence(catalog, snapshot, plan) is None


def _owned_iron_furnace(snapshot, *, fuel=5, output=0, recipe='iron-plate'):
    role = 'recipe:iron-plate'
    position = {'x': 5, 'y': 0}
    source = native_machine('stone-furnace', unit_number=44, position=position,
                            recipe=recipe, fuel={'coal': fuel},
                            input={}, output={'iron-plate': output}, crafting=False)
    snapshot.factory['entities'][role] = source
    snapshot.factory['production_sites'] = {
        'protocol': 1, 'session_id': snapshot.session_id, 'tick': snapshot.tick,
        'sources': {role: {
            'state': 'owned', 'reason': 'current_native_source',
            'anchor': 'cell-site:iron-plate', 'position': position,
            'belt_count': 1,
            'bill': {'stone-furnace': 1, 'burner-mining-drill': 1,
                     'burner-inserter': 2, 'wooden-chest': 1, 'transport-belt': 1},
            'source_unit': 44,
        }},
    }


def _engine_bill_child(kind):
    catalog, snapshot, _ = fixture(fuel=1, coal=5)
    catalog.recipes['steam-engine'] = native_recipe(
        'steam-engine', {'iron-plate': 1})
    snapshot.factory['entities'].pop('utility:engine')
    snapshot.inventory['steam-engine'] = 0
    snapshot.inventory['iron-plate'] = 0

    if kind == 'raw_gather':
        snapshot.factory['inventory_insertable'] = {'stone': 10}
        snapshot.factory['inventory_insertable_evidence'] = {
            'schema': 1, 'tick': snapshot.tick, 'session_id': snapshot.session_id,
            'actor_unit': 9001, 'surface_index': 1, 'force_index': 1,
            'inventory': 'character_main', 'quality': 'normal',
            'method': 'get_insertable_count', 'items': {'stone': 10},
            'basis': 'native_insertable_count_estimate',
        }
    elif kind == 'handcraft':
        catalog.recipes['steam-engine'] = native_recipe(
            'steam-engine', {'iron-gear-wheel': 1})
        snapshot.inventory['iron-plate'] = 2
    elif kind == 'output_pickup':
        _owned_iron_furnace(snapshot, fuel=5, output=1)
    elif kind == 'recipe_input_transfer':
        snapshot.inventory['iron-ore'] = 10
        _owned_iron_furnace(snapshot, fuel=5)
    elif kind == 'furnace_fuel_transfer':
        _owned_iron_furnace(snapshot, fuel=0)
    else:
        raise AssertionError(kind)

    planner = ReadyWorkPlanner(catalog, snapshot, 'steam_power')
    planner._set_focus('steam-engine', 1)
    plan = planner._powered('utility:lab', ('item:automation-science-pack',))
    assert plan is not None and len(plan.steps) == 1
    row = candidate_evidence(snapshot, catalog, [plan])[plan.id]
    return catalog, snapshot, plan, row


def _captured_0146_furnace_fuel_candidate():
    """Sanitized reconstruction of the private 0146 request/observation fields.

    The fixture keeps the observed five paid coal, an idle owned iron furnace,
    and the steam-engine -> iron-plate catalog edge. Session, coordinates,
    event IDs, and other private campaign state are deliberately synthetic.
    """
    catalog, snapshot, _ = fixture(goal='rocket_launch', fuel=1, coal=5)
    catalog.recipes['steam-engine'] = native_recipe('steam-engine', {'iron-plate': 10})
    catalog.technologies['automation'] = {
        'enabled': True, 'effects': [], 'prerequisites': [], 'trigger': None,
        'count': 10, 'energy_ticks': 60,
        'ingredients': [{'name': 'automation-science-pack', 'amount': 1}],
    }
    snapshot.researched = []
    snapshot.factory['research'] = ''
    snapshot.factory['entities'].pop('utility:engine', None)
    snapshot.inventory.update({'steam-engine': 0, 'iron-plate': 0})
    _owned_iron_furnace(snapshot, fuel=0, recipe='')
    planner = ReadyWorkPlanner(catalog, snapshot, 'rocket_launch')
    planner._set_focus('steam-engine', 1)
    plan = planner._powered('utility:lab', ('technology:automation',))
    assert plan is not None and plan.steps[0].action == 'factory_insert'
    row = candidate_evidence(snapshot, catalog, [plan])[plan.id]
    return catalog, snapshot, plan, row


@pytest.mark.parametrize(('fixture_kind', 'witness_kind'), [
    ('raw_gather', 'utility_chain_raw_gather_start'),
    ('handcraft', 'utility_chain_handcraft_start'),
    ('output_pickup', 'utility_chain_output_pickup_start'),
    ('recipe_input_transfer', 'utility_chain_recipe_input_transfer_start'),
    ('furnace_fuel_transfer', 'utility_chain_furnace_fuel_transfer_start'),
])
def test_exact_engine_bill_child_uses_current_native_start_evidence(
        fixture_kind, witness_kind):
    from jev_factorio.judgments import _qualified_utility_power_dependency

    catalog, snapshot, plan, row = _engine_bill_child(fixture_kind)
    witness = row['utility_power_prerequisite_start_evidence']

    assert witness['next_action_kind'] == witness_kind
    assert witness['child_start_evidence']['kind'] == witness_kind
    assert witness['child_start_evidence']['observed_tick'] == snapshot.tick
    assert witness['child_start_evidence']['witness_fields']
    assert row['work_scope'] == 'immediate'
    assert 'current_power_consumer_prerequisite' in row['reasons']
    assert witness['does_not_establish_electricity_or_research_completion'] is True
    if fixture_kind == 'furnace_fuel_transfer':
        assert plan.materials['fuel_service']['acquisition_performed_by_this_plan'] is False
        assert witness['child_start_evidence']['witnesses'][
            'fuel_transfer_start_evidence']['service_basis'] == (
                'current_primary_fuel_service_consumer')
    elif plan.steps[0].action != 'factory_insert':
        assert _qualified_utility_power_dependency(plan, row, snapshot.tick)


def test_captured_0146_furnace_child_reports_paid_action_and_local_recipe_edge():
    catalog, snapshot, plan, row = _captured_0146_furnace_fuel_candidate()
    evidence = row['utility_power_prerequisite_start_evidence']
    child = evidence['child_start_evidence']
    fuel = row['fuel_transfer_start_evidence']
    dependency = evidence['local_recipe_dependency']
    receipt = plan.steps[0].parameters['receipt']

    assert plan.materials['local_objective'] == {
        'item': 'steam-engine', 'inventory_target': 1,
        'ultimate_goal': 'rocket_launch',
    }
    assert plan.steps[0].parameters == {
        'role': 'recipe:iron-plate', 'item': 'coal', 'quantity': 5,
        'receipt': receipt,
    }
    assert child['item'] == 'coal'
    assert child['role'] == 'recipe:iron-plate'
    assert child['quantity'] == 5
    assert child['planner_item_path'] == ['steam-engine', 'iron-plate']
    assert fuel['planner_item_path'] == child['planner_item_path']
    assert fuel['local_recipe_dependency'] == dependency
    assert dependency == {
        'observed_tick': snapshot.tick,
        'session_id': snapshot.session_id,
        'basis': 'same_tick_enabled_local_recipe_ingredient_and_owned_furnace',
        'planner_item_path': ['steam-engine', 'iron-plate'],
        'local_target_item': 'steam-engine',
        'local_target_inventory_now': 0,
        'local_target_inventory_target': 1,
        'local_target_shortfall_now': 1,
        'local_recipe': 'steam-engine',
        'local_recipe_enabled_now': True,
        'ingredient_item': 'iron-plate',
        'ingredient_amount_per_batch': 10,
        'producer_role': 'recipe:iron-plate',
        'producer_unit': 44,
        'producer_recipe': 'iron-plate',
        'producer_recipe_enabled_now': True,
        'producer_machine': 'stone-furnace',
        'producer_machine_recipe_now': '',
        'producer_owned_source_identity_current': True,
        'does_not_establish_furnace_output_or_goal_completion': True,
    }
    assert fuel['coal_in_inventory_now'] == snapshot.inventory['coal'] == 5
    assert fuel['coal_to_transfer'] == 5
    assert fuel['native_receipt'] == receipt
    assert evidence['actor_coal_now'] == 5
    assert evidence['paid_inventory_sufficient_now'] is True
    assert evidence['planned_native_receipt'] == receipt
    assert evidence['planned_receipt_absent_now'] is True
    assert evidence['transfer_receipt_observed_now'] is False
    # The furnace child is separate from the outer boiler/power-chain facts.
    assert evidence['boiler_coal_now'] is None
    assert evidence['boiler_coal_deficit_to_five'] is None
    assert evidence['connections_current'] == {
        'water_to_boiler': True,
        'boiler_to_engine_steam': False,
        'engine_to_consumer_electricity': False,
    }
    assert evidence['utility_units_current']['steam_engine'] is None
    assert evidence['does_not_establish_electricity_or_research_completion'] is True


@pytest.mark.parametrize('mutate', [
    lambda catalog, snapshot, plan: catalog.recipes['steam-engine'].update(enabled=False),
    lambda catalog, snapshot, plan: catalog.recipes['steam-engine'].update(
        ingredients=[{'name': 'iron-gear-wheel', 'amount': 8, 'type': 'item'}]),
    lambda catalog, snapshot, plan: snapshot.factory['entities']['recipe:iron-plate'].update(
        recipe='copper-plate'),
    lambda catalog, snapshot, plan: snapshot.factory['production_sites'].update(
        tick=snapshot.tick - 1),
    lambda catalog, snapshot, plan: snapshot.factory['production_sites']['sources'][
        'recipe:iron-plate'].update(source_unit=999),
    lambda catalog, snapshot, plan: snapshot.factory['receipts'].__setitem__(
        plan.steps[0].parameters['receipt'], {'role': 'recipe:iron-plate'}),
    lambda catalog, snapshot, plan: setattr(snapshot, '_atomic_inventory_verified',
                                              ('other-session', snapshot.tick)),
])
def test_furnace_local_dependency_requires_current_recipe_source_and_paid_receipt(
        mutate):
    catalog, snapshot, plan, _ = _captured_0146_furnace_fuel_candidate()
    mutate(catalog, snapshot, plan)
    row = candidate_evidence(snapshot, catalog, [plan])[plan.id]

    assert row['utility_power_prerequisite_start_evidence'] is None


def test_receipt_tracked_ready_work_craft_is_a_valid_recompiled_power_child():
    from jev_factorio.background import BackgroundWorkLoop
    from jev_factorio.judgments import _qualified_utility_power_dependency

    catalog, snapshot, plan, _ = _engine_bill_child('handcraft')
    snapshot.factory['craft_jobs_protocol'] = 1
    loop = BackgroundWorkLoop.__new__(BackgroundWorkLoop)
    loop.catalog = catalog
    tracked = loop._tracked_plan(plan, snapshot)

    assert tracked.steps[0].action == 'factory_craft_job'
    assert len(tracked.steps[0].parameters['receipt']) == 32
    row = candidate_evidence(snapshot, catalog, [tracked])[tracked.id]
    witness = row['utility_power_prerequisite_start_evidence']
    assert witness['next_action_kind'] == 'utility_chain_handcraft_start'
    assert witness['child_start_evidence']['action'] == 'factory_craft_job'
    assert witness['child_start_evidence']['planner_item_path']
    assert _qualified_utility_power_dependency(tracked, row, snapshot.tick)


def test_ready_work_rocket_launch_plan_qualifies_its_current_utility_child():
    from jev_factorio.judgments import _qualified_utility_power_dependency

    catalog, snapshot, _ = fixture(fuel=1, coal=5)
    catalog.recipes['iron-gear-wheel'] = native_recipe(
        'iron-gear-wheel', {'iron-plate': 2})
    catalog.recipes['pipe'] = native_recipe('pipe', {'iron-plate': 1})
    catalog.recipes['steam-engine'] = native_recipe(
        'steam-engine', {'iron-plate': 10, 'iron-gear-wheel': 8, 'pipe': 5})
    snapshot.factory['entities'].pop('utility:engine')
    snapshot.inventory.update({'steam-engine': 0, 'iron-plate': 1,
                               'iron-gear-wheel': 0, 'pipe': 0})
    _owned_iron_furnace(snapshot, fuel=1, output=2)
    planner = ReadyWorkPlanner(catalog, snapshot, 'rocket_launch')
    plan = planner.plan()
    assert plan is not None and plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['item'] == 'iron-plate'
    assert plan.steps[0].parameters['quantity'] == 2
    assert plan.materials['utility_power_prerequisite']['research'] == 'study'

    # Exercise the same public ready-work frontier used by the controller,
    # including candidate evidence and the downstream judgment qualifier.
    candidates = planner.candidates()
    assert plan.id in {candidate.id for candidate in candidates}
    row = candidate_evidence(snapshot, catalog, [plan])[plan.id]
    witness = row['utility_power_prerequisite_start_evidence']
    assert witness['next_action_kind'] == 'utility_chain_output_pickup_start'
    assert witness['consumer_demand'] == {
        'kind': 'current_technology_lab_demand',
        'technology': 'study',
        'technology_not_researched_now': True,
        'technology_prerequisites_satisfied_now': True,
    }
    assert _qualified_utility_power_dependency(plan, row, snapshot.tick)


def test_utility_output_pickup_child_binds_native_transfer_item():
    catalog, snapshot, plan, row = _engine_bill_child('output_pickup')
    step = plan.steps[0]
    assert step.action == 'factory_extract' and step.item == ''
    assert step.parameters['item'] == 'iron-plate'
    child = row['utility_power_prerequisite_start_evidence']['child_start_evidence']
    pickup = child['witnesses']['output_pickup_start_evidence']
    assert child['item'] == pickup['ready_output_item'] == 'iron-plate'
    assert child['quantity'] == pickup['planned_pickup_quantity']
    assert child['planner_item_path'][-1] == child['item']


def test_utility_output_pickup_rejects_native_item_provenance_mismatch():
    catalog, snapshot, plan, _ = _engine_bill_child('output_pickup')
    # The machine and planner provenance still identify ready iron output.
    # An unrelated extraction parameter cannot borrow that native witness.
    plan.steps[0].parameters['item'] = 'copper-plate'
    row = candidate_evidence(snapshot, catalog, [plan])[plan.id]
    assert row.get('output_pickup_start_evidence') is None
    assert row.get('utility_power_prerequisite_start_evidence') is None


def test_engine_bill_gather_without_current_native_headroom_does_not_qualify():
    _, snapshot, _, row = _engine_bill_child('raw_gather')
    snapshot.factory['inventory_insertable']['stone'] = 0
    snapshot.factory['inventory_insertable_evidence']['items']['stone'] = 0
    # Evidence is recomputed from the changed snapshot, not reused from the row.
    catalog = economic_catalog()
    catalog.machines['boiler'] = {
        'categories': {}, 'speed': 0, 'burner': True, 'electric': False,
    }
    catalog.recipes['steam-engine'] = native_recipe('steam-engine', {'iron-plate': 1})
    snapshot.factory['entities'].pop('utility:engine', None)
    snapshot.inventory['steam-engine'] = 0
    snapshot.inventory['iron-plate'] = 0
    planner = ReadyWorkPlanner(catalog, snapshot, 'steam_power')
    planner._set_focus('steam-engine', 1)
    plan = planner._powered('utility:lab', ('item:automation-science-pack',))
    assert row['utility_power_prerequisite_start_evidence'] is not None
    assert evidence(catalog, snapshot, plan) is None
