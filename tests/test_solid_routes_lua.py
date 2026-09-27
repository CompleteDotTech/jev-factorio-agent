"""Run the real new Lua module against deterministic API-shape doubles, not Factorio."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / "src/jev_factorio/lua/solid_routes.lua"


def runtime():
    lua = pytest.importorskip("lupa.lua52").LuaRuntime()
    lua.execute((ROOT / "tests/fixtures/solid_routes_runtime.lua").read_text())
    lua.execute(LUA.read_text())
    lua.execute("configure()")
    return lua


@pytest.mark.parametrize("code", [
    'local row=offer();assert(row.state=="proposed" and #row.steps==4 and paid_calls==0)',
    'local row=offer();assert(row.layout==offer().layout and paid_calls==0)',
    'local cell=build_all();assert(paid_calls==4 and stock.inserter==8 and stock["transport-belt"]==98 and cell.flow.received==0)',
    'local cell=build_all();pulse();pulse();pulse();assert(not cell.fault and cell.flow.received==3 and cell.flow.sent==3 and cell.flow.positive_samples==3)',
    'local cell=build_all();game.tick=game.tick+180;campaign.observe();assert(cell.flow.received==0 and not cell.fault)',
    'local cell=build_all();target.input.values["iron-gear-wheel"]=5;game.tick=game.tick+60;campaign.observe();assert(cell.fault)',
    'local cell=build_all();source.output.values["iron-gear-wheel"]=19;game.tick=game.tick+60;campaign.observe();assert(cell.fault)',
    'local cell=build_all();cell.parts["belt:1"].entity.direction=0;campaign.observe();assert(cell.fault)',
    'local cell=build_all();cell.parts["belt:1"].entity.valid=false;campaign.observe();assert(cell.fault)',
    'local cell=build_all();cell.parts.receive.entity.drop_target=source;campaign.observe();assert(cell.fault)',
    'local cell=build_all();source.products_finished=-1;game.tick=game.tick+1;campaign.observe();assert(cell.fault)',
    'local cell=build_all();game.tick=game.tick-1;campaign.observe();assert(cell.fault)',
    'local cell=build_all();source.output.values["copper-plate"]=1;campaign.observe();assert(cell.fault)',
    'local cell=build_all();cell.parts["belt:1"].entity.lines[1].values.coal=1;campaign.observe();assert(cell.fault)',
    'local cell=build_all();cell.parts.send.entity.energy=0;game.tick=game.tick+60;campaign.observe();assert(not cell.fault and cell.reason=="no_power")',
    'local cell=build_all();target.input.capacity=0;game.tick=game.tick+60;campaign.observe();assert(not cell.fault and cell.reason=="backpressure" and paid_calls==4)',
    'local cell=build_all();assert(not pcall(campaign.transfer,cell.source.role,cell.item,1,"transfer-fixture",false));assert(transfers==0)',
    'local cell=build_all();campaign.transfer(cell.target.role,"copper-plate",1,"transfer-fixture",false);assert(transfers==1)',
    'local cell=build_all();assert(not pcall(campaign.configure,cell.target.role,"iron-gear-wheel"))',
    'local row=offer();local p=args(row);stock.inserter=1;assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0 and next(storage.solid_routes.cells)==nil)',
    'local row=offer();local p=args(row,"send");assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0)',
    'local row=offer();local p=args(row);player.crafting_queue_size=1;assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0)',
    'local row=offer();local p=args(row);campaign.prepare_solid_route(p);blocked=true;assert(not pcall(campaign.build_solid_route,p));assert(paid_calls==0)',
    'blocked=true;assert(next(campaign.observe().solid_routes.routes)==nil and paid_calls==0)',
    'pole.valid=false;assert(next(campaign.observe().solid_routes.routes)==nil and paid_calls==0)',
    'target.recipe.ingredients[1].name="iron-plate";assert(next(campaign.observe().solid_routes.routes)==nil)',
    'source.productivity_bonus=0.1;assert(next(campaign.observe().solid_routes.routes)==nil)',
    'source.recipe.products[1].amount=2;assert(next(campaign.observe().solid_routes.routes)==nil)',
    'campaign.entities.alias=source;assert(next(campaign.observe().solid_routes.routes)==nil)',
    'target.surface={index=2};assert(next(campaign.observe().solid_routes.routes)==nil)',
    'local row=offer();local p=args(row);campaign.prepare_solid_route(p);campaign.build_solid_route(p);campaign.prepare_solid_route(p);campaign.build_solid_route(p);assert(paid_calls==1)',
    'local row=offer();local p=args(row);campaign.prepare_solid_route(p);campaign.build_solid_route(p);p.receipt="different";assert(not pcall(campaign.build_solid_route,p));assert(paid_calls==1)',
    'local row=offer();local p=args(row);campaign.prepare_solid_route(p);lose_place_receipt=true;assert(not pcall(campaign.build_solid_route,p));campaign.observe();assert(storage.solid_routes.cells[row.route].fault and paid_calls==1);assert(not pcall(campaign.build_solid_route,p))',
    'local row=offer();local p=args(row);campaign.prepare_solid_route(p);p.receipt="another";assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0)',
    'local row=offer();local p=args(row);p.extra=true;assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0)',
    'local row=offer();local p=args(row);p.layout="stale";assert(not pcall(campaign.prepare_solid_route,p));assert(paid_calls==0)',
])
def test_native_lua_contract(code):
    runtime().execute(code)


def test_receipt_journal_recovers_registration_failure_without_payment_replay():
    lua = runtime()
    lua.execute('''
        local row=offer();local p=args(row);campaign.prepare_solid_route(p)
        local fail_once=true
        setmetatable(campaign.entities,{__newindex=function(t,k,v)
            if fail_once then fail_once=false;error("fixture receipt persistence boundary") end
            rawset(t,k,v)
        end})
        assert(not pcall(campaign.build_solid_route,p))
        local cell=storage.solid_routes.cells[row.route]
        assert(cell.pending.phase=="placed" and paid_calls==1)
        campaign.observe()
        assert(cell.pending==nil and cell.parts.receive.paid==1 and paid_calls==1 and not cell.fault)
        campaign.build_solid_route(p);assert(paid_calls==1)
    ''')


def test_reattach_preserves_paid_prefix_and_immutable_intents():
    lua = runtime()
    lua.execute('local row=offer();local p=args(row);campaign.prepare_solid_route(p);campaign.build_solid_route(p);original_observer=campaign.observe')
    for _ in range(5):
        lua.execute(LUA.read_text())
        lua.execute('configure();assert(campaign.observe==original_observer and paid_calls==1)')
    lua.execute('assert(not pcall(campaign.set_solid_intents,{{source="other",target="target",item="coal",destination="fuel"}}))')


def test_inflight_craft_accounting_and_source_production():
    runtime().execute('''
        local cell=build_all()
        source.products_finished=1 -- exactly one new gear was crafted then transported
        target.crafting=true -- target consumed the gear at craft start
        game.tick=game.tick+60;campaign.observe()
        assert(not cell.fault and cell.flow.sent==1 and cell.flow.received==1)
        target.crafting=false;target.products_finished=1
        game.tick=game.tick+60;campaign.observe()
        assert(not cell.fault and cell.flow.received==1)
    ''')


def test_old_inflight_items_are_not_new_flow_proof():
    runtime().execute('''
        local cell=build_all()
        cell.previous=nil;cell.parts.send.entity.held_stack={valid_for_read=true,name=cell.item,count=1,quality={name="normal"}}
        campaign.observe()
        cell.parts.send.entity.held_stack={valid_for_read=false}
        target.input.values[cell.item]=1;game.tick=game.tick+60;campaign.observe()
        assert(not cell.fault and cell.flow.received==0 and cell.flow.sent==0)
    ''')


def test_default_player_binding_uses_the_existing_fair_actor():
    lua = runtime()
    lua.execute('storage.jev_player_index=nil;assert(offer().state=="proposed")')


def test_native_intent_list_is_detached_from_callers():
    lua = runtime()
    lua.execute('''
        local input={{source="recipe:iron-gear-wheel",target="recipe:automation-science-pack",item="iron-gear-wheel",destination="input"}}
        campaign.set_solid_intents(input)
        input[1].source="unrequested"
        assert(offer().source.role=="recipe:iron-gear-wheel")
    ''')


@pytest.mark.parametrize('case', [
    'campaign.set_solid_intents({})',
    'campaign.set_solid_intents({{source="source",target="target",item="iron-plate",destination="fuel"}})',
])
def test_native_intents_reject_unsupported_empty_or_nonfuel(case):
    lua = pytest.importorskip('lupa.lua52').LuaRuntime()
    lua.execute((ROOT/'tests/fixtures/solid_routes_runtime.lua').read_text())
    lua.execute(LUA.read_text())
    lua.execute('assert(not pcall(function() '+case+' end))')


def test_one_receipt_cannot_pay_for_two_different_components():
    runtime().execute('''
        local row=offer();local first=args(row);campaign.prepare_solid_route(first);campaign.build_solid_route(first)
        local second=args(row,row.steps[2].part);second.receipt=first.receipt
        assert(not pcall(campaign.prepare_solid_route,second))
        assert(paid_calls==1 and storage.solid_routes.cells[row.route].pending==nil)
    ''')


def test_unsupported_construction_quality_rejected_before_any_spend():
    runtime().execute('''
        player.get_main_inventory=function()
            local v=inv(stock)
            v[1]={valid_for_read=true,name="inserter",count=stock.inserter,quality={name="rare"}}
            return v
        end
        local p=args(offer())
        assert(not pcall(campaign.prepare_solid_route,p))
        assert(paid_calls==0)
    ''')


def test_other_protocol_one_runtime_is_not_treated_as_same_installed_extension():
    lua = runtime()
    lua.execute('''
        local row=offer();local p=args(row);campaign.prepare_solid_route(p)
        retained=storage.solid_routes
        saved_observer,saved_transfer,saved_configure=campaign.observe,campaign.transfer,campaign.configure
        storage.solid_routes.contract_family="different-solid-contract-v1"
    ''')
    with pytest.raises(Exception, match="reconciliation"):
        lua.execute(LUA.read_text())
    lua.execute('''
        assert(storage.solid_routes==retained and campaign.observe==saved_observer
            and campaign.transfer==saved_transfer and campaign.configure==saved_configure
            and paid_calls==0)
    ''')
