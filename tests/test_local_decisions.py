"""Selection regressions are synthetic, not native performance measurements."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.controller import HierarchicalLoop
from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.jev_client import MockJevClient
from jev_factorio.planning.decision_support import candidate_evidence, distinct_candidates, scheduling_context
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.skills import Plan, Step
from test_factory import FactorySimulation, catalog, machine, recipe, snapshot
from test_causal_trace import Sink, events
from test_deadline_scheduling import scenario
from test_hierarchical import CountingModel


def transfers(state=None, data=None):
    state, data = state or snapshot(), data or catalog()
    state.inventory['iron-plate'] = 0
    state.factory['entities'].update({
        'far': machine('wooden-chest', unit_number=81, position={'x': 50, 'y': 0},
                       output={'iron-plate': 10}),
        'near': machine('wooden-chest', unit_number=82, position={'x': 5, 'y': 0},
                        output={'iron-plate': 10}),
    })
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    return state, data, [worker._transfer(role, 'iron-plate', 10, extracting=True)
                         for role in ('far', 'near')]


def test_focus_is_structured_without_mutating_material_bill():
    state, data = snapshot(), catalog()
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    plan = worker._need('iron-plate', 20)
    assert plan.materials['local_objective'] == {
        'item': 'iron-plate', 'inventory_target': 20, 'ultimate_goal': 'rocket_launch'}
    assert 'local_objective' not in (worker.materials or {})
    serial = FactoryPlanner(data, state, 'rocket_launch')._need('iron-plate', 20)
    assert 'local_objective' not in (serial.materials or {})


def test_stone_gather_explains_current_lab_recipe_dependency_without_claiming_output():
    state, data = snapshot(), catalog()
    data.recipes['lab'] = recipe('lab', {'iron-plate': 1})
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    plan = worker._need('lab', 1)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters['resource'] == 'stone'
    assert plan.materials['local_objective']['item'] == 'lab'
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['raw_prerequisite'] == {
        'observed_tick': state.tick,
        'direct_recipe': 'stone-furnace',
        'direct_product': 'stone-furnace',
        'planner_item_path': ['lab', 'iron-plate', 'stone-furnace', 'stone'],
        'basis': 'current_planner_dependency_and_native_catalog_recipe',
        'later_steps_require_fresh_native_preconditions': True,
    }
    assert row['gather_start_evidence'] == {
        'resource_in_current_observation': True,
        'fair_target_identity_observed': True,
        'resource_inventory_now': 0,
        'target_inventory_after_this_step': 5,
        'travel_is_lower_bound_not_arrival_proof': True,
    }
    context, questions, offered = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'rocket_launch')},
        [plan])
    assert offered == [plan]
    assert context['candidate_evidence'][plan.id]['gather_start_evidence'] == row['gather_start_evidence']
    assert 'best next action' in questions['candidate']['instructions']
    assert 'ultimate goal' in questions['candidate']['instructions']
    assert 'observed raw resource' in questions[plan.id + '/needs_observation']['instructions']
    assert row['delivers_or_crafts'] == []
    assert row['processed_units_basis'] == 'handling_volume_not_useful_production'
    assert not row['requires_investment']
    assert not plan.steps[0].allowed(snapshot(nearby_resources={}))


def test_stale_or_unrelated_raw_dependency_never_enters_candidate_evidence():
    state, data = snapshot(), catalog()
    plan = FactoryPlanner(data, state, 'iron_smelting')._need('iron-plate', 10)
    assert candidate_evidence(state, data, [plan])[plan.id]['raw_prerequisite'] is not None
    stale = replace(plan, materials={**plan.materials, 'raw_prerequisite': {
        **plan.materials['raw_prerequisite'], 'observed_tick': state.tick - 1}})
    unrelated = replace(plan, materials={**plan.materials, 'raw_prerequisite': {
        **plan.materials['raw_prerequisite'], 'direct_product': 'lab'}})
    assert candidate_evidence(state, data, [stale])[stale.id]['raw_prerequisite'] is None
    assert candidate_evidence(state, data, [unrelated])[unrelated.id]['raw_prerequisite'] is None
    state.factory['fair_resource_targets'].pop('stone')
    missing_site = candidate_evidence(state, data, [plan])[plan.id]
    assert missing_site['gather_start_evidence']['fair_target_identity_observed'] is False
    assert 'travel:factory_gather' in missing_site['unknowns']


def test_tree_named_wood_target_is_valid_gather_start_evidence():
    state, data = snapshot(), catalog()
    data.recipes['wooden-chest'] = recipe('wooden-chest', {'wood': 2})
    state.nearby_resources['wood'] = 3
    state.factory['fair_resource_targets']['wood'] = {
        'name': 'tree-01', 'surface_index': 1,
        'position': {'x': 3, 'y': 0},
    }
    plan = FactoryPlanner(data, state, 'rocket_launch')._need('wooden-chest', 1)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters == {'resource': 'wood', 'quantity': 1}
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['gather_start_evidence']['fair_target_identity_observed'] is True
    assert row['raw_prerequisite']['direct_product'] == 'wooden-chest'


def test_receipt_tracked_handcraft_has_current_start_facts_without_fake_travel():
    state, data = snapshot(inventory={'stone': 5}), catalog()
    state.factory['craft_jobs_protocol'] = 1
    plan = FactoryPlanner(data, state, 'iron_smelting')._need('stone-furnace', 1)
    step = plan.steps[0]
    plan = replace(plan, steps=(replace(step, action='factory_craft_job',
        effect='craft_job_complete', parameters={**step.parameters, 'receipt': 'stone-test'}),))
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['unknowns'] == []
    assert row['travel_tiles_lower_bound'] == 0
    assert row['delivers_or_crafts'] == []  # A queued craft has not delivered output.
    assert row['craft_start_evidence'] == {
        'observed_tick': state.tick, 'native_recipe': 'stone-furnace',
        'input_costs_match_native_recipe': True, 'inputs_in_inventory_now': True,
        'recipe_unlocked_and_handcraftable': True,
        'player_connected_and_bound': True, 'crafting_queue_empty': True,
        'craft_job_protocol_ready': True,
        'expected_products_after_native_verification': {'stone-furnace': 1},
        'native_receipt_required_for_completion': True,
    }
    context, questions, _ = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'iron_smelting')},
        [plan])
    assert context['candidate_evidence'][plan.id]['craft_start_evidence'] == row['craft_start_evidence']
    assert 'expected output still needs native verification' in str(questions)
    assert plan.steps[0].allowed(state)

    state.factory['craft_jobs_protocol'] = True  # Bool must not impersonate protocol version 1.
    bad = candidate_evidence(state, data, [plan])[plan.id]['craft_start_evidence']
    assert bad['craft_job_protocol_ready'] is False
    assert not plan.steps[0].allowed(state)
    state.factory['craft_jobs_protocol'] = 1
    state.inventory['stone'] = 0
    bad = candidate_evidence(state, data, [plan])[plan.id]['craft_start_evidence']
    assert bad['inputs_in_inventory_now'] is False
    assert not plan.steps[0].allowed(state)
    stale = replace(plan, materials={'work_intent': {'observed_tick': state.tick - 1}})
    assert candidate_evidence(state, data, [stale])[stale.id]['craft_start_evidence'] is None
    changed = replace(plan, steps=(replace(plan.steps[0], costs={'stone': 4}),))
    assert candidate_evidence(state, data, [changed])[changed.id]['craft_start_evidence'] is None


def test_furnace_craft_keeps_current_lab_planner_provenance_without_claiming_lab():
    state, data = snapshot(inventory={'stone': 5}), catalog()
    data.recipes['lab'] = recipe('lab', {'iron-plate': 1})
    state.factory['craft_jobs_protocol'] = 1
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('lab', 1)
    plan = planner._need('lab', 1)
    assert plan.steps[0].action == 'factory_craft'
    assert plan.materials['local_objective']['item'] == 'lab'
    assert plan.materials['craft_dependency'] == {
        'observed_tick': state.tick, 'recipe': 'stone-furnace',
        'product': 'stone-furnace',
        'planner_item_path': ['lab', 'iron-plate', 'stone-furnace'],
    }
    step = plan.steps[0]
    plan = replace(plan, steps=(replace(step, action='factory_craft_job',
        effect='craft_job_complete', parameters={**step.parameters, 'receipt': 'lab-test'}),))
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['craft_dependency'] == {
        'observed_tick': state.tick,
        'planner_item_path': ['lab', 'iron-plate', 'stone-furnace'],
        'current_craft_product': 'stone-furnace',
        'basis': 'current_recursive_planner_provenance_and_native_recipe',
        'later_steps_require_fresh_native_preconditions': True,
    }
    assert row['craft_start_evidence']['expected_products_after_native_verification'] == {
        'stone-furnace': 1}
    assert row['delivers_or_crafts'] == []
    context, questions, _ = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'rocket_launch')},
        [plan])
    assert context['candidate_evidence'][plan.id]['craft_dependency'] == row['craft_dependency']
    assert 'later production still needs fresh native checks' in str(questions)
    stale = replace(plan, materials={**plan.materials, 'craft_dependency': {
        **plan.materials['craft_dependency'], 'observed_tick': state.tick - 1}})
    assert candidate_evidence(state, data, [stale])[stale.id]['craft_dependency'] is None
    unrelated = replace(plan, materials={**plan.materials, 'craft_dependency': {
        **plan.materials['craft_dependency'], 'planner_item_path': ['unrelated', 'stone-furnace']}})
    assert candidate_evidence(state, data, [unrelated])[unrelated.id]['craft_dependency'] is None
    missing_target = replace(plan, materials={key: value for key, value in plan.materials.items()
                                              if key != 'local_objective'})
    assert candidate_evidence(state, data, [missing_target])[missing_target.id]['craft_dependency'] is None
    empty_target = replace(plan, materials={**plan.materials, 'local_objective': {'item': ''}})
    assert candidate_evidence(state, data, [empty_target])[empty_target.id]['craft_dependency'] is None
    data.recipes['stone-furnace']['enabled'] = False
    assert candidate_evidence(state, data, [plan])[plan.id]['craft_dependency'] is None


def test_paid_joint_furnace_placement_has_observed_site_and_lab_dependency():
    state, data = snapshot(inventory={'stone-furnace': 1}, player_position=(0, 0)), catalog()
    data.recipes['copper-plate'] = recipe('copper-plate', {'copper-ore': 1}, 'smelting')
    role, anchor = 'recipe:copper-plate', 'cell-site:copper-ore:-42:-109:0:2'
    state.factory['production_sites'] = {
        'protocol': 1, 'session_id': state.session_id, 'tick': state.tick,
        'sources': {role: {
            'state': 'proposed', 'reason': 'joint_layout_available', 'anchor': anchor,
            'position': {'x': -42, 'y': -109}, 'belt_count': 5,
            'bill': {'stone-furnace': 1, 'burner-mining-drill': 1,
                     'burner-inserter': 2, 'wooden-chest': 1, 'transport-belt': 5},
        }},
    }
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner._set_focus('lab', 1)
    plan = planner._machine(role, 'stone-furnace', ('item:lab', 'item:copper-plate'))
    assert plan.steps[0].allowed(state)
    assert plan.materials['local_objective']['item'] == 'lab'
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['unknowns'] == []
    assert row['travel_tiles_lower_bound'] == round((42**2 + 109**2) ** .5, 3)
    assert row['placement_start_evidence']['site_position'] == {'x': -42, 'y': -109}
    assert row['placement_start_evidence']['paid_furnace_in_inventory_now'] is True
    assert row['placement_start_evidence']['native_offer_checked_current_site_clearance'] is True
    assert row['placement_start_evidence']['player_connected_and_bound_now'] is True
    assert row['placement_start_evidence']['crafting_queue_empty_now'] is True
    assert row['placement_dependency'] == {
        'observed_tick': state.tick, 'planner_item_path': ['lab', 'copper-plate'],
        'machine_for_recipe': role,
        'basis': 'current_recursive_planner_and_validated_native_site',
        'later_flow_and_output_require_fresh_native_preconditions': True,
    }
    assert row['delivers_or_crafts'] == []
    context, questions, _ = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'rocket_launch')},
        [plan])
    assert context['candidate_evidence'][plan.id]['placement_dependency'] == row['placement_dependency']
    assert 'native transport and output remain unverified' in str(questions)
    assert 'unverified walking path or future build receipt' in str(questions)
    missing = replace(plan, materials={key: value for key, value in plan.materials.items()
                                       if key != 'local_objective'})
    assert candidate_evidence(state, data, [missing])[missing.id]['placement_dependency'] is None
    state.inventory['stone-furnace'] = 0
    unfunded = candidate_evidence(state, data, [plan])[plan.id]
    assert unfunded['placement_start_evidence']['paid_furnace_in_inventory_now'] is False
    assert unfunded['placement_dependency'] is None
    assert not plan.steps[0].allowed(state)
    state.inventory['stone-furnace'] = 1
    state.factory['crafting_queue'] = 1
    busy = candidate_evidence(state, data, [plan])[plan.id]
    assert busy['placement_start_evidence']['crafting_queue_empty_now'] is False
    assert busy['placement_dependency'] is None
    state.factory['crafting_queue'] = 0
    state.factory['player_bound'] = False
    unbound = candidate_evidence(state, data, [plan])[plan.id]
    assert unbound['placement_start_evidence']['player_connected_and_bound_now'] is False
    assert unbound['placement_dependency'] is None
    state.factory['player_bound'] = True
    state.factory['entities'][role] = machine(position={'x': -42, 'y': -109})
    occupied = candidate_evidence(state, data, [plan])[plan.id]
    assert occupied['placement_start_evidence']['no_source_owned_at_role_now'] is False
    assert occupied['placement_dependency'] is None
    assert not plan.steps[0].allowed(state)
    state.factory['entities'].pop(role)
    state.factory['production_sites']['tick'] -= 1
    stale = candidate_evidence(state, data, [plan])[plan.id]
    assert stale['placement_start_evidence'] is None and stale['placement_dependency'] is None
    assert 'travel:factory_place' in stale['unknowns']
    assert not plan.steps[0].allowed(state)
    state.factory['production_sites']['tick'] = state.tick
    data.recipes['copper-plate']['enabled'] = False
    assert candidate_evidence(state, data, [plan])[plan.id]['placement_dependency'] is None
    data.recipes['copper-plate']['enabled'] = True
    state.factory['production_sites']['sources'][role]['reason'] = 'survey_not_due'
    assert candidate_evidence(state, data, [plan])[plan.id]['placement_start_evidence'] is None


def test_new_owned_copper_furnace_requests_bounded_startup_coal_with_current_evidence():
    state, data = snapshot(inventory={}, player_position=(0, 0)), catalog()
    data.recipes['copper-plate'] = recipe('copper-plate', {'copper-ore': 1}, 'smelting')
    role = 'recipe:copper-plate'
    state.factory['entities'][role] = machine(unit_number=2546, fuel={},
                                               products_finished=0)
    state.factory['output_buffers'] = {'protocol': 1, 'session_id': state.session_id,
                                       'tick': state.tick, 'sources': {}}
    state.factory['input_routes'] = {'protocol': 1, 'session_id': state.session_id,
                                     'tick': state.tick, 'sources': {}}
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner._set_focus('lab', 1)
    path = ('item:lab', 'item:copper-plate')
    plan = planner._fuel(role, path)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters == {'resource': 'coal', 'quantity': 5}
    assert plan.steps[0].threshold == 5
    assert plan.materials['fuel_prerequisite']['source_unit'] == 2546
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['unknowns'] == []
    assert row['raw_prerequisite'] is None
    assert row['gather_start_evidence']['resource_in_current_observation'] is True
    assert row['gather_start_evidence']['fair_target_identity_observed'] is True
    assert row['fuel_prerequisite'] == {
        'observed_tick': state.tick, 'planner_item_path': ['lab', 'copper-plate'],
        'burner_role': role, 'burner_unit': 2546, 'fuel_now': 0,
        'startup_target': 5, 'current_required_units': 5,
        'basis': 'current_planner_fuel_need_and_owned_native_burner',
        'later_fuel_transfer_and_output_require_fresh_native_preconditions': True,
    }
    assert row['delivers_or_crafts'] == []
    context, questions, _ = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'rocket_launch')},
        [plan])
    assert context['candidate_evidence'][plan.id]['fuel_prerequisite'] == row['fuel_prerequisite']
    assert "owned burner's startup need" in questions[plan.id + '/benefit']['instructions']

    stale = replace(plan, materials={**plan.materials, 'fuel_prerequisite': {
        **plan.materials['fuel_prerequisite'], 'observed_tick': state.tick - 1}})
    assert candidate_evidence(state, data, [stale])[stale.id]['fuel_prerequisite'] is None
    missing = replace(plan, materials={key: value for key, value in plan.materials.items()
                                       if key != 'local_objective'})
    assert candidate_evidence(state, data, [missing])[missing.id]['fuel_prerequisite'] is None
    oversized = replace(plan, steps=(replace(plan.steps[0], threshold=50,
        parameters={'resource': 'coal', 'quantity': 50}),))
    assert candidate_evidence(state, data, [oversized])[oversized.id]['fuel_prerequisite'] is None
    state.factory['entities'][role].pop('fuel')
    assert candidate_evidence(state, data, [plan])[plan.id]['fuel_prerequisite'] is None
    state.factory['entities'][role]['fuel'] = {}
    state.factory['fair_resource_targets'].pop('coal')
    absent_site = candidate_evidence(state, data, [plan])[plan.id]
    assert absent_site['fuel_prerequisite'] is None
    assert absent_site['gather_start_evidence']['fair_target_identity_observed'] is False


def test_established_burner_retains_bulk_service_target():
    state, data = snapshot(inventory={}), catalog()
    state.factory['entities']['recipe:iron-plate'] = machine(
        unit_number=81, fuel={'coal': 0}, products_finished=20)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('iron-plate', 1)
    plan = planner._fuel('recipe:iron-plate', ('item:iron-plate',))
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].threshold == 50
    assert plan.materials['fuel_prerequisite']['startup'] is False


def test_native_shaped_startup_fuel_transfer_has_current_paid_start_evidence():
    state, data = snapshot(inventory={'coal': 5}, player_position=(0, 0)), catalog()
    data.recipes['copper-plate'] = recipe('copper-plate', {'copper-ore': 1}, 'smelting')
    role = 'recipe:copper-plate'
    state.factory['entities'][role] = machine(unit_number=2546, fuel={},
                                               products_finished=0)
    state.factory['player_connected'] = True
    state.factory['player_bound'] = True
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner._set_focus('lab', 1)
    plan = planner._fuel(role, ('item:lab', 'item:copper-plate'))
    step = plan.steps[0]
    assert step.action == 'factory_insert'
    assert step.parameters == {'role': role, 'item': 'coal', 'quantity': 5,
                               'receipt': f'{state.tick}:factory_insert:{role}:coal'}
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['fuel_prerequisite'] is None
    assert row['fuel_transfer_start_evidence'] == {
        'observed_tick': state.tick, 'planner_item_path': ['lab', 'copper-plate'],
        'burner_role': role, 'burner_unit': 2546, 'fuel_now': 0,
        'coal_in_inventory_now': 5, 'coal_to_transfer': 5,
        'native_receipt': step.parameters['receipt'],
        'basis': 'current_planner_need_owned_burner_and_paid_inventory',
        'native_transfer_and_later_output_require_verification': True,
    }
    context, questions, _ = question_batch(
        {'facts': state.for_jev(), **scheduling_context(state, data, [plan], 'rocket_launch')},
        [plan])
    assert context['candidate_evidence'][plan.id]['fuel_transfer_start_evidence'] == row['fuel_transfer_start_evidence']
    assert 'paid coal transfer' in questions[plan.id + '/benefit']['instructions']
    assert 'future transfer outcome' in questions[plan.id + '/needs_observation']['instructions']

    def missing(changed_plan=plan):
        return candidate_evidence(state, data, [changed_plan])[changed_plan.id][
            'fuel_transfer_start_evidence'] is None

    stale = replace(plan, materials={**plan.materials, 'fuel_prerequisite': {
        **plan.materials['fuel_prerequisite'], 'observed_tick': state.tick - 1}})
    assert missing(stale)
    wrong_role = replace(plan, steps=(replace(step, parameters={**step.parameters,
        'role': 'recipe:iron-plate'}),))
    assert missing(wrong_role)
    wrong_quantity = replace(plan, steps=(replace(step, parameters={**step.parameters,
        'quantity': 6}),))
    assert missing(wrong_quantity)
    wrong_receipt = replace(plan, steps=(replace(step, parameters={**step.parameters,
        'receipt': 'stale'}),))
    assert missing(wrong_receipt)
    state.inventory['coal'] = 4
    assert missing()
    state.inventory['coal'] = 5
    state.factory['entities'][role].pop('fuel')
    assert missing()
    state.factory['entities'][role]['fuel'] = {}
    state.factory['player_bound'] = False
    assert missing()


def test_local_rubric_does_not_require_one_pickup_to_launch_a_rocket():
    state, data, plans = transfers()
    support = scheduling_context(state, data, plans, 'rocket_launch')
    context, questions, offered = question_batch({'facts': state.for_jev(), **support}, plans)
    assert offered == plans
    assert 'local_objective' in questions['candidate']['instructions']
    assert 'all actions needed' not in str(questions[plans[0].id + '/benefit'])
    assert context['candidate_evidence'][plans[1].id]['travel_tiles_lower_bound'] == 5
    assert support['local_objective']['success_authority'].startswith('unchanged native')


def test_rank_prefers_near_collection_without_mutation_or_claiming_a_path():
    state, data, plans = transfers()
    before = deepcopy((state, plans))
    support = scheduling_context(state, data, plans, 'rocket_launch')
    assert support['deterministic_ranking'] == [plans[1].id, plans[0].id]
    assert support['candidate_evidence'][plans[1].id]['actor_ticks_estimate'] == 400
    assert (state, plans) == before
    assert support['selection_contract']['heuristics_are_not_native_timing_measurements']


@pytest.mark.parametrize('position', [None, {}, {'x': float('nan'), 'y': 0}, {'x': 1}])
def test_missing_geometry_is_unknown_not_zero_cost(position):
    state, data, plans = transfers()
    state.factory['entities']['near']['position'] = position
    support = scheduling_context(state, data, plans, 'rocket_launch')
    row = support['candidate_evidence'][plans[1].id]
    assert row['travel_tiles_lower_bound'] is None and row['actor_ticks_estimate'] is None
    assert row['unknowns']
    assert support['deterministic_ranking'][0] == plans[0].id


def test_urgent_fuel_wins_over_nearby_collection():
    state, data, plans = transfers()
    state.inventory['coal'] = 5
    state.factory['entities']['burner'] = machine(position={'x': 200, 'y': 0}, fuel={'coal': 0})
    fuel = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('burner', 'coal', 5)
    support = scheduling_context(state, data, [*plans, fuel], 'rocket_launch')
    assert support['deterministic_ranking'][0] == fuel.id
    assert support['candidate_evidence'][fuel.id]['urgency'] == 3


def test_due_science_delivery_precedes_speculative_stock_collection():
    state, data = scenario(available=0)
    state.inventory['red'] = 20
    state, data, plans = transfers(state, data)
    lab = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('utility:lab', 'red', 20)
    support = scheduling_context(state, data, [*plans, lab], 'rocket_launch')
    assert support['deterministic_ranking'][0] == lab.id
    assert 'due_research_delivery:red' in support['candidate_evidence'][lab.id]['reasons']


@pytest.mark.parametrize('fuel,expected', [(0, 0), (5, 2)])
def test_last_ingredient_only_unblocks_a_supplied_machine(fuel, expected):
    state, data = snapshot(inventory={'iron-ore': 10}), catalog()
    state.factory['entities']['furnace'] = machine(recipe='iron-plate', fuel={'coal': fuel})
    plan = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('furnace', 'iron-ore', 10)
    assert candidate_evidence(state, data, [plan])[plan.id]['urgency'] == expected


def test_passive_wait_cannot_outrank_ready_work_due_to_zero_actor_cost():
    state, data, plans = transfers()
    wait = ReadyWorkPlanner(data, state, 'rocket_launch')._wait('machine_output', 'iron-plate', 20, 'far')
    support = scheduling_context(state, data, [wait, *plans], 'rocket_launch')
    assert support['deterministic_ranking'][-1] == wait.id


def test_identical_options_collapse_but_different_receipts_remain_distinct():
    _, _, plans = transfers()
    duplicate = replace(plans[0], id='duplicate')
    assert distinct_candidates([plans[0], duplicate]) == [plans[0]]
    changed = replace(duplicate, steps=(replace(duplicate.steps[0], parameters={
        **duplicate.steps[0].parameters, 'receipt': 'new-receipt'}),))
    assert len(distinct_candidates([plans[0], changed])) == 2


@pytest.mark.parametrize('policy,scheduling,source,calls', [
    ('hybrid', 'ready-work', 'deterministic-singleton', 0),
    ('jev', 'ready-work', 'mock', 1),
    ('hybrid', 'serial', 'mock', 1),
    ('deterministic', 'ready-work', 'deterministic', 0),
])
def test_singleton_elision_preserves_explicit_policy_and_model_attribution(policy, scheduling, source, calls):
    backend, client, sink = FactorySimulation(), CountingModel(), Sink()
    backend.state.inventory['stone-furnace'] = 1
    client.last_model, client.last_usage = 'previous-request', {'input_tokens': 999}
    loop = HierarchicalLoop(backend, client, policy=policy, target='iron_smelting',
                            factory_scheduling=scheduling, research_log=sink, tick_seconds=0)
    record = loop.step()
    assert client.calls == calls and record['decision']['source'] == source
    assert record['model_call'] is bool(calls)
    assert len(events(sink, 'model_request')) == calls
    assert events(sink, 'decision')[0]['model_called'] is bool(calls)
    if not calls:
        assert record['resolved_model'] is None and record['usage'] is None
        assert events(sink, 'decision')[0]['diagnostics']['model_skipped']
    assert backend.actions  # Real controller dispatched the admitted synthetic action.


def test_hybrid_fallback_uses_recorded_ranking_and_retains_abstention_evidence():
    state, data, plans = transfers()
    class Client(CountingModel):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            result['candidate']['confidence'] = 0.1
            return result
    backend, client = FactorySimulation(), Client()
    backend.state.factory['entities'] = deepcopy(state.factory['entities'])
    loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                            factory_scheduling='ready-work', tick_seconds=0)
    loop._compile_candidates = lambda observation: (plans, '')
    record = loop.step()
    assert client.calls == 1 and backend.actions[0][1]['role'] == 'near'
    assert record['decision']['source'] == 'deterministic-fallback'
    assert record['decision']['diagnostics']['outcome'] == 'low_choice_confidence'
    assert record['decision']['answers']['candidate']['confidence'] == 0.1


def test_fresh_observation_still_prevents_spending_changed_inventory():
    backend, client = FactorySimulation(), CountingModel()
    backend.state.inventory['stone-furnace'] = 1
    original = backend.observe
    observations = 0
    def observe():
        nonlocal observations
        observations += 1
        if observations == 2:
            backend.state.inventory['stone-furnace'] = 0
        return original()
    backend.observe = observe
    loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                            factory_scheduling='ready-work', tick_seconds=0)
    record = loop.step()
    assert client.calls == 0 and not backend.actions
    assert record['action'] == 'observe' and 'precondition' in record['outcome']


@pytest.mark.parametrize('cause,outcome', [
    ('observe', 'model_abstention'), ('confidence', 'low_choice_confidence'),
    ('missing', 'all_candidates_rejected'), ('benefit', 'all_candidates_rejected'),
    ('disruption', 'all_candidates_rejected'), ('malformed', 'invalid_answer'),
    ('json', 'invalid_provider_payload'),
])
def test_distinct_failure_causes_are_auditable_without_lowering_confidence(cause, outcome):
    state, data, plans = transfers()
    class Client(MockJevClient):
        def evaluate(self, state, questions):
            if cause == 'json':
                raise ValueError('invalid JSON payload')
            answers = super().evaluate(state, questions)
            if cause == 'observe':
                answers['candidate']['choice'] = 'observe'
                answers['candidate']['probabilities'] = {key: float(key == 'observe')
                                                         for key in questions['candidate']['criteria']}
            elif cause == 'confidence':
                answers['candidate']['confidence'] = 0.1
            elif cause == 'malformed':
                answers.pop('candidate')
            else:
                for plan in plans:
                    if cause == 'missing':
                        answers[plan.id + '/needs_observation']['noul'] = 0.8
                    else:
                        answers[plan.id + '/' + cause]['confidence'] = 0.1
            return answers
    result = select_plan(Client(), scheduling_context(state, data, plans, 'rocket_launch'), plans)
    assert result.model_called and result.plan_id is None
    assert result.diagnostics['outcome'] == outcome
    if outcome == 'all_candidates_rejected':
        assert set(result.diagnostics['candidate_rejections']) == {p.id for p in plans}


def test_request_pruning_removes_unoffered_feature_rows_and_reports_ids():
    state, data, plans = transfers()
    plans = [replace(plans[0], id=f'candidate-{i}') for i in range(20)]
    support = scheduling_context(state, data, plans, 'rocket_launch')
    context, _, offered = question_batch(support, plans)
    assert set(context['candidate_evidence']) == {p.id for p in offered}
    assert set(context['deterministic_ranking']) == {p.id for p in offered}
    result = select_plan(MockJevClient(), support, plans)
    assert result.diagnostics['offered_candidates'] < 20
    assert len(result.diagnostics['pruned_candidate_ids']) + result.diagnostics['offered_candidates'] == 20


def test_canonical_replay_keeps_singleton_non_model_decision(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
    from jev_factorio.replay import replay_log
    path = tmp_path / 'research'
    backend, client = FactorySimulation(), CountingModel()
    backend.state.inventory['stone-furnace'] = 1
    with ResearchLog(path, RunConfiguration('mock', 'hierarchical', 'hybrid'), environ={}) as sink:
        loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                                factory_scheduling='ready-work', research_log=sink, tick_seconds=0)
        loop.step()
    verify_run(path)
    report = replay_log(path, format='research-v1')
    assert report.status != 'invalid', report.to_dict()
    assert not any('model' in finding.code for finding in report.findings)
    assert client.calls == 0
