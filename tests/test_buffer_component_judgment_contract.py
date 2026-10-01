from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio.judgments import _qualified_buffer_component
from test_utility_power_prerequisite_evidence import native_recipe


def bridge(stage='pickup'):
    stock = {'iron-plate': 1 if stage == 'pickup' else 3}
    if stage == 'inserter':
        stock = {'iron-plate': 1, 'iron-gear-wheel': 1}
    paid = {'chest': {'paid': 1, 'role': 'output-chest:7',
                     'unit_number': 8, 'receipt': 'paid-chest'}}
    row = {'source': 'recipe:iron-plate', 'item': 'iron-plate', 'source_unit': 7, 'layout': 'layout',
           'state': 'building', 'parts': paid}
    facts = {'game_version': '2.0.77', 'session_id': 'session', 'inventory': stock,
             'factory': {'output_buffers': {'protocol': 1, 'session_id': 'session',
                          'tick': 100, 'sources': {'recipe:iron-plate': row}},
                         'entities': {'recipe:iron-plate': {'unit_number': 7},
                                      'output-chest:7': {'name': 'wooden-chest', 'unit_number': 8}}}}
    item = {'pickup': 'iron-plate', 'gear': 'iron-gear-wheel',
            'inserter': 'burner-inserter'}[stage]
    inserter = native_recipe('burner-inserter', {'iron-plate': 1, 'iron-gear-wheel': 1})
    gear = native_recipe('iron-gear-wheel', {'iron-plate': 2})
    recipes = {'pickup': {'burner-inserter': inserter, 'iron-gear-wheel': gear},
               'gear': {'burner-inserter': inserter}, 'inserter': {}}[stage]
    required = 3 if stage == 'pickup' else 1
    proof = {'basis': 'current_paid_output_buffer_missing_component_bill',
             'observed_tick': 100, 'native_catalog_version': '2.0.77',
             'source_role': 'recipe:iron-plate', 'source_unit': 7, 'layout': 'layout',
             'paid_parts': paid, 'next_part': 'inserter', 'component_item': 'burner-inserter',
             'component_quantity': 1, 'pickup_item': item, 'actor_item_now': stock.get(item, 0),
             'component_input_required': required,
             'component_input_deficit': required - stock.get(item, 0),
             'native_recipes': recipes, 'native_recipe_batches': {name: 1 for name in recipes},
             'actor_inventory_now': stock,
             'component_craft_build_and_flow_require_native_verification': True}
    path = ['pipe', 'burner-inserter'] + ([] if stage == 'inserter' else ['iron-gear-wheel'])
    if stage == 'pickup':
        path.append('iron-plate')
        step = SimpleNamespace(action='factory_extract', item='', costs={}, parameters={})
        start = {'ready_output_item': item, 'planned_pickup_quantity': 2,
                 'buffer_component_start_evidence': proof}
    else:
        costs = {'iron-plate': 2} if stage == 'gear' else {'iron-plate': 1, 'iron-gear-wheel': 1}
        step = SimpleNamespace(action='factory_craft', item=item, costs=costs,
                               parameters={'recipe': item, 'batches': 1})
        start = {'observed_tick': 100, 'native_recipe': item,
                 'expected_products_after_native_verification': {item: 1},
                 **{key: True for key in ('input_costs_match_native_recipe',
                   'inputs_in_inventory_now', 'recipe_unlocked_and_handcraftable',
                   'player_connected_and_bound', 'crafting_queue_empty')}}
    plan = SimpleNamespace(steps=[step], materials={'buffer_component_prerequisite': proof})
    return facts, plan, proof, start, path


@pytest.mark.parametrize('stage', ['pickup', 'gear', 'inserter'])
def test_current_paid_component_bill_qualifies_each_bounded_stage(stage):
    assert _qualified_buffer_component(*bridge(stage), 100)


@pytest.mark.parametrize('change', ['stale', 'wrong_session', 'unpaid', 'wrong_unit',
                                  'wrong_version', 'wrong_bill', 'wrong_stock', 'overpickup', 'bool_quantity', 'mixed_item',
                                  'aliased_paid_role', 'aliased_paid_unit', 'cross_owner_alias'])
def test_stale_mismatched_unpaid_or_inflated_component_claim_gets_no_hint(change):
    facts, plan, proof, start, path = deepcopy(bridge())
    if change == 'stale':
        facts['factory']['output_buffers']['tick'] = 99
    elif change == 'wrong_session':
        facts['factory']['output_buffers']['session_id'] = 'other'
    elif change == 'unpaid':
        proof['paid_parts']['chest']['paid'] = 0
    elif change == 'wrong_unit':
        facts['factory']['entities']['output-chest:7']['unit_number'] = 9
    elif change == 'wrong_version':
        proof['native_catalog_version'] = '2.0.0'
    elif change == 'wrong_bill':
        proof['component_input_required'] = 2
        proof['component_input_deficit'] = 1
    elif change == 'wrong_stock':
        facts['inventory'] = {'iron-plate': 2}
    elif change == 'overpickup':
        start['planned_pickup_quantity'] = 10
    elif change == 'mixed_item':
        facts['factory']['output_buffers']['sources']['recipe:iron-plate']['item'] = 'copper-plate'
    elif change == 'aliased_paid_role':
        proof['paid_parts']['chest']['role'] = 'recipe:iron-plate'
    elif change == 'aliased_paid_unit':
        proof['paid_parts']['chest']['unit_number'] = 7
    elif change == 'cross_owner_alias':
        facts['factory']['output_buffers']['sources']['recipe:copper-plate'] = {
            'source': 'recipe:copper-plate', 'item': 'copper-plate', 'source_unit': 9,
            'layout': 'other-layout', 'state': 'building', 'parts': deepcopy(proof['paid_parts'])}
    else:
        start['planned_pickup_quantity'] = True
    assert not _qualified_buffer_component(facts, plan, proof, start, path, 100)


@pytest.mark.parametrize('change', ['missing_inputs', 'queue_busy', 'excess_output'])
def test_craft_bridge_does_not_qualify_an_unstartable_or_excess_craft(change):
    facts, plan, proof, start, path = bridge('gear')
    if change == 'missing_inputs':
        start['inputs_in_inventory_now'] = False
    elif change == 'queue_busy':
        start['crafting_queue_empty'] = False
    else:
        start['expected_products_after_native_verification']['iron-gear-wheel'] = 2
    assert not _qualified_buffer_component(facts, plan, proof, start, path, 100)
