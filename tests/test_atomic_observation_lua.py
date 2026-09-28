"""Actual v2 Lua executed with synthetic LuaObjects; not Factorio qualification."""
import json
from importlib.resources import files
from pathlib import Path

import pytest
from test_atomic_observation import setup

CODE = files('jev_factorio').joinpath('lua/observation_v2.lua').read_text()
EXPANDED_CODE = files('jev_factorio').joinpath('lua/observation_v2_anchor_v3.lua').read_text()
WATER_ORIGIN_CODE = files('jev_factorio').joinpath('lua/observation_v2_water_origin_v4.lua').read_text()


def converted(value):
    """Convert synthetic Lua tables into the transport's JSON shape."""
    if hasattr(value, 'items'):
        entries = dict(value.items())
        if entries and set(entries) == set(range(1, len(entries) + 1)):
            return [converted(entries[i]) for i in range(1, len(entries) + 1)]
        return {key: converted(item) for key, item in entries.items()}
    return value


def runtime(code=CODE):
    lua = pytest.importorskip('lupa.lua52').LuaRuntime()
    lua.execute(Path('tests/fixtures/atomic_observation_runtime.lua').read_text())
    lua.execute(code)
    return lua


def test_native_one_fresh_campaign_inventory_and_control_each_call():
    lua = runtime()
    lua.execute('''storage.campaign.observation_snapshot_v2(0)
        actor_items[1].count=4;game.tick=11;storage.campaign.observation_snapshot_v2(0)
        assert(campaign_count==2 and control_count==2 and lease==191)
        assert(discovery_count==5 and captured.cache.hits==5)
        assert(captured.inventory.coal==4 and captured.factory.receipts.fresh.sequence==2)
        assert(captured.tick==11 and captured.controls.tick==11)
        assert(query_count==6) -- bootstrap + oil + water per call, no FLE helpers
        for _,q in ipairs(queries) do assert(q.limit==129 and q.radius<=1000) end
    ''')


@pytest.mark.parametrize('change', ['game.tick=1811', 'game.tick=1', 'player.position.x=16',
    'player.character.unit_number=18', 'surface.index=2', 'force.index=3',
    'storage.jev_session_id="new"'])
def test_native_discovery_invalidation(change):
    lua = runtime()
    lua.execute('storage.campaign.observation_snapshot_v2(0);'+change+';storage.campaign.observation_snapshot_v2(0);assert(discovery_count==10)')


@pytest.mark.parametrize('change', ['resources.coal.amount=0', 'resources.coal.valid=false',
    'resources.coal.minable=false', 'resources.coal.obscured=true', 'resources.coal.unit_number=999'])
def test_positive_cache_revalidates_native_target(change):
    lua = runtime()
    lua.execute('storage.campaign.observation_snapshot_v2(0);'+change+';storage.campaign.observation_snapshot_v2(0);assert(discovery_count==6)')


def test_native_negative_results_and_failed_mutation_epoch_not_cached():
    lua = runtime()
    lua.execute('''absent=true;storage.campaign.observation_snapshot_v2(0);storage.campaign.observation_snapshot_v2(0)
        assert(discovery_count==10);absent=false;storage.campaign.observation_snapshot_v2(0)
        storage.campaign.observation_snapshot_v2(1);assert(discovery_count==20)''')


def test_bootstrap_native_binding_and_chest_read():
    lua = runtime()
    lua.execute('''add_drill(60,0,0);selected=add_drill(51,10,0);add_chest(52,12,-.203125)
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.bootstrap.drill.unit_number==51 and captured.bootstrap.drill.fuel.coal==3)
        assert(captured.bootstrap.iron_ore_collected==7 and captured.bootstrap.output_connected)
        selected.unit_number=99
        assert(not pcall(storage.campaign.observation_snapshot_v2,0,51))''')


def test_bound_bootstrap_survives_actor_travel_via_exact_site_lookup():
    lua = runtime()
    lua.execute('''selected=add_drill(51,10,0);add_chest(52,12,0)
        storage.campaign.observation_snapshot_v2(0)
        player.position={x=2000,y=0}
        storage.campaign.observation_snapshot_v2(0,51,{x=10,y=0})
        assert(captured.bootstrap.drill.unit_number==51)
        assert(captured.bootstrap.output_connected and captured.bootstrap.iron_ore_collected==7)
        assert(not pcall(storage.campaign.observation_snapshot_v2,0,51,{x=11,y=0}))
        selected.unit_number=99
        assert(not pcall(storage.campaign.observation_snapshot_v2,0,51,{x=10,y=0}))''')


def test_bootstrap_overflow_fails_closed_not_truncated():
    lua = runtime()
    lua.execute('for i=1,129 do add_chest(i,0,0) end;assert(not pcall(storage.campaign.observation_snapshot_v2,0))')


@pytest.mark.parametrize('change', ['actor_items[1].count=-1','actor_items[1].count=1.5',
    'actor_items[1].quality="legendary"','actor_items[1].name=""'])
def test_native_bad_inventory_has_no_usable_envelope(change):
    lua=runtime()
    lua.execute(change+';assert(not pcall(storage.campaign.observation_snapshot_v2,0));assert(captured==nil)')


def test_anchor_reads_are_bounded_and_depleted_or_changed_anchors_disappear():
    lua=runtime()
    lua.execute('''oil={name="crude-oil",surface=surface,force=force,position={x=5,y=5},amount=10,valid=true}
        entities[1]=oil;tiles[1]={name="water",position={x=0,y=1},surface=surface,valid=true}
        storage.campaign.observation_snapshot_v2(0);assert(captured.anchors.water and captured.anchors["crude-oil"])
        oil.amount=0;tiles[1].name="landfill"
        storage.campaign.observation_snapshot_v2(0);assert(not next(captured.anchors))''')


