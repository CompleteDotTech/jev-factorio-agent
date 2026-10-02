"""Offline replay of native047 decision12; no model calls or actor actions."""
import json
from copy import deepcopy
from dataclasses import replace, asdict
from pathlib import Path

import pytest

from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.input_controller import input_loop_type
from jev_factorio.outpost_controller import outpost_loop_type
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.state import GameSnapshot


def captured():
    data = json.loads((Path(__file__).parent / 'fixtures/native047-input-route-kit.json').read_text())
    assert data['provenance']['offline_only'] and data['provenance']['events_prefix_truncated']
    state = GameSnapshot(**deepcopy(data['snapshot']))
    # Rehydrate only the private identity markers recorded as accepted in the
    # captured observation. This remains offline evidence, never live authority.
    state._atomic_inventory_verified = state._coherent_observation_verified = (state.session_id, state.tick)
    catalog = Catalog.from_dict(data['catalog'])
    assert catalog.version == state.game_version == '2.0.77'
    plans = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    assert len(plans) == 1
    return state, catalog, plans[0], data['historical_answers']


def context(state, catalog, plan):
    loop_type = outpost_loop_type(input_loop_type(buffered_loop_type(HierarchicalLoop)))
    facts = loop_type._model_facts(object.__new__(loop_type), state)
    # Apply the ready-work model renderer's established receipt compaction
    # (controller.step), without altering the authoritative captured snapshot.
    receipts = facts['factory'].pop('receipts', {})
    facts['factory'].pop('connectors', None)
    facts['factory']['native_transfer_receipt_count'] = len(receipts)
    return {'facts': facts,
            **scheduling_context(state, catalog, [plan], plan.goal)}


def test_native047_one_ore_keeps_bounded_kit_and_science_parent_distinct():
    state, catalog, plan, _ = captured()
    step = plan.steps[0]
    assert step.action == 'factory_gather' and step.item == 'iron-ore'
    assert step.threshold == 1 and step.parameters == {'resource': 'iron-ore', 'quantity': 1}
    batch = context(state, catalog, plan)
    assert batch['local_objective']['primary_target'] == {
        'item': 'burner-inserter', 'inventory_target': 1, 'ultimate_goal': 'rocket_launch'}
    row = batch['candidate_evidence'][plan.id]
    parent = row['input_route_kit_parent_purpose']
    assert parent['source'] == 'recipe:iron-plate' and parent['source_unit'] == 2547
    assert parent['parent_local_objective']['item'] == 'automation-science-pack'
    assert parent['parent_local_objective']['inventory_target'] == 10
    assert parent['parent_planner_item_path'] == ['automation-science-pack', 'iron-gear-wheel', 'iron-plate']
    assert parent['remaining_route_bill'] == {
        'burner-inserter': 1, 'burner-mining-drill': 1, 'transport-belt': 25}
    assert parent['kit_kind'] == 'route_component'
    assert parent['construction_fuel_inventory_target'] == 15
    assert row['raw_prerequisite']['planner_item_path'] == ['burner-inserter', 'iron-plate', 'iron-ore']
    assert row['local_target_completion_evidence'] is None
    _, questions, offered = question_batch(batch, [plan])
    assert offered == [plan]
    assert '`raw_prerequisite` binds this bounded gather' in questions[plan.id+'/useful_progress']['instructions']
    assert 'supplies a useful recipe input (level 1)' in questions[plan.id+'/benefit']['instructions']
    assert 'not a completed kit' in batch['local_objective']['instruction']


@pytest.mark.parametrize('change', ['schema', 'schema_bool', 'tick', 'session', 'unit',
    'layout', 'bill', 'kit_target', 'parent_target', 'parent_path', 'raw_path',
    'native_unit', 'native_tick', 'native_protocol', 'native_session', 'atomic', 'coherent',
    'catalog_version'])
