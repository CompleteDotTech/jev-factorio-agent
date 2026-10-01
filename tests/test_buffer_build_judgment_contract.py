from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio.judgments import _qualified_buffer_build, _qualified_buffer_fuel, _qualified_power_child
from test_buffer_component_judgment_contract import bridge


def build():
    facts, _, previous, _, _ = bridge('inserter')
    facts['inventory'] = {'burner-inserter': 1, 'coal': 5}
    factory = facts['factory']
    factory.update(player_connected=True, player_bound=True, crafting_queue=0, receipts={})
    factory['entities']['recipe:iron-plate']['name'] = 'stone-furnace'
    parameters = {'source': 'recipe:iron-plate', 'layout': 'layout', 'part': 'inserter',
                  'receipt': 'buffer:100:7:inserter'}
    step = SimpleNamespace(action='factory_buffer_build', effect='buffer_component', item='',
                           costs={'burner-inserter': 1}, parameters=parameters)
    proof = {'basis': 'current_paid_partial_output_buffer_next_component', 'observed_tick': 100,
             'native_receipt_query': {'schema':1,'session_id':'session','tick':100,
                 'receipt_count':0,'receipt':parameters['receipt'],'present':False,'map_verified':True},
             'session_id': 'session', 'native_catalog_version': '2.0.77',
             'source_role': 'recipe:iron-plate', 'source_unit': 7, 'source_item': 'iron-plate',
             'layout': 'layout', 'paid_parts': previous['paid_parts'], 'part': 'inserter',
             'component_item': 'burner-inserter', 'component_quantity': 1,
             'receipt': parameters['receipt'], 'actor_inventory_now': facts['inventory'],
             **{key: True for key in ('paid_component_in_inventory_now', 'planned_receipt_absent_now',
                 'player_connected_and_bound_now', 'crafting_queue_empty_now',
                 'native_prepare_rechecks_geometry_and_clearance',
                 'approach_and_placement_require_native_verification', 'flow_not_established')}}
    return facts, step, proof


def test_current_paid_component_can_enter_native_preparation_without_clearance_claim():
    assert _qualified_buffer_build(*build(), 100)


@pytest.mark.parametrize('change', ['tick', 'session', 'unit', 'source_item', 'unpaid', 'stock',
                                   'bool_stock', 'receipt', 'used_receipt', 'queue', 'actor',
                                   'part', 'cost', 'prototype', 'alias', 'flow', 'version',
                                   'missing_receipts', 'wrong_receipts'])
def test_contrary_or_mismatched_build_facts_do_not_qualify(change):
    facts, step, proof = deepcopy(build())
    factory = facts['factory']
    row = factory['output_buffers']['sources']['recipe:iron-plate']
    if change == 'tick': factory['output_buffers']['tick'] = 99
    elif change == 'session': proof['session_id'] = 'other'
    elif change == 'unit': factory['entities']['recipe:iron-plate']['unit_number'] = 9
    elif change == 'source_item': row['item'] = 'copper-plate'
    elif change == 'unpaid': row['parts']['chest']['paid'] = 0
    elif change == 'stock': facts['inventory'] = {'burner-inserter': 0}
    elif change == 'bool_stock': facts['inventory']['burner-inserter'] = True
    elif change == 'receipt': step.parameters['receipt'] = 'another'
    elif change == 'used_receipt': factory['receipts'][proof['receipt']] = {'built': True}
    elif change == 'queue': factory['crafting_queue'] = 1
    elif change == 'actor': factory['player_bound'] = False
    elif change == 'part': proof['part'] = 'chest'
    elif change == 'cost': step.costs = {'burner-inserter': 2}
    elif change == 'prototype': factory['entities']['output-chest:7']['name'] = 'burner-inserter'
    elif change == 'alias': row['parts']['chest']['unit_number'] = 7
    elif change == 'flow': proof['flow_not_established'] = False
    elif change == 'version': proof['native_catalog_version'] = '2.0.0'
    elif change == 'missing_receipts': factory.pop('receipts')
    elif change == 'wrong_receipts': factory['receipts'] = []
    assert not _qualified_buffer_build(facts, step, proof, 100)


def test_power_child_build_requires_same_independent_current_facts():
    facts, step, proof = build()
    row = {'buffer_build_start_evidence': proof}
    child = {'observed_tick': 100, 'kind': 'utility_chain_buffer_build_start',
             'action': step.action, 'item': '', 'role': step.parameters['source'], 'quantity': None,
             'step_costs': step.costs, 'witness_fields': ['buffer_build_start_evidence'],
             'witnesses': row.copy(), 'planner_item_path': ['pipe'],
             'completion_requires_fresh_native_receipt_or_inventory': True}
    evidence = {'next_action_kind': child['kind'], 'child_start_evidence': child}
    assert _qualified_power_child(step, row, evidence, 100, facts)
    facts['factory']['player_bound'] = False
    assert not _qualified_power_child(step, row, evidence, 100, facts)
    assert not _qualified_power_child(step, row, evidence, 100)


