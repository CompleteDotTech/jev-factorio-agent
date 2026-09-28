-- Fixed point-in-time ownership projection. No writes, initialization, handlers,
-- campaign callbacks, fair.actor(), observation callbacks or action requests.
local rt=jev_fle_runtime
local c=rt and rt.campaign
local agent=rt and rt.agent_characters and rt.agent_characters[1]
local index=rt and rt.jev_player_index or 1
local player=type(index)=="number" and index%1==0 and index>0 and game.get_player(index) or nil
local result={schema=2,tick=game.tick,speed=game.speed,tick_paused=game.tick_paused,
    marked=storage.jev_factorio_session==true,runtime_present=rt~=nil,campaign_present=c~=nil,
    fair_present=rt~=nil and rt.fair~=nil,session_id=rt and rt.jev_session_id or "",
    player_index=index,bound_player_index=rt and rt.jev_bound_player_index or 0,
    connected=player~=nil and player.connected==true and #game.connected_players==1 and game.connected_players[1]==player,
    bound=agent~=nil and agent.valid and player~=nil and player.character==agent,
    actor_unit=agent and agent.valid and agent.unit_number or 0,
    surface_index=agent and agent.valid and agent.surface.index or 0,
    force_index=agent and agent.valid and agent.force.index or 0,
    mods=script.active_mods,entities={},input_routes={},output_buffers={},outposts={},
    solid_routes={},coal_supply={},idle={},truncated=false,ownership_complete=false}
local function text(v,n)
    return type(v)=="string" and #v>0 and #v<=(n or 128) and not v:find("[^ -~]")
end
local function integer(v,min)
    return type(v)=="number" and v%1==0 and v>=(min or 0) and v<=9007199254740991
end
local function bounded(t,n)
    assert(type(t)=="table","Unknown ownership table")
    local count=0
    for _ in pairs(t) do count=count+1
        if count>n then result.truncated=true;error("Ownership bound") end
    end
    return t
end
local budget=0
local function copy(value,depth)
    depth=depth or 0;budget=budget+1
    if depth>8 or budget>12000 then result.truncated=true;error("Projection bound") end
    local kind=type(value)
    if kind=="table" then
        local out={}
        for key,item in pairs(bounded(value,128)) do
            assert(text(key) or integer(key,1),"Unknown projection key")
            out[key]=copy(item,depth+1)
        end
        return out
    end
    assert(kind=="boolean" or kind=="string" and #value<=256
        or kind=="number" and value==value and math.abs(value)<=9007199254740991,"Unknown projected value")
    return value
end
local function point(p)
    assert(p and type(p.x)=="number" and type(p.y)=="number"
        and p.x==p.x and p.y==p.y and math.abs(p.x)<=1000000 and math.abs(p.y)<=1000000,"Unknown position")
    return {x=p.x,y=p.y}
end
local function same(a,b) return a.x==b.x and a.y==b.y end
local function entity(row)
    assert(row and row.valid and integer(row.unit_number,1) and row.quality and row.quality.name=="normal"
        and row.surface.index==result.surface_index and row.force.index==result.force_index,"Unknown owned entity")
    local recipe=""
    if row.type=="assembling-machine" or row.type=="furnace" then
        local current=row.get_recipe();recipe=current and current.name or ""
    end
    return {name=row.name,unit_number=row.unit_number,position=point(row.position),direction=row.direction,
        surface_index=row.surface.index,force_index=row.force.index,quality=row.quality.name,
        bounds={left_top=point(row.bounding_box.left_top),right_bottom=point(row.bounding_box.right_bottom)},recipe=recipe}
end
local function journals(cell)
    assert(type(cell)=="table" and not cell.fault,"Faulted ownership")
    if cell.pending then result.idle.construction_pending=true end
    if cell.manual_pending then result.idle.manual_pending=true end
end
local paid_units,paid_receipts={},{}
local function parts(cell,steps)
    local out={}
    for name,p in pairs(bounded(cell.parts,66)) do
        assert(text(name) and type(p)=="table" and text(p.role) and text(p.receipt)
            and integer(p.unit_number,1) and p.paid==1 and p.entity==c.entities[p.role],"Unknown paid part")
        local current=result.entities[p.role]
        assert(current and current.unit_number==p.unit_number and not paid_units[p.unit_number]
            and not paid_receipts[p.receipt],"Duplicate or missing paid identity")
        paid_units[p.unit_number]=true;paid_receipts[p.receipt]=true
        if steps then
            local spec=nil
            for _,candidate in ipairs(bounded(steps,66)) do if candidate.part==name then
                assert(not spec,"Duplicate part geometry");spec=candidate
            end end
            assert(spec and current.name==spec.name and current.direction==spec.direction
                and same(current.position,spec.position),"Paid geometry mismatch")
        end
        out[name]={role=p.role,unit_number=p.unit_number,receipt=p.receipt,paid=p.paid}
    end
    return out
end
local function offers(runtime,maximum)
    for key,cell in pairs(bounded(runtime.offers or {},maximum)) do
        journals(cell)
        -- Older routes retain the offer reference after freezing the same cell.
        assert(runtime.cells[key]==cell or next(bounded(cell.parts,66))==nil,"Unowned paid offer")
    end
