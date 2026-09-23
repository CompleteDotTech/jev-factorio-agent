"""Synthetic investment and native-contract tests, not live throughput claims."""
from copy import deepcopy

import pytest

from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning.economics import capability_technology, investment_cost, remaining_products
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.scheduling import next_technology
from test_factory import catalog, snapshot, recipe, machine


def economic_catalog():
    data = catalog()
    data.recipes['assembling-machine-1'] = recipe('assembling-machine-1', {'iron-plate': 5}, enabled=False)
    data.recipes['automation-science-pack'] = recipe('automation-science-pack', {'iron-plate': 1})
    data.recipes['automation-science-pack']['energy'] = 5
    data.recipes['iron-gear-wheel'] = recipe('iron-gear-wheel', {'iron-plate': 2})
    data.machines['assembling-machine-1'] = {'categories': {'crafting': True}, 'electric': True,
                                           'burner': False, 'speed': 0.5}
    data.technologies['automation'] = {'enabled': True, 'effects': [
        {'type': 'unlock-recipe', 'recipe': 'assembling-machine-1'}], 'prerequisites': [],
        'count': 10, 'energy_ticks': 60, 'ingredients': [{'name': 'automation-science-pack', 'amount': 1}]}
    data.technologies['study'] = {'enabled': True, 'effects': [], 'prerequisites': [],
        'count': 200, 'energy_ticks': 60, 'ingredients': [{'name': 'automation-science-pack', 'amount': 1}]}
    data.technologies['rocket-silo'] = {'enabled': True, 'effects': [], 'prerequisites': ['study'],
        'count': 20, 'energy_ticks': 60, 'ingredients': [{'name': 'automation-science-pack', 'amount': 1}]}
    return data


def economic_state():
    state = snapshot(researched=['automation'], inventory={'iron-plate': 20, 'coal': 50, 'assembling-machine-1': 1})
    state.factory.update(research='study', research_progress=0)
    entities = state.factory['entities']
    entities['utility:lab'] = machine('lab', unit_number=20, energy=100, electric_network_id=1)
    entities['utility:water'] = machine('offshore-pump', unit_number=21, fluid_ports=[{'id': 1, 'fluid': 'water'}])
    entities['utility:boiler'] = machine('boiler', unit_number=22, fuel={'coal': 50},
        fluid_ports=[{'id': 1, 'fluid': 'water'}, {'id': 2, 'fluid': 'steam'}])
    entities['utility:engine'] = machine('steam-engine', unit_number=23, electric_network_id=1,
        fluid_ports=[{'id': 2, 'fluid': 'steam'}])
    return state


def test_basic_assembler_unlock_precedes_rocket_dependency_order():
    data, state = economic_catalog(), economic_state()
    state.researched = []
    state.factory['research'] = ''
    assert capability_technology(data, []) == 'automation'
    plan = ReadyWorkPlanner(data, state, 'rocket_launch').plan()
    assert plan.steps[0].action == 'factory_research'
    assert plan.steps[0].parameters['technology'] == 'automation'
    assert plan.materials['economics']['objective'] == 'unlock_basic_assembly'
    assert next_technology(data, [], '') == 'automation'
    assert next_technology(data, [], 'automation') == 'study'
    # The explicit serial baseline retains its original behavior.
    assert FactoryPlanner(data, state, 'rocket_launch').plan().steps[0].parameters['technology'] == 'study'


def test_current_research_is_not_cancelled_to_prioritize_automation():
    data, state = economic_catalog(), economic_state()
    state.researched = []
    plan = ReadyWorkPlanner(data, state, 'rocket_launch').plan()
    assert not (plan.steps[0].action == 'factory_research'
                and plan.steps[0].parameters['technology'] != 'study')
    assert state.factory['research'] == 'study'


def test_short_bootstrap_stays_handcrafted_and_recurring_science_builds_machine():
    data, state = economic_catalog(), economic_state()
    state.factory['research'] = 'automation'
    small = ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 10)
    assert small.steps[0].action == 'factory_craft'
    state.factory['research'] = 'study'
    large = ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20)
    assert large.steps[0].action == 'factory_place'
    assert large.steps[0].parameters['name'] == 'assembling-machine-1'
    assert large.steps[0].costs == {'assembling-machine-1': 1}
    assert not large.steps[0].satisfied(state)
    assert large.materials['economics']['workload'] >= 100


