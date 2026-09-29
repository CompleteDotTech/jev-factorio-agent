"""Source-contract tests; they do not qualify a Factorio installation."""
from importlib.resources import files

import pytest
from lupa.lua52 import LuaRuntime


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        storage={jev_session_id='cycle-session'}
        local surface={index=1};local force={index=1}
        local actor={valid=true,unit_number=101,surface=surface,force=force}
        player={index=1,character=actor,position={x=0,y=0},
            walking_state={walking=false},get_item_count=function() return actor_coal end}
        actor_coal=0
        local target_a={valid=true,unit_number=201,surface=surface,force=force,burner={}}
        local target_b={valid=true,unit_number=202,surface=surface,force=force,burner={}}
        storage.agent_characters={actor}
        storage.fair={actor=function() return player end,job=nil}
        storage.campaign={entities={a=target_a,b=target_b},receipts={},receipt_order={}}
        storage.coal_supply={revision=4,committed=false,targets={'a','b'},
            rows={a={},b={}}}
        storage.coal_manual_journal_v1={protocol=1,pending=nil,rows={}}
        game={tick=100,speed=1,tick_paused=false,connected_players={player}}
        script={on_nth_tick=function(slot,fn)
            assert(slot==1);installed_tick=fn
        end}
        storage.coal_manual_journal_v1.tick_handler=function(event)
            gather_ticks=(gather_ticks or 0)+1
        end
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_manual_cycle_v2.lua').read_text())
    return lua


def gathered(lua):
    lua.execute('''
        game.tick=104;actor_coal=3
        storage.coal_manual_journal_v1.rows.gather={
            status='complete',pending=false,fault=false,overflow=false,
            started_tick=101,finished_tick=103,coal_before=0,coal_after=3,
            walking_ticks=0,mining_ticks=2}
    ''')


def delivery(lua, receipt, role, unit, count):
    lua.eval('storage.coal_manual_cycle_v2.begin_delivery')("cycle", receipt, role)
    lua.globals().game.tick += 1
    lua.execute('''
        local c=storage.campaign
        local receipt,role,unit,count=delivery_receipt,delivery_role,delivery_unit,delivery_count
        c.receipts[receipt]={role=role,unit_number=unit,item='coal',quantity=count,
            extracting=false,tick=game.tick}
        c.receipt_order[#c.receipt_order+1]=receipt
        actor_coal=actor_coal-count
    ''')
    lua.eval('storage.coal_manual_cycle_v2.finish_delivery')("cycle", receipt)


def set_delivery(lua, receipt, role, unit, count):
    g = lua.globals()
    g.delivery_receipt, g.delivery_role = receipt, role
    g.delivery_unit, g.delivery_count = unit, count
    delivery(lua, receipt, role, unit, count)


def test_two_target_receipt_cycle_is_bounded_and_does_not_authorize_action():
    lua = runtime()
    cycle = lua.eval('storage.coal_manual_cycle_v2')
    cycle.begin('cycle', 'gather')
    gathered(lua)
    set_delivery(lua, 'first', 'a', 201, 1)
    set_delivery(lua, 'second', 'b', 202, 2)
    lua.globals().game.tick = 107
    result = cycle.finish('cycle', lua.table_from(['a', 'b']))
    assert result['delivered'] == 3
    assert cycle.rows['cycle']['status'] == 'complete'
    assert cycle.active is None
    assert lua.eval('storage.coal_supply.committed') is False


@pytest.mark.parametrize('change', [
    "storage.campaign.receipts.other={role='a',unit_number=201,item='coal',quantity=1,extracting=false,tick=106};table.insert(storage.campaign.receipt_order,'other')",
    "storage.campaign.receipts.first.unit_number=999",
    "actor_coal=1",
])
def test_foreign_transfer_replaced_target_or_stock_fails_closed(change):
    lua = runtime(); cycle = lua.eval('storage.coal_manual_cycle_v2')
    cycle.begin('cycle', 'gather'); gathered(lua)
    set_delivery(lua, 'first', 'a', 201, 1)
    set_delivery(lua, 'second', 'b', 202, 2)
    lua.execute(change)
    with pytest.raises(Exception):
        cycle.finish('cycle', lua.table_from(['a', 'b']))
    assert cycle.rows['cycle'] is None


def test_unreceipted_actor_coal_change_faults_pending_cycle():
    lua = runtime(); cycle = lua.eval('storage.coal_manual_cycle_v2')
    cycle.begin('cycle', 'gather'); gathered(lua)
    lua.eval('storage.coal_manual_cycle_v2.begin_delivery')('cycle', 'first', 'a')
    lua.execute('game.tick=105;actor_coal=4;installed_tick{tick=105}')
    assert cycle.active['fault'] == 'unreceipted_coal_change'
    with pytest.raises(Exception):
        cycle.finish_delivery('cycle', 'first')


def test_consecutive_walking_ticks_are_counted_and_gather_callback_preserved():
    lua = runtime(); cycle = lua.eval('storage.coal_manual_cycle_v2')
    cycle.begin('cycle', 'gather'); gathered(lua)
    cycle.begin_delivery('cycle', 'first', 'a')
    lua.execute('''
        storage.fair.job={kind='walk',status='walking',unit=101}
        player.walking_state.walking=true
        game.tick=105;player.position={x=1,y=0};installed_tick{tick=105}
        game.tick=106;player.position={x=2,y=0};installed_tick{tick=106}
    ''')
    assert cycle.active['pending_delivery']['walking_ticks'] == 2
    assert lua.globals().gather_ticks == 2


def test_target_set_must_equal_native_proposals():
    lua = runtime(); cycle = lua.eval('storage.coal_manual_cycle_v2')
    cycle.begin('cycle', 'gather'); gathered(lua)
    set_delivery(lua, 'first', 'a', 201, 1)
    set_delivery(lua, 'second', 'b', 202, 2)
    with pytest.raises(Exception):
        cycle.finish('cycle', lua.table_from(['b', 'a']))
