-- Read-only joint production-cell survey; paid native builders remain authoritative.
local campaign, fair = storage.campaign, storage.fair
assert(campaign and fair and storage.input_routes, "Production sites require input-route support")
local sites = storage.production_sites or {protocol=1, offers={}, owned={}, checked={}, reasons={}}
assert(sites.protocol==1, "Unsupported production-site runtime")
storage.production_sites=sites
local ores={["recipe:iron-plate"]="iron-ore",["recipe:copper-plate"]="copper-ore"}
local vectors={{x=0,y=-1},{x=1,y=0},{x=0,y=1},{x=-1,y=0}}
local function point(p) return {x=p.x or p[1],y=p.y or p[2]} end
local function add(a,b) return {x=a.x+b.x,y=a.y+b.y} end
local function rotate(p,n) p=point(p);for _=1,n do p={x=-p.y,y=p.x} end;return p end
local function center(p) return {x=math.floor(p.x)+0.5,y=math.floor(p.y)+0.5} end
local function same(a,b) return math.abs(a.x-b.x)<0.01 and math.abs(a.y-b.y)<0.01 end
local function distance(a,b) return math.abs(a.x-b.x)+math.abs(a.y-b.y) end
local function spec(name,position,direction,part)
    return {name=name,position=position,direction=direction or 0,part=part}
end
local function extent(name,direction)
    local p=prototypes.entity[name]
    local w,h=p and p.tile_width or 1,p and p.tile_height or 1
    if direction==4 or direction==12 then w,h=h,w end
    return w/2-0.01,h/2-0.01
end
local function overlaps(a,b)
    local ax,ay=extent(a.name,a.direction);local bx,by=extent(b.name,b.direction)
    return math.abs(a.position.x-b.position.x)<ax+bx and math.abs(a.position.y-b.position.y)<ay+by
end
local function conflicts(candidate,parts)
    for _,other in ipairs(parts) do if overlaps(candidate,other) then return true end end
    return false
end
local function clear(candidate,player)
    return player.surface.can_place_entity{name=candidate.name,position=candidate.position,
        direction=candidate.direction,force=player.force,build_check_type=defines.build_check_type.manual}
