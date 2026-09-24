-- Owned, bounded ore routes. Only fair.place and ordinary paid transfers mutate the game.
local campaign, fair, output = storage.campaign, storage.fair, storage.output_buffers
assert(campaign and fair and output, "Input routes require verified output-buffer support")
local r = storage.input_routes or {protocol = 1, cells = {}, offers = {}, serial = 0}
assert(r.protocol == 1, "Unsupported input-route runtime")
storage.input_routes = r
local ores = {["recipe:iron-plate"] = "iron-ore", ["recipe:copper-plate"] = "copper-ore"}
local max_belts = 64
local vectors = {{x=0,y=-1}, {x=1,y=0}, {x=0,y=1}, {x=-1,y=0}}
local function point(p) return {x=p.x or p[1], y=p.y or p[2]} end
local function center(p) return {x=math.floor(p.x)+0.5, y=math.floor(p.y)+0.5} end
local function same(a,b) return math.abs(a.x-b.x)<0.01 and math.abs(a.y-b.y)<0.01 end
local function key(p) return p.x .. ":" .. p.y end
local function rotate(p, turns)
    p=point(p); for _=1,turns do p={x=-p.y,y=p.x} end; return p
end
local function offset(p,v) return {x=p.x+v.x,y=p.y+v.y} end
local function direction(a,b)
    for index,v in ipairs(vectors) do if same(offset(a,v),b) then return (index-1)*4 end end
    error("Non-adjacent belt cells")
end
local function amount(entity, kind, item)
    local inv=entity.get_inventory(kind); return inv and inv.get_item_count(item) or 0
end
local function held(entity, item)
    local stack=entity.held_stack
    assert(not stack.valid_for_read or stack.name==item or stack.name=="coal", "Foreign inserter item")
    return stack.valid_for_read and stack.name==item and stack.count or 0
end
local function source_for(role)
    local p=fair.actor(); local cell=output.cells[role]; local entity=campaign.entities[role]
    assert(ores[role] and cell and cell.flow and not cell.fault and entity and entity.valid
        and entity==cell.entity and entity.unit_number==cell.source_unit
        and entity.name=="stone-furnace" and entity.force==p.force and entity.surface==p.surface,
        "Input source or commissioned output changed")
    assert(cell.parts.inserter.entity.valid and cell.parts.chest.entity.valid
        and cell.parts.inserter.entity.pickup_target==entity
        and cell.parts.inserter.entity.drop_target==cell.parts.chest.entity,
        "Output topology changed")
    local recipe=entity.get_recipe()
    assert(not recipe or "recipe:"..recipe.name==role, "Input recipe changed")
    return entity,cell
end
local function geometry(cell)
    local source,out=source_for(cell.source)
    assert(source.force.mining_drill_productivity_bonus==0, "Productivity accounting changed")
    assert(source==cell.entity and source.unit_number==cell.source_unit
        and same(source.position,cell.source_position)
        and out.layout==cell.output_layout, "Input source identity changed")
    for _,spec in ipairs(cell.steps) do
        local part=cell.parts[spec.part]
        if part then
            local e=part.entity
            assert(e and e.valid and e.unit_number==part.unit_number and e.name==spec.name
                and campaign.entities[part.role]==e and e.surface==source.surface and e.force==source.force
                and same(e.position,spec.position) and e.direction==spec.direction,
                "Input component identity or orientation changed")
        end
    end
    return source,out
end
local function can_build(source,name,position,dir)
    return source.surface.can_place_entity{name=name,position=position,direction=dir,
        force=source.force,build_check_type=defines.build_check_type.manual}
end
local function clear_layout(cell)
    geometry(cell)
    for _,spec in ipairs(cell.steps) do
        if not cell.parts[spec.part] then
            assert(can_build(cell.entity,spec.name,spec.position,spec.direction), "Input route obstructed")
        end
    end