def test_expanded_oil_anchor_finds_previously_generated_site_beyond_256():
    lua=runtime(EXPANDED_CODE)
    lua.execute('''player.position={x=62.27734375,y=-27.12890625}
        oil={name="crude-oil",surface=surface,force=force,
            position={x=15.5,y=383.5},amount=10,valid=true}
        entities[1]=oil
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors["crude-oil"].position.x==15.5)
        assert(captured.anchors["crude-oil"].position.y==383.5)
        assert(captured.bounds.anchor_radius==1024)
        assert(queries[2].radius==256 and queries[3].radius==512)
        assert(queries[2].limit==129 and queries[3].limit==129)
        assert(query_count==4) -- bootstrap, two oil queries, water
    ''')


def test_depleted_near_oil_does_not_hide_positive_512_tile_witness():
    lua=runtime(EXPANDED_CODE)
    lua.execute('''player.position={x=62.27734375,y=-27.12890625}
        entities[1]={name="crude-oil",surface=surface,force=force,
            position={x=62,y=-27},amount=0,valid=true}
        entities[2]={name="crude-oil",surface=surface,force=force,
            position={x=15.5,y=383.5},amount=10,valid=true}
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors["crude-oil"].position.y==383.5)
        assert(captured.anchor_diagnostics.oil.radius==512)
        assert(queries[2].radius==256 and queries[3].radius==512)
        assert(query_count==4)
    ''')


def test_expanded_oil_anchor_remains_bounded_and_depleted_is_not_a_witness():
    lua=runtime(EXPANDED_CODE)
    lua.execute('''oil={name="crude-oil",surface=surface,force=force,
            position={x=1003,y=4},amount=10,valid=true}
        entities[1]=oil
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors["crude-oil"].position.x==1003)
        assert(queries[2].radius==256 and queries[3].radius==512
            and queries[4].radius==1024 and query_count==5)
        oil.amount=0;storage.campaign.observation_snapshot_v2(0)
        assert(not captured.anchors["crude-oil"])
        assert(queries[#queries-1].radius==1024)
        oil.amount=10;oil.position.x=1030
        storage.campaign.observation_snapshot_v2(0)
        assert(not captured.anchors["crude-oil"])
    ''')


def test_expanded_oil_saturation_keeps_verified_bounded_witness():
    lua=runtime(EXPANDED_CODE)
    lua.execute('''for i=1,129 do
            entities[i]={name="crude-oil",surface=surface,force=force,
                position={x=4+i/10,y=4},amount=10,valid=true}
        end
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors["crude-oil"].name=="crude-oil")
        assert(captured.anchor_diagnostics.oil.radius==256)
        assert(captured.anchor_diagnostics.oil.saturated==true)
        assert(captured.anchor_diagnostics.selection=="bounded_witness_not_global_nearest")
        assert(queries[2].limit==129 and query_count==3)
    ''')


def test_expanded_water_anchor_is_bounded_tile_center_under_saturation():
    lua=runtime(EXPANDED_CODE)
    lua.execute('''for i=1,129 do
            tiles[i]={name="water",position={x=27,y=44+i-1},
                surface=surface,valid=true}
        end
        player.position={x=27,y=44}
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors.water.position.x==27.5
            and captured.anchors.water.position.y==44.5)
        assert(captured.bounds.water_radius==256)
        assert(queries[#queries].radius==256 and queries[#queries].limit==129)
    ''')


def test_water_origin_observer_uses_fle_tile_distance_and_exact_origin():
    lua = runtime(WATER_ORIGIN_CODE)
    lua.execute('''tiles[1]={name="water",position={x=0,y=0},surface=surface,valid=true}
        tiles[2]={name="water",position={x=1,y=0},surface=surface,valid=true}
        player.position={x=.8,y=.1}
        storage.campaign.observation_snapshot_v2(0)
        assert(captured.anchors.water.position.x==1
            and captured.anchors.water.position.y==0)
        assert(captured.anchor_diagnostics.water.radius==256)
        assert(queries[#queries].limit==129)
    ''')


def test_actual_lua_payload_crosses_python_decoder(monkeypatch):
    backend,native,payload,calls=setup(monkeypatch)
    lua=runtime(EXPANDED_CODE)
    lua.execute('add_drill(51,0,0);add_chest(52,2,0);storage.campaign.observation_snapshot_v2(0)')
    actual=converted(lua.globals().captured)
    # Factorio empty Lua maps/arrays are represented as [] in the transport.
    for parent,key in [(actual,'anchors'),(actual['factory'],'entities'),(actual['factory'],'researched')]:
        if parent[key]=={}: parent[key]=[]
    payload.clear();payload.update(actual)
    state=backend.observe()
    assert state.inventory=={'coal':8} and state.drill_fuel==3
    assert state.iron_ore_collected==7 and len(calls)==1
    assert set(state.nearby_resources)=={'wood','coal','iron-ore','copper-ore','stone'}


def test_reattach_invalidates_only_advisory_discovery_not_campaign_receipts():
    lua=runtime();lua.execute('storage.campaign.observation_snapshot_v2(0)')
    lua.execute(CODE)
    lua.execute('storage.campaign.observation_snapshot_v2(0);assert(discovery_count==10 and campaign_count==2)')
