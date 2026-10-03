"""Offline prospective ownership on captured067 facts; never native acceptance."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from jev_factorio.bootstrap_output import ROLE, binding
from jev_factorio.judgments import question_batch, _qualified_bootstrap_output_pickup
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.state import GameSnapshot


def prospective_frontier():
    """Simulate a future verified binding; retained native observations unchanged."""
    fixtures = Path(__file__).parent / 'fixtures'
    captured = json.loads((fixtures / 'native067-candidate-objective-contract.json').read_text())
    catalog = Catalog.from_dict(json.loads(
        (fixtures / 'native061-candidate-local-raw-demand.json').read_text())['catalog'])
    snapshot = GameSnapshot(**deepcopy(captured['snapshot']))
    tick, session = snapshot.tick, snapshot.session_id
    row = {'protocol': 1, 'tick': tick, 'session_id': session, 'actor_unit': 2543,
        'surface_index': 1, 'force_index': 1, 'role': ROLE,
        'origin': 'legacy_authorized_current_asset', 'binding_id': 'a' * 64,
        'authorization_sha256': 'a' * 64, 'bound_at_tick': tick,
        'ownership_effective_now': True, 'native_pending': False,
        'historical_paid_placement_proven': False, 'paid_drill_unit': False,
        'paid_chest_unit': False, 'drill_unit': 2544, 'chest_unit': 2545,
        'drill_position': {'x': 49, 'y': -81},
        'drop_position': {'x': 48.5, 'y': -82.296875},
        'chest_position': {'x': 48.5, 'y': -82.5}, 'output': {'iron-ore': 33},
        'capacity': {'schema': 1, 'tick': tick, 'session_id': session, 'actor_unit': 2543,
            'surface_index': 1, 'force_index': 1, 'quality': 'normal',
            'inventory': 'character_main', 'item': 'iron-ore', 'count': 100}}
    snapshot.factory['bootstrap_output'] = row
    snapshot.factory['drill_output_role'] = ROLE
    snapshot.factory['entities'][ROLE] = {'name': 'wooden-chest', 'unit_number': 2545,
        'position': deepcopy(row['chest_position']), 'output': {'iron-ore': 33}}
    snapshot._coherent_observation_verified = (session, tick)
    snapshot._atomic_inventory_verified = (session, tick)
    snapshot._bootstrap_output_ownership_witness = {
        key: deepcopy(row[key]) for key in ('session_id', 'actor_unit', 'surface_index',
            'force_index', 'drill_unit', 'chest_unit', 'drill_position', 'drop_position',
            'chest_position', 'origin', 'authorization_sha256', 'bound_at_tick')}
    snapshot._bootstrap_output_ownership_witness.update(
        schema='jev.bootstrap-output-ownership.v1', asset_sha256='b' * 64)
    assert binding(snapshot) is row
    before = deepcopy(snapshot.__dict__)
    plans = MiningOutpostPlanner(catalog, snapshot, 'rocket_launch').candidates()
    rows = candidate_evidence(snapshot, catalog, plans)
    assert snapshot.__dict__ == before
    state = deepcopy(captured['state'])
    state['facts'] = snapshot.for_jev()
    for key in ('acceptance_runtime', 'consumed', 'observation_snapshot_schema',
                'observation_query_bounds', 'inventory_insertable_evidence'):
        state['facts']['factory'].pop(key, None)
    receipts = state['facts']['factory'].pop('receipts', {})
    state['facts']['factory'].pop('connectors', None)
    state['facts']['factory']['native_transfer_receipt_count'] = len(receipts)
    state['candidate_evidence'] = rows
    state['deterministic_ranking'] = [plan.id for plan in plans]
    return captured, snapshot, catalog, plans, rows, state


def test_actual067_prospective_owned_stock_survives_full_request_cap():
    captured, snapshot, catalog, plans, rows, state = prospective_frontier()
    assert [plan.steps[0].action for plan in plans] == ['factory_extract'] * 2
    assert [plan.steps[0].parameters['quantity'] for plan in plans] == [13, 20]
    assert len({plan.id for plan in plans}) == 2
    assert all(plan.steps[0].allowed(snapshot) for plan in plans)
    before = deepcopy(state)
    packet, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert [p.id for p in offered] == [p.id for p in plans]
    assert packet['facts'] == state['facts'] and packet['history'] == captured['state']['history']
    assert packet['local_objective'] == captured['state']['local_objective']
    manual = plans[1]
    assert rows[manual.id]['local_target']['item'] == 'automation-science-pack'
    assert _qualified_bootstrap_output_pickup(manual, state['facts'], rows[manual.id])
    assert 'bootstrap_output_pickup_start_evidence' in questions[manual.id + '/useful_progress']['instructions']
    assert 'candidate_evidence[' in questions[manual.id + '/useful_progress']['instructions']
    assert 'candidate_plans[' in questions[manual.id + '/useful_progress']['instructions']
    assert 'this candidate\'s evidence row local_target' in questions[manual.id + '/useful_progress']['criteria']['useful']
    assert 'For qualified candidate-local raw demand' in packet['execution_contract']
    assert 'historical placement proof' in questions[manual.id + '/useful_progress']['instructions']
    assert 'bootstrap_output_pickup_start_evidence' in questions[manual.id + '/benefit']['instructions']
    assert 'gathering supplies' not in questions[manual.id + '/benefit']['instructions']
    assert state == before
    assert len(json.dumps({'state': packet, 'questions': questions}, ensure_ascii=False,
        allow_nan=False).encode()) <= 48000


@pytest.mark.parametrize('change', ['coherent', 'atomic', 'witness', 'authority', 'stock',
    'headroom', 'pending', 'catalog', 'path', 'quantity', 'local', 'bill', 'role'])
def test_producer_rejects_missing_or_forged_current_stock_proof(change):
    _, snapshot, catalog, plans, _, _ = prospective_frontier()
    plan = plans[1]
    if change == 'coherent': snapshot._coherent_observation_verified = None
    elif change == 'atomic': snapshot._atomic_inventory_verified = None
    elif change == 'witness': snapshot._bootstrap_output_ownership_witness = None
    elif change == 'authority': snapshot.factory['bootstrap_output']['authorization_sha256'] = 'c' * 64
    elif change == 'stock': snapshot.factory['entities'][ROLE]['output']['iron-ore'] = 19
    elif change == 'headroom': snapshot.factory['bootstrap_output']['capacity']['count'] = 19
    elif change == 'pending': snapshot.factory['bootstrap_output']['native_pending'] = True
    elif change == 'catalog': catalog = replace(catalog, version='2.0.76')
    elif change == 'path': plan.materials['bootstrap_output_pickup']['planner_item_path'][1] = 'transport-belt'
    elif change == 'quantity': plans[1] = replace(plan, steps=(replace(plan.steps[0], parameters={
        **plan.steps[0].parameters, 'quantity': 19}),))
    elif change == 'local': plan.materials['local_objective']['inventory_target'] += 1
    elif change == 'bill': plan.materials['shortages']['iron-ore'] += 1
    elif change == 'role': snapshot.factory['entities'][ROLE]['unit_number'] = 999
    rows = candidate_evidence(snapshot, catalog, plans)
    assert 'bootstrap_output_pickup_start_evidence' not in rows[plans[1].id]


@pytest.mark.parametrize('change', ['tick', 'schema', 'session', 'version', 'source', 'binding',
    'quantity', 'receipt', 'inventory', 'digest', 'capacity', 'pending', 'paid_history',
    'path', 'scope', 'unknown', 'reason', 'step_quantity', 'local', 'missing_stock',
    'origin_type', 'extra_proof', 'verification'])
def test_consumer_rejects_stale_or_forged_bootstrap_proof(change):
    _, _, _, plans, rows, state = prospective_frontier()
    plan = plans[1]
    proof = rows[plan.id]['bootstrap_output_pickup_start_evidence']
    if change == 'tick': proof['observed_tick'] -= 1
    elif change == 'schema': proof['schema'] = True
    elif change == 'session': proof['session_id'] = 'other'
    elif change == 'version': proof['catalog_version'] = '2.0.76'
    elif change == 'source': proof['source_unit'] = 999
    elif change == 'binding': proof['binding_id'] = 'c' * 64
    elif change == 'quantity': proof['planned_pickup_quantity'] = True
    elif change == 'receipt': proof['planned_native_receipt_id'] = 'other'
    elif change == 'inventory': proof['inventory_now'] = True
    elif change == 'digest': state['facts']['factory']['bootstrap_output']['chest_position']['x'] += 1
    elif change == 'capacity': state['facts']['factory']['bootstrap_output']['capacity']['count'] = 19
    elif change == 'pending': state['facts']['factory']['bootstrap_output']['native_pending'] = True
    elif change == 'paid_history': state['facts']['factory']['bootstrap_output']['historical_paid_placement_proven'] = True
    elif change == 'path': proof['planner_item_path'][1] = 'transport-belt'
    elif change == 'scope': rows[plan.id]['work_scope'] = 'lookahead'
    elif change == 'unknown': rows[plan.id]['unknowns'] = ['travel']
    elif change == 'reason': rows[plan.id]['reasons'] = ['investment']
    elif change == 'step_quantity': plan = replace(plan, steps=(replace(plan.steps[0], parameters={
        **plan.steps[0].parameters, 'quantity': 19}),))
    elif change == 'local': rows[plan.id]['local_target']['inventory_target'] = True
    elif change == 'missing_stock': state['facts']['factory']['bootstrap_output']['output'] = {}
    elif change == 'origin_type': state['facts']['factory']['bootstrap_output']['origin'] = []
    elif change == 'extra_proof': proof['unknown_authority'] = True
    elif change == 'verification': plan = replace(plan, steps=(replace(plan.steps[0], verification={'role': ROLE}),))
    assert not _qualified_bootstrap_output_pickup(plan, state['facts'], rows[plans[1].id])
    _, questions, offered = question_batch(state, [plan], max_bytes=100000)
    assert offered == [plan]
    assert 'bootstrap_output_pickup_start_evidence' not in questions[plan.id + '/useful_progress']['instructions']
    assert questions[plan.id + '/useful_progress']['criteria']['useful'] == (
        'Current evidence supports useful progress toward the supplied objective')


def test_unowned_native067_still_has_original_frontier_and_contract():
    from test_candidate_objective_contract import captured_frontier
    captured, plans, state = captured_frontier()
    assert [plan.steps[0].action for plan in plans] == ['factory_gather'] * 3
    assert all('bootstrap_output_pickup_start_evidence' not in row
        for row in state['candidate_evidence'].values())
    packet, _, offered = question_batch(state, plans, max_bytes=48000)
    assert [plan.id for plan in offered] == [plan.id for plan in plans]
    assert packet['facts'] == captured['state']['facts']


@pytest.mark.parametrize('change', ['legacy_binding', 'legacy_drill', 'legacy_chest', 'paid_binding'])
def test_consumer_rejects_internally_consistent_noncanonical_ownership(change):
    from jev_factorio.planning.decision_support import _bootstrap_output_ownership_digest
    _, _, _, plans, rows, state = prospective_frontier()
    plan = plans[1]
    owned = state['facts']['factory']['bootstrap_output']
    proof = rows[plan.id]['bootstrap_output_pickup_start_evidence']
    if change == 'legacy_binding': owned['binding_id'] = 'c' * 64
    elif change == 'legacy_drill': owned['paid_drill_unit'] = owned['drill_unit']
    elif change == 'legacy_chest': owned['paid_chest_unit'] = owned['chest_unit']
    else:
        owned.update(origin='native_paid_bootstrap_placement', historical_paid_placement_proven=True,
            authorization_sha256=False, paid_drill_unit=owned['drill_unit'],
            paid_chest_unit=owned['chest_unit'], binding_id='forged-paid-binding')
    proof['binding_id'] = owned['binding_id']
    proof['ownership_sha256'] = _bootstrap_output_ownership_digest(owned)
    assert proof['ownership_sha256'] is not None
    assert not _qualified_bootstrap_output_pickup(plan, state['facts'], rows[plan.id])


def test_consumer_accepts_canonical_native_paid_ownership():
    from jev_factorio.planning.decision_support import _bootstrap_output_ownership_digest
    _, _, _, plans, rows, state = prospective_frontier()
    plan = plans[1]
    owned = state['facts']['factory']['bootstrap_output']
    proof = rows[plan.id]['bootstrap_output_pickup_start_evidence']
    owned.update(origin='native_paid_bootstrap_placement', historical_paid_placement_proven=True,
        authorization_sha256=False, paid_drill_unit=owned['drill_unit'],
        paid_chest_unit=owned['chest_unit'],
        binding_id=f"paid:{owned['drill_unit']}:{owned['chest_unit']}")
    proof['binding_id'] = owned['binding_id']
    proof['ownership_sha256'] = _bootstrap_output_ownership_digest(owned)
    assert _qualified_bootstrap_output_pickup(plan, state['facts'], rows[plan.id])
