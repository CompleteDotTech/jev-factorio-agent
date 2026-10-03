"""Recorded native074 rejection remains rejection; no new model/native calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from jev_factorio.skills import Plan
from jev_factorio.judgments import question_batch, select_plan


def actual_frontier():
    captured = json.loads((Path(__file__).parent / 'fixtures/native074-bootstrap-pickup-prospective.json').read_text())
    state = deepcopy(captured['state'])
    owned = state['facts']['factory']['bootstrap_output']
    assert owned['authorization_sha256'] == '[REDACTED]'
    # Event logging redacts *_sha256 authority; native binding_id carries the same
    # nonsecret signed installer digest. Strict ownership digest validates restoration.
    owned['authorization_sha256'] = owned['binding_id']
    plans = []
    for value in state['candidate_plans'].values():
        row = deepcopy(value)
        for key in row.pop('shared_materials_keys', []):
            row['materials'][key] = deepcopy(state['shared_plan_materials'][key])
        plans.append(Plan.from_dict(row))
    return captured, state, plans


def test_actual074_prospective_pickup_has_current_proof_without_future_receipt():
    captured, state, plans = actual_frontier()
    before = deepcopy(state)
    packet, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert [p.steps[0].parameters['quantity'] for p in offered] == [13, 20]
    assert packet['facts'] == before['facts']
    assert packet['candidate_evidence'] == captured['state']['candidate_evidence']
    assert packet['local_objective'] == captured['state']['local_objective']
    for plan in offered:
        old = captured['questions'][plan.id+'/useful_progress']['instructions']
        assert 'verification are required; missing' in old
        new = questions[plan.id+'/useful_progress']['instructions']
        # Recorded pre-projection proof remains unchanged and cannot claim the new scoped contract.
        assert 'current_raw_demand' not in packet['candidate_evidence'][plan.id]['bootstrap_output_pickup_start_evidence']
        assert 'their absence alone is not contrary start evidence' not in new
        for suffix in ('/disruption','/needs_observation'):
            assert questions[plan.id+suffix] == captured['questions'][plan.id+suffix]
    assert state == before
    assert len(json.dumps({'state':packet,'questions':questions},ensure_ascii=False,allow_nan=False).encode()) <= 48000


@pytest.mark.parametrize('field,value', [('observed_tick',0),('source_unit',999),('ownership_sha256','0'*64),('planned_pickup_quantity',1)])
def test_unqualified_pickup_does_not_get_prospective_exception(field,value):
    captured, state, plans = actual_frontier()
    state['candidate_evidence'][plans[0].id]['bootstrap_output_pickup_start_evidence'][field]=value
    _, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert plans[0].id in [p.id for p in offered]
    assert 'their absence alone' not in questions[plans[0].id+'/useful_progress']['instructions']


def test_actual074_recorded_unsupported_votes_still_reject():
    captured,state,plans=actual_frontier()
    answers=deepcopy(captured['answers'])
    decision=select_plan(SimpleNamespace(evaluate=lambda context,questions:deepcopy(answers)),state,plans,max_bytes=48000)
    assert decision.plan_id is None
    assert decision.reason == 'Candidate evidence insufficient'
    assert decision.answers == answers == captured['answers']