end
local function reservations(except)
    local result={}
    for role,site in pairs(sites.owned) do if role~=except then
        for _,part in ipairs(site.specs) do result[#result+1]=part end
    end end
    for role,site in pairs(sites.offers) do if role~=except and not sites.owned[role] then
        for _,part in ipairs(site.specs) do result[#result+1]=part end
    end end
    return result
end
campaign.production_reserved=function(name,position,direction)
    return conflicts(spec(name,position,direction),reservations())
end
local function arms(furnace,drill,output)
    local prototype=prototypes.entity["burner-inserter"];local result={}
    for dx=-3,3 do for dy=-3,3 do for turn=0,3 do
        local p={x=furnace.x+dx+0.5,y=furnace.y+dy+0.5}
        local pickup=add(p,rotate(prototype.inserter_pickup_position,turn))
        local drop=add(p,rotate(prototype.inserter_drop_position,turn))
        local inside=output and pickup or drop
        if math.abs(inside.x-furnace.x)<0.9 and math.abs(inside.y-furnace.y)<0.9 then
            result[#result+1]={arm=spec("burner-inserter",p,turn*4,output and "output-arm" or "inserter"),
                              end_position=center(output and drop or pickup)}
        end
    end end end
    table.sort(result,function(a,b)
        local da,db=distance(a.arm.position,drill),distance(b.arm.position,drill)
        if da~=db then return output and da>db or not output and da<db end
        if a.arm.position.x~=b.arm.position.x then return a.arm.position.x<b.arm.position.x end
        if a.arm.position.y~=b.arm.position.y then return a.arm.position.y<b.arm.position.y end
        return a.arm.direction<b.arm.direction
    end)
    return result
end
local function line(start,finish,x_first)
    local result={point(start)};local p=point(start)
    for _,axis in ipairs(x_first and {"x","y"} or {"y","x"}) do
        while p[axis]~=finish[axis] do
            if #result>=64 then return nil end
            p=point(p);p[axis]=p[axis]+(finish[axis]>p[axis] and 1 or -1)
            result[#result+1]=p
        end
    end
    return result
end
local function direction(a,b)
    for n,v in ipairs(vectors) do if same(add(a,v),b) then return (n-1)*4 end end
    error("Nonadjacent production route")
end
local function survey(role)
    local player=fair.actor();local ore=ores[role]
    local fp,dp,ip=prototypes.entity["stone-furnace"],prototypes.entity["burner-mining-drill"],prototypes.entity["burner-inserter"]
    if not (fp and fp.tile_width==2 and fp.tile_height==2 and dp and dp.tile_width==2
        and dp.tile_height==2 and dp.vector_to_place_result and ip
        and ip.inserter_pickup_position and ip.inserter_drop_position) then return nil,"unsupported_prototypes" end
    local radius=campaign.exploration_radius or 8
    assert(type(radius)=="number" and radius%1==0 and radius>=1 and radius<=32,"Invalid generated-area bound")
    local resources=player.surface.find_entities_filtered{name=ore,position={x=0,y=0},radius=radius*32,limit=128}
    table.sort(resources,function(a,b)
        local da,db=distance(a.position,player.position),distance(b.position,player.position)
        if da~=db then return da<db end
        if a.position.x~=b.position.x then return a.position.x<b.position.x end
        return a.position.y<b.position.y
    end)
    if #resources==0 then return nil,"no_observed_ore_in_generated_area" end
    local budget,cache,reserved=4096,{},reservations(role)
    local function free(part)
        if conflicts(part,reserved) then return false end
        local key=part.name..":"..part.position.x..":"..part.position.y..":"..part.direction
        if cache[key]==nil then
            if budget<=0 then return false end
            budget=budget-1;cache[key]=clear(part,player)
            if part.name=="transport-belt" and cache[key] then
                cache[key]=#player.surface.find_entities_filtered{position=part.position,radius=1.01,
                    type={"transport-belt","underground-belt","splitter","loader","loader-1x1","linked-belt"},limit=16}==0
            end
        end
        return cache[key]
    end
    for index=1,math.min(8,#resources) do
        local resource=resources[index]
        if resource.valid and resource.minable and resource.amount>=100 then
            local d={x=math.floor(resource.position.x),y=math.floor(resource.position.y)}
            local mixed=false
            for _,r in pairs(player.surface.find_entities_filtered{type="resource",position=d,radius=3,limit=64}) do
                if r.name~=ore then mixed=true end
            end
            if not mixed then for turn=0,3 do
                local drill=spec("burner-mining-drill",d,turn*4,"drill")
                local start=center(add(d,rotate(dp.vector_to_place_result,turn)))
                if free(drill) then for _,span in ipairs({8,12,16}) do for _,v in ipairs(vectors) do
                    local f={x=d.x+span*v.x,y=d.y+span*v.y}
                    local furnace=spec("stone-furnace",f,0,"furnace")
                    if free(furnace) then
                        local incoming,outgoing=arms(f,d,false),arms(f,d,true)
                        for ai=1,math.min(4,#incoming) do for ao=1,math.min(4,#outgoing) do
                            local input,output=incoming[ai],outgoing[ao]
                            local chest=spec("wooden-chest",output.end_position,0,"output-chest")
                            local parts={furnace,drill};local legal=true
                            for _,part in ipairs({input.arm,output.arm,chest}) do
                                if not free(part) or conflicts(part,parts) then legal=false;break end
                                parts[#parts+1]=part
                            end
                            if legal then for _,x_first in ipairs({true,false}) do
                                local path=line(start,input.end_position,x_first)
                                local belts={};local ok=path~=nil
                                if path then for n,p in ipairs(path) do
                                    local belt=spec("transport-belt",p,direction(p,path[n+1] or input.arm.position),"belt:"..n)
                                    if not free(belt) or conflicts(belt,parts) then ok=false;break end
                                    belts[#belts+1]=belt
                                end end
                                if ok then
                                    -- Local standing clearance is not a proof of a complete native path.
                                    local access=false
                                    for _,delta in ipairs(vectors) do
                                        local p={x=f.x+delta.x*3,y=f.y+delta.y*3}
                                        local actor=spec("character",p,0)
                                        if not conflicts(actor,parts) and not conflicts(actor,belts) and free(actor) then access=true;break end
                                    end
                                    if access then
                                        local steps={input.arm}
                                        for n=#belts,1,-1 do steps[#steps+1]=belts[n];parts[#parts+1]=belts[n] end
                                        steps[#steps+1]=drill
                                        sites.serial=(sites.serial or 0)+1
                                        local anchor=string.format("cell-site:%s:%d:%d:%d:%d",ore,f.x,f.y,turn,sites.serial)
                                        return {role=role,ore=ore,item=string.sub(role,8),anchor=anchor,position=f,
                                            surface=player.surface,force=player.force,resource=resource,
                                            resource_position=point(resource.position),specs=parts,input_steps=steps,
                                            output_arm=output.arm,chest=chest,belt_count=#belts,
                                            checks=4096-budget},"joint_layout_available"
                                    end
                                end
                            end end
                        end end
                    end
                    if budget<=0 then return nil,"survey_budget_exhausted" end
                end end end
            end end
        end
    end
    return nil,"no_clear_joint_layout"
end
local function valid(site,owned)
    local player=fair.actor()
    assert(site.surface==player.surface and site.force==player.force,"Production site surface or force changed")
    if owned then
        local entity=campaign.entities[site.role]
        assert(entity and entity.valid and entity==site.entity and entity.unit_number==site.source_unit
            and entity.name=="stone-furnace" and same(entity.position,site.position),"Production source identity changed")
    else
        assert(not campaign.entities[site.role],"Production role already occupied")
        assert(site.resource.valid and site.resource.minable and site.resource.name==site.ore
            and same(site.resource.position,site.resource_position) and site.resource.amount>=100,"Production ore target changed")
        for _,part in ipairs(site.specs) do assert(clear(part,player),"Production site obstructed") end
    end
    return player
end
campaign.observe_production_sites=function()
    local rows={}
    for _,role in ipairs({"recipe:iron-plate","recipe:copper-plate"}) do
        local site=sites.owned[role];local state,reason="rejected",sites.reasons[role]
        if site then
            local ok=pcall(valid,site,true);state=ok and "owned" or "fault"
            reason=ok and (sites.reasons[role] or "joint_layout_owned") or "production_source_identity_changed"
        elseif campaign.entities[role] then
            sites.offers[role]=nil
            reason="existing_manual_cell"
        else
            site=sites.offers[role]
            if site and not pcall(valid,site,false) then site=nil;sites.offers[role]=nil end
            if not site and (not sites.checked[role] or game.tick-sites.checked[role]>=300) then
                sites.checked[role]=game.tick
                local ok,value,why=pcall(survey,role)
                site=ok and value or nil;reason=ok and why or "survey_evidence_invalid"
                sites.offers[role],sites.reasons[role]=site,reason
            end
            if site then state,reason="proposed","joint_layout_available" end
        end
        local row={state=state,reason=reason or "survey_not_due",fallback="batched_manual_supply"}
        if site then
            row.anchor,row.position,row.belt_count=site.anchor,point(site.position),site.belt_count
            row.source_unit=site.source_unit
            row.bill={["stone-furnace"]=1,["burner-mining-drill"]=1,["burner-inserter"]=2,
                ["wooden-chest"]=1,["transport-belt"]=site.belt_count}
            row.components=site.specs;row.checks=site.checks
        end
        rows[role]=row
    end
    return {protocol=1,session_id=storage.jev_session_id,tick=game.tick,sources=rows}
end
local function chosen(role,name,anchor)
    assert(ores[role] and name=="stone-furnace" and type(anchor)=="string" and #anchor<=128,"Unsupported production-site command")
    local site=sites.offers[role]
    assert(site and site.anchor==anchor,"Stale production-site offer")
    local player=valid(site,false)
    assert(player.crafting_queue_size==0,"Do not build during native crafting")
    return site,player
end
campaign.prepare_production_site=function(role,name,anchor)
    local site=chosen(role,name,anchor)
    rcon.print(helpers.table_to_json({position=site.position,name=name}))
end
campaign.build_production_site=function(role,name,anchor)
    local site,player=chosen(role,name,anchor)
    local before=player.get_item_count(name)
    fair.place(name,site.position,0)
    local entity=player.surface.find_entity(name,site.position)
    assert(entity and entity.valid and entity.unit_number and entity.force==player.force
        and player.get_item_count(name)==before-1,"Production furnace placement not paid")
    campaign.entities[role]=entity;site.entity=entity;site.source_unit=entity.unit_number
    sites.owned[role]=site;sites.offers[role]=nil
    rcon.print(helpers.table_to_json({unit_number=entity.unit_number}))
end
campaign.production_output_offer=function(role,item)
    local site=sites.owned[role]
    if not site then return false end
    local player=valid(site,true)
    if not clear(site.output_arm,player) or not clear(site.chest,player) then
        sites.reasons[role]="reserved_output_obstructed";return true,nil
    end
    local unit=site.source_unit
    return true,{source=role,item=item,source_unit=unit,source_position=point(site.position),entity=site.entity,
        layout="output:"..unit..":joint",chest_position=site.chest.position,inserter_position=site.output_arm.position,
        direction=site.output_arm.direction,chest_role="output-chest:"..unit,inserter_role="output-arm:"..unit,parts={}}
end
campaign.production_input_offer=function(role)
    local site=sites.owned[role]
    if not site then return false end
    local player=valid(site,true);local output=storage.output_buffers.cells[role]
    if not output or not output.flow or output.fault then return true,nil end
    for _,part in ipairs(site.input_steps) do if not clear(part,player) then
        sites.reasons[role]="reserved_input_obstructed";return true,nil
    end end
    return true,{source=role,source_unit=site.source_unit,source_position=point(site.position),entity=site.entity,
        item=site.item,ore=site.ore,output_layout=output.layout,layout="input:"..site.source_unit..":joint",
        steps=site.input_steps,parts={},belt_count=site.belt_count,reserve_belts=0}
end
