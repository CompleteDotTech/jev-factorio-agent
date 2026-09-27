"""Same-command coal headroom: Lua API doubles, decoder, and real fuel planner.

These tests do not run Factorio or establish native inventory/travel performance.
"""
from copy import deepcopy

import pytest

from test_atomic_observation import setup
from test_atomic_observation_lua import converted, runtime
from test_grouped_fuel_service import due_scenario
from jev_factorio.planning.input_routes import InputRoutePlanner


def capacity(value=3, tick=10):
    return {
        'schema': 1, 'tick': tick, 'inventory': 'character_main',
        'quality': 'normal', 'method': 'get_insertable_count',
        'items': {'coal': value},
    }


@pytest.mark.parametrize('headroom', [0, 1, 3, 50, 4294967295])
@pytest.mark.parametrize('craft', [False, True])
def test_capacity_reaches_planning_without_an_extra_transport(monkeypatch, headroom, craft):
    backend, native, payload, calls = setup(monkeypatch, craft=craft)
    payload['inventory_capacity'] = capacity(headroom)
    state = backend.observe()
    assert state.factory['inventory_insertable'] == {'coal': headroom}
    assert state.factory['inventory_insertable_evidence'] == {
        **capacity(headroom), 'session_id': 'atomic-fixture',
        'actor_unit': 17, 'surface_index': 1, 'force_index': 2,
        'basis': 'native_insertable_count_estimate',
    }
    assert len(calls) == 1 and not backend.last_observation_profile['subcalls']
    assert state.inventory == {'coal': 8}  # Headroom is not credited stock.
    assert state.factory['receipts'] == {}


@pytest.mark.parametrize('bad', [None, True, -1, 1.5, '3', 4294967296,
                                 float('inf'), float('nan'), {}, []])
def test_capacity_count_must_be_a_uint32(monkeypatch, bad):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = capacity(bad)
    with pytest.raises(ValueError, match='capacity'):
        backend.observe()
    assert len(calls) == 1 and getattr(native, '_coherent_identity', None) is None
    assert not backend._resources and backend._drill is None


@pytest.mark.parametrize('field,bad', [
    ('schema', True), ('schema', 2), ('tick', True), ('tick', 9), ('tick', 11),
    ('inventory', 'character_trash'), ('quality', 'uncommon'),
    ('method', 'get_item_count'), ('items', []), ('items', {'iron-ore': 3}),
    ('items', {'coal': 3, 'stone': 2}), ('unexpected', 1),
])
def test_capacity_metadata_fails_before_native_view_is_published(monkeypatch, field, bad):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = capacity()
    payload['inventory_capacity'][field] = bad
    with pytest.raises(ValueError, match='capacity'):
        backend.observe()
    assert getattr(native, '_coherent_identity', None) is None
    assert len(calls) == 1


@pytest.mark.parametrize('bad', [None, True, 0, [], 'unknown'])
def test_only_explicit_false_or_absence_means_unknown(monkeypatch, bad):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = bad
    with pytest.raises(ValueError, match='capacity'):
        backend.observe()
    assert len(calls) == 1


@pytest.mark.parametrize('present', [False, True])
def test_legacy_or_unsupported_capacity_is_unknown_not_stale(monkeypatch, present):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = capacity(17)
    assert backend.observe().factory['inventory_insertable'] == {'coal': 17}
    if present:
        payload['inventory_capacity'] = False
    else:
        del payload['inventory_capacity']
    # A nested campaign/decorator field is not the actor inventory read.
    payload['factory']['inventory_insertable'] = {'coal': 999}
    payload['factory']['inventory_insertable_evidence'] = {'tick': 0}
    state = backend.observe()
    assert 'inventory_insertable' not in state.factory
    assert 'inventory_insertable_evidence' not in state.factory
    assert len(calls) == 2


def test_capacity_is_detached_and_refreshed_each_observation(monkeypatch):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = capacity(17)
    first = backend.observe()
    payload['inventory_capacity']['items']['coal'] = 1
    second = backend.observe()
    assert first.factory['inventory_insertable'] == {'coal': 17}
    assert second.factory['inventory_insertable'] == {'coal': 1}
    second.factory['inventory_insertable']['coal'] = 99
    assert second.factory['inventory_insertable_evidence']['items']['coal'] == 1
    assert len(calls) == 2


