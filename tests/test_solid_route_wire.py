"""Real NativeFactory command/call + new adapter + Lua source, fake transport/engine.

No socket or game runs. Empty Lua maps are exercised with both known JSON forms.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio.backends.native_factory import NativeFactory
from jev_factorio.backends.solid_routes import SolidRouteFactory
from jev_factorio import solid_routes as routes
from jev_factorio.state import GameSnapshot

ROOT = Path(__file__).resolve().parents[1]


def wire(empty_arrays=False, fuel=False):
    lua = pytest.importorskip('lupa.lua52').LuaRuntime()
    lua.execute((ROOT/'tests/fixtures/solid_routes_runtime.lua').read_text())
    if fuel:
        lua.execute('''
            source.name="wooden-chest";source.type="container";source.recipe=nil
            source.bounding_box={left_top={x=.15,y=.15},right_bottom={x=.85,y=.85}}
            source.output.values["iron-gear-wheel"]=nil;source.output.values.coal=20
            target.name="boiler";target.type="boiler";target.recipe=nil;target.burner={}
            target.bounding_box={left_top={x=6.1,y=-.4},right_bottom={x=8.9,y=1.4}}
        ''')
    printed, calls = [], []
    table_type = type(lua.table())

    def to_json(value):
        def plain(value):
            if not isinstance(value, table_type):
                return value
            keys = list(value.keys())
            if (not keys and empty_arrays) or (keys and set(keys) == set(range(1, len(keys)+1))):
                return [plain(value[i]) for i in range(1, len(keys)+1)]
            return {str(k): plain(value[k]) for k in keys}
        return json.dumps(plain(value), allow_nan=False)

    lua.globals().rcon = lua.table_from({'print': lambda value: printed.append(str(value))})
    lua.globals().helpers = lua.table_from({'table_to_json': to_json,
        'json_to_table': lambda raw: lua.table_from(json.loads(raw), recursive=True)})

    class Rcon:
        def send_command(self, command):
            assert command.startswith('/sc ')
            printed.clear(); calls.append(command)
            lua.execute(command[4:])
            return '\n'.join(printed)

    backend = SimpleNamespace(_instance=SimpleNamespace(rcon_client=Rcon()),
                              _fair=SimpleNamespace(approach=lambda *a: calls.append('approach')))
    native = NativeFactory.__new__(NativeFactory)
    native.backend = backend
    # Keep NativeFactory.call/command exactly as shipped. Only bypass FLE discovery.
    def observe(snapshot):
        snapshot.factory = json.loads(native.command('rcon.print(helpers.table_to_json(storage.campaign.observe()))'))
        snapshot.tick = snapshot.factory['tick']
        return snapshot
    native.observe = observe
    intents = [{'source':'recipe:iron-gear-wheel', 'target':'recipe:automation-science-pack',
                'item':'coal' if fuel else 'iron-gear-wheel', 'destination':'fuel' if fuel else 'input'}]
    factory = SolidRouteFactory(native, intents)
    state = GameSnapshot(session_id='solid-fixture', tick=300)
    return factory, state, lua, calls


def test_native_call_serializes_prepare_and_keeps_actor_order():
    factory, state, lua, calls = wire()
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    p = {'route':row['route'], 'layout':row['layout'], 'part':'receive', 'receipt':'wire-receipt'}
    before = len(calls)
    factory.execute(routes.COMMAND, p)
    added = calls[before:]
    assert 'prepare_solid_route' in added[0]
    assert added[1] == 'approach'
    assert 'build_solid_route' in added[2]
    assert len(added) == 3 and lua.globals().paid_calls == 1


@pytest.mark.parametrize('empty_arrays', [False, True])
def test_native_empty_tables_round_trip(empty_arrays):
    factory, state, _, _ = wire(empty_arrays)
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    assert row['parts'] == row['flow'] == row['pending'] == {}


def build_wire(factory, state):
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    for step in row['steps']:
        factory.execute(routes.COMMAND, {'route':row['route'], 'layout':row['layout'],
                        'part':step['part'], 'receipt':'wire:'+step['part']})
        factory.observe(state)
    return next(iter(routes.routes(state).values()))


@pytest.mark.parametrize('empty_arrays', [False, True])
@pytest.mark.parametrize('fuel', [False, True])
def test_python_and_real_lua_agree_on_paid_prefix_and_flow(empty_arrays, fuel):
    factory, state, lua, _ = wire(empty_arrays, fuel)
    row = build_wire(factory, state)
    assert routes.current(row, state) and not routes.flow_complete(row['route'], row['layout'], state)
    for _ in range(3):
        lua.execute('''
            local cell=next(storage.solid_routes.cells) and select(2,next(storage.solid_routes.cells))
            source.output.values[cell.item]=source.output.values[cell.item]-1
            local destination=cell.target.inventory=="fuel" and target.fuel or target.input
            destination.values[cell.item]=(destination.values[cell.item] or 0)+1
            game.tick=game.tick+60
        ''')
        factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    assert row['flow']['received'] == row['flow']['sent'] == 3
    assert routes.flow_complete(row['route'], row['layout'], state)
    lua.execute('target.'+('fuel' if fuel else 'input')+'.capacity=0;game.tick=game.tick+60')
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    assert row['reason']=='backpressure' and not routes.flow_complete(row['route'], row['layout'], state)
    assert row['flow']['received']==3  # Historical evidence is retained, not relabeled as current flow.


def test_fuel_delivery_is_a_lower_bound_not_a_claimed_burn_counter():
    factory, state, lua, _ = wire(fuel=True)
    row = build_wire(factory, state)
    lua.execute('source.output.values.coal=17;target.fuel.values.coal=1;game.tick=game.tick+60')
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    assert row['flow']['sent']==3 and row['flow']['received']==1
    assert row['flow']['unattributed_loss']==2
    assert row['flow']['method']=='exclusive_fuel_lower_bound'
    assert not routes.flow_complete(row['route'], row['layout'], state)


@pytest.mark.parametrize('mutation', [
    'target.fuel.values.coal=5',
    'source.quality.name="rare"',
    'target.burner=nil',
    'source.output.values["copper-plate"]=1',
])
def test_coal_contract_rejects_wrong_input_quality_or_consumer(mutation):
    factory, state, lua, _ = wire(fuel=True)
    build_wire(factory, state)
    lua.execute(mutation+';game.tick=game.tick+60')
    factory.observe(state)
    row = next(iter(routes.routes(state).values()))
    assert row['state']=='fault' and not routes.current(row, state)
    assert not routes.flow_complete(row['route'], row['layout'], state)


@pytest.mark.parametrize('turns', [0, 1, 2, 3])
def test_native_and_python_validate_all_cardinal_corridors(turns):
    factory, state, lua, _ = wire()
    for _ in range(turns):
        lua.execute('''
            local function rotate(p) return {x=1-p.y,y=p.x} end
            for _,e in ipairs(all_entities) do
                e.position=rotate(e.position)
                local box=e.bounding_box
                local a,b=rotate(box.left_top),rotate(box.right_bottom)
                e.bounding_box={left_top={x=math.min(a.x,b.x),y=math.min(a.y,b.y)},
                                right_bottom={x=math.max(a.x,b.x),y=math.max(a.y,b.y)}}
            end
        ''')
    row = build_wire(factory, state)
    assert routes.current(row, state) and row['topology'] and len(row['parts'])==4
    assert lua.globals().paid_calls == 4


@pytest.mark.parametrize('mutation,reason', [
    ('pole.valid=false','missing_owned_power'),
    ('blocked=true','obstructed_corridor'),
    ('campaign.entities["recipe:iron-gear-wheel"]=nil','endpoint_unavailable'),
    ('source.output.values.coal=1','mixed_source_items'),
    ('campaign.entities.alias=source','aliased_identity'),
])
def test_missing_corridor_has_a_bounded_explanatory_diagnostic(mutation,reason):
    factory,state,lua,_ = wire()
    lua.execute(mutation)
    factory.observe(state)
    assert routes.routes(state)=={}
    assert state.factory['solid_routes']['diagnostics']==[
        {'intent_index':1,'state':'unavailable','reason':reason}]
