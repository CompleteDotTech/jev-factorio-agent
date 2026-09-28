"""Negotiated snapshot tests; fake native transport, not a running game."""
import copy
import json
import sys
from types import SimpleNamespace as NS

import pytest

from jev_factorio.backends.fle import FleBackend
from jev_factorio.backends.observed_factory import ObservedFactory
from jev_factorio.backends.craft_jobs import CraftJobFactory


def envelope():
    return {
        'schema': 2, 'tick': 10, 'session_id': 'atomic-fixture',
        'actor_unit': 17, 'surface_index': 1, 'force_index': 2,
        'position': {'x': 3, 'y': 4}, 'inventory': {'coal': 8},
        'controls': {'tick': 10, 'position': {'x': 3, 'y': 4}, 'status': 'idle',
                     'walking': False, 'mining': False, 'movement_started': False,
                     'path_requests': 0, 'gained': 0},
        'factory': {'tick': 10, 'entities': [], 'receipts': [], 'researched': [],
                    'rockets_launched': 0, 'rocket_baseline': 0,
                    'player_bound': True, 'player_connected': True,
                    'acceptance_runtime': {'schema': 1, 'session_id': 'atomic-fixture',
                        'actor_unit': 17, 'surface_index': 1, 'force_index': 2,
                        'speed': 1, 'tick_paused': False}},
        'bootstrap': {'placed_entities': [], 'drill': False, 'output_connected': False,
                      'iron_ore_collected': 0, 'query_limit': 129},
        'targets': [], 'anchors': [], 'cache': {'hits': 0, 'misses': 5},
        'bounds': {'anchor_radius': 256, 'anchor_limit': 129,
                   'bootstrap_radius': 1000, 'bootstrap_limit': 129,
                   'bootstrap_output_radius': .75, 'bootstrap_output_limit': 2},
    }


def setup(monkeypatch, craft=False):
    monkeypatch.setitem(sys.modules, 'fle.env', NS(Position=lambda **kw: NS(**kw), Prototype=NS(BurnerMiningDrill='drill', WoodenChest='chest')))
    payload = envelope()
    calls = []
    class Tools:
        def __getattr__(self, name):
            raise AssertionError(f'Unexpected FLE helper: {name}')
    class Client:
        def send_command(self, command):
            calls.append(command)
            return 'JEV_SNAPSHOT|' + json.dumps(payload)
    backend = FleBackend()
    backend.consolidated_observations = True
    backend._instance = NS(namespace=Tools(), rcon_client=Client())
    backend._fair = NS(call=lambda *args: (_ for _ in ()).throw(AssertionError('duplicate fair call')))
    native = ObservedFactory.__new__(ObservedFactory)
    native.backend = backend
    native.catalog = NS(version='2.0.77')
    native._discovery_epoch = 0
    native.coherent_observation_version = 2
    backend._factory = native
    if craft:
        wrapper = CraftJobFactory.__new__(CraftJobFactory)
        wrapper.native = native
        backend._factory = wrapper
        payload['factory']['craft_job_inventory'] = {'tick': 10, 'items': {'coal': 8}}
    return backend, native, payload, calls


@pytest.mark.parametrize('craft', [False, True])
def test_atomic_path_one_transport_no_fle_helpers(monkeypatch, craft):
    backend, native, payload, calls = setup(monkeypatch, craft)
    state = backend.observe()
    assert state.inventory == {'coal': 8}
    assert state.player_position == (3, 4) and state.tick == 10
    assert state.session_id == 'atomic-fixture' and state._native_controls['tick'] == 10
    assert len(calls) == 1 and 'observation_snapshot_v2' in calls[0]
    assert state._coherent_observation_verified == ('atomic-fixture', 10)
    assert not backend.last_observation_profile['subcalls']


@pytest.mark.parametrize('field,value', [('session_id','other'), ('actor_unit',18),
    ('surface_index',2), ('force_index',3), ('tick',9), ('inventory',{'coal':True})])
def test_atomic_rejects_identity_tick_or_inventory_changes(monkeypatch, field, value):
    backend, native, payload, calls = setup(monkeypatch)
    backend.observe()
    payload[field] = value
    with pytest.raises(ValueError):
        backend.observe()
    assert len(calls) == 2  # no fallback helper or second read after invalid data


@pytest.mark.parametrize('part', ['controls', 'factory'])
def test_mixed_ticks_rejected(monkeypatch, part):
    backend, native, payload, _ = setup(monkeypatch)
    payload[part]['tick'] = 11
    with pytest.raises(ValueError): backend.observe()


@pytest.mark.parametrize('where', ['position', 'controls'])
def test_position_mismatch_or_nonfinite_rejected(monkeypatch, where):
    backend, native, payload, _ = setup(monkeypatch)
    if where == 'position': payload['position']['x'] = float('inf')
    else: payload['controls']['position']['y'] = 5
    with pytest.raises(ValueError): backend.observe()


def test_crafting_wrapper_must_agree_with_coherent_inventory(monkeypatch):
    backend, native, payload, _ = setup(monkeypatch, craft=True)
    payload['factory']['craft_job_inventory']['items']['coal'] = 7
    with pytest.raises(ValueError, match='inventory'): backend.observe()