def test_bad_capacity_cannot_advance_prior_tick_or_discovery_cache(monkeypatch):
    backend, native, payload, calls = setup(monkeypatch)
    payload['inventory_capacity'] = capacity()
    payload['targets'] = {'coal': {'name': 'coal', 'surface_index': 1,
                                   'position': {'x': 7, 'y': 4}}}
    original = backend.observe()
    before = dict(backend._resources)
    payload['tick'] = payload['controls']['tick'] = payload['factory']['tick'] = 11
    payload['targets']['coal']['position']['x'] = 8
    # The stale capacity tick must not be accepted with newer actor facts.
    with pytest.raises(ValueError, match='capacity'):
        backend.observe()
    assert native._coherent_tick == 10 and backend._resources == before
    assert original.factory['inventory_insertable'] == {'coal': 3}
    assert len(calls) == 2


def with_native_capacity(lua, expression='3'):
    lua.execute('''capacity_calls=0
        local inv={valid=true, get_contents=function() return actor_items end}
        inv.get_insertable_count=function(item)
            capacity_calls=capacity_calls+1
            assert(item.name=="coal" and item.quality=="normal")
            return ''' + expression + '''
        end
        player.get_main_inventory=function() return inv end''')


@pytest.mark.parametrize('value', [0, 3, 4294967295])
def test_actual_lua_uses_normal_coal_main_inventory_once(value):
    lua = runtime()
    with_native_capacity(lua, str(value))
    lua.execute('storage.campaign.observation_snapshot_v2(0)')
    assert converted(lua.globals().captured.inventory_capacity) == capacity(value)
    lua.execute('''assert(capacity_calls==1 and campaign_count==1 and control_count==1)
        assert(captured.inventory.coal==8 and query_count==3)''')


def test_actual_lua_drops_nested_actor_capacity_from_a_wrapper():
    lua = runtime()
    with_native_capacity(lua)
    lua.execute('''local previous=storage.campaign.observe
        storage.campaign.observe=function()
            local value=previous()
            value.inventory_insertable={coal=999}
            value.inventory_insertable_evidence={tick=0}
            return value
        end
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.factory.inventory_insertable==nil)
        assert(captured.factory.inventory_insertable_evidence==nil)
        assert(captured.inventory_capacity.items.coal==3)''')


@pytest.mark.parametrize('expression', ['-1', '1.5', 'true', 'nil', '"3"',
                                       '4294967296', 'math.huge', '0/0'])
def test_actual_lua_rejects_bad_capacity_without_a_snapshot(expression):
    lua = runtime()
    with_native_capacity(lua, expression)
    lua.execute('''assert(not pcall(storage.campaign.observation_snapshot_v2,0))
        assert(captured==nil and capacity_calls==1)''')


@pytest.mark.parametrize('property_error', [False, True])
def test_actual_lua_unsupported_method_is_explicit_unknown(property_error):
    lua = runtime()
    if property_error:
        lua.execute('''local inv=setmetatable({valid=true,
            get_contents=function() return actor_items end},
            {__index=function() error("Unsupported member") end})
            player.get_main_inventory=function() return inv end''')
    lua.execute('storage.campaign.observation_snapshot_v2(0)')
    assert lua.globals().captured.inventory_capacity is False


def test_actual_lua_method_execution_error_is_not_unknown_fallback():
    lua = runtime()
    with_native_capacity(lua, 'error("native read failed")')
    lua.execute('''assert(not pcall(storage.campaign.observation_snapshot_v2,0))
        assert(captured==nil and capacity_calls==1)''')


def test_actual_lua_does_not_cache_headroom_or_mix_ticks():
    lua = runtime()
    with_native_capacity(lua, 'headroom')
    lua.execute('''headroom=9;storage.campaign.observation_snapshot_v2(0)
        assert(captured.inventory_capacity.items.coal==9)
        headroom=0;game.tick=11;storage.campaign.observation_snapshot_v2(0)
        assert(captured.inventory_capacity.items.coal==0 and captured.inventory_capacity.tick==11)
        assert(capacity_calls==2 and discovery_count==5)''')
    lua.execute('''captured=nil
        player.get_main_inventory=function() return {valid=true,
            get_contents=function() return actor_items end,
            get_insertable_count=function() game.tick=game.tick+1;return 3 end} end
        assert(not pcall(storage.campaign.observation_snapshot_v2,0));assert(captured==nil)''')