def test_stale_wrong_identity_or_forged_parent_cannot_qualify_recipe_input(change):
    state, catalog, plan, _ = captured()
    materials = deepcopy(plan.materials)
    marker = materials['input_route_kit_prerequisite']
    if change == 'schema': marker['schema'] = 2
    elif change == 'schema_bool': marker['schema'] = True
    elif change == 'tick': marker['observed_tick'] -= 1
    elif change == 'session': marker['session_id'] = 'other'
    elif change == 'unit': marker['source_unit'] += 1
    elif change == 'layout': marker['layout'] = 'other'
    elif change == 'bill': marker['remaining_route_bill']['burner-inserter'] = 2
    elif change == 'kit_target':
        marker['kit_inventory_target'] = materials['local_objective']['inventory_target'] = 2
    elif change == 'parent_target': marker['parent_local_objective']['inventory_target'] = 1
    elif change == 'parent_path': marker['parent_planner_item_path'] = ['automation-science-pack', 'copper-plate']
    elif change == 'raw_path': materials['raw_prerequisite']['planner_item_path'] = ['automation-science-pack', 'iron-plate', 'iron-ore']
    elif change == 'native_unit': state.factory['entities']['recipe:iron-plate']['unit_number'] += 1
    elif change == 'native_tick': state.factory['input_routes']['tick'] -= 1
    elif change == 'native_protocol': state.factory['input_routes']['protocol'] = True
    elif change == 'native_session': state.factory['input_routes']['session_id'] = 'other'
    elif change == 'atomic': state._atomic_inventory_verified = ('other', state.tick)
    elif change == 'coherent': state._coherent_observation_verified = ('other', state.tick)
    elif change == 'catalog_version': catalog = replace(catalog, version='stale-version')
    plan = replace(plan, materials=materials)
    if change in {'native_unit', 'native_tick', 'native_protocol', 'native_session'}:
        # The real model renderer rejects these malformed native observations
        # before creating any prompt; decision evidence independently rejects.
        with pytest.raises(ValueError):
            context(state, catalog, plan)
        batch = {'facts': {}, **scheduling_context(state, catalog, [plan], plan.goal)}
    else:
        batch = context(state, catalog, plan)
    row = batch['candidate_evidence'][plan.id]
    assert row['input_route_kit_parent_purpose'] is None
    assert row['raw_prerequisite'] is None
    assert row['local_target_completion_evidence'] is None
    assert batch['local_objective']['primary_target'] is None
    _, questions, _ = question_batch(batch, [plan])
    assert '`raw_prerequisite` binds this bounded gather' not in questions[plan.id+'/useful_progress']['instructions']


def test_original_model12_answers_still_reject_without_gate_changes():
    state, catalog, plan, answers = captured()
    class HistoricalClient:
        def evaluate(self, state, questions):
            return deepcopy(answers)
    result = select_plan(HistoricalClient(), context(state, catalog, plan), [plan])
    assert result.plan_id is None
    assert result.reason == 'Candidate evidence insufficient'
    assert result.answers == answers


def test_kit_acquisition_restores_outer_focus_even_if_planning_fails(monkeypatch):
    from jev_factorio.planning.input_routes import InputRoutePlanner
    from jev_factorio.planning.output_buffers import OutputBufferPlanner
    state, catalog, _, _ = captured()
    planner = InputRoutePlanner(catalog, state, 'rocket_launch')
    planner._set_focus('automation-science-pack', 10)
    planner._route_kit_parent = state.factory['input_routes']['sources']['recipe:iron-plate']
    def fail(*args, **kwargs): raise ValueError('fixture failure')
    monkeypatch.setattr(OutputBufferPlanner, '_need', fail)
    with pytest.raises(ValueError, match='fixture failure'):
        planner._acquire('burner-inserter', 1, ('item:automation-science-pack', 'item:iron-gear-wheel'))
    assert planner.focus == ('automation-science-pack', 10)
    assert planner._acquiring_route is False and planner._economic_acquiring is False


def test_current_top_planner_variant_revalidation_is_bounded_and_readonly(monkeypatch):
    from jev_factorio.planning.input_routes import InputRoutePlanner
    state, catalog, plan, _ = captured()
    before = deepcopy(asdict(state))
    visits = []
    original = InputRoutePlanner._visit
    def visit(self, key, path):
        result = original(self, key, path)
        visits.append(self.expansions)
        return result
    monkeypatch.setattr(InputRoutePlanner, '_visit', visit)
    batch = context(state, catalog, plan)
    assert batch['candidate_evidence'][plan.id]['input_route_kit_parent_purpose'] is not None
    assert visits and max(visits) <= 512
    assert asdict(state) == before
