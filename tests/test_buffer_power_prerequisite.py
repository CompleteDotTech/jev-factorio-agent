"""Offline buffer prerequisites must retain their current power-chain purpose."""
from copy import deepcopy
from dataclasses import replace
import pytest
from jev_factorio.judgments import _qualified_utility_power_dependency
from jev_factorio.planning.output_buffers import OutputBufferPlanner, construction_pickup_bill
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from test_factory import recipe, machine
from test_utility_power_prerequisite_evidence import fixture, _owned_iron_furnace


def buffer_power_fixture(planner_type=OutputBufferPlanner):
    data, state, _ = fixture()
    data.recipes['pipe'] = recipe('pipe', {'iron-plate': 1})
    data.recipes['burner-inserter'] = recipe(
        'burner-inserter', {'iron-plate': 1, 'iron-gear-wheel': 1})
    state.factory['entities']['utility:boiler']['fluid_ports'] = [
        {'id': 1, 'fluid': 'water'}, {'fluid': 'steam'}]
    state.factory['entities']['utility:boiler']['position'] = {'x': 34.5, 'y': 43}
    state.factory['entities']['utility:engine']['position'] = {'x': 44.5, 'y': 43.5}
    state.inventory = {'pipe': 30, 'iron-plate': 1, 'coal': 5, 'wooden-chest': 1}
    _owned_iron_furnace(state, fuel=5, output=10)
    source = state.factory['entities']['recipe:iron-plate']
    source.update(products_finished=20, input={'iron-ore': 20}, crafting=True)
    state.factory['entities']['output-chest:44'] = machine(
        'wooden-chest', unit_number=45)
    for field in ('input_routes', 'mining_outposts'):
        state.factory[field] = {
            'protocol': 1, 'session_id': state.session_id,
            'tick': state.tick, 'sources': {}}
    state.factory['output_buffers'] = {
        'protocol': 1, 'session_id': state.session_id, 'tick': state.tick,
        'sources': {'recipe:iron-plate': {
            'source': 'recipe:iron-plate', 'source_unit': 44,
            'layout': 'output:44:1:0:4', 'item': 'iron-plate',
            'state': 'building', 'parts': {'chest': {
                'role': 'output-chest:44', 'unit_number': 45,
                'receipt': 'chest-receipt', 'paid': 1}}, 'held': 0,
            'chest_role': 'output-chest:44', 'topology': False,
        }},
    }
    plan = planner_type(data, state, 'rocket_launch')._powered(
        'utility:lab', ('technology:study',))
    return data, state, plan


@pytest.mark.parametrize('planner_type', [
    OutputBufferPlanner, InputRoutePlanner, MiningOutpostPlanner])
def test_output_buffer_kit_pickup_keeps_current_power_dependency(planner_type):
    data, state, plan = buffer_power_fixture(planner_type)
    assert plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['quantity'] == 2
    assert plan.materials['output_pickup']['planner_item_path'] == [
        'pipe', 'burner-inserter', 'iron-gear-wheel', 'iron-plate']
    row = candidate_evidence(state, data, [plan])[plan.id]
    witness = row['utility_power_prerequisite_start_evidence']
    assert witness['next_action_kind'] == 'utility_chain_output_pickup_start'
    assert witness['connections_current']['boiler_to_engine_steam'] is False
    assert witness['does_not_establish_electricity_or_research_completion'] is True
    assert _qualified_utility_power_dependency(plan, row, state.tick)
    bill = row['buffer_component_prerequisite_start_evidence']
    assert bill['component_input_required'] == 3
    assert bill['actor_item_now'] == 1
    assert bill['component_input_deficit'] == 2
    assert bill['next_part'] == 'inserter'
    assert bill['native_recipe_batches'] == {'iron-gear-wheel': 1, 'burner-inserter': 1}


@pytest.mark.parametrize('mutation', [
    'stale', 'unit', 'role', 'path', 'quantity', 'unowned', 'unpaid',
    'missing_chest', 'receipt', 'chest_unit', 'wrong_scope'])