end
local function path(source,start,finish,drill,arm,budget,cache)
    local left,right=math.min(start.x,finish.x)-3,math.max(start.x,finish.x)+3
    local top,bottom=math.min(start.y,finish.y)-3,math.max(start.y,finish.y)+3
    local function free(p)
        if same(p,arm) or (math.abs(p.x-drill.x)<1.5 and math.abs(p.y-drill.y)<1.5) then return false end
        local k=key(p)
        if cache[k]==nil then
            if budget.left<=0 then return false end
            budget.left=budget.left-1
            -- Do not join or side-load somebody else's belt network.
            cache[k]=can_build(source,"transport-belt",p,0) and #source.surface.find_entities_filtered{
                position=p,radius=1.01,type={"transport-belt","underground-belt","splitter",
                    "loader","loader-1x1","linked-belt"},limit=16}==0
        end
        return cache[k]
    end
    if not free(start) or not free(finish) then return nil end
    local queue,head={{position=start,depth=1}},1
    local seen,previous={[key(start)]=true},{}
    while queue[head] do
        local node=queue[head]; head=head+1
        local p=node.position
        if same(p,finish) then
            local result={p}
            while previous[key(p)] do p=previous[key(p)]; table.insert(result,1,p) end
            return result
        end
        if node.depth<max_belts then
            for _,v in ipairs(vectors) do
                local q=offset(p,v); local k=key(q)
                if not seen[k] and q.x>=left and q.x<=right and q.y>=top and q.y<=bottom then
                    seen[k]=true
                    if free(q) then previous[k]=p; queue[#queue+1]={position=q,depth=node.depth+1} end
                end
            end
        end
    end
end
local function survey(role)
    if campaign.production_input_offer then
        local managed, cell = campaign.production_input_offer(role)
        if managed then return cell end
    end
    if storage.mining_outposts and storage.mining_outposts.cells[ores[role]] then return nil end
    local source,out=source_for(role)
    assert(source.force.mining_drill_productivity_bonus==0, "Productivity accounting is unsupported")
    local dp,ip=prototypes.entity["burner-mining-drill"],prototypes.entity["burner-inserter"]
    assert(dp and dp.tile_width==2 and dp.tile_height==2 and dp.vector_to_place_result
        and ip and ip.inserter_pickup_position and ip.inserter_drop_position, "Unsupported route prototypes")
    local resources=source.surface.find_entities_filtered{name=ores[role],position=source.position,radius=40,limit=128}
    -- No local source means no candidate can exist. Do not enumerate receiver
    -- arms or run placement/path probes for this rejected survey. The observer
    -- retains its existing rejection, resurvey interval and native build guards.
    if #resources==0 then return nil end
    table.sort(resources,function(a,b)
        local da=math.abs(a.position.x-source.position.x)+math.abs(a.position.y-source.position.y)
        local db=math.abs(b.position.x-source.position.x)+math.abs(b.position.y-source.position.y)
        if da~=db then return da<db end
        if a.position.x~=b.position.x then return a.position.x<b.position.x end
        return a.position.y<b.position.y
    end)
    local arms={}; local box=source.bounding_box
    for dx=-3,3 do for dy=-3,3 do for turn=0,3 do
        local p={x=math.floor(source.position.x)+dx+0.5,y=math.floor(source.position.y)+dy+0.5}
        local drop=offset(p,rotate(ip.inserter_drop_position,turn))
        if drop.x>box.left_top.x and drop.x<box.right_bottom.x
            and drop.y>box.left_top.y and drop.y<box.right_bottom.y
            and can_build(source,"burner-inserter",p,turn*4) then
            arms[#arms+1]={position=p,direction=turn*4,pickup=center(offset(p,rotate(ip.inserter_pickup_position,turn)))}
        end
    end end end
    local budget,cache={left=4096},{}
    for index=1,math.min(#resources,8) do
        local resource=resources[index]
        if resource.valid and resource.minable and resource.amount>=100 then
            local drill={x=math.floor(resource.position.x),y=math.floor(resource.position.y)}
            -- Conservative preflight; the actual mining_area is validated after placement.
            local mixed=false
            for _,ore in pairs(source.surface.find_entities_filtered{type="resource",position=drill,radius=3,limit=64}) do
                if ore.name~=ores[role] then mixed=true end
            end
            if not mixed then for turn=0,3 do
                local start=center(offset(drill,rotate(dp.vector_to_place_result,turn)))
                if can_build(source,"burner-mining-drill",drill,turn*4) then
                    for _,arm in ipairs(arms) do
                        local overlap=math.abs(arm.position.x-drill.x)<1.5 and math.abs(arm.position.y-drill.y)<1.5
                        local route=not overlap and path(source,start,arm.pickup,drill,arm.position,budget,cache)
                        if route then
                            local steps={{part="inserter",name="burner-inserter",position=arm.position,direction=arm.direction}}
                            for n=#route,1,-1 do
                                steps[#steps+1]={part="belt:"..n,name="transport-belt",position=route[n],
                                    direction=direction(route[n],route[n+1] or arm.position)}
                            end
                            steps[#steps+1]={part="drill",name="burner-mining-drill",position=drill,direction=turn*4}
                            r.serial=r.serial+1
                            return {source=role,source_unit=source.unit_number,source_position=point(source.position),
                                entity=source,item=string.sub(role,8),ore=ores[role],output_layout=out.layout,
                                layout="input:"..source.unit_number..":"..r.serial,steps=steps,parts={},
                                belt_count=#route,reserve_belts=0}
                        end
                    end
                end
            end end
        end
    end
end
local function parameters(p)
    assert(type(p)=="table" and ores[p.source], "Invalid input command")
    local count=0
    for k,v in pairs(p) do
        if k=="reserve_belts" then
            assert(type(v)=="number" and v%1==0 and v>=0 and v<=200,"Invalid science reserve")
        else
            assert((k=="source" or k=="layout" or k=="part" or k=="receipt")
                and type(v)=="string" and #v>0 and #v<=128,"Invalid input command field")
        end
        count=count+1
    end
    assert(count==5 and p.reserve_belts~=nil,"Missing input command field")
end
local function next_step(cell)
    for _,spec in ipairs(cell.steps) do if not cell.parts[spec.part] then return spec end end
end
local function kit(cell, reserve)
    local need={["transport-belt"]=reserve}
    for _,spec in ipairs(cell.steps) do
        if not cell.parts[spec.part] then need[spec.name]=(need[spec.name] or 0)+1 end
    end
    for name,count in pairs(need) do assert(fair.actor().get_item_count(name)>=count,"Incomplete route kit or science reserve") end
end
campaign.prepare_input_route=function(p)
    parameters(p)
    assert(not storage.mining_outposts or not storage.mining_outposts.cells[ores[p.source]],
        "An ore outpost is already committed")
    local cell=r.cells[p.source] or r.offers[p.source]
    assert(cell and cell.layout==p.layout and not cell.fault,"Stale input layout")
    clear_layout(cell)
    local spec=next_step(cell)
    assert(spec and spec.part==p.part and fair.actor().crafting_queue_size==0,"Input predecessor or crafting conflict")
    if r.cells[p.source] then assert(cell.reserve_belts==p.reserve_belts,"Input science reserve changed") end
    kit(cell,p.reserve_belts)
    cell.reserve_belts=p.reserve_belts
    r.cells[p.source]=cell
    rcon.print(helpers.table_to_json({position=spec.position,name=spec.name}))
end
campaign.build_input_route=function(p)
    parameters(p)
    assert(not storage.mining_outposts or not storage.mining_outposts.cells[ores[p.source]],
        "An ore outpost is already committed")
    local cell=r.cells[p.source]
    assert(cell and cell.layout==p.layout and cell.reserve_belts==p.reserve_belts and not cell.fault,"Unprepared input layout")
    clear_layout(cell); kit(cell,p.reserve_belts)
    local spec=next_step(cell); local player=fair.actor()
    assert(spec and spec.part==p.part and player.crafting_queue_size==0,"Input component exists or predecessor missing")
    local role="input:"..cell.source_unit..":"..spec.part
    assert(not campaign.entities[role],"Input component role occupied")
    local before=player.get_item_count(spec.name)
    fair.place(spec.name,spec.position,spec.direction)
    local entity=player.surface.find_entity(spec.name,spec.position)
    assert(entity and entity.valid and entity.unit_number and player.get_item_count(spec.name)==before-1,"Unpaid input build")
    campaign.entities[role]=entity
    cell.parts[spec.part]={entity=entity,unit_number=entity.unit_number,role=role,receipt=p.receipt,paid=1}
    cell.built_tick=game.tick
    geometry(cell)
    rcon.print(helpers.table_to_json({unit_number=entity.unit_number}))
end
local function topology(cell)
    if next_step(cell) then return false end
    local drill,arm=cell.parts.drill.entity,cell.parts.inserter.entity
    if drill.drop_target~=cell.parts["belt:1"].entity or arm.drop_target~=cell.entity
        or arm.pickup_target~=cell.parts["belt:"..cell.belt_count].entity then return false end
    for n=1,cell.belt_count do
        local belt=cell.parts["belt:"..n].entity
        local neighbours=belt.belt_neighbours
        local expected_in=n>1 and cell.parts["belt:"..(n-1)].entity or nil
        local expected_out=n<cell.belt_count and cell.parts["belt:"..(n+1)].entity or nil
        if #neighbours.inputs~=(expected_in and 1 or 0) or #neighbours.outputs~=(expected_out and 1 or 0)
            or (expected_in and neighbours.inputs[1]~=expected_in)
            or (expected_out and neighbours.outputs[1]~=expected_out) then return false end
    end
    return true
end
local function sample(cell)
    local source,out=geometry(cell)
    if not topology(cell) then return false end
    local drill,arm=cell.parts.drill.entity,cell.parts.inserter.entity
    if not cell.patch then
        cell.patch=source.surface.find_entities_filtered{type="resource",area=drill.mining_area,limit=65}
        assert(#cell.patch>0 and #cell.patch<=64,"Unsupported mining area")
        for _,e in ipairs(cell.patch) do assert(e.name==cell.ore and e.minable,"Mixed resource mining area") end
    end
    if drill.mining_target then
        local found=false
        for _,e in ipairs(cell.patch) do if e==drill.mining_target then found=true end end
        assert(found,"Unobserved mining target")
    end
    local ore_left=0
    for _,e in ipairs(cell.patch) do if e.valid then ore_left=ore_left+e.amount end end
    local belt_items=0
    for n=1,cell.belt_count do for lane=1,2 do
        local line=cell.parts["belt:"..n].entity.get_transport_line(lane)
        local count=line.get_item_count(cell.ore)
        assert(line.get_item_count()==count,"Foreign belt contents")
        belt_items=belt_items+count
    end end
    local furnace_work=amount(source,defines.inventory.furnace_source,cell.ore)+(source.is_crafting() and 1 or 0)
    local pipeline=belt_items+held(arm,cell.ore)+furnace_work
    local chest=out.parts.chest.entity
    local stored=amount(chest,defines.inventory.chest,cell.item)
    local output_total=stored+amount(source,defines.inventory.furnace_result,cell.item)+held(out.parts.inserter.entity,cell.item)
    local produced=source.products_finished
    if cell.flow then return true end
    local previous=cell.previous
    if previous then
        assert(game.tick>=previous.tick and produced>=previous.produced and ore_left<=previous.ore_left,"Input counter regressed")
        assert(pipeline-previous.pipeline+produced-previous.produced==previous.ore_left-ore_left,"Input conservation failed")
        assert(output_total-previous.output_total==produced-previous.produced,"Output conservation failed")
        assert(stored>=previous.stored,"Uncommissioned output removed")
        cell.mined=cell.mined+previous.ore_left-ore_left
        cell.delivered=cell.delivered+furnace_work-previous.furnace_work+produced-previous.produced
        if stored>previous.stored then cell.positive=cell.positive+1 end
        local received=stored-cell.start_stored
        local new_plates=received-cell.initial_work-cell.initial_output
        if game.tick-cell.first_tick>=120 and cell.positive>=3 and cell.mined>=3
            and cell.delivered>=3 and new_plates>=3 then
            cell.flow={layout=cell.layout,source_unit=cell.source_unit,first_tick=cell.first_tick,last_tick=game.tick,
                positive_samples=cell.positive,received=received,mined=cell.mined,delivered=cell.delivered,
                new_plates=new_plates,conservation=true}
        end
    else
        cell.first_tick,cell.positive,cell.mined,cell.delivered=game.tick,0,0,0
        cell.start_stored,cell.initial_work=stored,furnace_work
        cell.initial_output=amount(source,defines.inventory.furnace_result,cell.item)+held(out.parts.inserter.entity,cell.item)
    end
    cell.previous={tick=game.tick,ore_left=ore_left,pipeline=pipeline,produced=produced,stored=stored,furnace_work=furnace_work,output_total=output_total}
    return true
end
local previous_observe=campaign.observe
assert(previous_observe==output.observer or previous_observe==r.observer, "Unexpected input observer owner")
if previous_observe==r.observer then previous_observe=r.previous_observe end
r.previous_observe=previous_observe
r.observer=function()
    local result=previous_observe(); local rows={}; local diagnostics={}
    for _,source in ipairs({"recipe:iron-plate","recipe:copper-plate"}) do
        if storage.mining_outposts and storage.mining_outposts.cells[ores[source]] and not r.cells[source] then
            r.offers[source]=nil
        end
        local cell=r.cells[source] or r.offers[source]
        if cell and not r.cells[source] and not pcall(clear_layout,cell) then cell=nil; r.offers[source]=nil end
        if not cell then
            -- Throttle unsuccessful surveys; cached offers have immutable identities.
            r.last_survey=r.last_survey or {}
            if not r.last_survey[source] or game.tick-r.last_survey[source]>=300 then
                r.last_survey[source]=game.tick
                local ok,offer=pcall(survey,source)
                if ok then cell=offer; r.offers[source]=offer end
            end
        end
        if cell then
            local ok=pcall(geometry,cell)
            if not ok then cell.fault="input_identity_changed" end
            local linked=false
            if not cell.fault and r.cells[source] then
                local valid,value=pcall(sample,cell)
                if valid then linked=value else cell.fault="input_flow_or_identity_mismatch" end
                if not next_step(cell) and not linked and game.tick-(cell.built_tick or 0)>120 then
                    cell.fault="input_topology_changed"
                end
            end
            local parts={}
            for name,p in pairs(cell.parts) do parts[name]={unit_number=p.unit_number,role=p.role,receipt=p.receipt,paid=p.paid} end
            rows[source]={source=source,source_unit=cell.source_unit,item=cell.item,ore=cell.ore,layout=cell.layout,
                steps=cell.steps,parts=parts,reserve_belts=cell.reserve_belts,topology=linked==true,flow=cell.flow or {},
                state=cell.fault and "fault" or (r.cells[source] and (linked and "ready" or "building") or "proposed")}
        end
    end
    for _,role in ipairs({"recipe:iron-plate","recipe:copper-plate"}) do
        if not rows[role] then
            local source=campaign.entities[role];local out=output.cells[role]
            local reason="no_clear_route_within_budget"
            if not source or not source.valid then reason="producer_missing"
            elseif not out or not out.flow or out.fault then reason="output_not_commissioned"
            elseif not source.surface.find_entities_filtered then reason="resource_survey_unavailable"
            elseif #source.surface.find_entities_filtered{name=ores[role],position=source.position,radius=40,limit=1}==0 then
                reason="ore_outside_local_survey" end
            diagnostics[role]={reason=reason,fallback="batched_manual_supply",max_belts=max_belts,survey_radius=40}
        end
    end
    result.input_routes={protocol=1,session_id=storage.jev_session_id,tick=result.tick,sources=rows,diagnostics=diagnostics}
    if campaign.observe_production_sites then result.production_sites=campaign.observe_production_sites() end
    return result
end
campaign.observe=r.observer
local previous_transfer=campaign.transfer
assert(previous_transfer==output.transfer or previous_transfer==r.transfer, "Unexpected input transfer owner")
if previous_transfer==r.transfer then previous_transfer=r.previous_transfer end
r.previous_transfer=previous_transfer
r.transfer=function(role,item,quantity,receipt,extracting)
    for source,cell in pairs(r.cells) do
        assert(not cell.fault,"Input-route reconciliation required")
        geometry(cell)
        for _,part in pairs(cell.parts) do if role==part.role then
            assert(not next_step(cell) and topology(cell) and not extracting and item=="coal"
                and (part==cell.parts.drill or part==cell.parts.inserter),"Invalid route component transfer")
        end end
        if not next_step(cell) then
            if role==source then assert(not extracting and item=="coal","Do not manually feed automated ore") end
            local out=output.cells[source]
            if role==out.chest_role and extracting then assert(cell.flow,"Input flow not commissioned") end
        end
    end
    return previous_transfer(role,item,quantity,receipt,extracting)
end
campaign.transfer=r.transfer