def test_existing_machine_is_used_even_when_recipe_is_handcraftable():
    data, state = economic_catalog(), economic_state()
    state.factory['entities']['recipe:automation-science-pack'] = machine('assembling-machine-1',
        unit_number=30, recipe='automation-science-pack', energy=100, electric_network_id=1)
    plan = ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters == {'role': 'recipe:automation-science-pack', 'item': 'iron-plate',
                                      'quantity': 20, 'receipt': '10:factory_insert:recipe:automation-science-pack:iron-plate'}


def test_machine_batch_wait_is_limited_to_actual_paid_potential():
    data, state = economic_catalog(), economic_state()
    data.recipes['automation-science-pack']['ingredients'][0]['amount'] = 20
    data.stack_sizes['iron-plate'] = 100
    state.factory['entities']['recipe:automation-science-pack'] = machine('assembling-machine-1',
        unit_number=30, recipe='automation-science-pack', energy=100, electric_network_id=1,
        input={'iron-plate': 80}, crafting=True)
    wait = ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20)
    assert wait.steps[0].action == 'factory_wait' and wait.steps[0].threshold == 5
    state.tick += 100000
    assert not wait.steps[0].satisfied(state)
    state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 5
    assert wait.steps[0].satisfied(state)


def test_assembler_collection_retains_real_batch_threshold():
    data, state = economic_catalog(), economic_state()
    state.factory['entities']['recipe:automation-science-pack'] = machine('assembling-machine-1',
        unit_number=30, recipe='automation-science-pack', energy=100, electric_network_id=1,
        input={'iron-plate': 15}, crafting=True, output={'automation-science-pack': 2})
    plan = ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20)
    assert plan.steps[0].action == 'factory_wait' and plan.steps[0].threshold == 10
    state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 10
    assert ReadyWorkPlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20).steps[0].action == 'factory_extract'


def test_machine_construction_does_not_recursively_invest_in_its_own_gear_assembler():
    data, state = economic_catalog(), economic_state()
    state.inventory = {'iron-plate': 20}
    data.recipes['assembling-machine-1'] = recipe('assembling-machine-1', {'iron-gear-wheel': 5}, enabled=True)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._economic_products = {'iron-gear-wheel': 1000}
    step = planner._machine('recipe:automation-science-pack', 'assembling-machine-1', ()).steps[0]
    assert step.action == 'factory_craft' and step.parameters['recipe'] == 'iron-gear-wheel'
    assert not getattr(planner, '_economic_acquiring', False)


def test_optional_gear_machine_bootstraps_only_its_kit_without_recursive_investment():
    data, state = economic_catalog(), economic_state()
    state.inventory = {'iron-plate': 40}
    data.recipes['assembling-machine-1'] = recipe(
        'assembling-machine-1', {'iron-gear-wheel': 5}, enabled=True
    )
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._economic_products = {'iron-gear-wheel': 1000}

    step = planner._need('iron-gear-wheel', 20).steps[0]

    assert step.action == 'factory_craft'
    assert step.parameters == {'recipe': 'iron-gear-wheel', 'batches': 5}
    assert step.costs == {'iron-plate': 10}
    assert 'factory_place' not in step.action


def test_unsupported_or_locked_investment_is_not_free_capability():
    data = economic_catalog()
    assert investment_cost(data, 'assembling-machine-1', []) is None
    data.recipes['assembling-machine-1']['ingredients'][0]['name'] = 'unobtainable'
    assert investment_cost(data, 'assembling-machine-1', ['automation']) is None
    data.recipes['automation-science-pack']['ingredients'][0]['type'] = 'fluid'
    state = economic_state()
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    # Unsupported economic route falls through to native production logic, not a fake paid investment.
    assert planner._investment_machine(data.recipes['automation-science-pack']) is None


def capacity_fixture():
    data, state = economic_catalog(), economic_state()
    data.recipes['iron-plate']['energy'] = 10
    state.inventory.update({'iron-ore': 50, 'stone-furnace': 1})
    state.factory['entities']['recipe:iron-plate'] = machine(recipe='iron-plate',
        fuel={'coal': 50}, input={'iron-ore': 30}, crafting=True, products_finished=100)
    from jev_factorio.planning.capacity_evidence import CapacityHistory
    history = CapacityHistory()
    for offset in range(3):
        state.tick = 10 + offset * 600
        state.factory['entities']['recipe:iron-plate']['products_finished'] = 100 + offset
        history.observe(state, data)
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._economic_products = {'iron-plate': 200}
    wait = planner._wait('machine_output', 'iron-plate', 10, 'recipe:iron-plate')
    return data, state, planner, wait