def test_buffer_power_proof_rejects_changed_current_dependency(mutation):
    data, state, plan = buffer_power_fixture()
    materials = deepcopy(plan.materials)
    annotation = materials['utility_power_prerequisite']
    if mutation == 'stale':
        annotation['observed_tick'] -= 1
    elif mutation == 'unit':
        annotation['consumer_unit'] = 999
    elif mutation == 'role':
        annotation['consumer_role'] = 'recipe:iron-plate'
    elif mutation == 'path':
        annotation['planner_path'] = ['technology:automation']
        annotation['research'] = 'automation'  # already researched; not current demand
    elif mutation == 'quantity':
        step = plan.steps[0]
        plan = replace(plan, steps=[replace(step, parameters={
            **step.parameters, 'quantity': 3})])
    elif mutation == 'unowned':
        state.factory['production_sites']['sources']['recipe:iron-plate']['source_unit'] = 999
    elif mutation == 'unpaid':
        state.factory['output_buffers']['sources']['recipe:iron-plate']['parts']['chest']['paid'] = 0
    elif mutation == 'missing_chest':
        # With no committed chest, this pickup is no longer the next kit action.
        state.factory['output_buffers']['sources']['recipe:iron-plate']['parts'].clear()
    elif mutation == 'receipt':
        state.factory['output_buffers']['sources']['recipe:iron-plate']['parts']['chest']['receipt'] = ''
    elif mutation == 'chest_unit':
        state.factory['entities']['output-chest:44']['unit_number'] = 999
    elif mutation == 'wrong_scope':
        materials['work_intent']['scope'] = 'lookahead'
    plan = replace(plan, materials=materials)
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['utility_power_prerequisite_start_evidence'] is None


def test_component_bill_uses_carried_intermediates_not_an_inflated_flattened_bill():
    data, state, _ = buffer_power_fixture()
    row = state.factory['output_buffers']['sources']['recipe:iron-plate']
    state.inventory = {'iron-gear-wheel': 1, 'coal': 5}
    bill = construction_pickup_bill(state, data, row, 'inserter', 'iron-plate')
    assert bill['component_input_required'] == 1
    assert bill['component_input_deficit'] == 1
    assert bill['native_recipe_batches'] == {'burner-inserter': 1}


@pytest.mark.parametrize('mutation', ['tick', 'session', 'version', 'paid', 'identity', 'missing_part', 'entity_name', 'source_item'])
def test_component_bill_rejects_stale_or_unpaid_construction(mutation):
    data, state, _ = buffer_power_fixture()
    row = state.factory['output_buffers']['sources']['recipe:iron-plate']
    if mutation == 'tick':
        state.factory['output_buffers']['tick'] -= 1
    elif mutation == 'session':
        state.factory['output_buffers']['session_id'] = 'other'
    elif mutation == 'version':
        state.game_version = '2.0.76'
    elif mutation == 'paid':
        row['parts']['chest']['paid'] = 0
    elif mutation == 'identity':
        row = {**row, 'layout': 'different'}
    elif mutation == 'missing_part':
        row['parts'] = {}
    elif mutation == 'source_item':
        row['item'] = 'copper-plate'
    else:
        state.factory['entities']['output-chest:44']['name'] = 'stone-furnace'
    assert construction_pickup_bill(state, data, row, 'inserter', 'iron-plate') is None


def test_component_pickup_collects_ready_stock_without_forecasting_more_output():
    data, state, _ = buffer_power_fixture()
    state.factory['entities']['recipe:iron-plate'].update(crafting=False, fuel={'coal': 0})
    plan = OutputBufferPlanner(data, state, 'rocket_launch')._powered('utility:lab', ('technology:study',))
    assert plan.steps[0].parameters['quantity'] == 2
    state.factory['entities']['recipe:iron-plate']['output']['iron-plate'] = 1
    plan = OutputBufferPlanner(data, state, 'rocket_launch')._powered('utility:lab', ('technology:study',))
    assert plan.steps[0].parameters['quantity'] == 1
    assert plan.materials['buffer_component_prerequisite']['component_input_deficit'] == 2