def test_empty_native_maps_are_valid_but_nonempty_arrays_are_not(monkeypatch):
    backend, native, payload, _ = setup(monkeypatch, craft=True)
    payload['inventory'] = []
    payload['factory']['craft_job_inventory']['items'] = []
    assert backend.observe().inventory == {}
    payload['inventory'] = ['coal']
    with pytest.raises(ValueError): backend.observe()


def test_bad_wrapper_cannot_return_placeholder_state(monkeypatch):
    backend, native, _, _ = setup(monkeypatch)
    native.observe = lambda snapshot: snapshot
    with pytest.raises(ValueError, match='Coherent'): backend.observe()


def test_bootstrap_without_fle_entity_conversion(monkeypatch):
    backend, native, payload, _ = setup(monkeypatch)
    payload['bootstrap'] = {
        'query_limit': 129, 'placed_entities': ['burner-mining-drill', 'wooden-chest'],
        'drill': {'name':'burner-mining-drill', 'unit_number':51,
            'position':{'x':0,'y':0}, 'drop_position':{'x':2,'y':-.296875},
            'status':'working', 'fuel':{'coal':3}},
        'output_connected':True, 'iron_ore_collected':7}
    payload['factory']['entities'] = {
        'bootstrap:output': {'name': 'wooden-chest',
                             'position': {'x': 2, 'y': -.5}}}
    result = backend.observe()
    assert result.drill_fuel == 3 and result.drill_status == 'working'
    assert result.iron_ore_collected == 7 and result.drill_output_connected
    assert result.factory['drill_output_role'] == 'bootstrap:output'
    assert backend._drill.unit_number == 51
    payload['bootstrap']['drill']['unit_number'] = 52
    with pytest.raises(ValueError, match='bootstrap'): backend.observe()


def test_resources_and_anchors_are_fresh_and_never_cached_client_side(monkeypatch):
    backend, native, payload, _ = setup(monkeypatch)
    payload['targets'] = {'iron-ore': {'name':'iron-ore','surface_index':1,
                                     'position':{'x':6,'y':8}}}
    payload['anchors'] = {'water': {'name':'water','surface_index':1,
                                   'position':{'x':3,'y':6}}}
    result = backend.observe()
    assert result.nearby_resources == {'iron-ore':5,'water':2}
    payload['targets'] = payload['anchors'] = []
    result = backend.observe()
    assert not result.nearby_resources and not backend._resources


@pytest.mark.parametrize('bad', [True, 0, 130])
def test_native_query_bounds_not_silently_relaxed(monkeypatch, bad):
    backend, native, payload, _ = setup(monkeypatch)
    payload['bounds']['bootstrap_limit'] = bad
    with pytest.raises(ValueError): backend.observe()


def test_unknown_or_oversized_bootstrap_does_not_replace_valid_state(monkeypatch):
    backend, native, payload, _ = setup(monkeypatch)
    backend.observe()
    payload['bootstrap']['placed_entities'] = ['wooden-chest'] * 129
    with pytest.raises(ValueError): backend.observe()


def test_no_invented_fallback_when_atomic_envelope_version_changes(monkeypatch):
    backend, native, payload, calls = setup(monkeypatch)
    payload['schema'] = 1
    with pytest.raises(ValueError): backend.observe()
    assert len(calls) == 1


def test_installation_requires_exact_native_readback(monkeypatch):
    from jev_factorio.backends.native_factory import NativeFactory
    def init(self, backend): self.backend=backend
    monkeypatch.setattr(NativeFactory, '__init__', init)
    commands=[]
    monkeypatch.setattr(ObservedFactory, 'command', lambda self, script: commands.append(script) or
                        ('JEV_ATOMIC_READY|2' if 'JEV_ATOMIC_READY|2' in script else ''))
    native=ObservedFactory(NS())
    assert native.coherent_observation_version==2 and len(commands)==2
    monkeypatch.setattr(ObservedFactory, 'command', lambda self, script: 'unsupported')
    with pytest.raises(RuntimeError, match='negotiation'): ObservedFactory(NS())


def test_bootstrap_fuel_uses_unit_binding_at_native_insert(monkeypatch):
    from jev_factorio.backends.fair_actions import FairActions
    fair=FairActions.__new__(FairActions)
    fair.approach=lambda *args: None
    calls=[]
    fair.call=lambda *args: calls.append(args) or {'quantity':3}
    entity=NS(name='burner-mining-drill',unit_number=51,position=NS(x=1,y=2))
    assert fair.insert_item(NS(value=['coal']),entity,3)==3
    assert calls==[('insert','burner-mining-drill',{'x':1.0,'y':2.0},'coal',3,51)]


@pytest.mark.parametrize('name',['water','deepwater'])
def test_native_water_anchor_names_are_explicitly_supported(monkeypatch,name):
    backend,native,payload,_=setup(monkeypatch)
    payload['anchors']={'water':{'name':name,'surface_index':1,'position':{'x':3,'y':6}}}
    assert backend.observe().nearby_resources['water']==2


def test_arbitrary_native_tile_cannot_claim_water(monkeypatch):
    backend,native,payload,_=setup(monkeypatch)
    payload['anchors']={'water':{'name':'grass-1','surface_index':1,'position':{'x':3,'y':6}}}
    with pytest.raises(ValueError,match='discovery identity'):backend.observe()
