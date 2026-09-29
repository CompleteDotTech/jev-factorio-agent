-- Fixed read-only native economics projection, never admission or actuation.
-- No runtime callbacks, storage writes, handlers, grants or connector creation.
local out={schema="jev.coal-native-economics.v5",base_version=script.active_mods.base,
    mods=script.active_mods,query_status="unsupported",reason="unqualified",
    epoch={},registry={},connector_routes={},prototypes={},buffer_witnesses={},poles={},supply_surveys={},electric_members={},
    fluid_members={},fuel_targets={},sources={},
    material_scope={status="unavailable",reason="not_observed",closure_complete=false,
        actor_unit=0,actor_items={},crafting_queue=0,owned_stock={}},
    research_work={status="unavailable",reason="no_current_research",technology="",
        progress=0,unit_count=0,cost_multiplier=0,ignore_cost_multiplier=false,
        unit_energy=0,ingredients={},lab={},targets={}},
    manual_cycle={status="unavailable",reason="journal_not_installed",
        journal_asset_sha256="",gathers={},deliveries={}}}
local function need(ok,code) if not ok then error(code,0) end end
local function finite(x,lo,hi)
    return type(x)=="number" and x==x and x>=lo and x<=hi
end
local function integer(x,lo,hi) return finite(x,lo,hi) and x%1==0 end
local function bounded(t,n)
    need(type(t)=="table","table_unavailable")
    local size=0;for _ in pairs(t) do size=size+1;need(size<=n,"survey_bound") end
    return t
end
local function sequence(t,n)
    bounded(t,n);local count=0
    for key in pairs(t) do need(integer(key,1,n),"invalid_array_key");count=count+1 end
    for i=1,count do need(t[i]~=nil,"sparse_array") end
    return t
end
local function text(s)
    return type(s)=="string" and #s>0 and #s<=128 and not s:find("[^ -~]")
end
local function point(p)
    need(p and finite(p.x,-1000000,1000000) and finite(p.y,-1000000,1000000),"invalid_position")
    return {x=p.x,y=p.y}
end
local function box(b) return {left_top=point(b.left_top),right_bottom=point(b.right_bottom)} end
local function overlap(a,b)
    return a.left_top.x<b.right_bottom.x and b.left_top.x<a.right_bottom.x
        and a.left_top.y<b.right_bottom.y and b.left_top.y<a.right_bottom.y
end
local function sorted(t,key) table.sort(t,function(a,b) return a[key]<b[key] end);return t end
local function numbers(t) table.sort(t);return t end
local allowed_loads={["electric-mining-drill"]=true,inserter=true,["assembling-machine-1"]=true,lab=true}
local allowed_poles={["small-electric-pole"]=true,["medium-electric-pole"]=true,
    ["big-electric-pole"]=true,substation=true}
