    -- Inserted into the fixed v5 graph query before its success assignment.
    -- This narrow census refuses any owner or material source it cannot count.
    local installation=rt.native_installation
    local cycle=rt.coal_manual_cycle_v2
    need(installation and installation.profile==
        "e759-observation-v2-water-origin-v4-manual-cycle-v6"
        and installation.assets and installation.assets.coal_manual_cycle_v2==
            "__CYCLE_ASSET_SHA256__"
        and installation.callbacks and cycle and cycle.protocol==2
        and installation.callbacks.cycle_tick==cycle.combined_tick_handler
        and type(cycle.combined_tick_handler)=="function"
        and cycle.session_id==rt.jev_session_id
        and cycle.actor_unit==actor.unit_number
        and cycle.actor_index==index
        and cycle.surface_index==actor.surface.index
        and cycle.force_index==actor.force.index
        and cycle.active==nil,"material_census_cycle_unqualified")
    sequence(cycle.order,32);bounded(cycle.rows,32)
    local cycle_count=0;for _ in pairs(cycle.rows) do cycle_count=cycle_count+1 end
    need(cycle_count==#cycle.order,"material_census_cycle_count")
    local cycle_rows={};local cycle_seen={}
    for _,receipt in ipairs(cycle.order) do
        need(text(receipt) and not cycle_seen[receipt],"material_census_cycle_receipt")
        cycle_seen[receipt]=true
        local row=cycle.rows[receipt]
        local g=row and journal.rows[row.gather_receipt]
        need(row and row.receipt==receipt and row.status=="complete"
            and row.fault==false and row.pending_delivery==nil
            and row.session_id==rt.jev_session_id and row.actor_index==index
            and row.actor_unit==actor.unit_number
            and row.surface_index==actor.surface.index
            and row.force_index==actor.force.index
            and integer(row.started_tick,0,game.tick)
            and integer(row.finished_tick,row.started_tick+1,game.tick)
            and g and g.status=="complete" and g.coal_before==0
            and g.coal_after>=1 and g.coal_after<=200
            and row.gather and row.gather.started_tick==g.started_tick
            and row.gather.finished_tick==g.finished_tick
            and row.gather.coal_before==g.coal_before
            and row.gather.coal_after==g.coal_after
            and row.gather.walking_ticks==g.walking_ticks
            and row.gather.mining_ticks==g.mining_ticks
            and integer(row.receipt_count,0,#c.receipt_order)
            and integer(row.receipt_end,row.receipt_count,#c.receipt_order),
            "material_census_cycle_changed")
        sequence(row.deliveries,4)
        need(#row.deliveries==#targets,"material_census_cycle_targets")
        local deliveries={};local total=0
        local previous=g.finished_tick
        for i,d in ipairs(row.deliveries) do
            local native=c.receipts[d.receipt]
            local target=c.entities[d.role]
            need(d.role==targets[i] and target and target.valid
                and d.unit==target.unit_number
                and native and native.role==d.role
                and native.unit_number==d.unit and native.item=="coal"
                and native.extracting==false and native.quantity==d.coal
                and native.tick==d.tick and integer(d.coal,1,200)
                and integer(d.started_tick,previous,row.finished_tick)
                and integer(d.finished_tick,d.started_tick,row.finished_tick)
                and d.started_tick<=d.tick and d.tick<=d.finished_tick
                and integer(d.walking_ticks,0,d.finished_tick-d.started_tick),
                "material_census_cycle_delivery")
            total=total+d.coal
            previous=d.finished_tick
            deliveries[#deliveries+1]={receipt=d.receipt,role=d.role,
                unit=d.unit,coal=d.coal,tick=d.tick,
                started_tick=d.started_tick,finished_tick=d.finished_tick,
                walking_ticks=d.walking_ticks}
        end
        need(total<=g.coal_after and row.expected_coal==g.coal_after-total,
            "material_census_cycle_conservation")
        local actual={}
        for i=row.receipt_count+1,row.receipt_end do
            local id=c.receipt_order[i];local transfer=c.receipts[id]
            if transfer and transfer.item=="coal" then actual[#actual+1]=id end
        end
        need(#actual==#deliveries,"material_census_cycle_receipts")
        for i,d in ipairs(deliveries) do
            need(actual[i]==d.receipt,"material_census_cycle_receipts")
        end
        cycle_rows[#cycle_rows+1]={receipt=receipt,
            gather_receipt=row.gather_receipt,
            started_tick=row.started_tick,finished_tick=row.finished_tick,
            gathered_coal=g.coal_after,delivered_coal=total,
            gather_mining_ticks=g.mining_ticks,deliveries=deliveries}
    end
    out.cycle_v2={status="observed",journal_asset_sha256=
        installation.assets.coal_manual_cycle_v2,cycle_complete=false,rows=cycle_rows}
    local surfaces=0
    for _,surface in pairs(game.surfaces) do
        surfaces=surfaces+1
        need(surface==actor.surface,"material_census_other_surface")
    end
    need(surfaces==1,"material_census_other_surface")
    local expected={}
    for _,entry in ipairs(out.registry) do
        need(not expected[entry.unit],"material_census_alias")
        expected[entry.unit]=entry
    end
    local found=actor.surface.find_entities_filtered{force=actor.force,limit=2049}
    need(#found<2049,"material_census_bound")
    local fuel_target={}
    for _,role in ipairs(q.targets) do fuel_target[role]=true end
    local seen={}
    local function inventory_items(inv)
        need(inv and inv.valid,"material_census_inventory")
        local values={}
        for _,stack in pairs(inv.get_contents()) do
            need(text(stack.name) and integer(stack.count,1,200000)
                and (not stack.quality or stack.quality=="normal"
                    or stack.quality.name=="normal"),"material_census_item")
            values[#values+1]={name=stack.name,count=stack.count}
            need(#values<=128,"material_census_item_bound")
        end
        sorted(values,"name")
        for i=2,#values do need(values[i-1].name~=values[i].name,
            "material_census_item_alias") end
        return values
    end
    local rows={}
    for _,e in ipairs(found) do
        if e~=actor then
            local entry=expected[e.unit_number]
            need(e.valid and entry and not seen[e.unit_number]
                and entry.name==e.name and entry.quality==e.quality.name
                and e.surface==actor.surface and e.force==actor.force,
                "material_census_foreign_entity")
            seen[e.unit_number]=true
            need(e.type~="underground-belt" and e.type~="splitter"
                and e.type~="loader" and e.type~="loader-1x1"
                and e.type~="car" and e.type~="spider-vehicle"
                and e.type~="cargo-wagon","material_census_unsupported_carrier")
            need(not e.burner or fuel_target[entry.role],
                "material_census_alternate_burner")
            local maximum=e.get_max_inventory_index()
            need(integer(maximum,0,128),"material_census_inventory_range")
            local inventories={}
            for index=1,maximum do
                local inv=e.get_inventory(index)
                if inv then inventories[#inventories+1]={index=index,items=inventory_items(inv)} end
            end
            local belts={}
            if e.type=="transport-belt" then
                for lane=1,2 do
                    local line=e.get_transport_line(lane)
                    need(line and line.valid,"material_census_belt")
                    belts[#belts+1]={lane=lane,items=inventory_items(line)}
                end
            end
            local held={}
            if e.type=="inserter" then
                local stack=e.held_stack
                need(stack,"material_census_hand")
                if stack.valid_for_read then
                    need(text(stack.name) and integer(stack.count,1,200000)
                        and stack.quality and stack.quality.name=="normal",
                        "material_census_hand")
                    held={{name=stack.name,count=stack.count}}
                end
            end
            if e.type=="furnace" or e.type=="assembling-machine"
                or e.type=="rocket-silo" then
                need(not e.is_crafting(),"material_census_inflight")
            end
            local fuel_inv=e.get_fuel_inventory()
            local output_inv=e.get_output_inventory()
            rows[#rows+1]={role=entry.role,unit=entry.unit,name=entry.name,
                inventories=inventories,belts=belts,held=held,
                fuel=fuel_inv and inventory_items(fuel_inv) or {},
                output=output_inv and inventory_items(output_inv) or {}}
        end
    end
    for unit in pairs(expected) do need(seen[unit],"material_census_missing_entity") end
    sorted(rows,"role")
    local stock_entities=actor.surface.find_entities_filtered{
        type={"container","logistic-container","assembling-machine","furnace",
            "transport-belt","underground-belt","splitter","inserter",
            "mining-drill","lab","rocket-silo","car","spider-vehicle",
            "cargo-wagon","item-entity"},limit=2049}
    need(#stock_entities<2049,"material_census_alternate_bound")
    for _,e in ipairs(stock_entities) do
        need(e.valid and expected[e.unit_number] and seen[e.unit_number],
            "material_census_alternate_stock")
    end
    -- Neutral ground stock and harvestable wood are alternatives. They cannot
    -- silently disappear from a positive closed-world material deficit.
    need(#actor.surface.find_entities_filtered{type="item-entity",limit=1}==0,
        "material_census_ground_item")
    need(#actor.surface.find_entities_filtered{type="tree",limit=1}==0,
        "material_census_tree_fuel")
    need(player.crafting_queue_size==0,"material_census_handcraft")
    local cursor=player.cursor_stack
    need(not cursor or not cursor.valid_for_read,"material_census_cursor")
    local actor_maximum=player.get_max_inventory_index()
    need(integer(actor_maximum,1,128),"material_census_actor_range")
    local actor_inventories={}
    for index=1,actor_maximum do
        local inv=player.get_inventory(index)
        if inv then actor_inventories[#actor_inventories+1]={index=index,
            items=inventory_items(inv)} end
    end
    out.material_census={status="observed",reason="bounded_owned_surface",
        owned_surface_coverage_complete=true,actor_unit=actor.unit_number,
        cycle_asset_sha256=installation.assets.coal_manual_cycle_v2,
        actor_items=inventory_items(player.get_main_inventory()),entities=rows,
        actor_inventories=actor_inventories,ground_items=0,trees=0,
        crafting_queue=0}
