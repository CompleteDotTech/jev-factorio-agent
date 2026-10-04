from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_shared_parent_comparison import frontier
from jev_factorio.judgments import question_batch, select_plan


def test_qualified_pair_uses_parent_without_changing_candidate_targets():
    plans, rows, state = frontier()
    original = deepcopy(state)
    context, questions, offered = question_batch(state, plans, max_bytes=48000)
    objective = context['local_objective']
    assert objective['kind'] == 'qualified_shared_parent_branches'
    assert objective['primary_target'] == context['shared_parent_comparison']['parent_target']
    assert objective['primary_target']['item'] == 'automation-science-pack'
    assert objective['primary_target']['inventory_target'] == 20
    assert context['candidate_evidence'][plans[0].id]['local_target']['inventory_target'] == 25
    assert context['candidate_evidence'][plans[1].id]['local_target']['inventory_target'] == 20
    assert context['shared_parent_comparison']['kit_branch']['physical_route_bill']['transport-belt'] == 5
    assert context['shared_parent_comparison']['kit_branch']['belt_reserve_for_twenty_logistic_science'] == 20
    assert offered == plans
    assert state == original
    assert len(json.dumps({'state': context, 'questions': questions}, ensure_ascii=False).encode()) <= 48000
    objective['primary_target']['inventory_target'] = 999
    assert context['shared_parent_comparison']['parent_target']['inventory_target'] == 20


@pytest.mark.parametrize('mutation', ['missing', 'malformed', 'stale', 'parent', 'catalog', 'one_branch'])
def test_unqualified_pair_preserves_original_global_objective(mutation):
    plans, rows, state = frontier()
    if mutation == 'missing':
        rows[plans[0].id]['input_route_kit_parent_purpose'] = None
    elif mutation == 'malformed':
        rows[plans[0].id]['input_route_kit_parent_purpose']['parent_local_objective']['inventory_target'] = True
    elif mutation == 'stale':
        state['facts']['tick'] += 1
    elif mutation == 'parent':
        rows[plans[1].id]['local_target']['inventory_target'] += 1
    elif mutation == 'catalog':
        state['facts']['factory']['recipe_dependency_catalog']['recipes'].pop('logistic-science-pack')
    elif mutation == 'one_branch':
        plans = plans[:1]
    original = deepcopy(state['local_objective'])
    context, _, _ = question_batch(state, plans, max_bytes=48000)
    assert 'shared_parent_comparison' not in context
    assert context['local_objective'] == original


def test_recorded101_refusal_remains_below_unchanged_floor():
    plans, _, state = frontier()
    answers = json.loads((Path(__file__).parent / 'fixtures/native101_recorded_numeric_answers.json').read_text())
    class Recorded:
        def evaluate(self, *args, **kwargs):
            return deepcopy(answers)
    decision = select_plan(Recorded(), state, plans)
    assert answers['candidate']['confidence'] == 0.24
    assert decision.plan_id is None
    assert decision.reason == 'low choice confidence'
