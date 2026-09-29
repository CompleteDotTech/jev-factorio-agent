-- Optional, source-qualified cycle journal candidate. No installation here.
-- The ordinary v5 gather journal and pinned factory transfer code stay intact.
local rt=assert(storage)
assert(rt.coal_manual_cycle_v2==nil,"Coal cycle journal already installed")
local c=assert(rt.campaign)
local fair=assert(rt.fair)
local gather=assert(rt.coal_manual_journal_v1)
local actor=assert(rt.agent_characters and rt.agent_characters[1])
local player=assert(fair.actor())
assert(actor.valid and player.character==actor and actor.unit_number
    and type(rt.jev_session_id)=="string" and #rt.jev_session_id>0,
    "Coal cycle actor unavailable")

local function identifier(value)
    return type(value)=="string" and #value>=1 and #value<=128
        and not value:find("[^%g]")
end
local journal
local function coal()
    assert(player==fair.actor() and player.character==actor and actor.valid
        and rt.jev_session_id==journal.session_id
        and player.index==journal.actor_index
        and actor.unit_number==journal.actor_unit
        and actor.surface.index==journal.surface_index
        and actor.force.index==journal.force_index
        and game.speed==1 and not game.tick_paused
        and #game.connected_players==1 and game.connected_players[1]==player,
        "Coal cycle actor epoch changed")
    return player.get_item_count("coal")
end
journal={protocol=2,session_id=rt.jev_session_id,
    actor_unit=actor.unit_number,actor_index=player.index,
    surface_index=actor.surface.index,force_index=actor.force.index,
    active=nil,rows={},order={}}
rt.coal_manual_cycle_v2=journal

local function failed(row,reason)
    row.fault=reason
    row.status="failed"
    return false
end
local function check(row)
    if not row or row.status~="pending" or row.fault then
        error("Coal cycle requires reconciliation")
    end
    if game.tick-row.started_tick>216000 then
        failed(row,"tick_window");error("Coal cycle exceeded bound")
    end
    return row
end

-- Begin before the source-qualified gather starts. This is not a gameplay
-- action and does not authorize one. Only one unspent cycle receipt may exist.
function journal.begin(cycle_receipt,gather_receipt)
    assert(identifier(cycle_receipt) and identifier(gather_receipt)
        and cycle_receipt~=gather_receipt and not journal.active
        and not journal.rows[cycle_receipt] and #journal.order<32,
        "Coal cycle receipt unavailable")
    assert(not gather.pending and (not fair.job or fair.job.status=="completed"
        or fair.job.status=="failed"),"Coal cycle actor busy")
    local q=assert(rt.coal_supply)
    assert(q.revision==4 and not q.committed and #q.targets>=2 and #q.targets<=4,
        "Coal cycle requires whole unpaid proposal")
    for _,target in ipairs(q.targets) do
        local proposal=q.rows[target]
        assert(proposal and not proposal.pending and not proposal.manual_pending
            and not proposal.fault,"Coal cycle proposal requires reconciliation")
    end
    assert(coal()==0,"Coal cycle requires empty actor coal stock")
    local receipt_count=#assert(c.receipt_order)
    assert(receipt_count<=4096,"Coal transfer history exceeds bound")
    local row={schema="jev.coal-manual-cycle.v2",status="pending",fault=false,
        session_id=journal.session_id,actor_index=journal.actor_index,
        actor_unit=journal.actor_unit,surface_index=journal.surface_index,
        force_index=journal.force_index,receipt=cycle_receipt,
        gather_receipt=gather_receipt,started_tick=game.tick,
        finished_tick=game.tick,receipt_count=receipt_count,
        expected_coal=0,deliveries={},pending_delivery=nil,gather={}}
    journal.active=row
    return {receipt=cycle_receipt,started_tick=game.tick}
end

function journal.begin_delivery(cycle_receipt,receipt,target_role)
    local row=check(journal.active)
    assert(row.receipt==cycle_receipt and identifier(receipt)
        and identifier(target_role) and receipt~=row.gather_receipt,
        "Coal delivery identity changed")
    assert(not row.pending_delivery and #row.deliveries<4,
        "Coal delivery already pending or bounded")
    local g=gather.rows[row.gather_receipt]
    assert(g and g.status=="complete" and not g.pending and not g.fault
        and not g.overflow and g.started_tick>=row.started_tick
        and g.finished_tick<=game.tick and g.coal_before==0
        and g.coal_after>=1 and g.coal_after<=200
        and type(g.walking_ticks)=="number" and g.walking_ticks%1==0
        and type(g.mining_ticks)=="number" and g.mining_ticks%1==0
        and g.walking_ticks>=0 and g.mining_ticks>=1
        and g.walking_ticks+g.mining_ticks<=g.finished_tick-g.started_tick,
        "Coal gather unavailable for cycle")
    if #row.deliveries==0 then
        assert(coal()==g.coal_after,"Coal gather stock changed")
        row.expected_coal=g.coal_after
        row.gather={started_tick=g.started_tick,finished_tick=g.finished_tick,
            coal_before=g.coal_before,coal_after=g.coal_after,
            walking_ticks=g.walking_ticks,mining_ticks=g.mining_ticks}
    else
        assert(coal()==row.expected_coal,"Coal delivery stock changed")
    end
    assert(not fair.job or fair.job.status=="completed"
        or fair.job.status=="failed","Coal delivery actor busy")
    local target=c.entities[target_role]
    assert(target and target.valid and target.unit_number
        and target.surface==actor.surface and target.force==actor.force
        and target.burner and not c.receipts[receipt],
        "Coal delivery target unavailable")
    row.pending_delivery={receipt=receipt,role=target_role,
        unit=target.unit_number,started_tick=game.tick,
        walking_ticks=0,last_tick=game.tick,
        last_position={x=player.position.x,y=player.position.y}}
    return {receipt=receipt,unit=target.unit_number}
