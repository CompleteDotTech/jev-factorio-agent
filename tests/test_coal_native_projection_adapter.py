"""The native economics read path never becomes a payment credential."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from jev_factorio.backends.coal_supply import CoalSupplyFactory
from jev_factorio.coal_economic_observation import NativeEconomicsUnavailable
from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.connection_identity import connection_key


def connector_binding(session):
    params = {'source': 'utility:boiler', 'target': 'utility:engine',
              'kind': 'pipe', 'fluid': 'steam'}
    receipt = connection_key(params)
    return {'protocol': 1, 'session_id': session, 'routes': {receipt: {
        'id': receipt, **params, 'source_unit': 11, 'target_unit': 12,
        'actor_unit': 321, 'surface_index': 2, 'force_index': 3,
        'session_id': session, 'state': 'complete', 'paid': 1,
        'external': 0, 'owned': True, 'pending': None,
        'cells': [{'index': 1, 'position': {'x': .5, 'y': 1.5},
                   'unit_number': 22, 'paid': True, 'external': False}]}}}
def fixture(monkeypatch):
    raw = {'epoch': {'session_id': 'synthetic-projection', 'tick': 10000,
                     'actor_index': 1, 'actor_unit': 321, 'surface_index': 2,
                     'force_index': 3}}
    bundle = {'recipe:iron-plate': {'target': 'recipe:iron-plate'},
              'utility:boiler': {'target': 'utility:boiler'}}
    response = json.dumps(raw, separators=(',', ':'))
    calls = []

    def command(source):
        calls.append(source)
        return response

    adapter = CoalSupplyFactory.__new__(CoalSupplyFactory)
    adapter.coal_economic_admission = True
    adapter.targets = sorted(bundle)
    adapter.native = SimpleNamespace(command=command)
    snapshot = SimpleNamespace(session_id=raw['epoch']['session_id'], tick=raw['epoch']['tick'],
        factory={'acceptance_runtime': {'session_id': raw['epoch']['session_id'],
                                        'actor_unit': raw['epoch']['actor_unit']},
                 'coal_supply': {'protocol': 2, 'admission': {'qualified': False},
                                 'committed': False, 'actor_index': raw['epoch']['actor_index'],
                                 'surface_index': raw['epoch']['surface_index'],
                                 'force_index': raw['epoch']['force_index']}})
    binding = connector_binding(snapshot.session_id)
    route = next(iter(binding['routes'].values()))
    snapshot.factory['connector_ownership'] = {'protocol': 1, 'session_id': snapshot.session_id,
        'tick': snapshot.tick, 'active': None, 'routes': {route['id']: {
            **{key: value for key, value in route.items() if key != 'cells'},
            'cell_count': len(route['cells'])}}}
    snapshot.memory = CampaignMemory(snapshot.session_id, 'rocket_launch', connector_ownership=binding)
    monkeypatch.setattr('jev_factorio.coal_supply.sources', lambda _: bundle)
    monkeypatch.setattr('jev_factorio.coal_supply.commitment', lambda row: row)
    received_connectors = {}
    def decode_native_graph(value, *, expected_epoch, expected_bundle, unit_qualification,
                            expected_connectors, expected_routes):
        if value['epoch'] != expected_epoch or expected_bundle != bundle:
            raise NativeEconomicsUnavailable('native_epoch_or_bundle_mismatch')
        received_connectors.update(expected_connectors)
        assert set(expected_routes) == set(snapshot.memory.connector_ownership['routes'])
        assert unit_qualification['base_version'] == '2.0.77'
        return SimpleNamespace(epoch=SimpleNamespace(tick=value['epoch']['tick']),
                               actor_unit=value['epoch']['actor_unit'],
                               mutation_authorized=False, native_payback_proven=False)
    monkeypatch.setattr('jev_factorio.coal_economic_observation.decode', decode_native_graph)
    return adapter, snapshot, raw, calls, received_connectors


def test_fresh_native_query_decodes_bound_unpaid_graph(monkeypatch):
    adapter, snapshot, raw, calls, received = fixture(monkeypatch)
    result = adapter.economic_projection(snapshot, snapshot.memory)
    assert len(calls) == 1 and calls[0].startswith('-- Fixed read-only native economics projection')
    assert result['native'].epoch.tick == snapshot.tick
    assert result['native'].actor_unit == raw['epoch']['actor_unit']
    assert result['native'].mutation_authorized is False
    assert result['native'].native_payback_proven is False
    assert list(received.values()) == [{'unit': 22, 'name': 'pipe',
                                       'position': {'x': .5, 'y': 1.5}}]
    assert len(result['query_sha256']) == len(result['response_sha256']) == 64


@pytest.mark.parametrize('change', [
    lambda adapter, snapshot: setattr(adapter, 'coal_economic_admission', False),
    lambda adapter, snapshot: snapshot.factory['coal_supply'].update(committed=True),
    lambda adapter, snapshot: snapshot.factory['acceptance_runtime'].update(actor_unit=0),
    lambda adapter, snapshot: snapshot.factory['acceptance_runtime'].update(session_id='other'),
    lambda adapter, snapshot: setattr(snapshot, 'tick', snapshot.tick + 1),
])
def test_changed_treatment_owner_or_epoch_refuses_projection(monkeypatch, change):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    change(adapter, snapshot)
    with pytest.raises((ValueError, NativeEconomicsUnavailable)):
        adapter.economic_projection(snapshot, snapshot.memory)
    if snapshot.tick == 10000:
        assert not calls


def test_resume_queries_current_epoch_again_without_reusing_prior_token(monkeypatch):
    adapter, snapshot, raw, calls, _ = fixture(monkeypatch)
    first = adapter.economic_projection(snapshot, snapshot.memory)
    assert first['native'].epoch.tick == 10000
    next_raw = deepcopy(raw)
    next_raw['epoch']['tick'] += 1
    adapter.native = SimpleNamespace(command=lambda source: (calls.append(source), json.dumps(next_raw))[1])
    snapshot.tick += 1
    snapshot.factory['connector_ownership']['tick'] = snapshot.tick
    next_projection = adapter.economic_projection(snapshot, snapshot.memory)
    assert next_projection['native'].epoch.tick == 10001
    assert next_projection['response_sha256'] != first['response_sha256']
    assert len(calls) == 2


def test_absent_initial_proposal_never_queries_or_admits(monkeypatch):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    monkeypatch.setattr('jev_factorio.coal_supply.sources', lambda _: {})
    with pytest.raises(ValueError, match='current unpaid owner'):
        adapter.economic_projection(snapshot, snapshot.memory)
    assert calls == []


def test_unresolved_connector_route_never_queries(monkeypatch):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    binding = snapshot.memory.connector_ownership
    route = next(iter(binding['routes'].values()))
    route.update(state='building', owned=False)
    with pytest.raises(ValueError, match='unresolved connector'):
        adapter.economic_projection(snapshot, snapshot.memory)
    assert calls == []


def test_replayed_connector_binding_from_other_actor_never_queries(monkeypatch):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    binding = snapshot.memory.connector_ownership
    next(iter(binding['routes'].values()))['actor_unit'] += 1
    with pytest.raises(ValueError, match='connector epoch'):
        adapter.economic_projection(snapshot, snapshot.memory)
    assert calls == []


def test_more_than_128_paid_checkpoint_cells_never_queries(monkeypatch):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    binding = snapshot.memory.connector_ownership
    route = next(iter(binding['routes'].values()))
    route['cells'] = [{'index': i, 'position': {'x': i + .5, 'y': 1.5},
                       'unit_number': 9000 + i, 'paid': True, 'external': False}
                      for i in range(1, 130)]
    route['paid'] = len(route['cells'])
    summary = next(iter(snapshot.factory['connector_ownership']['routes'].values()))
    summary.update(paid=route['paid'], cell_count=len(route['cells']))
    with pytest.raises(ValueError, match='exceeds bound'):
        adapter.economic_projection(snapshot, snapshot.memory)
    assert calls == []


@pytest.mark.parametrize('change', [
    lambda snapshot: snapshot.factory['connector_ownership'].update(tick=snapshot.tick - 1),
    lambda snapshot: snapshot.factory['connector_ownership'].update(active=next(iter(snapshot.memory.connector_ownership['routes']))),
    lambda snapshot: snapshot.factory['connector_ownership']['routes'].clear(),
    lambda snapshot: next(iter(snapshot.factory['connector_ownership']['routes'].values())).update(source='pole:2000'),
    lambda snapshot: next(iter(snapshot.factory['connector_ownership']['routes'].values())).update(source_unit=99),
    lambda snapshot: next(iter(snapshot.factory['connector_ownership']['routes'].values())).update(paid=0),
])
def test_snapshot_connector_summary_must_match_reconciled_checkpoint(monkeypatch, change):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    change(snapshot)
    with pytest.raises(ValueError, match='connector'):
        adapter.economic_projection(snapshot, snapshot.memory)
    assert not calls


def test_arbitrary_connector_dict_is_not_checkpoint_authority(monkeypatch):
    adapter, snapshot, _, calls, _ = fixture(monkeypatch)
    with pytest.raises(ValueError, match='session checkpoint'):
        adapter.economic_projection(snapshot, snapshot.memory.connector_ownership)
    assert not calls
