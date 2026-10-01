import pytest

from jev_factorio.jev_client import MockJevClient
from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.planning.decision_support import scheduling_context
from test_utility_power_prerequisite_evidence import _engine_bill_child


def power_pickup_batch():
    catalog, snapshot, plan, _ = _engine_bill_child('output_pickup')
    state = {'facts': snapshot.for_jev(),
             **scheduling_context(snapshot, catalog, [plan], plan.goal)}
    return state, plan


def test_independent_usefulness_and_benefit_share_qualified_power_pickup_facts():
    state, plan = power_pickup_batch()
    _, questions, offered = question_batch(state, [plan])
    assert offered == [plan]
    for suffix in ('/useful_progress', '/benefit'):
        text = questions[plan.id + suffix]['instructions']
        assert 'utility_power_prerequisite_start_evidence' in text
        assert 'contrary' in text
        assert 'receipt and fresh postcondition checks' in text
    assert 'other questions\' answers are unavailable' in questions[
        plan.id + '/useful_progress']['instructions']
    assert 'score level' not in questions[plan.id + '/useful_progress']['instructions']
    assert 'level 1' not in questions[plan.id + '/useful_progress']['instructions']


@pytest.mark.parametrize('change', ['stale', 'wrong_receipt', 'lookahead', 'missing'])
def test_unqualified_power_pickup_does_not_get_eligibility_guidance(change):
    state, plan = power_pickup_batch()
    row = state['candidate_evidence'][plan.id]
    if change == 'stale':
        row['output_pickup_start_evidence']['observed_tick'] -= 1
        row['utility_power_prerequisite_start_evidence']['observed_tick'] -= 1
    elif change == 'wrong_receipt':
        row['output_pickup_start_evidence']['planned_native_receipt_id'] = 'other'
    elif change == 'lookahead':
        row['work_scope'] = 'lookahead'
    else:
        row['output_pickup_start_evidence'] = None
        row['utility_power_prerequisite_start_evidence'] = None
    _, questions, _ = question_batch(state, [plan])
    for suffix in ('/useful_progress', '/benefit'):
        text = questions[plan.id + suffix]['instructions']
        assert 'utility_power_prerequisite_start_evidence' not in text


def test_qualified_guidance_does_not_override_contrary_independent_answer():
    state, plan = power_pickup_batch()

    class Contrary(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            answers[plan.id + '/useful_progress'] = {
                'type': 'choice', 'choice': 'unsupported', 'confidence': .9,
                'probabilities': {'useful': .2, 'unsupported': .8},
            }
            return answers

    decision = select_plan(Contrary(), state, [plan])
    assert decision.plan_id is None
    assert 'no_demonstrated_progress' in decision.diagnostics['candidate_rejections'][plan.id]
