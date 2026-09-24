-- Additive owned producers. No resource, entity, speed or control writes here.
-- Existing fair builders/transfers/craft jobs remain the only mutation authority.
local c,fair=assert(storage.campaign),assert(storage.fair)
assert(storage.input_routes and storage.output_buffers and storage.production_sites,
    "Successors require owned input routes and production sites")
assert(c.craft_jobs and c.begin_craft_job,"Successors require receipt-tracked crafting")
assert(not storage.mining_outposts,"Successor/outpost composition is not yet supported")
local s=storage.successors or {protocol=1,records={},pending_use=nil}
assert(s.protocol==1,"Unsupported successor runtime")
storage.successors=s;c.successors_enabled=true
local roles={["growth:iron-plate"]="iron-ore",["growth:copper-plate"]="copper-ore"}
local useful={['iron-gear-wheel']=true,['copper-cable']=true,['electronic-circuit']=true,
    ['automation-science-pack']=true,['logistic-science-pack']=true}
local function integer(n) return type(n)=="number" and n%1==0 and n>=0 and n<9007199254740992 end
local function predecessor(role) return "recipe:"..string.sub(role,8) end
local function identity(role,m)
    local p=fair.actor();local old=c.entities[predecessor(role)]
    assert(old and old.valid and old.unit_number==m.predecessor_unit and old.name=="stone-furnace"
        and old.surface==p.surface and old.force==p.force,"Successor predecessor identity changed")
    if m.source_unit>0 then
        local e=c.entities[role];local site=storage.production_sites.owned[role]
        assert(e and e.valid and e.unit_number==m.source_unit and e~=old and site and site.entity==e
            and site.anchor==m.anchor and e.force==p.force and e.surface==p.surface,
            "Successor identity changed")
    end
    return p
end
c.successor_admit=function(site,building)
    if not roles[site.role] then return end
    local p=fair.actor();local old=c.entities[predecessor(site.role)]
    assert(old and old.valid and old.name=="stone-furnace" and old.products_finished>=20,
        "An established predecessor is required")
    local m=s.records[site.role]
    if m then assert(not m.fault and m.anchor==site.anchor,"Successor intent changed");identity(site.role,m) end
    if building then assert(m and m.source_unit==0,"Successor furnace must be prepared once") end
    for role,other in pairs(s.records) do
        assert(role==site.role or other.qualification~=nil,"Only one unqualified successor project is allowed")
    end
    local kit={["stone-furnace"]=1,["burner-mining-drill"]=1,["burner-inserter"]=2,
        ["wooden-chest"]=1,["transport-belt"]=site.belt_count+20,coal=30,[site.ore]=10}
    for name,count in pairs(kit) do assert(p.get_item_count(name)>=count,"Successor full paid kit or science reserve missing") end
end
c.successor_prepare=function(site)
    if not roles[site.role] then return end
    c.successor_admit(site,false)
    if not s.records[site.role] then
        s.records[site.role]={anchor=site.anchor,predecessor_unit=c.entities[predecessor(site.role)].unit_number,
            source_unit=0,paid=0,seeded=0,seed_uncollected=0,trial_collected=0,credit=0,
            expected_count=fair.actor().get_item_count(site.item),attribution_resets=0,
            started_tick=game.tick}
    end
end
c.successor_bound=function(site)
    if not roles[site.role] then return end
    local m=assert(s.records[site.role],"Missing prepared successor")
    assert(m.source_unit==0 and site.source_unit~=m.predecessor_unit,"Duplicate successor bind")
    m.source_unit=site.source_unit;m.paid=1;identity(site.role,m)
end
local function input_ready(role)
    local route=storage.input_routes.cells[role]
    return route and not route.fault and route.flow and route.flow.conservation==true
end
local function synchronize(m,before)
    if m.expected_count~=before then m.credit=0;m.attribution_resets=m.attribution_resets+1 end
end
c.successor_before_transfer=function(role,item,quantity,extracting)
    local p=fair.actor()
    for source,m in pairs(s.records) do
        identity(source,m);assert(not m.fault,"Successor requires reconciliation")
        local out=storage.output_buffers.cells[source]
        if role==source then
            assert(not extracting,"Do not manually extract successor furnace output")
            if item~="coal" then
                assert(item==roles[source] and not input_ready(source) and m.seeded+quantity<=10
                    and not storage.input_routes.cells[source],"Only bounded output-commissioning seed is permitted")
            end
        end
        if out and role==out.chest_role then
            assert(extracting and input_ready(source),"Successor output requires ore-to-plate flow")
            assert(m.qualification or m.trial_collected+quantity<=200,"Successor trial collection budget exhausted")
        end
    end
    return p.get_item_count(item)
end
c.successor_after_transfer=function(role,item,quantity,receipt,extracting,before)
    local p=fair.actor();local after=p.get_item_count(item)
    assert(after-before==(extracting and quantity or -quantity),"Successor attribution transfer mismatch")
    for source,m in pairs(s.records) do
        if role==source and not extracting and item==roles[source] then
            m.seeded=m.seeded+quantity;m.seed_uncollected=m.seed_uncollected+quantity
        end
        if item==string.sub(source,8) then
            synchronize(m,before)
            local out=storage.output_buffers.cells[source]
            if extracting and out and role==out.chest_role and input_ready(source) then
                local old=math.min(quantity,m.seed_uncollected)
                m.seed_uncollected=m.seed_uncollected-old
                m.credit=m.credit+quantity-old;m.trial_collected=m.trial_collected+quantity
            elseif not extracting then
                local attributable=math.max(0,quantity-(before-m.credit))
                m.credit=math.max(0,m.credit-attributable)
            end
            m.expected_count=after
        end
    end
