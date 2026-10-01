"""Synthetic contract tests; no native harvest or model progress claims."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.judgments import question_batch
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_factory import catalog, recipe, snapshot
from test_local_decisions import _native_lab_craft_plan


def batch():
    state = snapshot(inventory={'iron-plate': 8, 'pipe': 30, 'iron-ore': 0})
    data = catalog()
    data.recipes['pipe'] = recipe('pipe', {'iron-plate': 1})
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    planner._set_focus('pipe', 41)
    plan = planner._need('iron-ore', 3, ('item:pipe', 'item:iron-plate'))
    context = {'facts': state.for_jev(),
               **scheduling_context(state, data, [plan], 'rocket_launch')}
    return context, plan


def instructions(context, plan):
    _, questions, _ = question_batch(context, [plan])
    return questions[plan.id + '/useful_progress']['instructions'], questions[plan.id + '/benefit']['instructions']


def test_current_recipe_input_facts_explained_to_both_independent_judgments():
    context, plan = batch()
    useful, benefit = instructions(context, plan)
    assert '`raw_prerequisite` binds this bounded gather' in useful
    assert 'does not establish harvested inventory' in useful
    assert 'Contrary current facts can make usefulness unsupported' in useful
    assert 'level 1' not in useful and 'level 2' not in useful
    assert 'supplies a useful recipe input (level 1)' in benefit


def test_raw_gather_eligibility_context_survives_another_offered_candidate():
    context, plan = batch()
    other = replace(plan, id=plan.id + ':other')
    _, questions, offered = question_batch(context, [plan, other], max_bytes=48000)
    assert len(offered) == 2
    assert '`raw_prerequisite` binds this bounded gather' in questions[plan.id+'/useful_progress']['instructions']
    assert '`raw_prerequisite` binds this bounded gather' not in questions[other.id+'/useful_progress']['instructions']
    # Existing sole-candidate magnitude wording remains limited to that case.
    assert 'supplies a useful recipe input (level 1)' not in questions[plan.id+'/benefit']['instructions']


@pytest.mark.parametrize('change', ['session', 'start_tick', 'raw_tick', 'inventory',
                                  'inventory_bool', 'path', 'fair_target', 'scope'])
def test_stale_or_contradictory_raw_gather_does_not_receive_qualified_context(change):
    context, plan = batch()
    context = deepcopy(context)
    row = context['candidate_evidence'][plan.id]
    if change == 'session': row['gather_start_evidence']['session_id'] = 'other'
    elif change == 'start_tick': row['gather_start_evidence']['observed_tick'] -= 1
    elif change == 'raw_tick': row['raw_prerequisite']['observed_tick'] -= 1
    elif change == 'inventory': context['facts']['inventory']['iron-ore'] = 1
    elif change == 'inventory_bool': context['facts']['inventory']['iron-ore'] = False
    elif change == 'path': row['raw_prerequisite']['planner_item_path'][0] = 'unrelated'
    elif change == 'fair_target': row['gather_start_evidence']['fair_target_identity_observed'] = False
    elif change == 'scope': row['work_scope'] = 'lookahead'
    useful, benefit = instructions(context, plan)
    assert '`raw_prerequisite` binds this bounded gather' not in useful
    assert 'supplies a useful recipe input (level 1)' not in benefit


def partial_craft_batch():
    state, data, plan = _native_lab_craft_plan()
    plan = replace(plan, materials={**plan.materials, 'local_objective': {
        **plan.materials['local_objective'], 'inventory_target': 2}})
    return {'facts': state.for_jev(),
            **scheduling_context(state, data, [plan], 'rocket_launch')}, plan


def test_current_partial_target_craft_receives_factual_eligibility_context():
    context, plan = partial_craft_batch()
    useful, benefit = instructions(context, plan)
    assert 'currently carried ingredients can supply a useful partial target batch' in useful
    assert 'remaining shortfall stays open' in useful
    assert 'does not establish crafted inventory' in useful
    assert 'level 1' not in useful and 'level 2' not in useful
    assert 'leaves the current local-target shortfall open' in benefit


@pytest.mark.parametrize('change', ['session', 'tick', 'inventory', 'cost', 'queue',
                                  'path', 'expected_output', 'closure', 'native_queue',
                                  'native_player', 'native_protocol', 'local_target'])
def test_partial_craft_stale_or_contrary_proofs_do_not_receive_eligibility_hint(change):
    context, plan = partial_craft_batch()
    context = deepcopy(context)
    row = context['candidate_evidence'][plan.id]
    if change == 'session': row['local_target_completion_evidence']['session_id'] = 'other'
    elif change == 'tick': row['local_target_completion_evidence']['observed_tick'] -= 1
    elif change == 'inventory': context['facts']['inventory']['lab'] = 1
    elif change == 'cost':
        item = next(iter(plan.steps[0].costs))
        context['facts']['inventory'][item] = 0
    elif change == 'queue': row['craft_start_evidence']['crafting_queue_empty'] = False
    elif change == 'path': row['craft_dependency']['planner_item_path'] = ['unrelated']
    elif change == 'expected_output': row['local_target_completion_evidence']['expected_output_after_native_receipt'] = 9
    elif change == 'closure': row['local_target_completion_evidence']['would_close_current_shortfall_if_native_receipt_verifies'] = True
    elif change == 'native_queue': context['facts']['factory']['crafting_queue'] = 1
    elif change == 'native_player': context['facts']['factory']['player_bound'] = False
    elif change == 'native_protocol': context['facts']['factory']['craft_jobs_protocol'] = True
    elif change == 'local_target': row['local_target']['inventory_target'] = 99
    useful, _ = instructions(context, plan)
    assert 'currently carried ingredients can supply a useful partial target batch' not in useful
