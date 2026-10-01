"""Conditional connector semantics tests; no surveyed route or live outcome claims."""
import pytest

from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.jev_client import MockJevClient
from jev_factorio.skills import Plan, Step


def batch():
    plan = Plan(id='native-steam-connection', goal='rocket_launch',
                description='Connect boiler to engine with carried pipes', steps=(Step(
                    action='factory_connect', effect='connection', costs={'pipe': 41},
                    parameters={'source': 'utility:boiler', 'target': 'utility:engine',
                                'kind': 'pipe', 'fluid': 'steam'}),))
    state = {'active_goal': 'rocket_launch', 'facts': {
        'world_kind': 'fle', 'tick': 100, 'session_id': 'same-session',
        'inventory': {'pipe': 41}, 'factory': {
            'tick': 100, 'entities': {
                'utility:boiler': {'unit_number': 2550, 'name': 'boiler'},
                'utility:engine': {'unit_number': 2551, 'name': 'steam-engine'}},
            'connector_ownership': {'protocol': 1, 'tick': 100,
                                    'session_id': 'same-session', 'routes': {}}}}}
    return state, plan


def test_rubric_includes_additive_connections_and_contract_keeps_preparation_conditional():
    state, plan = batch()
    context, questions, _ = question_batch(state, [plan])
    disruption = questions[plan.id + '/disruption']
    assert len(disruption['criteria']) == 3
    assert 'pipes, poles or belts' in disruption['criteria'][1]
    assert 'without removing any existing entity' in disruption['criteria'][1]
    assert 'Stops, removes, or rebuilds' in disruption['criteria'][2]
    assert 'separately from uncertainty about approach' in disruption['instructions']
    contract = context['execution_contract']
    assert 'reuses matching existing connectors' in contract
    assert 'places only missing pipes or poles from carried stock' in contract
    assert 'does not mine, remove, stop or rebuild existing factory entities' in contract
    assert 'not a surveyed placement count' in contract
    assert 'flow is not established' in contract
    assert context['facts'] == state['facts']


@pytest.mark.parametrize('change', ['mock', 'missing_world', 'missing_ownership',
                                  'protocol_bool', 'legacy_protocol', 'stale_tick',
                                  'wrong_session', 'missing_routes', 'missing_entity',
                                  'unit_bool', 'unit_alias', 'factory_tick'])
def test_missing_or_legacy_native_authority_does_not_get_native_execution_claim(change):
    state, plan = batch()
    facts = state['facts']; factory = facts['factory']; ownership = factory['connector_ownership']
    if change == 'mock': facts['world_kind'] = 'mock'
    elif change == 'missing_world': facts.pop('world_kind')
    elif change == 'missing_ownership': factory.pop('connector_ownership')
    elif change == 'protocol_bool': ownership['protocol'] = True
    elif change == 'legacy_protocol': ownership['protocol'] = 0
    elif change == 'stale_tick': ownership['tick'] = 99
    elif change == 'wrong_session': ownership['session_id'] = 'other'
    elif change == 'missing_routes': ownership.pop('routes')
    elif change == 'missing_entity': factory['entities'].pop('utility:engine')
    elif change == 'unit_bool': factory['entities']['utility:engine']['unit_number'] = True
    elif change == 'unit_alias': factory['entities']['utility:engine']['unit_number'] = 2550
    elif change == 'factory_tick': factory['tick'] = 99
    context, questions, _ = question_batch(state, [plan])
    assert 'current paid native connector protocol' not in context['execution_contract']
    assert 'pipes, poles or belts' in questions[plan.id+'/disruption']['criteria'][1]


def test_low_disruption_confidence_still_rejects_despite_additive_contract():
    state, plan = batch()
    class LowDisruption(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            answers[plan.id + '/disruption']['confidence'] = .37
            return answers
    decision = select_plan(LowDisruption(), state, [plan], confidence_floor=.45)
    assert decision.plan_id is None
    assert 'low_disruption_confidence' in decision.diagnostics['candidate_rejections'][plan.id]
