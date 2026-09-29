"""Synthetic goal relevance; never an economic admission or native trial."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.coal_goal_alignment import evaluate
from jev_factorio.state import GameSnapshot
from test_coal_native_evidence import checked, observed_research_fixture
from test_factory import catalog, recipe


def case():
    raw, bundle = observed_research_fixture()
    native = checked(raw, bundle)
    snapshot = GameSnapshot(tick=native.epoch.tick, session_id=native.epoch.session_id,
                            world_kind='fle', game_version='2.0.77', researched=[])
    snapshot.factory = {'research': 'current-study', 'acceptance_runtime': {
        'session_id': snapshot.session_id, 'tick': snapshot.tick,
        'actor_unit': native.actor_unit, 'player_index': native.epoch.actor_index,
        'surface_index': native.epoch.surface_index,
        'force_index': native.epoch.force_index}}
    memory = CampaignMemory(snapshot.session_id, 'rocket_launch', active_goal='rocket_launch',
                            last_tick=snapshot.tick)
    memory.coal_economic_admission = True
    memory.coal_kit_policy = True
    data = catalog()
    study = {'enabled': True, 'effects': [], 'prerequisites': [], 'trigger': False,
             'count': 30, 'energy_ticks': 1800,
             'ingredients': [{'name': 'automation-science-pack', 'amount': 1}]}
    data.technologies['current-study'] = deepcopy(study)
    data.technologies['rocket-silo'] = {**deepcopy(study),
        'prerequisites': ['current-study']}
    return snapshot, memory, data, native


def aligned(args):
    return evaluate(*args)


def test_selected_research_on_rocket_path_is_only_current_relevance():
    args = case(); before = deepcopy(args[2].technologies)
    result = aligned(args)
    assert result == {'schema': 'jev.coal-goal-alignment.v1', 'relevant': True,
                      'reason': 'rocket_silo_prerequisite', 'technology': 'current-study',
                      'path': ['rocket-silo', 'current-study'],
                      'future_commitment': False, 'mutation_authorized': False}
    assert args[2].technologies == before


@pytest.mark.parametrize('change,reason', [
    (lambda a: setattr(a[1], 'active_goal', 'bootstrap_mining'), 'unqualified_inputs'),
    (lambda a: setattr(a[1], 'coal_economic_admission', False), 'unqualified_inputs'),
    (lambda a: setattr(a[1], 'last_tick', a[0].tick - 1), 'unqualified_inputs'),
    (lambda a: setattr(a[0], 'session_id', 'other'), 'unqualified_inputs'),
    (lambda a: a[0].factory['acceptance_runtime'].update(actor_unit=1),
     'native_epoch_or_scope_changed'),
    (lambda a: a[0].factory.update(research='unrelated'), 'selected_research_unavailable'),
    (lambda a: a[0].researched.append('current-study'), 'research_already_complete_or_unknown'),
    (lambda a: a[2].technologies['rocket-silo'].update(prerequisites=[]),
     'selected_research_unrelated_to_goal'),
    (lambda a: a[2].technologies['rocket-silo'].update(prerequisites=['rocket-silo']),
     'unsupported_goal_graph_or_binding'),
    (lambda a: a[2].technologies['current-study']['ingredients'][0].update(amount=2),
     'selected_technology_bill_changed'),
    (lambda a: a[2].technologies.pop('current-study'),
     'selected_technology_unsupported'),
    (lambda a: setattr(a[1], 'pending', {'action': 'factory_craft'}), 'unqualified_inputs'),
])
def test_changed_goal_epoch_catalog_or_pending_work_fails_closed(change, reason):
    args = case()
    change(args)
    result = aligned(args)
    assert result['relevant'] is False and result['reason'] == reason
    assert result['future_commitment'] is False and result['mutation_authorized'] is False


def test_unrelated_selected_technology_is_not_required_by_rocket_path():
    args = case()
    args[2].technologies['rocket-silo']['prerequisites'] = []
    assert aligned(args)['reason'] == 'selected_research_unrelated_to_goal'


def test_locked_assembler_capability_path_is_goal_relevant_but_uncommitted():
    args = case()
    data = args[2]
    data.technologies['rocket-silo']['prerequisites'] = []
    data.recipes['assembling-machine-1'] = recipe('assembling-machine-1', {'iron-plate': 5}, enabled=False)
    data.machines['assembling-machine-1'] = {'categories': {'crafting': True}}
    data.technologies['current-study']['effects'] = [
        {'type': 'unlock-recipe', 'recipe': 'assembling-machine-1'}]
    result = aligned(args)
    assert result['relevant'] is True
    assert result['reason'] == 'assembler_capability_prerequisite'
    assert result['path'] == ['current-study']
    assert result['future_commitment'] is False


def test_native_missing_research_or_claimed_mutation_never_binds_goal():
    args = case()
    args = (*args[:3], replace(args[3], research_work=None))
    assert aligned(args)['reason'] == 'selected_research_unavailable'
    args = case()
    args = (*args[:3], replace(args[3], mutation_authorized=True))
    assert aligned(args)['reason'] == 'native_epoch_or_scope_changed'


def test_native_catalog_shape_with_typed_ingredient_and_factory_research():
    snapshot, memory, data, native = case()
    # lua/catalog.lua exports force.current_research.name as a string and
    # carries the runtime Ingredient.type through unmodified.
    data.technologies['current-study']['ingredients'][0]['type'] = 'item'
    data.technologies['current-study'].update(researched=False, trigger=None)
    native_catalog = Catalog.from_dict({'version': '2.0.77', 'mods': {'base': '2.0.77'},
        'recipes': deepcopy(data.recipes), 'technologies': deepcopy(data.technologies),
        'machines': deepcopy(data.machines), 'hand_categories': deepcopy(data.hand_categories),
        'stack_sizes': deepcopy(data.stack_sizes)})
    assert snapshot.factory['research'] == native.research_work.technology
    assert evaluate(snapshot, memory, native_catalog, native)['relevant'] is True
    native_catalog.technologies['current-study']['ingredients'][0]['type'] = 'fluid'
    assert evaluate(snapshot, memory, native_catalog, native)['reason'] == 'selected_technology_bill_changed'
