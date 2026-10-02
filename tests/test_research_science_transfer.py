from copy import deepcopy
import json
from pathlib import Path

import pytest

from jev_factorio.state import GameSnapshot
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.decision_support import _research_science_transfer_start_evidence
from jev_factorio.judgments import _qualified_research_science_transfer, question_batch
from test_factory import catalog


def setup():
    capture = json.loads((Path(__file__).parent / 'fixtures/native055-lab-science-transfer.json').read_text())
    state = GameSnapshot(**deepcopy(capture['snapshot']))
    identity = (state.session_id, state.tick)
    state._coherent_observation_verified = identity
    state._atomic_inventory_verified = identity
    data = catalog()
    data.technologies['automation'] = capture['technology']
    planner = FactoryPlanner(data, state, 'rocket_launch')
    # The fixture already has the owned lab; isolate science transfer ordering.
    planner._machine = lambda *args: None
    plan = planner._research('automation')
    return data, state, plan, capture


def test_actual_empty_lab_with_ten_carried_packs_has_current_paid_purpose():
    data, state, plan, capture = setup()
    assert plan.id == 'factory:factory_insert:utility:lab'
    assert plan.steps[0].costs == {'automation-science-pack': 10}
    proof = _research_science_transfer_start_evidence(state, data, plan)
    assert proof and proof['technology'] == 'automation'
    assert proof['lab_input_now'] == {}
    row = {'research_science_transfer_start_evidence': proof}
    assert _qualified_research_science_transfer(plan, vars(state), row)
    compact = deepcopy(vars(state))
    receipts = compact['factory'].pop('receipts')
    compact['factory']['native_transfer_receipt_count'] = len(receipts)
    assert _qualified_research_science_transfer(plan, compact, row)
    _, questions, _ = question_batch({'facts': compact, 'history': {},
        'candidate_evidence': {plan.id: row}}, [plan], max_bytes=48000)
    for suffix in ('useful_progress', 'benefit', 'needs_observation'):
        assert 'research_science_transfer_start_evidence' in questions[plan.id + '/' + suffix]['instructions']
    # New factual context never rewrites the captured model rejection.
    assert capture['recorded_answers'][plan.id + '/useful_progress']['choice'] == 'unsupported'


@pytest.mark.parametrize('change', ['stale', 'coherence', 'atomic', 'catalog', 'inventory',
    'supplied', 'researched', 'other_research', 'locked', 'prerequisite', 'receipt', 'raw_receipts', 'unit'])
def test_producer_rejects_contrary_or_unverified_native_supply(change):
    data, state, plan, _ = setup()
    if change == 'stale': state.factory['tick'] -= 1
    elif change == 'coherence': state._coherent_observation_verified = None
    elif change == 'atomic': state._atomic_inventory_verified = None
    elif change == 'catalog': state.game_version = 'wrong'
    elif change == 'inventory': state.inventory['automation-science-pack'] = 9
    elif change == 'supplied': state.factory['entities']['utility:lab']['input']['automation-science-pack'] = 1
    elif change == 'researched': state.researched.append('automation')
    elif change == 'other_research': state.factory['research'] = 'logistics'
    elif change == 'locked': data.technologies['automation']['enabled'] = False
    elif change == 'prerequisite': data.technologies['automation']['prerequisites'] = ['unresearched']
    elif change == 'receipt': state.factory['receipts'][plan.steps[0].parameters['receipt']] = {}
    elif change == 'raw_receipts': state.factory.pop('receipts')
    elif change == 'unit': state.factory['entities']['utility:lab']['unit_number'] = True
    assert _research_science_transfer_start_evidence(state, data, plan) is None


@pytest.mark.parametrize('field,value', [('observed_tick', True), ('lab_unit', True),
    ('technology', 'logistics'), ('actor_science_now', 9), ('paid_quantity_to_transfer', 9),
    ('ingredients_per_unit', {}), ('research_count', True), ('native_catalog_version', 'wrong')])
def test_consumer_rejects_malformed_or_crosswired_proof(field, value):
    data, state, plan, _ = setup()
    proof = _research_science_transfer_start_evidence(state, data, plan)
    proof[field] = value
    assert not _qualified_research_science_transfer(plan, vars(state), {'research_science_transfer_start_evidence': proof})


@pytest.mark.parametrize('change', ['receipt_count', 'receipt', 'present', 'verified',
    'missing_query_and_map', 'fact_tick', 'wrong_unit', 'stock', 'research'])
def test_compact_consumer_requires_fresh_native_stock_and_receipt_absence(change):
    data, state, plan, _ = setup()
    proof = _research_science_transfer_start_evidence(state, data, plan)
    facts = deepcopy(vars(state))
    receipts = facts['factory'].pop('receipts')
    facts['factory']['native_transfer_receipt_count'] = len(receipts)
    if change == 'receipt_count': proof['native_receipt_query']['receipt_count'] += 1
    elif change == 'receipt': proof['native_receipt_query']['receipt'] = 'different'
    elif change == 'present': proof['native_receipt_query']['present'] = True
    elif change == 'verified': proof['native_receipt_query']['map_verified'] = False
    elif change == 'missing_query_and_map': proof.pop('native_receipt_query')
    elif change == 'fact_tick': facts['tick'] = True
    elif change == 'wrong_unit': facts['factory']['entities']['utility:lab']['unit_number'] += 1
    elif change == 'stock': facts['inventory']['automation-science-pack'] = 9
    elif change == 'research': facts['factory']['research'] = 'logistics'
    assert not _qualified_research_science_transfer(plan, facts, {'research_science_transfer_start_evidence': proof})


def test_boolean_annotation_tick_cannot_alias_integer_observation_identity():
    data, state, plan, _ = setup()
    state.tick = state.factory['tick'] = 1
    state._coherent_observation_verified = state._atomic_inventory_verified = (state.session_id, 1)
    plan.materials['research_science_transfer']['observed_tick'] = 1
    proof = _research_science_transfer_start_evidence(state, data, plan)
    assert proof
    plan.materials['research_science_transfer']['observed_tick'] = True
    assert _research_science_transfer_start_evidence(state, data, plan) is None
    assert not _qualified_research_science_transfer(plan, vars(state), {'research_science_transfer_start_evidence': proof})