@pytest.mark.parametrize('headroom', [0, 1, 3, 50])
def test_actual_lua_to_decoder_to_fuel_acquisition(monkeypatch, headroom):
    backend, native, payload, calls = setup(monkeypatch)
    due, catalog = due_scenario()
    lua = runtime()
    with_native_capacity(lua, str(headroom))
    lua.execute(f'game.tick={due.tick};storage.jev_session_id="input-test";'
                'player.character.unit_number=1700;actor_items={};'
                'storage.campaign.observation_snapshot_v2(0)')
    actual = converted(lua.globals().captured)
    factory = actual['factory']
    # Synthetic factory topology comes from existing real-planner regression data;
    # actor stock/headroom and binding pass through the real Lua/Python boundary.
    factory.update({k: deepcopy(v) for k, v in due.factory.items()
                    if k not in {'tick', 'acceptance_runtime'}})
    factory['researched'] = list(due.researched)
    actual['bootstrap']['placed_entities'] = []
    payload.clear()
    payload.update(actual)
    state = backend.observe()
    planner = InputRoutePlanner(catalog, state, 'rocket_launch')
    if headroom == 0:
        with pytest.raises(ValueError, match='capacity'):
            planner._need('iron-plate', 10)
    else:
        plan = planner._need('iron-plate', 10)
        assert plan.steps[0].action == 'factory_gather'
        assert plan.steps[0].parameters['quantity'] == min(8, headroom)
        assert plan.materials['fuel_service']['inventory_insertable'] == headroom
    assert len(calls) == 1 and state.inventory == {}


@pytest.mark.parametrize('headroom', [0, 1, 3, -1, True, None, 3.5, float('nan'), 4294967296])
def test_fresh_headroom_blocks_an_older_oversized_step(headroom):
    state, catalog = due_scenario()
    step = InputRoutePlanner(catalog, state, 'rocket_launch')._need('iron-plate', 10).steps[0]
    assert step.parameters['quantity'] == 8
    state.factory['inventory_insertable'] = {'coal': headroom}
    assert not step.allowed(state)


@pytest.mark.parametrize('headroom', [8, 50, 4294967295])
def test_sufficient_fresh_headroom_does_not_block_ordinary_gather(headroom):
    state, catalog = due_scenario()
    step = InputRoutePlanner(catalog, state, 'rocket_launch')._need('iron-plate', 10).steps[0]
    state.factory['inventory_insertable'] = {'coal': headroom}
    assert step.allowed(state)


@pytest.mark.parametrize('headroom', [0, 1, 3])
def test_composed_controller_rechecks_capacity_before_dispatch(tmp_path, headroom):
    from test_input_route_integration import controller, RouteLoop
    from test_maintenance_progress import progress_scenario
    backend, catalog = progress_scenario(coal=0, science=0)
    backend.state.factory['entities']['out:chest']['output'].clear()
    backend.state.factory['entities']['input:inserter']['fuel']['coal'] = 1
    backend.state.factory['inventory_insertable'] = {'coal': 8}
    plan = InputRoutePlanner(catalog, backend.state, 'rocket_launch')._need('iron-plate', 10)
    assert plan.steps[0].parameters['quantity'] == 8
    loop = controller(backend, tmp_path, kind=RouteLoop)
    loop.memory.active_plan = plan.to_dict()
    loop.memory.failures['older-unrelated-work'] = 2
    observations = []
    original = backend.observe
    def observe():
        observations.append(True)
        if len(observations) == 2:
            backend.state.factory['inventory_insertable'] = {'coal': headroom}
        return original()
    backend.observe = observe
    record = loop.step()
    assert len(observations) == 2 and backend.calls == []
    assert record['action'] == 'observe' and 'precondition' in record['outcome'].lower()
    assert loop.memory.pending is None and loop.memory.active_plan is None
    assert loop.memory.failures['older-unrelated-work'] == 2
    assert backend.state.inventory['coal'] == 0


def test_capacity_binding_stays_in_evidence_not_model_facts(tmp_path):
    from test_input_route_integration import controller, RouteLoop
    from test_maintenance_progress import progress_scenario
    backend, _ = progress_scenario()
    backend.state.factory['inventory_insertable'] = {'coal': 3}
    backend.state.factory['inventory_insertable_evidence'] = {
        **capacity(3, backend.state.tick), 'session_id': backend.state.session_id,
        'actor_unit': 17, 'surface_index': 1, 'force_index': 2,
        'basis': 'native_insertable_count_estimate',
    }
    retained = deepcopy(backend.state.factory['inventory_insertable_evidence'])
    loop = controller(backend, tmp_path, kind=RouteLoop)
    model = loop._model_facts(backend.state)
    assert 'inventory_insertable_evidence' not in model['factory']
    assert model['factory']['inventory_insertable'] == {'coal': 3}
    assert backend.state.factory['inventory_insertable_evidence'] == retained