end

function journal.tick_handler(event)
    local row=journal.active
    if not row or row.fault then return end
    if not event or event.tick~=game.tick or game.tick-row.started_tick>216000 then
        failed(row,"tick_window");return
    end
    local ok,current=pcall(coal)
    if not ok then failed(row,"actor_epoch");return end
    if row.pending_delivery then
        local d=row.pending_delivery
        if current~=row.expected_coal then failed(row,"unreceipted_coal_change");return end
        local job=fair.job
        if job and job.status~="completed" and job.status~="failed" then
            if job.kind~="walk" or job.unit~=actor.unit_number then
                failed(row,"foreign_fair_job");return
            end
            if job.status=="walking" and player.walking_state.walking
                and (player.position.x~=d.last_position.x
                    or player.position.y~=d.last_position.y) then
                d.walking_ticks=d.walking_ticks+1
            end
        end
        d.last_position={x=player.position.x,y=player.position.y}
        d.last_tick=game.tick
    elseif not gather.pending then
        local g=gather.rows[row.gather_receipt]
        if g and g.status=="complete" and g.coal_before==0
            and g.started_tick>=row.started_tick then
            if current~=g.coal_after then failed(row,"unreceipted_coal_change") end
        elseif current~=0 then
            failed(row,"unreceipted_coal_change")
        end
    end
end

function journal.finish_delivery(cycle_receipt,receipt)
    local row=check(journal.active)
    local d=row.pending_delivery
    assert(row.receipt==cycle_receipt and d and d.receipt==receipt,
        "Coal delivery pending receipt changed")
    local native=c.receipts[receipt]
    assert(native and native.role==d.role and native.unit_number==d.unit
        and native.item=="coal" and native.extracting==false
        and type(native.quantity)=="number" and native.quantity%1==0
        and native.quantity>=1 and native.quantity<=200
        and native.tick>=d.started_tick and native.tick<=game.tick,
        "Coal delivery native receipt changed")
    local target=c.entities[d.role]
    assert(target and target.valid and target.unit_number==d.unit
        and coal()==row.expected_coal-native.quantity,
        "Coal delivery stock or target changed")
    row.expected_coal=row.expected_coal-native.quantity
    d.coal=native.quantity;d.tick=native.tick;d.finished_tick=game.tick
    d.last_position=nil;d.last_tick=nil
    row.deliveries[#row.deliveries+1]=d
    row.pending_delivery=nil
    return {receipt=receipt,unit=d.unit,coal=d.coal}
end

function journal.finish(cycle_receipt,targets)
    local row=check(journal.active)
    assert(row.receipt==cycle_receipt and not row.pending_delivery
        and type(targets)=="table" and #targets>=2 and #targets<=4
        and #row.deliveries==#targets,
        "Coal cycle incomplete")
    local g=gather.rows[row.gather_receipt]
    assert(g and g.status=="complete" and g.coal_before==0
        and g.coal_after>=1 and g.coal_after<=200,
        "Coal cycle gather changed")
    for field,value in pairs(row.gather) do
        assert(g[field]==value,"Coal gather witness changed")
    end
    local q=assert(rt.coal_supply)
    assert(q.revision==4 and not q.committed and #q.targets==#targets,
        "Coal cycle target proposal changed")
    local expected={}
    for _,role in ipairs(q.targets) do expected[#expected+1]=role end
    table.sort(expected)
    local delivered=0
    for i,role in ipairs(targets) do
        local d=row.deliveries[i]
        assert(identifier(role) and d.role==role and role==expected[i],
            "Coal cycle target set changed")
        local native=c.receipts[d.receipt]
        local target=c.entities[role]
        assert(native and native.role==d.role and native.unit_number==d.unit
            and native.item=="coal" and native.extracting==false
            and native.quantity==d.coal and native.tick==d.tick
            and target and target.valid and target.unit_number==d.unit
            and target.surface==actor.surface and target.force==actor.force,
            "Coal delivery witness changed")
        delivered=delivered+d.coal
    end
    assert(delivered<=g.coal_after and coal()==g.coal_after-delivered,
        "Coal cycle conservation changed")
    local actual={}
    for i=row.receipt_count+1,#c.receipt_order do
        local id=c.receipt_order[i]
        local receipt=c.receipts[id]
        if receipt and receipt.item=="coal" then actual[#actual+1]=id end
    end
    assert(#actual==#row.deliveries,"Foreign coal transfer during cycle")
    for i,d in ipairs(row.deliveries) do
        assert(actual[i]==d.receipt,"Coal transfer order changed")
    end
    row.receipt_end=#c.receipt_order
    row.finished_tick=game.tick
    row.status="complete"
    journal.rows[row.receipt]=row
    journal.order[#journal.order+1]=row.receipt
    journal.active=nil
    return {receipt=row.receipt,delivered=delivered,finished_tick=game.tick}
end

-- Slot 1 already belongs to the v1 gather journal. Preserve its exact handler
-- in one source-qualified composite so both journals sample every game tick.
function journal.combined_tick_handler(event)
    gather.tick_handler(event)
    journal.tick_handler(event)
end
script.on_nth_tick(1,journal.combined_tick_handler)