def test_receipt_qualified_hypothetical_kit_sequence_retains_current_construction_purpose():
    """Offline replanning only; these states are not native acceptance evidence."""
    data, state, _ = buffer_power_fixture()
    state.inventory['iron-plate'] = 3  # hypothetical verified pickup of two
    state.factory['entities']['recipe:iron-plate']['output']['iron-plate'] = 8
    for expected_item in ('iron-gear-wheel', 'burner-inserter'):
        plan = OutputBufferPlanner(data, state, 'rocket_launch')._powered('utility:lab', ('technology:study',))
        assert plan.steps[0].action == 'factory_craft'
        assert plan.steps[0].item == expected_item
        evidence = candidate_evidence(state, data, [plan])[plan.id]
        bridge = evidence['buffer_component_prerequisite_start_evidence']
        assert bridge['component_item'] == 'burner-inserter'
        assert bridge['pickup_item'] == expected_item
        assert bridge['component_input_deficit'] == 1
        assert evidence['craft_start_evidence'] is not None
        assert _qualified_utility_power_dependency(plan, evidence, state.tick)
        for item, cost in plan.steps[0].costs.items():
            state.inventory[item] -= cost
        state.inventory[expected_item] = state.inventory.get(expected_item, 0) + 1
        state.tick += 1
        state._atomic_inventory_verified = (state.session_id, state.tick)
        state._coherent_observation_verified = (state.session_id, state.tick)
        state.factory['tick'] = state.tick
        for field in ('output_buffers', 'production_sites', 'utilities', 'input_routes', 'mining_outposts'):
            if field in state.factory:
                state.factory[field]['tick'] = state.tick
    plan = OutputBufferPlanner(data, state, 'rocket_launch')._powered('utility:lab', ('technology:study',))
    assert plan.steps[0].action == 'factory_buffer_build'
    assert plan.steps[0].parameters['part'] == 'inserter'
    assert plan.steps[0].costs == {'burner-inserter': 1}


@pytest.mark.parametrize('mutation', ['inputs', 'queue', 'player', 'atomic', 'bill', 'recipe', 'batches'])
def test_current_component_craft_bridge_rejects_changed_start_or_bill(mutation):
    data, state, _ = buffer_power_fixture()
    state.inventory['iron-plate'] = 3
    plan = OutputBufferPlanner(data, state, 'rocket_launch')._powered('utility:lab', ('technology:study',))
    if mutation == 'inputs':
        state.inventory['iron-plate'] = 0
    elif mutation == 'queue':
        state.factory['crafting_queue'] = 1
    elif mutation == 'player':
        state.factory['player_bound'] = False
    elif mutation == 'atomic':
        state._atomic_inventory_verified = None
    elif mutation == 'bill':
        materials = deepcopy(plan.materials)
        materials['buffer_component_prerequisite']['component_input_deficit'] = 2
        plan = replace(plan, materials=materials)
    else:
        step = plan.steps[0]
        parameters = {**step.parameters, 'recipe': 'burner-inserter'} if mutation == 'recipe' else {**step.parameters, 'batches': 2}
        plan = replace(plan, steps=(replace(step, parameters=parameters),))
    assert candidate_evidence(state, data, [plan])[plan.id]['buffer_component_prerequisite_start_evidence'] is None


@pytest.mark.parametrize('alias', ['source_role', 'other_paid_owner'])
def test_component_bill_rejects_paid_part_aliases_across_current_owners(alias):
    data, state, _ = buffer_power_fixture()
    rows = state.factory['output_buffers']['sources']
    row = rows['recipe:iron-plate']
    if alias == 'source_role':
        row['parts']['chest']['role'] = row['source']
    else:
        # Superficially valid chest identity, but two owners cannot share it.
        other = deepcopy(row)
        other.update(source='recipe:copper-plate', source_unit=46, item='copper-plate', layout='output:46')
        rows['recipe:copper-plate'] = other
        state.factory['entities']['recipe:copper-plate'] = machine(unit_number=46)
    assert construction_pickup_bill(state, data, row, 'inserter', 'iron-plate') is None