end
local function legacy(runtime,maximum,is_output)
    local out={present=runtime~=nil,protocol=runtime and runtime.protocol or 0,commitments={}}
    if not runtime then return out end
    assert(runtime.protocol==1,"Unknown legacy transport runtime")
    for source,cell in pairs(bounded(runtime.cells,maximum)) do
        assert(text(source),"Unknown source role");journals(cell)
        local current=result.entities[source]
        assert(current and current.unit_number==cell.source_unit and cell.entity==c.entities[source],"Unknown legacy source")
        local steps=cell.steps
        if is_output then
            steps={{part="chest",name="wooden-chest",position=cell.chest_position,direction=0},
                {part="inserter",name="burner-inserter",position=cell.inserter_position,direction=cell.direction}}
        end
        out.commitments[source]={source_unit=cell.source_unit,layout=cell.layout,parts=parts(cell,steps)}
    end
    offers(runtime,maximum)
    return out
end
local ok=pcall(function()
    assert(rt and c and rt.fair and agent and agent.valid and player and player.character==agent,"Missing runtime binding")
    local fair=rt.fair
    local jobs=c.craft_jobs
    result.idle={walking=player.walking_state.walking,mining=player.mining_state.mining,
        crafting_queue_size=player.crafting_queue_size,cheat_mode=player.cheat_mode,
        quarantined=fair.quarantined,fair_job_status=fair.job and fair.job.status or "absent",
        craft_job_status=jobs and jobs.job and jobs.job.status or "absent",
        legacy_queue_nonempty=false,construction_pending=false,manual_pending=false}
    if jobs and jobs.submitting then result.idle.construction_pending=true end
    for _,key in ipairs({"crafting_queue","harvest_queues","walking_queues"}) do
        if rt[key] then
            bounded(rt[key],128)
            if next(rt[key]) then result.idle.legacy_queue_nonempty=true end
        end
    end
    local units={}
    for role,e in pairs(bounded(c.entities,2048)) do
        assert(text(role),"Unknown owned role")
        local row=entity(e)
        assert(not units[row.unit_number],"Aliased owned role")
        units[row.unit_number]=true;result.entities[role]=row
    end
    result.input_routes=legacy(rt.input_routes,4,false)
    result.output_buffers=legacy(rt.output_buffers,5,true)
    local o=rt.mining_outposts
    result.outposts={present=o~=nil,protocol=o and o.protocol or 0,commitments={},receipts={}}
    if o then
        assert(o.protocol==1,"Unknown outpost runtime")
        for resource,cell in pairs(bounded(o.cells,2)) do
            assert(resource=="iron-ore" or resource=="copper-ore","Unknown outpost resource");journals(cell)
            result.outposts.commitments[resource]={layout=cell.layout,surface_index=cell.surface.index,
                force_index=cell.force.index,steps=copy(cell.steps),parts=parts(cell,cell.steps),flow=copy(cell.flow or {})}
        end
        result.outposts.receipts=copy(bounded(o.receipts,4))
        offers(o,2)
    end
    local r=assert(rt.solid_routes,"Missing solid runtime")
    assert(r.protocol==1 and r.implementation_revision==4 and r.contract_family=="straight-solid-corridor-v1"
        and r.reservation_contract=="full-corridor-manhattan-v1","Unknown solid runtime")
    result.solid_routes={present=true,protocol=r.protocol,implementation_revision=r.implementation_revision,
        contract_family=r.contract_family,reservation_contract=r.reservation_contract,
        intents=copy(bounded(r.intents,4)),binding=r.binding,commitments={}}
    for key,cell in pairs(bounded(r.cells,4)) do
        assert(text(key) and cell.route==key,"Unknown solid route");journals(cell)
        result.solid_routes.commitments[key]={route=cell.route,layout=cell.layout,item=cell.item,
            source=copy(cell.source),target=copy(cell.target),steps=copy(cell.steps),parts=parts(cell,cell.steps)}
    end
    offers(r,4)
    local q=assert(rt.coal_supply,"Missing coal runtime")
    assert(q==r.coal and q.revision==4 and type(q.committed)=="boolean"
        and type(q.admission_evidence)=="boolean","Unknown coal runtime")
    result.coal_supply={present=true,revision=q.revision,targets=copy(bounded(q.targets,4)),binding=q.binding,
        admission_evidence=q.admission_evidence,committed=q.committed,commitments={}}
    for target,row in pairs(bounded(q.rows,4)) do
        assert(text(target),"Unknown coal target");journals(row)
        if q.committed then
            result.coal_supply.commitments[target]={target=copy(row.target),layout=row.layout,steps=copy(row.steps),
                corridor=copy(row.corridor),chest_bounds=copy(row.chest_bounds),drill_bounds=copy(row.drill_bounds),
                mining_area=copy(row.mining_area),parts=parts(row,row.steps)}
        else
            assert(next(bounded(row.parts,2))==nil,"Uncommitted paid coal source")
        end
    end
end)
result.ownership_complete=ok
rcon.print(helpers.table_to_json(result))
