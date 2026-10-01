"""Current paid partial buffers expose start conditions, never completed flow."""
from copy import deepcopy
from dataclasses import replace
import pytest
from jev_factorio.planning.output_buffers import OutputBufferPlanner, buffer_build_start
from jev_factorio.planning.decision_support import candidate_evidence
from test_buffer_power_prerequisite import buffer_power_fixture


def build_fixture():
    catalog, state, _ = buffer_power_fixture()
    state.inventory['burner-inserter'] = 1
    plan = OutputBufferPlanner(catalog, state, 'rocket_launch')._powered(
        'utility:lab', ('technology:study',))
    assert plan.steps[0].action == 'factory_buffer_build'
    return catalog, state, plan


def test_paid_partial_next_component_build_has_current_start_and_power_child():
    catalog, state, plan = build_fixture()
    evidence = candidate_evidence(state, catalog, [plan])[plan.id]
    proof = evidence['buffer_build_start_evidence']
    assert proof['component_item'] == 'burner-inserter'
    assert proof['paid_parts']['chest']['unit_number'] == 45
    assert proof['component_quantity'] == 1
    assert proof['flow_not_established'] is True
    assert proof['native_prepare_rechecks_geometry_and_clearance'] is True
    assert proof['native_receipt_query'] == {'schema':1,'session_id':state.session_id,
        'tick':state.tick,'receipt':plan.steps[0].parameters['receipt'],
        'present':False,'map_verified':True,'receipt_count':len(state.factory['receipts'])}
    assert 'site_position' not in proof and 'site_clearance' not in proof
    child = evidence['utility_power_prerequisite_start_evidence']['child_start_evidence']
    assert child['kind'] == 'utility_chain_buffer_build_start'
    assert child['role'] == 'recipe:iron-plate'
    assert child['witnesses'] == {'buffer_build_start_evidence': proof}


def fuel_fixture():
    from test_factory import machine
    catalog, state, plan = build_fixture()
    state.inventory.pop('burner-inserter')
    row = state.factory['output_buffers']['sources']['recipe:iron-plate']
    row['parts']['inserter'] = {'role': 'output-arm:44', 'unit_number': 46,
        'receipt': plan.steps[0].parameters['receipt'], 'paid': 1}
    row.update(state='ready', topology=True)
    state.factory['entities']['output-arm:44'] = machine('burner-inserter', unit_number=46,
        fuel={'coal': 0}, fuel_insertable={'coal': 50})
    plan = OutputBufferPlanner(catalog, state, 'rocket_launch')._powered(
        'utility:lab', ('technology:study',))
    assert plan.steps[0].action == 'factory_insert'
    return catalog, state, plan


def test_hypothetical_paid_build_then_bounded_arm_fuel_then_observed_flow():
    catalog, state, plan = fuel_fixture()
    row = candidate_evidence(state, catalog, [plan])[plan.id]
    proof = row['buffer_fuel_start_evidence']
    assert proof['coal_to_transfer'] == 5
    assert proof['ready_source_output_now'] == 10
    assert proof['burner_unit'] == 46 and proof['flow_not_established'] is True
    assert row['utility_power_prerequisite_start_evidence']['next_action_kind'] == 'utility_chain_buffer_fuel_transfer_start'
    # Hypothetical receipt-qualified fuel observation still does not prove flow.
    state.inventory['coal'] = 0
    state.factory['entities']['output-arm:44']['fuel']['coal'] = 5
    state.factory['receipts'][plan.steps[0].parameters['receipt']] = {'paid': 5}
    waiting = OutputBufferPlanner(catalog, state, 'rocket_launch')._powered(
        'utility:lab', ('technology:study',))
    assert waiting.steps[0].action == 'factory_wait'
    assert waiting.steps[0].effect == 'buffer_flow'
    assert not waiting.steps[0].satisfied(state)