def test_capacity_is_added_at_supplied_bottleneck_without_replacing_paid_source():
    data, state, planner, wait = capacity_fixture()
    original = deepcopy(state.factory['entities']['recipe:iron-plate'])
    plan = planner._capacity_work(wait)
    assert plan.steps[0].action == 'factory_place'
    assert plan.steps[0].parameters['role'] == 'capacity:iron-plate:2'
    assert plan.steps[0].parameters['anchor'] == 'recipe:iron-plate'
    assert plan.materials['economics']['service_mode'] == 'bounded_manual_transfers'
    assert state.factory['entities']['recipe:iron-plate'] == original


@pytest.mark.parametrize('change', ['no_input', 'no_fuel', 'not_working', 'no_spare_input', 'no_history', 'small_demand'])
def test_capacity_does_not_mask_supply_or_bootstrap_problems(change):
    data, state, planner, wait = capacity_fixture()
    source = state.factory['entities']['recipe:iron-plate']
    if change == 'no_input': source['input'] = {}
    if change == 'no_fuel': source['fuel'] = {}
    if change == 'not_working': source['crafting'] = False
    if change == 'no_spare_input': state.inventory['iron-ore'] = 0
    if change == 'no_history': source['products_finished'] = 0
    if change == 'small_demand': planner._economic_products = {'iron-plate': 20}
    assert planner._capacity_work(wait) == wait


def test_extra_cell_is_serviced_not_rebuilt_and_cannot_create_a_third_cell():
    data, state, planner, wait = capacity_fixture()
    state.factory['entities']['capacity:iron-plate:2'] = machine(recipe='iron-plate', unit_number=31,
                                                               fuel={'coal': 50})
    step = planner._capacity_work(wait).steps[0]
    assert step.action == 'factory_insert' and step.parameters['role'] == 'capacity:iron-plate:2'
    state.factory['entities']['capacity:iron-plate:2'].update(input={'iron-ore': 10}, crafting=True)
    assert planner._capacity_work(wait) == wait


def test_unlocked_faster_tier_can_be_added_without_changing_main_furnace_identity():
    data, state, planner, wait = capacity_fixture()
    data.recipes['steel-furnace'] = recipe('steel-furnace', {'iron-plate': 10})
    data.machines['steel-furnace'] = {'categories': {'smelting': True}, 'burner': True,
                                     'electric': False, 'speed': 2}
    state.inventory['steel-furnace'] = 1
    plan = planner._capacity_work(wait)
    assert plan.steps[0].parameters['name'] == 'steel-furnace'
    assert state.factory['entities']['recipe:iron-plate']['name'] == 'stone-furnace'
    state.factory['entities']['capacity:iron-plate:2'] = machine('steel-furnace', unit_number=31,
                                                               recipe='iron-plate', fuel={'coal': 50})
    assert planner._capacity_work(wait).steps[0].parameters['item'] == 'iron-ore'


def test_research_workload_is_bounded_and_does_not_mutate_authoritative_stock():
    data, state = economic_catalog(), economic_state()
    before = deepcopy(state)
    data.technologies['study']['count'] = 1000000
    products = remaining_products(state, data)
    assert products['automation-science-pack'] <= 200
    assert all(v <= 2000 for v in products.values())
    assert state == before


def test_capability_composition_keeps_output_and_input_ownership_rules():
    data, state = economic_catalog(), economic_state()
    for key in ('output_buffers', 'input_routes'):
        state.factory[key] = {'protocol': 1, 'session_id': state.session_id, 'tick': state.tick, 'sources': {}}
    assert InputRoutePlanner(data, state, 'rocket_launch')._need('automation-science-pack', 20).steps[0].action == 'factory_place'


