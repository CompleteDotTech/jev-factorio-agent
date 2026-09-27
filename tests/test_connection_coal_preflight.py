"""Real connection-probe/coal-reservation/fair-placement Lua on an API double.

No engine physics or walking is simulated. Every actual placement still runs
the production fair.place cursor-payment code and the production coal guard.
"""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.errors import ConnectionPreflightRejected
from jev_factorio.backends.fair_actions import FairActions
from coal_supply_fixtures import plain
from test_coal_supply_lua import ROOT, runtime


def adapter(monkeypatch, name, *, mode='committed', detour=False):
    env = ModuleType('fle.env')
    env.Position = SimpleNamespace
    env.Direction = SimpleNamespace(UP=SimpleNamespace(value=0))
    monkeypatch.setitem(sys.modules, 'fle', ModuleType('fle'))
    monkeypatch.setitem(sys.modules, 'fle.env', env)
    lua = runtime()
    if mode == 'committed':
        lua.execute('coal_build("alpha","chest")')
    elif mode == 'absent':
        lua.execute('storage.coal_supply=nil')
    assert mode in {'committed', 'proposed', 'absent'}
    # Cross beta's reserved but unbuilt corridor after ordinary unreserved
    # placement cells. A pole route cannot jump the gap after that cell is gone.
    start, end = (-4.5, 11.5), (13.5, 11.5)
    if name == 'pipe':
        positions = {(x + .5, 11.5) for x in range(-5, 14)}
        if detour:
            positions |= {(1.5, y + .5) for y in range(8, 12)}
            positions |= {(13.5, y + .5) for y in range(8, 12)}
            positions |= {(x + .5, 8.5) for x in range(1, 14)}
    else:
        positions = {(-4.5, 11.5), (1.5, 11.5), (7.5, 11.5), (13.5, 11.5)}
        if detour:
            positions |= {(1.5, 5.5), (7.5, 5.5), (13.5, 5.5)}
    lua.globals().connection_name = name
    lua.globals().fixture_placeable = lambda x, y: (x, y) in positions
    lua.execute('''
        prototypes.entity.pipe={collision_box={left_top={x=-.29,y=-.29},right_bottom={x=.29,y=.29}}}
        prototypes.entity["small-electric-pole"]={collision_box={left_top={x=-.2,y=-.2},right_bottom={x=.2,y=.2}}}
        defines.direction={north=0,east=4,south=8,west=12}
        defines.events={on_tick=1,on_script_path_request_finished=2}
        script={on_nth_tick=function() end,on_event=function() end,get_event_handler=function() end}
        connection_entities={};connection_payments={};stock[connection_name]=100
        surface.find_entities_filtered=function() return {} end
        surface.find_entity=function(name,p) return connection_entities[name..":"..p.x..":"..p.y] end
        surface.can_place_entity=function(q)
            return fixture_placeable(q.position.x,q.position.y) and not surface.find_entity(q.name,q.position)
        end
        player.position={x=0,y=0};player.build_distance=100
        local cursor={count=0};player.cursor_stack=cursor
        local inventory={find_item_stack=function(name)
            return stock[name]>0 and {name=name,count=stock[name],valid_for_read=true} or nil end}
        player.get_main_inventory=function() return inventory end
        cursor.transfer_stack=function(stack)
            cursor.name=stack.name;cursor.count=stack.count;stock[stack.name]=0;return true end
        player.clear_cursor=function()
            if cursor.name then stock[cursor.name]=stock[cursor.name]+cursor.count end
            cursor.name=nil;cursor.count=0;return true end
        player.can_build_from_cursor=function(q)
            return surface.can_place_entity{name=cursor.name,position=q.position} end
        player.build_from_cursor=function(q)
            local e={name=cursor.name,position=q.position,unit_number=9000+#connection_payments,
                     type=cursor.name=="pipe" and "pipe" or "electric-pole",valid=true,force=force,fluidbox={}}
            connection_entities[e.name..":"..q.position.x..":"..q.position.y]=e
            connection_payments[#connection_payments+1]={x=q.position.x,y=q.position.y}
            cursor.count=cursor.count-1
        end
    ''')
    lua.execute((ROOT / 'src/jev_factorio/lua/fair_actions.lua').read_text())
    def decode(value):
        decoded = json.loads(value)
        return lua.table_from(decoded, recursive=True) if isinstance(decoded, (dict, list)) else decoded
    lua.globals().helpers.json_to_table = decode
    captured = []
    lua.globals().rcon.print = lambda value: captured.append(plain(value))
    fair = object.__new__(FairActions)

    def command(script):
        captured.clear()
        lua.execute(script)
        assert len(captured) == 1
        return json.dumps(captured[0])

    fair.command = command
    # All fixture cells are in reach. Placement itself remains production Lua.
    fair.approach_build = lambda *args: None
    return fair, lua, start, end


def connect(fair, start, end, name):
    fair.connect(SimpleNamespace(x=start[0], y=start[1]), SimpleNamespace(x=end[0], y=end[1]),
                 SimpleNamespace(value=(name,)), 'water' if name == 'pipe' else 'electricity')


@pytest.mark.parametrize('name', ['pipe', 'small-electric-pole'])
def test_reserved_later_cell_rejects_complete_connection_before_first_payment(monkeypatch, name):
    fair, lua, start, end = adapter(monkeypatch, name)
    assert not lua.eval('storage.coal_supply.placement_reserved')(name, lua.table_from(dict(zip(('x','y'), start))), 0)
    assert lua.eval('storage.coal_supply.placement_reserved')(name, lua.table_from({'x':7.5,'y':11.5}), 0)
    with pytest.raises(ConnectionPreflightRejected) as error:
        connect(fair, start, end, name)
    assert error.value.code == 'no_connection_route'
    assert lua.eval('#connection_payments') == 0
    assert lua.globals().stock[name] == 100
    assert lua.eval('paid_calls') == 1  # Only the prior coal bundle payment.


@pytest.mark.parametrize('name', ['pipe', 'small-electric-pole'])
def test_complete_safe_detour_is_selected_before_ordinary_paid_placements(monkeypatch, name):
    fair, lua, start, end = adapter(monkeypatch, name, detour=True)
    connect(fair, start, end, name)
    placements = plain(lua.globals().connection_payments)
    assert placements and len(placements) == 100 - lua.globals().stock[name]
    for position in placements:
        assert not lua.eval('storage.coal_supply.placement_reserved')(name, lua.table_from(position), 0)
    assert (placements[0]['x'], placements[0]['y']) == start
    assert (placements[-1]['x'], placements[-1]['y']) == end


@pytest.mark.parametrize('name', ['pipe', 'small-electric-pole'])
@pytest.mark.parametrize('mode', ['absent', 'proposed'])
def test_no_committed_coal_reservation_preserves_ordinary_connection(monkeypatch, name, mode):
    fair, lua, start, end = adapter(monkeypatch, name, mode=mode)
    connect(fair, start, end, name)
    placements = plain(lua.globals().connection_payments)
    assert {'x': 7.5, 'y': 11.5} in placements
    assert lua.globals().stock[name] == 100 - len(placements)