end
c.successor_craft_paid=function(job,before)
    local p=fair.actor();local claims={}
    for source,m in pairs(s.records) do
        local item=string.sub(source,8);local count=job.inputs[item]
        if count then
            synchronize(m,before[item])
            local attributable=math.max(0,count-(before[item]-m.credit))
            m.credit=math.max(0,m.credit-attributable);m.expected_count=p.get_item_count(item)
            if attributable>0 and useful[job.recipe] and input_ready(source) then
                claims[source]={quantity=attributable,source_unit=m.source_unit,
                    layout=storage.input_routes.cells[source].layout}
            end
        end
    end
    -- Losing attribution is conservative: never reinterpret a different job as
    -- completion of an older one, and never change its native job status.
    if next(claims) then s.pending_use={job_id=job.id,claims=claims} else s.pending_use=nil end
end
c.observe_successors=function(result)
    local p=fair.actor();local pending=s.pending_use;local job=result.craft_job
    if pending and job and job.id==pending.job_id and job.status=="completed" and job.paid
        and job.queue_valid and job.accepted==job.requested and job.finished==job.requested
        and job.session_id==storage.jev_session_id and job.player_index==p.index
        and job.unit_number==p.character.unit_number and job.surface_index==p.surface.index
        and job.force_index==p.force.index then
        local complete=true
        for item,count in pairs(job.outputs) do
            if p.get_item_count(item)<job.baseline[item]+count then complete=false end
        end
        if complete then
            for source,claim in pairs(pending.claims) do
                local m=s.records[source];local route=storage.input_routes.cells[source]
                if m and route and input_ready(source) and m.source_unit==claim.source_unit
                    and route.layout==claim.layout and claim.quantity>=3 and not m.use then
                    m.use={job_id=job.id,recipe=job.recipe,quantity=claim.quantity,source_unit=m.source_unit,
                        input_layout=route.layout,completed_tick=job.completed_tick,
                        requested=job.requested,finished=job.finished,outputs=job.outputs}
                end
            end
            s.pending_use=nil
        end
    end
    local rows={}
    for role,m in pairs(s.records) do
        local ok=pcall(identity,role,m)
        if not ok then m.fault="successor_identity_changed" end
        local source=c.entities[role];local input=result.input_routes.sources[role]
        local output=result.output_buffers.sources[role]
        local phase="reserved";local remaining=0
        if m.source_unit>0 then phase="output_building" end
        if output and next(output.parts) and not next(output.flow) then phase="output_commissioning" end
        if output and next(output.flow) then phase="input_building" end
        local flowing=input and input.state=="ready" and input.topology and next(input.flow)
        if flowing then
            phase="producing"
            local native=storage.input_routes.cells[role]
            if native and native.patch then for _,ore in ipairs(native.patch) do if ore.valid then remaining=remaining+ore.amount end end end
            local count=source.products_finished
            local window=m.window
            if not integer(count) or window and count<window.last_produced then m.fault="successor_counter_regressed"
            elseif not m.qualification then
                if not window or game.tick-window.last_tick>1800 or game.tick-window.last_progress_tick>1800 then
                    m.window={first_tick=game.tick,last_tick=game.tick,baseline=count,last_produced=count,positive_samples=0,last_progress_tick=game.tick,max_gap=0}
                    window=m.window
                elseif game.tick-window.last_tick>=60 then
                    window.max_gap=math.max(window.max_gap,game.tick-window.last_tick)
                    if count>window.last_produced then window.positive_samples=window.positive_samples+1;window.last_progress_tick=game.tick end
                    window.last_tick=game.tick;window.last_produced=count
                end
                if window.positive_samples>=3 and window.last_tick-window.first_tick>=36000
                    and count-window.baseline>=3 and m.use then
                    m.qualification={first_tick=window.first_tick,last_tick=window.last_tick,
                        positive_samples=window.positive_samples,produced=count-window.baseline,
                        last_progress_tick=window.last_progress_tick,max_observation_gap=window.max_gap,
                        source_unit=m.source_unit,input_layout=input.layout,use_job_id=m.use.job_id}
                end
            end
            if m.qualification and not m.fault then phase="preferred" end
        end
        if m.fault then phase="fault" end
        local item=string.sub(role,8);synchronize(m,p.get_item_count(item));m.expected_count=p.get_item_count(item)
        rows[role]={source=role,item=item,anchor=m.anchor,predecessor=predecessor(role),
            predecessor_unit=m.predecessor_unit,source_unit=m.source_unit,paid=m.paid,
            phase=phase,seeded=m.seeded,trial_collected=m.trial_collected,credit=m.credit,
            attribution_resets=m.attribution_resets,started_tick=m.started_tick,remaining_ore=remaining,
            use=m.use or {},qualification=m.qualification or {},fault=m.fault}
    end
    return {protocol=1,session_id=storage.jev_session_id,tick=game.tick,sources=rows}
end