def test_real_controller_builds_supplies_verifies_and_reuses_science_machine(tmp_path):
    data, state = economic_catalog(), economic_state()
    state.inventory['iron-plate'] = 80
    class Backend:
        def __init__(self): self.calls = []
        def enable_factory(self): return data
        def observe(self): return deepcopy(state)
        def execute(self, action, parameters):
            self.calls.append((action, dict(parameters)))
            p = parameters
            entities = state.factory['entities']
            if action == 'factory_place':
                assert state.inventory[p['name']] >= 1
                state.inventory[p['name']] -= 1
                entities[p['role']] = machine(p['name'], unit_number=30, energy=100, electric_network_id=1)
            elif action == 'factory_configure':
                entities[p['role']]['recipe'] = p['recipe']
            elif action in {'factory_insert', 'factory_extract'}:
                entity, item, count = entities[p['role']], p['item'], p['quantity']
                extracting = action == 'factory_extract'
                if extracting:
                    assert entity['output'].get(item, 0) >= count
                    entity['output'][item] -= count
                    state.inventory[item] = state.inventory.get(item, 0) + count
                else:
                    assert state.inventory.get(item, 0) >= count
                    state.inventory[item] -= count
                    entity['input'][item] = entity['input'].get(item, 0) + count
                state.factory['receipts'][p['receipt']] = dict(p, extracting=extracting, unit_number=entity['unit_number'])
            elif action == 'factory_wait':
                pass
            else:
                raise AssertionError(action)
            return 'synthetic native-contract result'
    backend = Backend()
    loop = HierarchicalLoop(backend, policy='deterministic', target='rocket_launch',
                            factory_scheduling='ready-work', checkpoint=str(tmp_path / 'state.json'), tick_seconds=0)
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
                 completed_goals={g: 1 for g in loop.order[:-1]}, last_tick=state.tick)
    def candidates(current):
        planner = ReadyWorkPlanner(data, current, 'rocket_launch')
        return [planner._need('automation-science-pack', 20)], ''
    loop._compile_candidates = candidates
    records = []
    for cycle in range(2):
        for _ in range(12):
            records.append(loop.step())
            state.tick += 60
            producer = state.factory['entities'].get('recipe:automation-science-pack', {})
            if producer.get('recipe') == 'automation-science-pack' and producer.get('input', {}).get('iron-plate', 0):
                producer['crafting'] = True
                count = min(5, producer['input']['iron-plate'])
                producer['input']['iron-plate'] -= count
                producer['output']['automation-science-pack'] = producer['output'].get('automation-science-pack', 0) + count
                producer['products_finished'] = producer.get('products_finished', 0) + count
                producer['crafting'] = bool(producer['input']['iron-plate'])
            if state.inventory.get('automation-science-pack', 0) >= 20:
                break
        assert state.inventory['automation-science-pack'] == 20
        state.inventory['automation-science-pack'] = 0  # Synthetic downstream consumption between batches.
    assert sum(action == 'factory_place' for action, _ in backend.calls) == 1
    assert not any(action.startswith('factory_craft') for action, _ in backend.calls)
    assert state.factory['entities']['recipe:automation-science-pack']['products_finished'] == 40
    assert state.inventory['iron-plate'] == 40
    assert all(record['verified'] for record in records if record['action'] == 'factory_extract')


def test_capacity_requires_observed_extra_output_and_adds_supplied_production():
    data, state, planner, wait = capacity_fixture()
    role = 'capacity:iron-plate:2'
    state.factory['entities'][role] = machine(recipe='iron-plate', unit_number=31, fuel={'coal': 50})
    feed = planner._capacity_work(wait).steps[0]
    assert feed.action == 'factory_insert' and not feed.satisfied(state)
    amount = feed.parameters['quantity']
    state.inventory['iron-ore'] -= amount
    state.factory['entities'][role]['input']['iron-ore'] = amount
    state.factory['receipts'][feed.parameters['receipt']] = dict(feed.parameters, extracting=False, unit_number=31)
    assert feed.satisfied(state)
    assert planner._capacity_work(wait) == wait, 'Placement and feeding alone are not extra output'
    # Explicit synthetic production under equal elapsed ticks, using the native catalog recipe.
    # Both cells consume real fixture input; this is not a measured Factorio speedup.
    produced = {}
    for name in ('recipe:iron-plate', role):
        entity = state.factory['entities'][name]
        batches = min(10, entity['input']['iron-ore'])
        entity['input']['iron-ore'] -= batches
        entity['output']['iron-plate'] = batches
        entity['products_finished'] = entity.get('products_finished', 0) + batches
        entity['crafting'] = bool(entity['input']['iron-ore'])
        produced[name] = batches
    state.tick += 6000
    pickup = planner._capacity_work(wait).steps[0]
    assert pickup.action == 'factory_extract' and pickup.parameters['role'] == role
    assert pickup.parameters['quantity'] == produced[role] == 10
    assert sum(produced.values()) == 20 and produced['recipe:iron-plate'] == 10
    assert not pickup.satisfied(state), 'Available output is not a transfer receipt'
