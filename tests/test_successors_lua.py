"""Actual Lua adapters with synthetic engine controls, not native VM acceptance."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'src/jev_factorio/lua'


@pytest.fixture
def runtime(request):
    lua = pytest.importorskip('lupa.lua54').LuaRuntime()
    def scenario(code):
        if getattr(request, 'param', 'iron') == 'copper':
            code = code.replace('iron-plate', 'copper-plate').replace('iron-ore', 'copper-ore').replace('iron-gear-wheel', 'copper-cable')
        lua.execute(code)
    scenario((ROOT / 'tests/fixtures/input_routes_runtime.lua').read_text())
    scenario('''
        surface=source.surface;surface.index=1;force.index=1;player.index=1
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
        old=create("stone-furnace",{x=160,y=160},0,500)
        old.products_finished=100;old.get_inventory(1).values.coal=50
        storage.campaign.entities["recipe:iron-plate"]=old
        stock["stone-furnace"]=1;stock["wooden-chest"]=1;stock["burner-inserter"]=2
        stock["iron-ore"]=10;stock["iron-plate"]=0
        storage.campaign.transfer=function(role,item,count,receipt,extracting)
            local e=storage.campaign.entities[role];assert(e and e.valid)
            local kind=extracting and (e.type=="furnace" and 3 or 4) or (item=="coal" and 1 or 2)
            local inv=e.get_inventory(kind).values
            if extracting then assert((inv[item] or 0)>=count);inv[item]=inv[item]-count;stock[item]=(stock[item] or 0)+count
            else assert((stock[item] or 0)>=count);stock[item]=stock[item]-count;inv[item]=(inv[item] or 0)+count end
        end
        storage.fair.tick_handler=function() end;handlers[1]=storage.fair.tick_handler
        defines.events.on_pre_player_crafted_item=2
        defines.events.on_player_cancelled_crafting=3
        defines.events.on_player_crafted_item=4
        recipe={name="iron-gear-wheel",enabled=true,ingredients={{type="item",name="iron-plate",amount=2}},
            products={{type="item",name="iron-gear-wheel",amount=1}}}
        force.recipes={["iron-gear-wheel"]=recipe}
        player.crafting_queue={};begin_calls=0
        player.get_main_inventory=function() return {get_contents=function()
            local result={};for name,count in pairs(stock) do result[#result+1]={name=name,count=count} end;return result
        end} end
        player.begin_crafting=function(parameters)
            begin_calls=begin_calls+1
            stock["iron-plate"]=stock["iron-plate"]-2*parameters.count
            player.crafting_queue_size=1
            player.crafting_queue={{recipe=recipe.name,count=parameters.count,prerequisite=false}}
            handlers[2]{player_index=1,recipe=recipe}
            return parameters.count
        end
        function finish_one()
            game.tick=game.tick+60
            stock["iron-gear-wheel"]=(stock["iron-gear-wheel"] or 0)+1
            player.crafting_queue[1].count=player.crafting_queue[1].count-1
            if player.crafting_queue[1].count==0 then player.crafting_queue={};player.crafting_queue_size=0 end
            handlers[4]{player_index=1,recipe=recipe,item_stack={valid_for_read=true,
                name="iron-gear-wheel",count=1,quality={name="normal"}}}
        end
    ''')
    for asset in ('craft_jobs.lua', 'output_buffers.lua', 'input_routes.lua', 'production_sites.lua', 'successors.lua'):
        lua.execute((LUA / asset).read_text())
    scenario('''
        c=storage.campaign;growth="growth:iron-plate"
        function start_successor()
            local row=c.observe().production_sites.sources[growth]
            assert(row.state=="proposed",row.reason)
            assert(row.position.x<100 and row.position.y<100)
            c.prepare_production_site(growth,"stone-furnace",row.anchor)
            player.position=row.position -- Synthetic fixture movement, not native actuation.
            c.build_production_site(growth,"stone-furnace",row.anchor)
            site=storage.production_sites.owned[growth];new=site.entity
            assert(c.entities["recipe:iron-plate"]==old and old.unit_number==500)
            assert(new~=old and new.unit_number~=old.unit_number and placements==1)
            assert(c.observe().successors.sources[growth].phase~="preferred")
            return row
        end
        function output_ready()
            start_successor()
            local out=c.observe().output_buffers.sources[growth]
            for _,part in ipairs({"chest","inserter"}) do
                local p={source=growth,layout=out.layout,part=part,receipt="out:"..part}
                player.position=part=="chest" and site.chest.position or site.output_arm.position
                c.prepare_output_buffer(p);c.build_output_buffer(p)
            end
            output=storage.output_buffers.cells[growth];outchest=output.parts.chest.entity
            output.parts.inserter.entity.pickup_target=new;output.parts.inserter.entity.drop_target=outchest
            c.transfer(growth,"iron-ore",10,"seed",false)
            c.observe();handlers[1]({tick=game.tick})
            for n=1,10 do
                game.tick=game.tick+60;new.products_finished=new.products_finished+1
                new.get_inventory(2).values["iron-ore"]=new.get_inventory(2).values["iron-ore"]-1
                local inv=outchest.get_inventory(4).values;inv["iron-plate"]=(inv["iron-plate"] or 0)+1
                handlers[1]({tick=game.tick})
            end
            assert(output.flow and placements==3)
        end
        function built_successor()
            output_ready();game.tick=game.tick+301
            local row=c.observe().input_routes.sources[growth];assert(row)
            for _,spec in ipairs(row.steps) do
                player.position=spec.position
                local p={source=growth,layout=row.layout,part=spec.part,receipt="in:"..spec.part,reserve_belts=20}
                c.prepare_input_route(p);c.build_input_route(p)
            end
            route=storage.input_routes.cells[growth]
            route.parts.drill.entity.drop_target=route.parts["belt:1"].entity
            route.parts.drill.entity.drop_position=route.parts["belt:1"].entity.position
            route.parts.drill.entity.mining_target=site.resource
            route.parts.inserter.entity.pickup_target=route.parts["belt:"..route.belt_count].entity
            route.parts.inserter.entity.drop_target=new
            for n=1,route.belt_count do
                route.parts["belt:"..n].entity.belt_neighbours={
                    inputs=n>1 and {route.parts["belt:"..(n-1)].entity} or {},
                    outputs=n<route.belt_count and {route.parts["belt:"..(n+1)].entity} or {}}
            end
            c.observe();assert(not route.flow)
        end
        function produce(n)
            for i=1,n do
                game.tick=game.tick+60;site.resource.amount=site.resource.amount-1
                new.products_finished=new.products_finished+1
                local inv=outchest.get_inventory(4).values;inv["iron-plate"]=(inv["iron-plate"] or 0)+1
                c.observe()
            end
        end
        function flowing_successor()
            built_successor();produce(6)
            assert(route.flow and route.flow.new_plates>=3)
            assert(c.observe().successors.sources[growth].phase=="producing")
        end
    ''')
    return lua


def test_additive_distant_survey_and_full_kit_do_not_replace_or_modify_old_factory(runtime):
    runtime.execute('''
        local row=c.observe().production_sites.sources[growth]
        assert(row.state=="proposed" and placements==0 and old.position.x==160)
        assert(c.observe().production_sites.sources["recipe:iron-plate"].reason=="existing_manual_cell")
        stock["transport-belt"]=row.belt_count+19
        assert(not pcall(c.prepare_production_site,growth,"stone-furnace",row.anchor))
        assert(next(storage.successors.records)==nil and placements==0 and old.products_finished==100)
    ''')


def test_furnace_requires_prepared_intent_and_cannot_be_built_twice(runtime):
    runtime.execute('''
        local row=c.observe().production_sites.sources[growth];player.position=row.position
        assert(not pcall(c.build_production_site,growth,"stone-furnace",row.anchor))
        start_successor()
        assert(not pcall(c.build_production_site,growth,"stone-furnace",site.anchor))
        assert(placements==1 and stock["stone-furnace"]==0 and old.valid)
    ''')


def test_flow_and_completed_attributed_crafting_precede_ten_minute_qualification(runtime):
    runtime.execute('''
        flowing_successor()
        c.transfer(output.chest_role,"iron-plate",16,"trial",true)
        local m=storage.successors.records[growth]
        assert(m.seeded==10 and m.credit==6 and not m.use and not m.qualification)
        c.begin_craft_job("use:1","iron-gear-wheel",8)
        assert(not c.observe().successors.sources[growth].use.job_id)
        for i=1,8 do finish_one() end
        c.observe();assert(m.use and m.use.quantity==6 and not m.qualification)
        produce(600)
        local row=c.observe().successors.sources[growth]
        assert(row.phase=="preferred" and row.qualification.last_tick-row.qualification.first_tick>=36000)
        assert(row.qualification.positive_samples>=3 and row.qualification.use_job_id=="use:1")
        assert(old.valid and old.products_finished==100 and old.position.x==160)
        assert(game.speed==1 and begin_calls==1)
    ''')


def test_preloaded_seed_output_never_counts_as_new_automated_use(runtime):
    runtime.execute('''
        flowing_successor();c.transfer(output.chest_role,"iron-plate",10,"old-seed",true)
        assert(storage.successors.records[growth].credit==0)
        c.begin_craft_job("seed-only","iron-gear-wheel",5)
        for i=1,5 do finish_one() end
        c.observe();produce(600)
        local m=storage.successors.records[growth]
        assert(not m.use and not m.qualification)
    ''')


@pytest.mark.parametrize('change', [
    'handlers[3]{player_index=1}',
    'storage.campaign.craft_jobs.job.id="different-job"',
    'stock["iron-gear-wheel"]=0',
])
def test_cancelled_different_or_unobserved_output_job_does_not_qualify(runtime, change):
    runtime.execute('''
        flowing_successor();c.transfer(output.chest_role,"iron-plate",16,"trial",true)
        c.begin_craft_job("job","iron-gear-wheel",8)
        for i=1,8 do finish_one() end
    ''')
    if change.startswith('handlers'):
        runtime.execute('storage.campaign.craft_jobs.job.status="running"')
    runtime.execute(change)
    runtime.execute('c.observe();assert(not storage.successors.records[growth].use)')


def test_observed_inventory_contamination_discards_only_attribution_not_assets(runtime):
    runtime.execute('''
        flowing_successor();c.transfer(output.chest_role,"iron-plate",16,"trial",true)
        local m=storage.successors.records[growth];assert(m.credit==6)
        stock["iron-plate"]=stock["iron-plate"]+1 -- Synthetic external inventory change.
        c.observe();assert(m.credit==0 and m.attribution_resets==1 and m.trial_collected==16)
        assert(old.valid and new.valid and placements>3)
    ''')


def test_sustained_sampling_gap_does_not_pass_with_two_distant_observations(runtime):
    runtime.execute('''
        flowing_successor();local m=storage.successors.records[growth]
        local first=m.window.first_tick
        game.tick=game.tick+36001;c.observe()
        assert(m.window.first_tick>first and not m.qualification)
    ''')


def test_second_project_and_excessive_trial_collection_are_rejected(runtime):
    runtime.execute('''
        flowing_successor()
        stock["stone-furnace"]=1;stock["burner-mining-drill"]=1;stock["burner-inserter"]=2;stock["wooden-chest"]=1
        stock["copper-ore"]=10
        local e=create("stone-furnace",{x=200,y=160},0,501);e.products_finished=100
        c.entities["recipe:copper-plate"]=e
        local fake={role="growth:copper-plate",ore="copper-ore",item="copper-plate",anchor="cell-site:other",belt_count=3}
        assert(not pcall(c.successor_admit,fake,false))
        produce(190)
        c.transfer(output.chest_role,"iron-plate",200,"trial-full",true)
        assert(not pcall(c.transfer,output.chest_role,"iron-plate",1,"trial-extra",true))
        assert(old.valid and c.entities["recipe:iron-plate"]==old)
    ''')


def test_manual_seed_and_furnace_extraction_are_forbidden_after_route_construction(runtime):
    runtime.execute('''
        built_successor();stock["iron-ore"]=10
        assert(not pcall(c.transfer,growth,"iron-ore",1,"manual",false))
        assert(not pcall(c.transfer,growth,"iron-plate",1,"direct",true))
        assert(not pcall(c.transfer,output.chest_role,"iron-plate",1,"premature",true))
    ''')


def test_reattachment_preserves_paid_identity_receipts_and_base_handlers(runtime):
    runtime.execute('flowing_successor();observer=c.observe;transfer=c.transfer;paid=placements;old_source=old')
    for _ in range(5):
        runtime.execute((LUA / 'successors.lua').read_text())
        runtime.execute('assert(c.observe==observer and c.transfer==transfer and placements==paid and old_source==old)')
    runtime.execute('assert(c.observe().successors.sources[growth].source_unit==new.unit_number)')
