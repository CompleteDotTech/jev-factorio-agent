-- Ordinary connector payments remain attributable across lost RCON replies.
-- A same-force connector observed before this transaction is never paid ownership.
local campaign = assert(storage.campaign)
local fair = assert(storage.fair)
local ledger = campaign.connector_ledger or {protocol=1, routes={}, active=nil}
assert(ledger.protocol==1 and type(ledger.routes)=="table", "Connector ledger changed")
campaign.connector_ledger = ledger

local function actor() return fair.actor() end
local function point(p)
    assert(type(p)=="table" and type(p.x)=="number" and type(p.y)=="number"
        and p.x%1==.5 and p.y%1==.5, "Invalid connector cell")
    return {x=p.x,y=p.y}
end
local function entity(name,p)
    local player=actor()
    local e=player.surface.find_entity(name,p)
    if e then assert(e.valid and e.force==player.force and e.unit_number,
        "Connector identity changed") end
    return e
end
local function same(p,q) return p.x==q.x and p.y==q.y end
local function checked(row)
    local player=actor()
    assert(player.character.unit_number==row.actor_unit
        and player.surface.index==row.surface_index and player.force.index==row.force_index
        and storage.jev_session_id==row.session_id, "Connector owner changed")
    local source=campaign.entities[row.source]
    local target=campaign.entities[row.target]
    assert(source and source.valid and source.unit_number==row.source_unit
        and target and target.valid and target.unit_number==row.target_unit,
        "Connector endpoint identity changed")
    for _,cell in ipairs(row.cells) do
        if cell.unit_number then
            local e=entity(row.kind,cell.position)
            assert(e and e.unit_number==cell.unit_number, "Paid connector changed")
        end
    end
end

campaign.connector_begin=function(id,source,target,kind,fluid,path)
    assert(type(id)=="string" and id:match("^[0-9a-f]+$") and #id==64,
        "Invalid connector receipt")
    assert(type(source)=="string" and type(target)=="string" and source~=target
        and (kind=="pipe" or kind=="small-electric-pole") and type(fluid)=="string"
        and type(path)=="table" and #path>=1 and #path<=1200,
        "Invalid connector route")
    assert(not ledger.active, "Prior connector route needs reconciliation")
    assert(not ledger.routes[id], "Connector receipt cannot be replayed")
    local prior=0
    for _ in pairs(ledger.routes) do prior=prior+1 end
    assert(prior<128, "Connector ledger exceeds bound")
    local player=actor()
    local a,b=campaign.entities[source],campaign.entities[target]
    assert(a and a.valid and b and b.valid and a.unit_number and b.unit_number
        and a.force==player.force and b.force==player.force
        and a.surface==player.surface and b.surface==player.surface,
        "Connector endpoints unavailable")
    local row={id=id,source=source,target=target,source_unit=a.unit_number,
        target_unit=b.unit_number,kind=kind,fluid=fluid,
        actor_unit=player.character.unit_number,surface_index=player.surface.index,
        force_index=player.force.index,session_id=storage.jev_session_id,
        state="building",cells={},paid=0,external=0,tick=game.tick}
    local seen={}
    for _,raw in ipairs(path) do
        local p=point(raw)
        assert(type(raw.existing)=="boolean", "Connector preflight identity missing")
        local key=p.x..":"..p.y
        assert(not seen[key], "Connector route repeats a cell")
        seen[key]=true
        local e=entity(kind,p)
        assert((e~=nil)==raw.existing, "Connector preflight identity changed")
        local cell={position=p}
        if e then
            cell.unit_number=e.unit_number
            cell.external=true
            row.external=row.external+1
        end
        row.cells[#row.cells+1]=cell
    end
    -- Preflight every new cell before recording an active transaction. The
    -- paid placement still rechecks this immediately before payment.
    for _,cell in ipairs(row.cells) do if not cell.unit_number then
        assert(player.surface.can_place_entity{name=kind,position=cell.position,
            direction=defines.direction.north,force=player.force,
            build_check_type=defines.build_check_type.manual},
            "Connector route changed before payment")
    end end
    ledger.routes[id]=row
    ledger.active=id
    return {id=id,paid=0,external=row.external}
end

fair.connector_place=function(id,index,name,position,direction)
    assert(ledger.active==id, "Connector receipt is not active")
    local row=assert(ledger.routes[id])
    checked(row)
    assert(type(index)=="number" and index%1==0 and index>=1 and index<=#row.cells,
        "Invalid connector receipt index")
    local cell=row.cells[index]
    assert(name==row.kind and same(point(position),cell.position)
        and direction==defines.direction.north and not cell.unit_number
        and not row.pending, "Connector payment does not match prepared cell")
    -- Retained before payment: a failure after the native build cannot be
    -- mistaken for a clean preflight rejection or silently adopted on retry.
    row.pending=index
    local paid=fair.place(name,position,direction)
    assert(paid.unit_number and entity(name,position).unit_number==paid.unit_number,
        "Connector payment identity missing")
    cell.unit_number=paid.unit_number
    cell.paid=1
    cell.tick=game.tick
    row.paid=row.paid+1
    row.pending=nil
    return paid
end

campaign.connector_finish=function(id)
    assert(ledger.active==id, "Connector receipt is not active")
    local row=assert(ledger.routes[id])
    checked(row)
    assert(not row.pending, "Connector payment needs reconciliation")
    for _,cell in ipairs(row.cells) do assert(cell.unit_number, "Connector route incomplete") end
    row.state="complete"
    row.completed_tick=game.tick
    row.owned=(row.external==0 and row.paid==#row.cells)
    ledger.active=nil
    return {id=id,paid=row.paid,external=row.external,owned=row.owned}
end

campaign.observe_connector_ownership=function()
    local rows={}
    local count=0
    for id,row in pairs(ledger.routes) do
        count=count+1
        assert(count<=128, "Connector ledger exceeds bound")
        local valid=pcall(checked,row)
        rows[id]={id=id,source=row.source,target=row.target,
            source_unit=row.source_unit,target_unit=row.target_unit,
            kind=row.kind,fluid=row.fluid,actor_unit=row.actor_unit,
            surface_index=row.surface_index,force_index=row.force_index,
            session_id=row.session_id,state=valid and row.state or "fault",
            pending=row.pending,paid=row.paid,external=row.external,
            owned=valid and row.owned or false,cell_count=#row.cells,tick=row.tick,
            completed_tick=row.completed_tick}
    end
    return {protocol=1,session_id=storage.jev_session_id,tick=game.tick,
        active=ledger.active,routes=rows}
end

-- Detail queries are bounded so the normal campaign observation stays small.
campaign.connector_page=function(id,offset,limit)
    assert(type(id)=="string" and type(offset)=="number" and offset%1==0
        and offset>=1 and type(limit)=="number" and limit%1==0
        and limit>=1 and limit<=64, "Invalid connector page")
    local row=assert(ledger.routes[id], "Unknown connector receipt")
    local valid=pcall(checked,row)
    local cells={}
    for index=offset,math.min(#row.cells,offset+limit-1) do
        local cell=row.cells[index]
        cells[#cells+1]={index=index,position=cell.position,
            unit_number=cell.unit_number,paid=cell.paid or 0,
            external=cell.external or false,tick=cell.tick}
    end
    return {id=id,valid=valid,cell_count=#row.cells,offset=offset,cells=cells}
end
