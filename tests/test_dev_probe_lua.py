"""Real probe Lua over explicitly synthetic state; no native server involved."""
from importlib.resources import files
from copy import deepcopy

import pytest

from jev_factorio.controller import HierarchicalLoop
from test_factory import snapshot


def runtime():
    lua=pytest.importorskip('lupa').LuaRuntime()
    lua.execute('''
        local surface={index=1};local force={index=1}
        character={valid=true,unit_number=7,surface=surface,force=force}
        player={index=1,connected=true,character=character}
        game={tick=10,speed=1,tick_paused=false,get_player=function(i) return i==1 and player or nil end}
        script={active_mods={base="2.0.77"}}
        storage={jev_factorio_session=true}
        helpers={table_to_json=function(value) return value end}
        rcon={print=function(value) result=value end}
    ''')
    return lua


def test_missing_ephemeral_runtime_is_reported_not_created():
    lua=runtime();lua.execute(files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text())
    lua.execute('''assert(jev_fle_runtime==nil and result.runtime_present==false and result.bound==false)
        assert(game.speed==1 and game.tick==10 and player.character==character)''')


def test_live_identity_probe_does_not_call_campaign_or_fair_callbacks():
    lua=runtime();lua.execute('''
        jev_fle_runtime={jev_session_id="test",agent_characters={[1]=character},
            fair={actor=function() error("must not call") end},
            campaign={observe=function() error("must not call") end,entities={}}}
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text())
    lua.execute('''assert(result.runtime_present and result.bound and result.actor_unit==7)
        assert(result.session_id=="test" and result.truncated==false)
        assert(game.speed==1 and game.tick==10 and player.character==character)''')


def test_oversized_owned_entity_set_is_marked_incomplete():
    lua=runtime();lua.execute('''
        jev_fle_runtime={campaign={entities={}}}
        for n=1,513 do jev_fle_runtime.campaign.entities["e"..n]={valid=false} end
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text())
    assert lua.eval('result.truncated')


def test_acceptance_telemetry_does_not_mutate_snapshot_or_enter_model_prompt():
    state=snapshot();state.factory['acceptance_runtime']={'speed':1,'mods':{'base':'test'}}
    state.factory['consumed']={'iron-plate':123}
    original=deepcopy(state)
    loop=HierarchicalLoop.__new__(HierarchicalLoop)
    facts=loop._model_facts(state)
    assert 'acceptance_runtime' not in facts['factory'] and 'consumed' not in facts['factory']
    assert state==original


def test_record_configuration_is_explicit_for_disabled_capabilities():
    from test_factory import FactorySimulation
    backend=FactorySimulation()
    loop=HierarchicalLoop(backend,policy='deterministic',target='iron_smelting',factory_scheduling='ready-work',tick_seconds=0)
    record=loop.step()
    config=record['acceptance_configuration']
    assert config['factory_scheduling']=='ready-work'
    assert all(config[k] is False for k in config if k!='factory_scheduling')
