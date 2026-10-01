"""Offline buffer prerequisites must retain their current power-chain purpose."""
from copy import deepcopy
from dataclasses import replace
import pytest
from jev_factorio.judgments import _qualified_utility_power_dependency
from jev_factorio.planning.output_buffers import OutputBufferPlanner
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
    assert plan.steps[0].parameters['quantity'] == 1
    assert plan.materials['output_pickup']['planner_item_path'] == [
        'pipe', 'burner-inserter', 'iron-gear-wheel', 'iron-plate']
    row = candidate_evidence(state, data, [plan])[plan.id]
    witness = row['utility_power_prerequisite_start_evidence']
    assert witness['next_action_kind'] == 'utility_chain_output_pickup_start'
    assert witness['connections_current']['boiler_to_engine_steam'] is False
    assert witness['does_not_establish_electricity_or_research_completion'] is True
    assert _qualified_utility_power_dependency(plan, row, state.tick)


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
            **step.parameters, 'quantity': 2})])
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
