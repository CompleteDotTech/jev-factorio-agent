"""Prepaid kit evidence is bounded stock, not coal admission."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio import coal_supply
from jev_factorio.planning.coal_current_evidence import (
    decoded_gather_work, prepaid_kit, verified_coal_deliveries,
)
from jev_factorio.memory import CampaignMemory
from jev_factorio.telemetry import make_attempt, utc_now
from test_coal_admission_contract import v2
from test_coal_kit_funding import data
from test_coal_economic_binding import facts
from test_coal_economic_proof import example
from solid_routes_fixtures import fixture as solid_fixture


def ready():
    snapshot = v2()
    snapshot.inventory = {**coal_supply.remaining_kit(coal_supply.sources(snapshot), snapshot),
                          'coal': 20}
    snapshot.memory = CampaignMemory(snapshot.session_id, 'rocket_launch')
    snapshot.memory.solid_commitments = {}
    snapshot.memory.coal_commitments = {}
    snapshot.factory['connector_ownership'] = {'protocol': 1,
        'session_id': snapshot.session_id, 'tick': snapshot.tick,
        'active': None, 'routes': {}}
    return snapshot


def test_whole_current_unreserved_kit_has_zero_acquisition_cost_only():
    snapshot = ready()
    evidence = prepaid_kit(snapshot, data(), snapshot.memory)
    assert evidence['kit'] == {'electric-mining-drill': 2, 'wooden-chest': 2,
                               'inserter': 4, 'transport-belt': 2}
    assert evidence['acquisition_actor_ticks_forecast'] == 0
    assert evidence['mutation_authorized'] is False
    assert evidence['basis']['tick'] == snapshot.tick
    next_snapshot = deepcopy(snapshot)
    next_snapshot.tick += 1
    next_snapshot.factory['coal_supply']['tick'] += 1
    next_snapshot.factory['coal_supply']['admission']['tick'] += 1
    next_snapshot.factory['solid_routes']['tick'] += 1
    next_snapshot.factory['connector_ownership']['tick'] += 1
    assert prepaid_kit(next_snapshot, data(), next_snapshot.memory)['acquisition_evidence_sha256'] != evidence['acquisition_evidence_sha256']


@pytest.mark.parametrize('change', [
    lambda s: s.inventory.update({'electric-mining-drill': 1}),
    lambda s: s.factory['coal_supply'].update(committed=True),
    lambda s: s.factory['coal_supply']['admission'].update(qualified=True),
    lambda s: s.factory.update(crafting_queue=1),
])
def test_unpaid_current_kit_preconditions_fail_closed(change):
    snapshot = ready(); change(snapshot)
    with pytest.raises(ValueError):
        prepaid_kit(snapshot, data(), snapshot.memory)


def test_other_project_reservation_cannot_be_funded_again():
    snapshot = ready()
    snapshot.memory.reservations = {'other-plan': {'electric-mining-drill': 1}}
    with pytest.raises(ValueError, match='not fully carried'):
        prepaid_kit(snapshot, data(), snapshot.memory)


def test_background_job_blocks_zero_acquisition_claim():
    snapshot = ready()
    snapshot.memory.background_job = {'unreconciled': True}
    with pytest.raises(ValueError, match='idle checkpoint'):
        prepaid_kit(snapshot, data(), snapshot.memory)


def add_pending_route(snapshot):
    routes = solid_fixture().factory['solid_routes']['routes']
    key, row = next(iter(routes.items()))
    row = deepcopy(row)
    row.update(state='building', pending={'part': 'receive',
               'receipt': 'pending', 'phase': 'prepared'})
    snapshot.factory['solid_routes']['routes'][key] = row


@pytest.mark.parametrize('change', [
    lambda s: setattr(s.memory, 'background_attempt', {'unreconciled': True}),
    lambda s: setattr(s.memory, 'transfer_recovery', {'unreconciled': True}),
    lambda s: s.factory['connector_ownership'].update(active='a' * 64),
    add_pending_route,
])
def test_in_flight_owner_blocks_prepaid_kit_evidence(change):
    snapshot = ready(); change(snapshot)
    with pytest.raises(ValueError):
        prepaid_kit(snapshot, data(), snapshot.memory)


def test_missing_checkpoint_cannot_hide_reservations():
    snapshot = ready()
    with pytest.raises(ValueError, match='idle checkpoint'):
        prepaid_kit(snapshot, data(), None)


def delivered_fixture():
    raw = example(); native = facts(raw)
    snapshot = SimpleNamespace(session_id=native.epoch.session_id, tick=native.epoch.tick,
                               factory={'receipts': {}})
    memory = CampaignMemory(snapshot.session_id, 'rocket_launch')
    ids = []
    for index, source in enumerate(native.sources):
        receipt = f'coal-transfer-{index}'
        ids.append(receipt)
        tick = 9000 + index * 10
        snapshot.factory['receipts'][receipt] = {
            'role': source.target_role, 'item': 'coal', 'quantity': index + 2,
            'unit_number': source.target_unit, 'extracting': False, 'tick': tick}
        plan = {'id': f'manual-{index}', 'steps': [{'action': 'factory_insert',
                'parameters': {'receipt': receipt}}]}
        attempt = make_attempt(snapshot.session_id, 'rocket_launch', plan, 0,
                               {'started_tick': tick - 1},
                               process_id='a' * 32, unit_number=source.target_unit)
        attempt.update(outcome='verified', finished_tick=tick + 1,
                       finished_at_utc=utc_now(), latency_seconds=0)
        memory.attempt_outcomes.append(attempt)
    return native, snapshot, memory, ids


def test_exact_transfer_receipts_are_bound_but_not_a_complete_manual_cycle():
    native, snapshot, memory, ids = delivered_fixture()
    evidence = verified_coal_deliveries(native, snapshot, memory, ids)
    assert evidence['basis']['deliveries'][0]['target_unit'] == native.sources[0].target_unit
    assert len(evidence['receipts_sha256']) == 64
    assert evidence['cycle_complete'] is False and evidence['mutation_authorized'] is False


@pytest.mark.parametrize('change', [
    lambda n, s, m, ids: s.factory['receipts'][ids[0]].update(quantity=0),
    lambda n, s, m, ids: s.factory['receipts'][ids[0]].update(unit_number=999),
    lambda n, s, m, ids: s.factory['receipts'][ids[0]].update(extracting=True),
    lambda n, s, m, ids: m.attempt_outcomes[0].update(outcome='partial_transfer_reconciled'),
    lambda n, s, m, ids: m.attempt_outcomes[0].update(expected_unit_number=999),
    lambda n, s, m, ids: m.attempt_outcomes.pop(0),
    lambda n, s, m, ids: ids.reverse(),
    lambda n, s, m, ids: ids.__setitem__(0, []),
])
def test_manual_transfer_receipt_mismatch_fails_closed(change):
    native, snapshot, memory, ids = delivered_fixture()
    change(native, snapshot, memory, ids)
    with pytest.raises(ValueError):
        verified_coal_deliveries(native, snapshot, memory, ids)


def gathered_fixture():
    native, snapshot, memory, _ = delivered_fixture()
    receipt = 'coal-gather-native-v1'
    plan = {'id': 'manual-gather', 'steps': [{'action': 'factory_gather',
            'parameters': {'resource': 'coal', 'quantity': 5, 'receipt': receipt}}]}
    attempt = make_attempt(snapshot.session_id, 'rocket_launch', plan, 0,
                           {'started_tick': 8990}, process_id='b' * 32)
    attempt.update(outcome='verified', finished_tick=9020,
                   finished_at_utc=utc_now(), latency_seconds=0)
    memory.attempt_outcomes.append(attempt)
    journal = {'schema': 'jev.coal-manual-gather.v1', 'status': 'complete',
               'session_id': snapshot.session_id,
               'actor_index': native.epoch.actor_index, 'actor_unit': native.actor_unit,
               'surface_index': native.epoch.surface_index,
               'force_index': native.epoch.force_index, 'receipt': receipt,
               'resource': 'coal',
               'started_tick': 8992, 'finished_tick': 9018,
               'coal_before': 10, 'coal_after': 15,
               'walking_ticks': 8, 'mining_ticks': 9,
               'pending': False, 'overflow': False, 'fault': False}
    return native, snapshot, memory, journal, attempt['id']


def test_gather_decoder_reports_only_journaled_busy_work_without_admission():
    native, snapshot, memory, journal, attempt_id = gathered_fixture()
    result = decoded_gather_work(native, snapshot, memory, journal, attempt_id)
    assert result['gathered_coal'] == 5
    assert result['reported_actor_busy_ticks'] == 17
    assert result['native_producer_qualified'] is False
    assert result['cycle_complete'] is False and result['mutation_authorized'] is False


@pytest.mark.parametrize('field,value', [
    ('actor_index', 999), ('actor_unit', 999), ('surface_index', 999), ('force_index', 999),
    ('receipt', 'unrelated'),
    ('status', 'partial'), ('pending', True), ('overflow', True),
    ('fault', 'lost_tick'), ('resource', 'iron-ore'),
    ('coal_after', 10), ('walking_ticks', 20), ('mining_ticks', 27),
    ('started_tick', 9020), ('finished_tick', 8990),
])
def test_gather_decoder_rejects_changed_owner_partial_and_inflated_work(field, value):
    native, snapshot, memory, journal, attempt_id = gathered_fixture()
    journal[field] = value
    with pytest.raises(ValueError):
        decoded_gather_work(native, snapshot, memory, journal, attempt_id)


def test_gather_decoder_rejects_controller_elapsed_time_substitute():
    native, snapshot, memory, journal, attempt_id = gathered_fixture()
    journal['walking_ticks'] = journal['finished_tick'] - journal['started_tick']
    with pytest.raises(ValueError, match='invalid work'):
        decoded_gather_work(native, snapshot, memory, journal, attempt_id)


@pytest.mark.parametrize('change', [
    lambda m: m.attempt_outcomes[-1].update(outcome='wait_replanned'),
    lambda m: m.attempt_outcomes[-1].update(receipt=None),
    lambda m: m.attempt_outcomes[-1].update(action='factory_wait'),
    lambda m: m.attempt_outcomes.pop(),
])
def test_gather_decoder_requires_matching_verified_attempt(change):
    native, snapshot, memory, journal, attempt_id = gathered_fixture()
    change(memory)
    with pytest.raises(ValueError):
        decoded_gather_work(native, snapshot, memory, journal, attempt_id)


def test_gather_decoder_rejects_reused_receipt_in_checkpoint_history():
    native, snapshot, memory, journal, attempt_id = gathered_fixture()
    duplicate = deepcopy(memory.attempt_outcomes[-1])
    duplicate['id'] = 'c' * 32
    memory.attempt_outcomes.append(duplicate)
    with pytest.raises(ValueError, match='reused'):
        decoded_gather_work(native, snapshot, memory, journal, attempt_id)
