-- Additive ownership capability: no base callback replacement, no free items.
local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair)
assert(not rt.bootstrap_output_v1,"Bootstrap output capability already installed")
local authorization=assert(rt.bootstrap_output_install_authorization,
    "Explicit bootstrap installation authority required")
local player=assert(f.actor());local actor=assert(player.character)
assert(authorization.session_id==rt.jev_session_id and authorization.actor_unit==actor.unit_number
    and authorization.surface_index==actor.surface.index and authorization.force_index==actor.force.index)
local function integer(v,min) return type(v)=="number" and v%1==0 and v>=min and v<=9007199254740991 end
local function point(p) return {x=p.x,y=p.y} end
local function same(a,b) return a.x==b.x and a.y==b.y end
local function counts(inv)
    assert(inv and inv.valid~=false,"Missing bootstrap inventory")
    local out,n={},0;for key,value in pairs(inv.get_contents()) do
        local name,amount,quality
        if type(key)=="string" and type(value)=="number" then name,amount=key,value
        else assert(type(value)=="table");name,amount,quality=value.name,value.count,value.quality end
        quality=type(quality)=="table" and quality.name or quality
        assert(quality==nil or quality=="normal","Unsupported bootstrap inventory quality")
        assert(type(name)=="string" and #name>0 and #name<=128 and integer(amount,0))
        n=n+1;assert(n<=4096,"Bootstrap inventory content budget exceeded")
        out[name]=(out[name] or 0)+amount;assert(integer(out[name],0))
    end;return out
end
local role="bootstrap-output:iron-ore"
local function current_original_callbacks()
    return {fair_tick=f.tick_handler,observe=c.observe,snapshot_v1=c.observation_snapshot,
        snapshot_v2=c.observation_snapshot_v2,transfer=c.transfer,configure=c.configure,
        connector_begin=c.connector_begin,connector_finish=c.connector_finish,
        connector_page=c.connector_page,connector_observe=c.observe_connector_ownership,
        journal_tick=rt.coal_manual_journal_v1 and rt.coal_manual_journal_v1.tick_handler or nil,
        cycle_tick=rt.coal_manual_cycle_v2 and rt.coal_manual_cycle_v2.combined_tick_handler or nil}
end
local original_callbacks=current_original_callbacks()
local b={protocol=1,session_id=rt.jev_session_id,actor_unit=actor.unit_number,
    surface_index=actor.surface.index,force_index=actor.force.index,
    paid={},original_place=f.place,original_transfer=c.transfer,original_callbacks=original_callbacks,binding=false,
    phase="install_prepared",placement_pending=false,transfer_pending=false,
    install_authorization=authorization}
-- Publish the prepared journal BEFORE any registration/native item change.
-- A partial installer is retained and requires reconciliation, never replay.
rt.bootstrap_output_v1=b
local function owner()
    local p=assert(f.actor());local a=assert(p.character)
    assert(rt.jev_session_id==b.session_id and a.valid and a.unit_number==b.actor_unit
        and a.surface.index==b.surface_index and a.force.index==b.force_index,
        "Bootstrap output owner changed")
    assert(f.place==b.original_place and c.transfer==b.original_transfer,
        "Bootstrap output callback chain changed")
    local current=current_original_callbacks()
    for name,callback in pairs(b.original_callbacks) do
        assert(current[name]==callback,"Bootstrap retained callback chain changed")
    end
    for name,callback in pairs(current) do
        assert(b.original_callbacks[name]==callback,"Bootstrap retained callback chain changed")
    end
    return p,a
end
local function endpoint(drill_unit,chest_unit,drill_position,chest_position)
    local p,a=owner()
    assert(integer(drill_unit,1) and integer(chest_unit,1) and drill_unit~=chest_unit)
    local d=assert(a.surface.find_entity("burner-mining-drill",drill_position))
    local e=assert(a.surface.find_entity("wooden-chest",chest_position))
    assert(d.valid and e.valid and d.unit_number==drill_unit and e.unit_number==chest_unit
        and d.force==a.force and e.force==a.force and d.surface==a.surface and e.surface==a.surface,
        "Bootstrap output endpoint changed")
    assert(same(d.position,drill_position) and same(e.position,chest_position))
    local matches=a.surface.find_entities_filtered{name="wooden-chest",force=a.force,
        position=d.drop_position,radius=.75,limit=2}
    assert(#matches==1 and matches[1]==e and math.abs(e.position.x-d.drop_position.x)<.5
        and math.abs(e.position.y-d.drop_position.y)<.5,"Ambiguous bootstrap output endpoint")
    assert(e.get_inventory(defines.inventory.chest),"Bootstrap output inventory missing")
    return d,e
end
local function bind(d,e,origin,id,auth)
    assert(not b.binding and not c.entities[role],"Bootstrap output role already owned")
    for _,known in pairs(c.entities) do assert(known~=e,"Bootstrap output has another campaign role") end
    b.binding={drill=d,chest=e,drill_position=point(d.position),chest_position=point(e.position),
        drill_unit=d.unit_number,chest_unit=e.unit_number,
        origin=origin,binding_id=id,authorization_sha256=auth,bound_at_tick=game.tick}
    c.entities[role]=e -- Native registration, effective ownership FROM NOW.
end
b.place=function(name,position,direction)
    local p,a=owner()
    assert(b.phase=="ready" and not b.placement_pending and not b.transfer_pending,
        "Bootstrap placement requires reconciliation")
    assert(not b.binding,"Bootstrap endpoint is already bound")
    assert(name=="burner-mining-drill" or name=="wooden-chest")
    local count=0;for _,paid in pairs(b.paid) do
        count=count+1;assert(paid.name~=name,"Bootstrap item already placed")
    end;assert(count<2,"Bootstrap placement budget exhausted")
    local before=p.get_item_count(name)
    b.placement_pending={name=name,position=point(position),item_before=before,tick=game.tick,
        actor_unit=b.actor_unit,session_id=b.session_id}
    local result=b.original_place(name,position,direction)
    b.placement_pending.result=result -- Retain returned native identity before receipt checks.
    assert(integer(result.unit_number,1) and p.get_item_count(name)==before-1)
    assert(not b.paid[result.unit_number],"Paid bootstrap identity duplicated")
    b.paid[result.unit_number]={name=name,unit_number=result.unit_number,position=point(result.position),
        session_id=b.session_id,actor_unit=b.actor_unit,surface_index=b.surface_index,
        force_index=b.force_index,item_before=before,item_after=before-1,tick=game.tick}
    b.placement_pending=false
    return result
end
b.bind_paid=function(drill_unit,chest_unit)
    assert(b.phase=="ready" and not b.placement_pending and not b.transfer_pending)
    local dr=assert(b.paid[drill_unit]);local cr=assert(b.paid[chest_unit])
    assert(dr.name=="burner-mining-drill" and cr.name=="wooden-chest"
        and dr.item_before-dr.item_after==1 and cr.item_before-cr.item_after==1)
    local d,e=endpoint(drill_unit,chest_unit,dr.position,cr.position)
    bind(d,e,"native_paid_bootstrap_placement","paid:"..drill_unit..":"..chest_unit,false)
    return {role=role,drill_unit=drill_unit,chest_unit=chest_unit,origin=b.binding.origin}
end
b.observe=function()
    local p,a=owner();local row=b.binding
    assert(b.phase=="ready","Bootstrap install is incomplete")
    if not row then return false end
    local d,e=endpoint(row.drill_unit,row.chest_unit,row.drill_position,row.chest_position)
    assert(c.entities[role]==e,"Bootstrap output registration changed")
    local paid=row.origin=="native_paid_bootstrap_placement"
    return {protocol=1,tick=game.tick,session_id=b.session_id,actor_unit=b.actor_unit,
        surface_index=b.surface_index,force_index=b.force_index,role=role,
        origin=row.origin,binding_id=row.binding_id,authorization_sha256=row.authorization_sha256,
        bound_at_tick=row.bound_at_tick,ownership_effective_now=true,
        native_pending=b.placement_pending~=false or b.transfer_pending~=false,
        historical_paid_placement_proven=paid,paid_drill_unit=paid and d.unit_number or false,
        paid_chest_unit=paid and e.unit_number or false,drill_unit=d.unit_number,chest_unit=e.unit_number,
        drill_position=point(d.position),drop_position=point(d.drop_position),chest_position=point(e.position),
        output=counts(e.get_inventory(defines.inventory.chest)),
        capacity={schema=1,tick=game.tick,session_id=b.session_id,actor_unit=b.actor_unit,
            surface_index=b.surface_index,force_index=b.force_index,quality="normal",
            inventory="character_main",item="iron-ore",
            count=p.get_main_inventory().get_insertable_count{name="iron-ore",quality="normal"}}}
end
b.extract=function(item,quantity,receipt)
    local row=assert(b.observe());local p,a=owner()
    assert(not b.placement_pending and not b.transfer_pending,"Bootstrap pickup requires reconciliation")
    assert(item=="iron-ore" and integer(quantity,1) and quantity<=200)
    assert(type(receipt)=="string")
    local prefix=receipt:match("^(%d+):")
    assert(prefix and #prefix<=16 and integer(tonumber(prefix),0) and tonumber(prefix)<=game.tick
        and receipt==prefix..":factory_extract:"..role..":iron-ore","Malformed bootstrap receipt")
    assert(not c.receipts[receipt],"Bootstrap pickup receipt already exists")
    local source=assert(b.binding.chest.get_inventory(defines.inventory.chest))
    local target=assert(p.get_main_inventory())
    counts(target) -- Direct native calls also reject foreign-quality attribution.
    assert(p.can_reach_entity(b.binding.chest) and source.get_item_count(item)>=quantity
        and target.get_insertable_count(item)>=quantity,"Bootstrap pickup native precondition failed")
    local before=target.get_item_count(item)
    local source_before=source.get_item_count(item)
    b.transfer_pending={item=item,quantity=quantity,receipt=receipt,chest_unit=row.chest_unit,
        player_before=before,source_before=source_before,tick=game.tick,binding_id=row.binding_id}
    local ok,value=pcall(b.original_transfer,role,item,quantity,receipt,true)
    local result=c.receipts[receipt]
    -- Sample conservation in this SAME command, including errors after effect.
    -- Later producer output is never subtracted from this operation's proof.
    b.transfer_pending.effect={tick=game.tick,player_after=target.get_item_count(item),
        source_after=source.get_item_count(item),receipt=result and {
            role=result.role,unit_number=result.unit_number,item=result.item,
            quantity=result.quantity,extracting=result.extracting,tick=result.tick} or false}
    if not ok then error(value) end
    result=assert(result)
    assert(result.role==role and result.unit_number==row.chest_unit and result.item==item
        and result.quantity==quantity and result.extracting==true
        and target.get_item_count(item)-before==quantity
        and source_before-source.get_item_count(item)==quantity,"Bootstrap pickup postcondition failed")
    b.transfer_pending=false
    return result
end
b.reconcile_pending=function(expected_partial_receipt,expected_partial_quantity)
    -- Proof-only completion. Never repeat place/transfer or invent a receipt.
    local p,a=owner()
    assert(b.phase=="ready")
    local placement=b.placement_pending
    if placement then
        local result=assert(placement.result,"Native placement effect is ambiguous")
        local paid=assert(b.paid[result.unit_number],"Paid placement record missing")
        local entity=assert(a.surface.find_entity(placement.name,result.position))
        assert(entity.valid and entity.unit_number==result.unit_number and entity.force==a.force
            and entity.surface==a.surface and same(entity.position,result.position)
            and paid.session_id==b.session_id and paid.actor_unit==b.actor_unit
            and paid.item_before==placement.item_before and paid.item_after==placement.item_before-1
            and p.get_item_count(placement.name)==paid.item_after,"Paid placement reconciliation changed")
    end
    local pending=b.transfer_pending
    assert(placement or pending,"No native pending operation to reconcile")
    if pending then
        local row=assert(b.observe());local receipt=assert(c.receipts[pending.receipt],
            "Native transfer receipt missing; effect is ambiguous")
        local effect=assert(pending.effect,"Same-command transfer effect proof missing")
        local captured=assert(effect.receipt,"Captured native transfer receipt missing")
        local actual=receipt.quantity
        assert(pending.binding_id==row.binding_id and pending.chest_unit==row.chest_unit
            and receipt.role==role and receipt.unit_number==row.chest_unit
            and receipt.item==pending.item and integer(actual,1) and actual<=pending.quantity
            and receipt.extracting==true and integer(effect.tick,0) and effect.tick==pending.tick
            and effect.player_after-pending.player_before==actual
            and pending.source_before-effect.source_after==actual,
            "Native transfer reconciliation changed")
        for key,value in pairs(captured) do assert(receipt[key]==value,"Captured native receipt changed") end
        if actual~=pending.quantity then
            assert(expected_partial_receipt==pending.receipt and expected_partial_quantity==actual,
                "Partial native effect requires explicit exact outcome reconciliation")
        else assert(expected_partial_receipt==nil and expected_partial_quantity==nil) end
    end
    b.reconciliations=b.reconciliations or {}
    assert(#b.reconciliations<64,"Bootstrap reconciliation history budget exhausted")
    b.reconciliations[#b.reconciliations+1]={tick=game.tick,placement=placement,transfer=pending}
    b.placement_pending=false;b.transfer_pending=false
    return {status="verified_existing_effect",tick=game.tick}
end
b.complete_install=function()
    -- Only the retained exact installation scope may complete a prepared bind.
    owner();assert(b.phase=="install_prepared" or b.phase=="ready")
    assert(not b.placement_pending and not b.transfer_pending)
    if authorization.origin=="legacy_authorized_current_asset" then
    assert(type(authorization.authorization_sha256)=="string"
        and #authorization.authorization_sha256==64
        and authorization.authorization_sha256:match("^[0-9a-f]+$"))
    local d,e=endpoint(authorization.drill_unit,authorization.chest_unit,
        authorization.drill_position,authorization.chest_position)
    assert(same(d.drop_position,authorization.drop_position),"Bound drill output geometry changed")
        if not b.binding then
            bind(d,e,"legacy_authorized_current_asset",authorization.authorization_sha256,
                authorization.authorization_sha256)
        else
            local row=b.binding
            assert(row.drill==d and row.chest==e and row.drill_unit==authorization.drill_unit
                and row.chest_unit==authorization.chest_unit
                and row.authorization_sha256==authorization.authorization_sha256
                and row.origin=="legacy_authorized_current_asset")
            assert(not c.entities[role] or c.entities[role]==e,"Bootstrap role conflict")
            for name,known in pairs(c.entities) do
                assert(name==role or known~=e,"Bootstrap output has another campaign role")
            end
            c.entities[role]=e
        end
    else assert(authorization.origin=="future_native_paid_bootstrap_only") end
    f.bootstrap_place=b.place -- New callback only; original fair.place is untouched.
    rt.bootstrap_output_install_authorization=nil
    b.phase="ready"
    return {status="ready",tick=game.tick}
end
b.complete_install()
