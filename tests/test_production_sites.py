"""Real joint-site Lua and Python planners over explicit synthetic native state."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from jev_factorio.backends.input_routes import InputRouteFactory
from jev_factorio.factory_contract import allowed, satisfied
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.production_sites import sources, summary
from test_factory import snapshot, catalog, machine

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'src/jev_factorio/lua'


@pytest.fixture
def runtime():
    lua = pytest.importorskip('lupa.lua54').LuaRuntime()
    lua.execute((ROOT / 'tests/fixtures/input_routes_runtime.lua').read_text())
    lua.execute('''
        surface=source.surface; surface.index=1; force.index=1
        for _,e in ipairs(entities) do e.valid=false end
        storage.campaign.entities={};storage.output_buffers=nil
        storage.campaign.exploration_radius=8
        prototypes.entity["stone-furnace"]={tile_width=2,tile_height=2}
        prototypes.entity["character"]={tile_width=1,tile_height=1}
        local original=create
        create=function(name,pos,dir,id)
            local entity=original(name,pos,dir,id)
            entity.type=name=="stone-furnace" and "furnace" or name
            return entity
        end
        stock["stone-furnace"]=1;stock["wooden-chest"]=1;stock["burner-inserter"]=2
        storage.fair.tick_handler=function() end;handlers[1]=storage.fair.tick_handler
    ''')
    lua.execute((LUA / 'output_buffers.lua').read_text())
    lua.execute((LUA / 'input_routes.lua').read_text())
    lua.execute((LUA / 'production_sites.lua').read_text())
    return lua


def test_joint_survey_is_read_only_and_stable(runtime):
    runtime.execute('''
        local campaign=storage.campaign
        local row=campaign.observe().production_sites.sources["recipe:iron-plate"]
        assert(row.state=="proposed",row.reason);assert(row.belt_count>=1 and row.belt_count<=64)
        assert(row.checks<=4096 and row.bill["transport-belt"]==row.belt_count)
        assert(placements==0 and stock["stone-furnace"]==1 and next(campaign.entities)==nil)
        assert(campaign.observe().production_sites.sources["recipe:iron-plate"].anchor==row.anchor)
        -- All projected footprints are disjoint, including the producer itself.
        for i,a in ipairs(row.components) do for j,b in ipairs(row.components) do if i<j then
            local aw=(a.name=="stone-furnace" or a.name=="burner-mining-drill") and 2 or 1
            local bw=(b.name=="stone-furnace" or b.name=="burner-mining-drill") and 2 or 1
            assert(math.abs(a.position.x-b.position.x)>=(aw+bw)/2-0.03
                or math.abs(a.position.y-b.position.y)>=(aw+bw)/2-0.03)
        end end end
    ''')


def test_distant_observed_ore_gets_a_local_joint_cell(runtime):
    runtime.execute('''
        for _,r in ipairs(resources) do r.position.x=r.position.x+160 end
        local row=storage.campaign.observe().production_sites.sources["recipe:iron-plate"]
        assert(row.state=="proposed",row.reason);assert(row.position.x>100)
        assert(math.abs(row.position.x-resources[1].position.x)<40)
        assert(placements==0)
    ''')


@pytest.mark.parametrize('obstacle', ['transport-belt', 'stone-furnace', 'burner-inserter', 'character'])
def test_blocked_joint_layout_has_bounded_explicit_rejection(runtime, obstacle):
    runtime.globals().blocked_name = obstacle
    runtime.execute('''
        obstacle=function(q) return q.name==blocked_name end
        local row=storage.campaign.observe().production_sites.sources["recipe:iron-plate"]
        assert(row.state=="rejected" and (row.reason=="no_clear_joint_layout" or row.reason=="survey_budget_exhausted"))
        assert(row.fallback=="batched_manual_supply" and placements==0)
    ''')


def test_missing_or_mixed_ore_never_creates_items_or_fake_flow(runtime):
    runtime.execute('''
        resources[2].name="copper-ore"
        local row=storage.campaign.observe().production_sites.sources["recipe:iron-plate"]
        assert(row.state=="rejected" and placements==0)
        resources={};game.tick=game.tick+301
        assert(storage.campaign.observe().production_sites.sources["recipe:iron-plate"].reason=="no_observed_ore_in_generated_area")
    ''')


def test_paid_furnace_adopts_preflight_output_and_input_geometry(runtime):
    runtime.execute('''
        local c=storage.campaign
        local row=c.observe().production_sites.sources["recipe:iron-plate"]
        c.prepare_production_site("recipe:iron-plate","stone-furnace",row.anchor)
        assert(placements==0)
        player.position=row.position -- Synthetic movement; real adapter uses fair.approach.
        c.build_production_site("recipe:iron-plate","stone-furnace",row.anchor)
        assert(placements==1 and stock["stone-furnace"]==0)
        assert(c.observe().production_sites.sources["recipe:iron-plate"].state=="owned")
        local site=storage.production_sites.owned["recipe:iron-plate"]
        local out=c.observe().output_buffers.sources["recipe:iron-plate"]
        assert(out.state=="proposed" and not next(out.flow))
        for _,part in ipairs({"chest","inserter"}) do
            local p={source=out.source,layout=out.layout,part=part,receipt="out:"..part}
            local position=part=="chest" and site.chest.position or site.output_arm.position
            player.position=position
            c.prepare_output_buffer(p);c.build_output_buffer(p)
        end
        local cell=storage.output_buffers.cells[out.source]
        cell.parts.inserter.entity.pickup_target=cell.entity
        cell.parts.inserter.entity.drop_target=cell.parts.chest.entity
        c.observe();handlers[1]({tick=game.tick})
        for n=1,3 do
            game.tick=game.tick+60;cell.entity.products_finished=cell.entity.products_finished+1
            local inv=cell.parts.chest.entity.get_inventory(defines.inventory.chest)
            inv.values["iron-plate"]=(inv.values["iron-plate"] or 0)+1
            handlers[1]({tick=game.tick})
        end
        assert(cell.flow and placements==3)
        game.tick=game.tick+301
        local input=c.observe().input_routes.sources[out.source]
        assert(input and input.state=="proposed")
        assert(input.steps[1].position.x==site.input_steps[1].position.x)
        assert(input.steps[#input.steps].name=="burner-mining-drill")
        assert(not next(input.flow))
        source=cell.entity;chest=cell.parts.chest.entity
        for _,part in ipairs(input.steps) do
            player.position=part.position
            local p={source=input.source,layout=input.layout,part=part.part,receipt="in:"..part.part,reserve_belts=20}
            c.prepare_input_route(p);c.build_input_route(p)
        end
        connect()
        local owned=storage.input_routes.cells[input.source]
        owned.parts.drill.entity.mining_target=site.resource
        c.observe();assert(not owned.flow)
        for n=1,3 do
            game.tick=game.tick+60;site.resource.amount=site.resource.amount-1
            source.products_finished=source.products_finished+1
            local inv=chest.get_inventory(defines.inventory.chest)
            inv.values["iron-plate"]=inv.values["iron-plate"]+1
            c.observe()
        end
        assert(owned.flow and owned.flow.new_plates==3 and owned.flow.mined==3)
        assert(placements==3+#input.steps)
    ''')


@pytest.mark.parametrize('change', [
    'player.crafting_queue_size=1', 'storage.production_sites.offers["recipe:iron-plate"].resource.valid=false',
    'obstacle=function(q) return q.name=="transport-belt" end', 'stock["stone-furnace"]=0',
])
def test_stale_unpaid_offer_cannot_be_dispatched(runtime, change):
    runtime.execute('row=storage.campaign.observe().production_sites.sources["recipe:iron-plate"];player.position=row.position')
    runtime.execute(change)
    runtime.execute('''
        assert(not pcall(storage.campaign.build_production_site,"recipe:iron-plate","stone-furnace",row.anchor))
        assert(placements==0)
    ''')


def test_build_uses_native_reach_and_duplicate_dispatch_is_rejected(runtime):
    runtime.execute('''
        local c=storage.campaign;local row=c.observe().production_sites.sources["recipe:iron-plate"]
        player.position={x=1000,y=1000}
        assert(not pcall(c.build_production_site,"recipe:iron-plate","stone-furnace",row.anchor))
        player.position=row.position;c.build_production_site("recipe:iron-plate","stone-furnace",row.anchor)
        assert(not pcall(c.build_production_site,"recipe:iron-plate","stone-furnace",row.anchor))
        assert(placements==1)
    ''')


def test_reattachment_keeps_site_and_existing_observer_chain(runtime):
    runtime.execute('row=storage.campaign.observe().production_sites.sources["recipe:iron-plate"];original=storage.campaign.observe')
    for _ in range(5):
        runtime.execute((LUA / 'production_sites.lua').read_text())
        runtime.execute('assert(storage.campaign.observe==original);assert(storage.campaign.observe().production_sites.sources["recipe:iron-plate"].anchor==row.anchor)')


def offered_state():
    state = snapshot(inventory={'stone-furnace': 1})
    row = {'state': 'proposed', 'reason': 'joint_layout_available', 'anchor': 'cell-site:iron:8:0',
        'position': {'x': 8, 'y': 0}, 'belt_count': 5, 'components': [{'name': 'furnace'}],
        'bill': {'stone-furnace': 1, 'burner-mining-drill': 1, 'burner-inserter': 2,
                 'wooden-chest': 1, 'transport-belt': 5}}
    state.factory['production_sites'] = {'protocol': 1, 'session_id': state.session_id,
                                        'tick': state.tick, 'sources': {'recipe:iron-plate': row}}
    return state, row


def test_planner_uses_preflight_anchor_and_needs_owned_identity_for_success():
    state, row = offered_state()
    planner = InputRoutePlanner(catalog(), state, 'rocket_launch')
    plan = planner._machine('recipe:iron-plate', 'stone-furnace', ())
    step = plan.steps[0]
    assert step.parameters['anchor'] == row['anchor'] and step.allowed(state)
    assert not step.satisfied(state)
    state.factory['entities']['recipe:iron-plate'] = machine(position=row['position'])
    assert not step.satisfied(state), 'An unowned entity name is not a site receipt'
    row.update(state='owned', source_unit=17)
    assert step.satisfied(state)
    state.factory['entities']['recipe:iron-plate']['unit_number'] = 99
    assert not step.satisfied(state)


@pytest.mark.parametrize('change', [
    lambda s, r: s.factory['production_sites'].update(tick=s.tick-1),
    lambda s, r: r.update(belt_count=65),
    lambda s, r: r.update(state='fault'),
    lambda s, r: r['position'].update(x=float('nan')),
])
def test_invalid_site_fails_closed(change):
    state, row = offered_state()
    change(state, row)
    with pytest.raises(ValueError):
        sources(state)
    assert not allowed('factory_place', {'role': 'recipe:iron-plate', 'name': 'stone-furnace', 'anchor': row['anchor']}, state)


def test_no_site_preserves_manual_fallback_and_model_summary_is_compact():
    state, row = offered_state()
    compact = summary(state)
    assert 'components' not in compact['recipe:iron-plate']
    state.factory['production_sites']['sources']['recipe:iron-plate'] = {'state': 'rejected', 'reason': 'no_clear_joint_layout'}
    assert InputRoutePlanner(catalog(), state, 'rocket_launch')._machine(
        'recipe:iron-plate', 'stone-furnace', ()).steps[0].parameters['anchor'] == 'factory'


def test_native_site_adapter_prepare_walk_build_order_and_no_retry():
    calls = []
    native = SimpleNamespace(command=lambda code: None)
    def call(name, *args):
        calls.append(name)
        if name == 'build_production_site':
            raise TimeoutError('synthetic lost native response')
        return json.dumps({'position': {'x': 8, 'y': 0}, 'name': 'stone-furnace'})
    native.call = call
    native.backend = SimpleNamespace(_fair=SimpleNamespace(approach=lambda pos, name: calls.append('walk')))
    adapter = InputRouteFactory(native)
    with pytest.raises(TimeoutError):
        adapter.execute('factory_place', {'role': 'recipe:iron-plate', 'name': 'stone-furnace', 'anchor': 'cell-site:test'})
    assert calls == ['prepare_production_site', 'walk', 'build_production_site']


def test_existing_manual_cell_is_not_relocated(runtime):
    runtime.execute('''
        local e=create("stone-furnace",{x=100,y=100},0,500)
        storage.campaign.entities["recipe:iron-plate"]=e
        local observed=storage.campaign.observe()
        assert(observed.production_sites.sources["recipe:iron-plate"].reason=="existing_manual_cell")
        assert(observed.production_sites.sources["recipe:iron-plate"].fallback=="batched_manual_supply")
        assert(e.position.x==100 and placements==0)
        assert(observed.input_routes.diagnostics["recipe:iron-plate"].reason=="output_not_commissioned")
    ''')


from test_fair_actions import fair_runtime


def test_generic_build_search_preserves_reserved_cell_footprint(fair_runtime):
    fair_runtime.execute('''
        storage.campaign={production_reserved=function(name,p,direction)
            return math.abs(p.x)<0.1 and math.abs(p.y)<0.1
        end}
        local site=storage.fair.find_build_site("pipe",{x=0,y=0},1)
        assert(math.abs(site.position.x)>0.1 or math.abs(site.position.y)>0.1)
    ''')
