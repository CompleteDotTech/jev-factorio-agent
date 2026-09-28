"""Source-level Lua contract for an explicitly installed, diagnostic journal."""
from importlib.resources import files

import pytest


def runtime():
    lua = pytest.importorskip('lupa.lua52').LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        game = {tick=100}
        script = {on_nth_tick=function(interval, callback)
            assert(interval==1); journal_callback=callback
        end}
        local actor={valid=true,unit_number=42,surface={index=1},force={index=1}}
        player={index=1,character=actor,position={x=0,y=0},walking_state={walking=false},
            mining_state={mining=false},selected=nil,coal=0,
            get_item_count=function(item) assert(item=="coal"); return player.coal end}
        storage={jev_session_id="session",agent_characters={[1]=actor},
            fair={actor=function() return player end}}
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_text())
    return lua


def test_actual_busy_controls_are_counted_without_idle_or_path_waits():
    lua = runtime()
    lua.execute('''
        local j=storage.coal_manual_journal_v1
        j.begin("receipt")
        game.tick=101; storage.fair.job={kind="walk",status="path_pending",unit=42}
        journal_callback({tick=101})
        game.tick=102; storage.fair.job.status="walking"
        player.walking_state.walking=true; player.position={x=1,y=0}
        journal_callback({tick=102})
        game.tick=103; player.walking_state.walking=false
        local entity={valid=true}; player.selected=entity
        storage.fair.job={kind="mine",status="mining",unit=42,item="coal",entity=entity}
        player.mining_state.mining=true; journal_callback({tick=103})
        game.tick=104; player.coal=3; storage.fair.job.status="completed"
        result=j.finish("receipt")
    ''')
    result = lua.globals().result
    assert result.status == 'complete'
    assert result.walking_ticks == 1 and result.mining_ticks == 1
    assert result.coal_after - result.coal_before == 3
    assert result.pending is False and result.fault is False


@pytest.mark.parametrize('mutate', [
    'storage.fair.job={kind="mine",status="mining",unit=42,item="iron-ore"}',
    'player.character={valid=true,unit_number=99,surface={index=1},force={index=1}}',
])
def test_foreign_work_or_changed_actor_poison_pending_receipt(mutate):
    lua = runtime()
    lua.execute('storage.coal_manual_journal_v1.begin("receipt")')
    lua.execute('game.tick=101; ' + mutate + '; journal_callback({tick=101})')
    lua.execute('game.tick=102; player.coal=3; result=storage.coal_manual_journal_v1.finish("receipt")')
    result = lua.globals().result
    assert result.status == 'failed' and result.fault is not None


def test_duplicate_or_overlapping_receipt_cannot_start():
    lua = runtime()
    lua.execute('storage.coal_manual_journal_v1.begin("receipt")')
    with pytest.raises(Exception, match='pending or already used'):
        lua.execute('storage.coal_manual_journal_v1.begin("receipt")')


def test_duplicate_tick_callback_fails_closed_instead_of_inflating_work():
    lua = runtime()
    lua.execute('''
        storage.coal_manual_journal_v1.begin("receipt")
        game.tick=101; journal_callback({tick=101}); journal_callback({tick=101})
        game.tick=102; player.coal=3
        result=storage.coal_manual_journal_v1.finish("receipt")
    ''')
    assert lua.globals().result.status == 'failed'
    assert lua.globals().result.fault == 'tick_window'