def fuel():
    facts, _, _ = build()
    facts['inventory'] = {'coal': 5}
    factory = facts['factory'];row = factory['output_buffers']['sources']['recipe:iron-plate']
    row.update(state='ready',topology=True)
    row['parts']['inserter'] = {'paid': 1, 'role': 'output-arm:7', 'unit_number': 9, 'receipt': 'paid-arm'}
    factory['entities']['output-arm:7'] = {'name': 'burner-inserter', 'unit_number': 9,
                                         'fuel': {'coal': 0}, 'fuel_insertable': {'coal': 50}}
    factory['entities']['recipe:iron-plate']['output'] = {'iron-plate': 8}
    p = {'role':'output-arm:7','item':'coal','quantity':5,'receipt':'100:factory_insert:output-arm:7:coal'}
    step = SimpleNamespace(action='factory_insert',effect='transfer',item='',costs={'coal':5},parameters=p)
    proof = {'observed_tick':100,'session_id':'session','native_catalog_version':'2.0.77',
        'native_receipt_query':{'schema':1,'session_id':'session','tick':100,
            'receipt_count':0,'receipt':p['receipt'],'present':False,'map_verified':True},
        'source_role':row['source'],'source_unit':7,'source_item':'iron-plate','layout':'layout',
        'paid_parts':row['parts'],'burner_role':'output-arm:7','burner_unit':9,
        'fuel_now':0,'fuel_insertable_now':50,'coal_in_inventory_now':5,'coal_to_transfer':5,
        'current_coal_deficit':5,'ready_source_output_now':8,'native_receipt':p['receipt'],
        'actor_inventory_now':facts['inventory'],'basis':'current_paid_output_buffer_arm_commissioning',
        **{k:True for k in ('planned_receipt_absent_now','player_connected_and_bound_now',
            'crafting_queue_empty_now','native_transfer_and_later_flow_require_verification','flow_not_established')}}
    return facts,step,proof


def test_current_paid_arm_deficit_qualifies_without_claiming_flow():
    assert _qualified_buffer_fuel(*fuel(),100)


def test_power_child_fuel_requires_exact_witness_and_current_paid_arm_facts():
    facts,step,proof = fuel()
    row = {'buffer_fuel_start_evidence': proof}
    child = {'observed_tick':100,'kind':'utility_chain_buffer_fuel_transfer_start',
        'action':'factory_insert','item':'coal','role':step.parameters['role'],'quantity':5,
        'step_costs':step.costs,'witness_fields':['buffer_fuel_start_evidence'],
        'witnesses':row.copy(),'planner_item_path':['pipe'],
        'completion_requires_fresh_native_receipt_or_inventory':True}
    evidence = {'next_action_kind':child['kind'],'child_start_evidence':child}
    assert _qualified_power_child(step,row,evidence,100,facts)
    child['item'] = 'iron-plate'
    assert not _qualified_power_child(step,row,evidence,100,facts)
    child['item'] = 'coal'
    facts['factory']['entities']['output-arm:7']['fuel_insertable']['coal'] = 0
    assert not _qualified_power_child(step,row,evidence,100,facts)


@pytest.mark.parametrize('change',['unit','prototype','unpaid','capacity','ready','fuel','coal',
    'quantity','receipt','receipts','session','tick','topology','alias','queue','flow'])
def test_changed_paid_arm_fuel_start_fails_closed(change):
    facts,step,proof = deepcopy(fuel());factory=facts['factory']
    row=factory['output_buffers']['sources']['recipe:iron-plate'];arm=factory['entities']['output-arm:7']
    if change=='unit':arm['unit_number']=10
    elif change=='prototype':arm['name']='stone-furnace'
    elif change=='unpaid':row['parts']['inserter']['paid']=0
    elif change=='capacity':arm['fuel_insertable']['coal']=0
    elif change=='ready':factory['entities']['recipe:iron-plate']['output']['iron-plate']=0
    elif change=='fuel':arm['fuel']['coal']=2
    elif change=='coal':facts['inventory']['coal']=True
    elif change=='quantity':step.parameters['quantity']=6
    elif change=='receipt':factory['receipts'][step.parameters['receipt']]={}
    elif change=='receipts':factory.pop('receipts')
    elif change=='session':proof['session_id']='other'
    elif change=='tick':factory['output_buffers']['tick']=99
    elif change=='topology':row['topology']=False
    elif change=='alias':row['parts']['inserter']['unit_number']=8
    elif change=='queue':factory['crafting_queue']=1
    elif change=='flow':proof['flow_not_established']=False
    assert not _qualified_buffer_fuel(facts,step,proof,100)


@pytest.mark.parametrize('make', [build, fuel])
def test_bounded_native_receipt_query_survives_model_projection_and_rejects_mismatch(make):
    facts,step,proof = make()
    qualifier = _qualified_buffer_build if make is build else _qualified_buffer_fuel
    facts['factory'].pop('receipts')
    facts['factory']['native_transfer_receipt_count'] = 0
    assert qualifier(facts,step,proof,100)
    facts['factory']['native_transfer_receipt_count'] = 1
    assert not qualifier(facts,step,proof,100)
    facts['factory']['native_transfer_receipt_count'] = 0
    proof['native_receipt_query']['present'] = True
    assert not qualifier(facts,step,proof,100)


@pytest.mark.parametrize('make', [build,fuel])
@pytest.mark.parametrize('change',['missing','receipt','map','bool_count','paid_receipt_alias'])
def test_native_receipt_query_and_paid_receipt_alias_fail_closed(make,change):
    facts,step,proof=make()
    qualifier=_qualified_buffer_build if make is build else _qualified_buffer_fuel
    if change=='missing':proof.pop('native_receipt_query')
    elif change=='receipt':proof['native_receipt_query']['receipt']='other'
    elif change=='map':proof['native_receipt_query']['map_verified']=False
    elif change=='bool_count':proof['native_receipt_query']['receipt_count']=True
    elif change=='paid_receipt_alias':
        facts['factory']['output_buffers']['sources']['recipe:iron-plate']['parts']['chest']['receipt']=step.parameters['receipt']
    assert not qualifier(facts,step,proof,100)