local allowed_fluid={boiler=true,["steam-engine"]=true,pipe=true,["offshore-pump"]=true}
local ok,reason=pcall(function()
    local rt=jev_fle_runtime
    need(rt and rt.campaign and rt.fair and rt.coal_supply,"runtime_unavailable")
    local c,q=rt.campaign,rt.coal_supply
    local actor=rt.agent_characters and rt.agent_characters[1]
    local index=rt.jev_bound_player_index
    need(integer(index,1,1000000) and (rt.jev_player_index or 1)==index,"actor_binding")
    local player=game.get_player(index)
    need(player and player.connected and actor and actor.valid and player.character==actor
        and #game.connected_players==1 and game.connected_players[1]==player
        and not player.cheat_mode and game.speed==1 and not game.tick_paused,"actor_binding")
    need(text(rt.jev_session_id) and script.active_mods.base=="2.0.77","native_version")
    local mod_count=0;for _ in pairs(script.active_mods) do mod_count=mod_count+1 end
    need(mod_count==1,"native_version")
    out.epoch={session_id=rt.jev_session_id,tick=game.tick,actor_index=index,
        actor_unit=actor.unit_number,surface_index=actor.surface.index,force_index=actor.force.index}
    local owned,entities={},{}
    for role,e in pairs(bounded(c.entities,2048)) do
        need(text(role) and e and e.valid and integer(e.unit_number,1,9007199254740991)
            and not owned[e.unit_number] and e.quality.name=="normal"
            and e.surface==actor.surface and e.force==actor.force,"owned_identity")
        owned[e.unit_number]=role;entities[e.unit_number]=e
        out.registry[#out.registry+1]={role=role,unit=e.unit_number,name=e.name,quality=e.quality.name,
            surface_index=e.surface.index,force_index=e.force.index,position=point(e.position),bounds=box(e.bounding_box)}
    end
    -- Ordinary connector parts are paid into their own durable ledger, not the
    -- campaign role registry. Only complete, wholly paid routes may enter this
    -- economic graph. A partial or foreign cell cannot be adopted by proximity.
    local ledger=c.connector_ledger
    need(ledger and ledger.protocol==1 and not ledger.active and type(ledger.routes)=="table",
        "connector_ledger_unavailable")
    local connector_count=0
    for receipt,row in pairs(bounded(ledger.routes,128)) do
        need(type(receipt)=="string" and #receipt==64 and not receipt:find("[^0-9a-f]"),
            "connector_receipt_invalid")
        need(row.id==receipt and row.state=="complete" and row.owned==true and not row.pending
            and row.session_id==rt.jev_session_id and row.actor_unit==actor.unit_number
            and row.surface_index==actor.surface.index and row.force_index==actor.force.index
            and (row.kind=="pipe" or row.kind=="small-electric-pole")
            and type(row.cells)=="table" and #row.cells>=1 and #row.cells<=1200
            and row.paid==#row.cells and row.external==0,
            "connector_route_unqualified")
        local source,target=c.entities[row.source],c.entities[row.target]
        need(source and target and source.valid and target.valid
            and source.unit_number==row.source_unit and target.unit_number==row.target_unit,
            "connector_endpoint_changed")
        out.connector_routes[#out.connector_routes+1]={id=receipt,source=row.source,target=row.target,
            source_unit=row.source_unit,target_unit=row.target_unit,kind=row.kind,fluid=row.fluid,
            actor_unit=row.actor_unit,session_id=row.session_id,surface_index=row.surface_index,
            force_index=row.force_index,state=row.state,owned=row.owned,paid=row.paid,
            cell_count=#row.cells}
        for index,cell in ipairs(sequence(row.cells,1200)) do
            connector_count=connector_count+1;need(connector_count<=128,"survey_bound")
            local position=point(cell.position)
            local e=actor.surface.find_entity(row.kind,position)
            need(cell.paid==1 and not cell.external and not cell.pending
                and e and e.valid and e.force==actor.force and e.quality.name=="normal"
                and e.position.x==position.x and e.position.y==position.y
                and e.unit_number==cell.unit_number and not owned[e.unit_number],
                "connector_payment_changed")
            local role="connector:"..receipt..":"..index
            need(#out.registry<2048,"survey_bound")
            owned[e.unit_number]=role;entities[e.unit_number]=e
            out.registry[#out.registry+1]={role=role,unit=e.unit_number,name=e.name,quality=e.quality.name,
                surface_index=e.surface.index,force_index=e.force.index,position=point(e.position),bounds=box(e.bounding_box)}
        end
    end
    sorted(out.registry,"role");sorted(out.connector_routes,"id")
    -- An omitted prototype capacity reads as zero on this engine. Price future
    -- drills/inserters from actual owned, normal-quality instances instead.
    -- Base-only treatment and the bounded registry make this a narrow witness,
    -- never an adopted graph member or a reason to place a new entity.
    for _,name in ipairs({"electric-mining-drill","inserter"}) do
        local witness
        for _,row in ipairs(out.registry) do if row.name==name then
            local e=entities[row.unit]
            local capacity=e.electric_buffer_size
            need(finite(capacity,0,1000000),"buffer_witness_unavailable")
            if witness then need(capacity==witness.capacity,"buffer_witness_mismatch")
            else witness={name=name,unit=row.unit,capacity=capacity} end
        end end
        need(witness,"buffer_witness_unavailable")
        out.buffer_witnesses[#out.buffer_witnesses+1]=witness
    end
    sorted(out.buffer_witnesses,"name")
    local function own(e)
        need(e and e.valid and e.unit_number and entities[e.unit_number]==e,"foreign_graph_member")
        return e.unit_number
    end
    local status_names={}
    for name,value in pairs(defines.entity_status) do status_names[value]=name end
    local function operation(e,supply)
        local status=status_names[e.status]
        local row={active=e.active,status=status or "unknown",control_behavior_present=e.get_control_behavior()~=nil}
        need(row.active==true and not row.control_behavior_present,"inactive_or_controlled_member")
        need(status and status~="disabled" and status~="disabled_by_script"
            and status~="disabled_by_control_behavior" and status~="marked_for_deconstruction"
            and status~="frozen" and status~="broken" and status~="pipeline_overextended","unsupported_operating_status")
        if supply then need(status=="normal" or status=="working" or status=="full_output","unavailable_power_supply") end
        return row
    end
    local names={"electric-mining-drill","inserter","assembling-machine-1","lab","boiler",
        "steam-engine","stone-furnace","offshore-pump"}
    for _,name in ipairs(names) do
        local p=prototypes.entity[name];need(p,"prototype_unavailable")
        local ep=p.electric_energy_source_prototype
        local row={name=name,max_usage=p.get_max_energy_usage("normal"),
            max_production=p.get_max_energy_production("normal"),
            source=ep and "electric" or p.burner_prototype and "burner"
                or p.void_energy_source_prototype and "void" or "unsupported",
            drain=ep and ep.drain or 0,buffer_capacity=ep and ep.buffer_capacity or 0}
        if p.burner_prototype then row.burner_efficiency=p.burner_prototype.effectivity end
        if name=="boiler" then row.target_temperature=p.target_temperature;row.boiler_mode=p.boiler_mode end
        if name=="steam-engine" then row.generator_efficiency=p.effectivity
            row.fluid_usage_per_tick=p.get_fluid_usage_per_tick("normal")
            row.maximum_temperature=p.maximum_temperature end
        if name=="electric-mining-drill" then row.mining_speed=p.mining_speed end
        out.prototypes[#out.prototypes+1]=row
    end
    sorted(out.prototypes,"name")
    out.coal_fuel_joules=prototypes.item.coal.fuel_value
    out.fluid_prototypes={}
    for _,name in ipairs({"steam","water"}) do local p=prototypes.fluid[name]
        out.fluid_prototypes[#out.fluid_prototypes+1]={name=name,heat_capacity=p.heat_capacity,
            default_temperature=p.default_temperature,max_temperature=p.max_temperature} end
    local max_supply=0
    out.pole_prototypes={}
    for name in pairs(allowed_poles) do
        local radius=prototypes.entity[name].get_supply_area_distance("normal")
        need(finite(radius,0,18),"unsupported_supply_radius")
        max_supply=math.max(max_supply,radius)
        out.pole_prototypes[#out.pole_prototypes+1]={name=name,supply_radius=radius}
    end
    sorted(out.pole_prototypes,"name")
    local pole_entities,pole_rows,queue={},{},{}
    local function add_pole(e)
        local unit=own(e)
        need(e.type=="electric-pole" and allowed_poles[e.name],"unsupported_pole")
        if not pole_entities[unit] then
            need(#queue<32,"survey_bound");pole_entities[unit]=e;queue[#queue+1]=e
        end
    end
    local function coverage(bounds,label)
        local nearby=actor.surface.find_entities_filtered{area={
            {bounds.left_top.x-max_supply,bounds.left_top.y-max_supply},
            {bounds.right_bottom.x+max_supply,bounds.right_bottom.y+max_supply}},type="electric-pole",limit=65}
        need(#nearby<=64,"survey_bound")
        local units={}
        for _,p in ipairs(nearby) do
            local radius=p.prototype.get_supply_area_distance(p.quality)
            local area={left_top={x=p.position.x-radius,y=p.position.y-radius},
                right_bottom={x=p.position.x+radius,y=p.position.y+radius}}
            if overlap(bounds,area) then add_pole(p);units[#units+1]=p.unit_number end
        end
        need(#units>0,"missing_power_coverage")
        out.supply_surveys[#out.supply_surveys+1]={kind="coverage",key=label,bounds=bounds,poles=numbers(units)}
    end
    need(q.revision==4 and not q.committed,"unsupported_coal_state")
    local targets={};for _,name in ipairs(sequence(q.targets,4)) do targets[#targets+1]=name end
    need(#targets>=2 and #targets<=4,"unsupported_coal_bundle");table.sort(targets)
    local target_set={};for _,name in ipairs(targets) do need(not target_set[name],"aliased_target");target_set[name]=true end
    for name in pairs(bounded(q.rows,4)) do need(target_set[name],"foreign_coal_proposal") end
    local boiler=c.entities["utility:boiler"]
    need(boiler and boiler.name=="boiler","owned_boiler_missing");own(boiler)
    local boiler_selected=false
    for _,target in ipairs(targets) do
        local row=q.rows[target];local e=c.entities[target]
        need(row and not row.pending and not row.manual_pending and not row.fault
            and next(bounded(row.parts,2))==nil and e and own(e)==row.target.unit_number
            and (e.name=="boiler" or e.name=="stone-furnace"),"unsupported_fuel_target")
        if e==boiler then boiler_selected=true end
        local b=e.burner;need(b,"unsupported_fuel_target")
        local fuel=e.get_fuel_inventory();need(fuel and fuel.valid and #fuel<=2,"fuel_inventory")
        local coal=0
        for i=1,#fuel do local s=fuel[i];if s.valid_for_read then
            need(s.name=="coal" and s.quality.name=="normal" and integer(s.count,1,200),"unsupported_fuel")
            coal=coal+s.count end end
        local burning=b.currently_burning
        local burning_name=burning and (type(burning.name)=="string" and burning.name or burning.name.name) or ""
        need(burning_name=="" or burning_name=="coal","unsupported_fuel")
        need(not burning or not burning.quality or burning.quality.name=="normal","unsupported_fuel")
        out.fuel_targets[#out.fuel_targets+1]={role=target,unit=e.unit_number,name=e.name,coal=coal,
            burning=burning_name,remaining_burning_fuel=b.remaining_burning_fuel,heat=b.heat,
            operation=operation(e,e.name=="boiler")}
        local source={target=target,target_unit=e.unit_number,layout=row.layout,mining_area=box(row.mining_area),
            steps={},corridor={},resources={},neighbor_drills={}}
        local function step(s) return {part=s.part,name=s.name,position=point(s.position),direction=s.direction} end
        for _,s in ipairs(sequence(row.steps,2)) do source.steps[#source.steps+1]=step(s) end
        for _,s in ipairs(sequence(row.corridor,26)) do source.corridor[#source.corridor+1]=step(s) end
        source.drill_bounds=box(row.drill_bounds);source.chest_bounds=box(row.chest_bounds)
        coverage(source.drill_bounds,target..":drill")
        for _,s in ipairs(source.corridor) do if s.name=="inserter" then
            local p=s.position;coverage({left_top={x=p.x-.15,y=p.y-.15},right_bottom={x=p.x+.15,y=p.y+.15}},target..":"..s.part)
        end end
        local area=source.mining_area
        local resources=actor.surface.find_entities_filtered{area=area,type="resource",limit=65}
        need(#resources>0 and #resources<=64,"source_resource_bound")
        local retained={};for _,r in ipairs(sequence(row.resources,64)) do if r.valid then retained[r]=true end end
        for _,r in ipairs(resources) do
            local p=r.prototype;local m=p.mineable_properties
            need(retained[r] and r.name=="coal" and not p.infinite_resource and p.resource_category=="basic-solid"
                and m and m.minable and not m.required_fluid and #m.products==1,"unsupported_source")
            local product=m.products[1]
            need(product.type=="item" and product.name=="coal" and product.amount==1
                and (product.probability or 1)==1 and not product.amount_min and not product.amount_max,"unsupported_source")
            retained[r]=nil;source.resources[#source.resources+1]={name=r.name,position=point(r.position),
                amount=r.amount,mining_time=m.mining_time}
        end
        need(next(retained)==nil,"source_reference_changed")
        table.sort(source.resources,function(a,b) return a.position.x==b.position.x and a.position.y<b.position.y or a.position.x<b.position.x end)
        local drills=actor.surface.find_entities_filtered{area={{area.left_top.x-10,area.left_top.y-10},
            {area.right_bottom.x+10,area.right_bottom.y+10}},type="mining-drill",limit=65}
        need(#drills<=64,"survey_bound")
        for _,d in ipairs(drills) do need(d.mining_area,"unknown_mining_area")
            local bounds=box(d.mining_area);need(not overlap(area,bounds),"foreign_source_drill")
            source.neighbor_drills[#source.neighbor_drills+1]={unit=d.unit_number,mining_area=bounds} end
        sorted(source.neighbor_drills,"unit");out.sources[#out.sources+1]=source
    end
    need(boiler_selected,"owned_boiler_not_target")
    -- The opt-in journal is read in this same RPC as burner/research and graph
    -- facts. Rows remain diagnostic; prior burns cannot prove future demand.
    local journal=rt.coal_manual_journal_v1
    if journal then
        local installation=rt.native_installation
        need(installation and installation.profile==
            "e759-observation-v2-water-origin-v4-manual-cycle-v5"
            and installation.callbacks and installation.callbacks.journal_tick==journal.tick_handler
            and installation.assets and type(installation.assets.coal_manual_journal_v1)=="string"
            and journal.protocol==1 and journal.pending==nil
            and journal.session_id==rt.jev_session_id and journal.actor_index==index
            and journal.actor_unit==actor.unit_number
            and journal.surface_index==actor.surface.index and journal.force_index==actor.force.index,
            "manual_journal_unqualified")
        local gathers={};sequence(journal.order,64);bounded(journal.rows,64)
        local row_count=0;for _ in pairs(journal.rows) do row_count=row_count+1 end
        need(#journal.order==row_count,"manual_journal_bound")
        local seen={}
        for _,receipt in ipairs(journal.order) do
            need(text(receipt) and not seen[receipt],"manual_journal_receipt")
            seen[receipt]=true
            local row=journal.rows[receipt]
            need(row and row.receipt==receipt and row.status=="complete"
                and row.pending==false and row.overflow==false and row.fault==false
                and row.session_id==rt.jev_session_id and row.actor_index==index
                and row.actor_unit==actor.unit_number and row.surface_index==actor.surface.index
                and row.force_index==actor.force.index and row.resource=="coal",
                "manual_journal_incomplete")
            gathers[#gathers+1]={receipt=receipt,started_tick=row.started_tick,
                finished_tick=row.finished_tick,coal_before=row.coal_before,
                coal_after=row.coal_after,walking_ticks=row.walking_ticks,
                mining_ticks=row.mining_ticks}
        end
        local deliveries={}
        sequence(c.receipt_order,128);bounded(c.receipts,128)
        for _,receipt in ipairs(c.receipt_order) do
            local row=c.receipts[receipt]
            if row and row.item=="coal" and row.extracting==false and q.rows[row.role] then
                deliveries[#deliveries+1]={receipt=receipt,role=row.role,
                    unit=row.unit_number,coal=row.quantity,tick=row.tick}
            end
        end
        need(#deliveries<=128,"manual_delivery_bound")
        out.manual_cycle={status="observed",reason="qualified_journal_rows",
            journal_asset_sha256=installation.assets.coal_manual_journal_v1,
            gathers=gathers,deliveries=deliveries}
    end
    -- The actor and the named fuel targets are observed in the SAME RPC as the
    -- graph. This is deliberately a partial scope: unregistered stock, belts,
    -- other owned buffers and future alternative supplies are not closed.
    local function stock_rows(inv)
        need(inv and inv.valid,"material_inventory_unavailable")
        local values={}
        for _,stack in pairs(inv.get_contents()) do
            need(text(stack.name) and integer(stack.count,1,200000)
                and (not stack.quality or stack.quality=="normal"
                    or stack.quality.name=="normal"),"material_inventory_unsupported")
            values[#values+1]={name=stack.name,count=stack.count}
            need(#values<=128,"material_inventory_bound")
        end
        sorted(values,"name")
        for i=2,#values do need(values[i-1].name~=values[i].name,"material_inventory_alias") end
        return values
    end
    need(integer(player.crafting_queue_size,0,1000),"material_crafting_queue")
    local stock={}
    for target in pairs(q.rows) do
        local e=c.entities[target]
        need(e and e.valid and owned[e.unit_number]==target
            and (e.name=="stone-furnace" or e.name=="boiler"),"material_target_unowned")
        local output={}
        if e.name=="stone-furnace" then output=stock_rows(e.get_output_inventory()) end
        stock[#stock+1]={role=target,unit=e.unit_number,
            fuel=stock_rows(e.get_fuel_inventory()),output=output}
        need(#stock<=4,"material_target_bound")
    end
    sorted(stock,"role")
    out.material_scope={status="observed",reason="partial_actor_and_fuel_targets",
        closure_complete=false,actor_unit=actor.unit_number,
        actor_items=stock_rows(player.get_main_inventory()),
        crafting_queue=player.crafting_queue_size,owned_stock=stock}
    -- Current demand facts are projected in the SAME RPC as the owned graph.
    -- This only observes activity; it does not infer a future coal lower bound.
    local tech=actor.force.current_research
    if tech then
        local lab=c.entities["utility:lab"]
        local progress=actor.force.research_progress
        need(text(tech.name) and finite(progress,0,1),"research_identity")
        if not lab or not lab.valid or lab.name~="lab"
            or owned[lab.unit_number]~="utility:lab" then
            out.research_work={status="unavailable",reason="research_lab_unowned",
                technology=tech.name,progress=progress,unit_count=0,
                cost_multiplier=0,ignore_cost_multiplier=false,
                unit_energy=0,ingredients={},lab={},targets={}}
        else
        local targets={}
        local function bill_rows(items,allow_untyped)
            sequence(items,8)
            need(#items>=1,"research_bill_empty")
            local values={}
            for _,item in ipairs(items) do
                need(item.type=="item" or allow_untyped and item.type==nil,
                    "research_bill_kind")
                need(text(item.name) and integer(item.amount,1,1000)
                    and (item.probability==nil or item.probability==1)
                    and (item.independent_probability==nil
                        or item.independent_probability==1)
                    and item.shared_probability==nil
                    and (item.extra_count_fraction==nil or item.extra_count_fraction==0)
                    and item.percent_spoiled==nil and item.amount_min==nil
                    and item.amount_max==nil and item.quality_min==nil
                    and item.quality_max==nil and item.quality_change==nil,
                    "research_bill_unsupported")
                values[#values+1]={name=item.name,amount=item.amount}
            end
            sorted(values,"name")
            for i=2,#values do need(values[i-1].name~=values[i].name,
                "research_bill_alias") end
            return values
        end
        need(integer(tech.research_unit_count,1,1000000)
            and finite(tech.research_unit_energy,.001,100000),"research_unit_count")
        local multiplier=game.difficulty_settings and game.difficulty_settings.technology_price_multiplier
        local ignore=tech.prototype and tech.prototype.ignore_tech_cost_multiplier
        need(finite(multiplier,.001,100000) and type(ignore)=="boolean",
            "research_cost_setting")
        local ingredients=bill_rows(tech.research_unit_ingredients,true)
        for target in pairs(q.rows) do
            local e=c.entities[target]
            if e and e.name=="stone-furnace" then
                need(owned[e.unit_number]==target and e.burner,"research_furnace_unowned")
                local recipe=e.get_recipe()
                local burning=e.burner.currently_burning
                local burning_name=burning and (type(burning.name)=="string" and burning.name
                    or burning.name.name) or ""
                need(burning_name=="" or burning_name=="coal","research_fuel_unsupported")
                need(finite(e.crafting_speed,.001,1000),"research_crafting_speed")
                need(not recipe or finite(recipe.energy,.001,100000),"research_recipe_energy")
                targets[#targets+1]={role=target,unit=e.unit_number,
                    recipe=recipe and recipe.name or "",crafting=e.is_crafting(),
                    crafting_progress=e.crafting_progress or 0,burning=burning_name,
                    crafting_speed=e.crafting_speed,recipe_energy=recipe and recipe.energy or 0,
                    input=stock_rows(e.get_inventory(defines.inventory.furnace_source)),
                    recipe_ingredients=recipe and bill_rows(recipe.ingredients,false) or {},
                    recipe_products=recipe and bill_rows(recipe.products,false) or {}}
                need(#targets<=3,"research_target_bound")
            end
        end
        sorted(targets,"role")
        out.research_work={status="observed",reason="current_research_activity",
            technology=tech.name,progress=progress,unit_count=tech.research_unit_count,
            cost_multiplier=multiplier,ignore_cost_multiplier=ignore,
            unit_energy=tech.research_unit_energy,ingredients=ingredients,
            lab={role="utility:lab",unit=lab.unit_number,
                input=stock_rows(lab.get_inventory(defines.inventory.lab_input))},targets=targets}
        end
    end
    local cursor=1;local edge_count=0;local electric={}
    while cursor<=#queue do
        local p=queue[cursor];cursor=cursor+1;local unit=p.unit_number
        local row={unit=unit,network_id=p.electric_network_id,supply_radius=p.prototype.get_supply_area_distance("normal"),neighbors={}}
        need(integer(row.network_id,1,9007199254740991),"unpowered_pole")
        local connector=p.get_wire_connector(defines.wire_connector_id.pole_copper,false)
        need(connector and connector.valid and not connector.is_ghost,"copper_connector_missing")
        for _,wire in ipairs(connector.real_connections) do
            edge_count=edge_count+1;need(edge_count<=128,"survey_bound")
            local other=wire.target.owner;add_pole(other);row.neighbors[#row.neighbors+1]=other.unit_number
        end
        numbers(row.neighbors);out.poles[#out.poles+1]=row;pole_rows[unit]=row
        local radius=row.supply_radius;local bounds={left_top={x=p.position.x-radius,y=p.position.y-radius},
            right_bottom={x=p.position.x+radius,y=p.position.y+radius}}
        local found=actor.surface.find_entities_filtered{area=bounds,limit=129}
        need(#found<=128,"survey_bound")
        local members={}
        for _,e in ipairs(found) do if e.prototype.electric_energy_source_prototype then
            local id=own(e);need(allowed_loads[e.name] or e.name=="steam-engine","unsupported_electric_member")
            if not electric[id] then
                local n=0;for _ in pairs(electric) do n=n+1 end;need(n<64,"survey_bound")
                electric[id]=e;coverage(box(e.bounding_box),"unit:"..id)
            end
            members[#members+1]=id
        end end
        out.supply_surveys[#out.supply_surveys+1]={kind="supply",key=tostring(unit),bounds=bounds,members=numbers(members)}
    end
    sorted(out.poles,"unit")
    local network=out.poles[1].network_id;local engines={}
    for unit,e in pairs(electric) do
        need(e.electric_network_id==network,"overlapping_or_split_power")
        local mods=e.get_module_inventory()
        need(not mods or mods.is_empty(),"unsupported_module_effects")
        need(e.consumption_bonus==0 and e.productivity_bonus==0,"unsupported_module_effects")
        out.electric_members[#out.electric_members+1]={unit=unit,network_id=e.electric_network_id,
            buffer_capacity=e.electric_buffer_size,energy=e.energy,drain=e.electric_drain,
            operation=operation(e,e.name=="steam-engine")}
        if e.name=="steam-engine" then engines[unit]=true end
    end
    sorted(out.electric_members,"unit")
    local fluid_queue={boiler};local fluid_seen={[boiler.unit_number]=true};local fi=1;local links=0
    while fi<=#fluid_queue do
        local e=fluid_queue[fi];fi=fi+1;own(e)
        need(allowed_fluid[e.name],"unsupported_fluid_member")
        local row={unit=e.unit_number,boxes={},operation=e.name=="pipe" and {kind="passive"} or operation(e,true)}
        local boxes=e.fluidbox
        need(#boxes>0 and #boxes<=8,"unsupported_fluidbox")
        for i=1,#boxes do
            local fluid=boxes[i];local proto=boxes.get_prototype(i)
            local filter=proto.filter
            local f={index=i,segment_id=boxes.get_fluid_segment_id(i),capacity=boxes.get_capacity(i),
                filter=filter and filter.name or "",fluid=fluid and fluid.name or "",
                amount=fluid and fluid.amount or 0,temperature=fluid and fluid.temperature or 15,connections={}}
            for _,connection in ipairs(boxes.get_pipe_connections(i)) do if connection.target then
                links=links+1;need(links<=128,"survey_bound")
                local other=connection.target.owner;local unit=own(other)
                need(allowed_fluid[other.name],"unsupported_fluid_member")
                f.connections[#f.connections+1]={unit=unit,index=connection.target_fluidbox_index}
                if not fluid_seen[unit] then need(#fluid_queue<64,"survey_bound")
                    fluid_seen[unit]=true;fluid_queue[#fluid_queue+1]=other end
            end end
            table.sort(f.connections,function(a,b) return a.unit==b.unit and a.index<b.index or a.unit<b.unit end)
            row.boxes[#row.boxes+1]=f
        end
        out.fluid_members[#out.fluid_members+1]=row
    end
    for unit in pairs(engines) do need(fluid_seen[unit],"generator_fluid_disconnected") end
    sorted(out.fluid_members,"unit")
    table.sort(out.supply_surveys,function(a,b) return a.kind==b.kind and a.key<b.key or a.kind<b.kind end)
    out.query_status="observed";out.reason="bounded_native_projection"
end)
if not ok then
    -- Keep failed partial rows for private diagnostics, never a usable witness.
    out.reason=type(reason)=="string" and reason:match("^[a-z_]+$") and reason or "native_api_unsupported"
end
local encoded=helpers.table_to_json(out)
if #encoded>262144 then
    encoded=helpers.table_to_json{schema=out.schema,query_status="unsupported",reason="response_bound"}
end
rcon.print(encoded)