@pytest.mark.parametrize('mutation', [
    'unpaid', 'alias', 'source_unit', 'arm_unit', 'arm_name', 'topology', 'fault',
    'no_ready_output', 'ready_type', 'no_coal', 'already_fueled', 'no_capacity',
    'unknown_capacity', 'oversized_quantity', 'duplicate_receipt', 'missing_receipts',
    'bad_service', 'missing_service', 'service_count', 'paid_receipt_alias', 'atomic', 'queue',
])
def test_paid_arm_fuel_start_rejects_changed_current_conditions(mutation):
    catalog, state, plan = fuel_fixture()
    owned = state.factory['output_buffers']['sources']['recipe:iron-plate']
    arm = state.factory['entities']['output-arm:44']
    step = plan.steps[0]
    if mutation == 'unpaid':owned['parts']['inserter']['paid'] = 0
    elif mutation == 'alias':owned['parts']['inserter']['unit_number'] = 45
    elif mutation == 'source_unit':owned['source_unit'] = 999
    elif mutation == 'arm_unit':arm['unit_number'] = 999
    elif mutation == 'arm_name':arm['name'] = 'fast-inserter'
    elif mutation == 'topology':owned['topology'] = False
    elif mutation == 'fault':owned['state'] = 'fault'
    elif mutation == 'no_ready_output':state.factory['entities']['recipe:iron-plate']['output']['iron-plate'] = 0
    elif mutation == 'ready_type':state.factory['entities']['recipe:iron-plate']['output']['iron-plate'] = True
    elif mutation == 'no_coal':state.inventory['coal'] = 0
    elif mutation == 'already_fueled':arm['fuel']['coal'] = 2
    elif mutation == 'no_capacity':arm['fuel_insertable']['coal'] = 0
    elif mutation == 'unknown_capacity':arm.pop('fuel_insertable')
    elif mutation == 'oversized_quantity':step = replace(step,parameters={**step.parameters,'quantity':6},costs={'coal':6})
    elif mutation == 'duplicate_receipt':state.factory['receipts'][step.parameters['receipt']] = {}
    elif mutation == 'paid_receipt_alias':owned['parts']['inserter']['receipt'] = step.parameters['receipt']
    elif mutation == 'missing_receipts':state.factory.pop('receipts')
    elif mutation == 'atomic':state._atomic_inventory_verified = None
    elif mutation == 'queue':state.factory['crafting_queue'] = 1
    elif mutation == 'bad_service':
        materials=deepcopy(plan.materials);materials['fuel_service']['consumers'][0]['deficit']=50
        plan=replace(plan,materials=materials)
    elif mutation == 'missing_service':
        materials=deepcopy(plan.materials);materials['fuel_service']=None
        plan=replace(plan,materials=materials)
    elif mutation == 'service_count':
        materials=deepcopy(plan.materials);materials['fuel_service']['consumer_count']=99
        plan=replace(plan,materials=materials)
    plan=replace(plan,steps=(step,))
    row=candidate_evidence(state,catalog,[plan])[plan.id]
    assert row['buffer_fuel_start_evidence'] is None
    assert row['utility_power_prerequisite_start_evidence'] is None


@pytest.mark.parametrize('mutation', [
    'tick', 'session', 'catalog', 'atomic', 'coherent', 'runtime', 'missing_item',
    'item_type', 'receipt', 'duplicate_receipt', 'layout', 'source_unit', 'source_item',
    'paid', 'paid_unit', 'paid_name', 'paid_alias', 'fault', 'queue', 'disconnected',
    'unbound', 'cost', 'already_built', 'missing_receipts', 'invalid_receipts', 'paid_receipt_alias',
])
def test_start_proof_fails_closed_on_changed_native_precondition(mutation):
    catalog, state, plan = build_fixture()
    row = state.factory['output_buffers']['sources']['recipe:iron-plate']
    step = plan.steps[0]
    parameters = deepcopy(step.parameters)
    if mutation == 'tick':state.factory['output_buffers']['tick'] -= 1
    elif mutation == 'session':state.factory['output_buffers']['session_id'] = 'other'
    elif mutation == 'catalog':state.game_version = 'wrong'
    elif mutation == 'atomic':state._atomic_inventory_verified = None
    elif mutation == 'coherent':state._coherent_observation_verified = None
    elif mutation == 'runtime':state.factory['acceptance_runtime']['tick_paused'] = True
    elif mutation == 'missing_item':state.inventory['burner-inserter'] = 0
    elif mutation == 'item_type':state.inventory['burner-inserter'] = True
    elif mutation == 'receipt':parameters['receipt'] = 'wrong'
    elif mutation == 'paid_receipt_alias':row['parts']['chest']['receipt'] = parameters['receipt']
    elif mutation == 'duplicate_receipt':state.factory.setdefault('receipts', {})[parameters['receipt']] = {}
    elif mutation == 'missing_receipts':state.factory.pop('receipts', None)
    elif mutation == 'invalid_receipts':state.factory['receipts'] = []
    elif mutation == 'layout':parameters['layout'] = 'other'
    elif mutation == 'source_unit':row['source_unit'] = 999
    elif mutation == 'source_item':row['item'] = 'copper-plate'
    elif mutation == 'paid':row['parts']['chest']['paid'] = 0
    elif mutation == 'paid_unit':row['parts']['chest']['unit_number'] = 999
    elif mutation == 'paid_name':state.factory['entities']['output-chest:44']['name'] = 'steel-chest'
    elif mutation == 'paid_alias':row['parts']['chest']['unit_number'] = row['source_unit']
    elif mutation == 'fault':row['state'] = 'fault'
    elif mutation == 'queue':state.factory['crafting_queue'] = 1
    elif mutation == 'disconnected':state.factory['player_connected'] = False
    elif mutation == 'unbound':state.factory['player_bound'] = False
    elif mutation == 'cost':step = replace(step, costs={'burner-inserter': 2})
    elif mutation == 'already_built':row['parts']['inserter'] = deepcopy(row['parts']['chest'])
    plan = replace(plan, steps=(replace(step, parameters=parameters),))
    evidence = candidate_evidence(state, catalog, [plan])[plan.id]
    assert evidence['buffer_build_start_evidence'] is None
    assert evidence['utility_power_prerequisite_start_evidence'] is None
