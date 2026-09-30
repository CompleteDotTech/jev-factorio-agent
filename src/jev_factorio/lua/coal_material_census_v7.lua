    -- v7 census appended to the fixed v5 graph query. The installed v5
    -- observer/journal stays byte-identical; this query adds bounded current-
    -- surface alternatives and records future generated terrain as unknown.
    local installation=rt.native_installation
    local cycle=rt.coal_manual_cycle_v2
    local v5_profile="e759-observation-v2-water-origin-v4-manual-cycle-v5-connector-observer-v1"
    local v6_profile="e759-observation-v2-water-origin-v4-manual-cycle-v6-connector-observer-v1"
    local cycle_v2_installed=installation and installation.profile==v6_profile
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
        and cycle.active==nil
    local v5_only=installation and installation.profile==v5_profile
        and installation.assets and installation.assets.coal_manual_cycle_v2==nil
        and installation.callbacks and installation.callbacks.cycle_tick==nil
        and cycle==nil
    need(cycle_v2_installed or v5_only,"material_census_cycle_unqualified")
    local cycle_rows={};local cycle_seen={}
    if cycle_v2_installed then
      sequence(cycle.order,32);bounded(cycle.rows,32)
      local cycle_count=0;for _ in pairs(cycle.rows) do cycle_count=cycle_count+1 end
      need(cycle_count==#cycle.order,"material_census_cycle_count")
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
    end
    out.cycle_v2={status=cycle_v2_installed and "observed" or "not_installed",
        journal_asset_sha256=cycle_v2_installed and
            installation.assets.coal_manual_cycle_v2 or false,
        cycle_complete=false,rows=cycle_rows}
    -- The guard persists its one-use journal in Factorio's global storage.
    -- Read that exact namespace here; `rt` is only the runtime API table.
    local admission_journal=rt.coal_native_admission_journal_v1
    local admission_history=false
    if admission_journal then
        need(admission_journal.schema=="jev.native-coal-admission-journal.v1"
            and admission_journal.protocol==1
            and admission_journal.session_id==rt.jev_session_id
            and admission_journal.actor_unit==actor.unit_number
            and admission_journal.native_profile==installation.profile
            and admission_journal.guard_asset_sha256=="__ADMISSION_ASSET_SHA256__"
            and admission_journal.builder_asset_sha256=="__COAL_SUPPLY_ASSET_SHA256__"
            and admission_journal.legacy_manual_history
            and admission_journal.legacy_manual_history.status=="observed"
            and admission_journal.legacy_manual_history.reason=="qualified_journal_rows"
            and admission_journal.legacy_manual_history.journal_asset_sha256==
                installation.assets.coal_manual_journal_v1
            and type(admission_journal.order)=="table"
            and type(admission_journal.rows)=="table",
            "material_census_admission_journal_identity")
        sequence(admission_journal.order,32);bounded(admission_journal.rows,32)
        local attempts={};local attempt_seen={}
        for _,receipt in ipairs(admission_journal.order) do
            need(text(receipt) and not attempt_seen[receipt],
                "material_census_admission_receipt_alias")
            attempt_seen[receipt]=true
            local row=admission_journal.rows[receipt]
            need(row and row.schema=="jev.native-coal-admission-attempt.v1"
                and row.receipt==receipt and row.session_id==rt.jev_session_id
                and row.actor_unit==actor.unit_number
                and row.source_asset_sha256=="__ADMISSION_ASSET_SHA256__"
                and row.builder_asset_sha256=="__COAL_SUPPLY_ASSET_SHA256__"
                and (row.phase=="dispatching" or row.phase=="unknown" or row.phase=="paid")
                and text(row.target) and text(row.layout) and row.part=="chest"
                and integer(row.tick,0,game.tick)
                and row.admission and row.admission.schema=="jev.coal-native-admission.v1"
                and row.admission.qualified==true
                and row.admission.source_asset_sha256==row.source_asset_sha256
                and row.admission.session_id==row.session_id
                and row.admission.actor_unit==row.actor_unit
                and row.admission.tick==row.tick,
                "material_census_admission_attempt")
            local source_row=q.rows[row.target]
            local part=source_row and source_row.parts and source_row.parts[row.part]
            local current_payment=false
            if part then
                local entity=c.entities[part.role]
                need(part.role=="coal:"..row.target..":"..row.part
                    and part.receipt==receipt and part.paid==1 and entity and entity.valid
                    and part.unit_number==entity.unit_number,
                    "material_census_admission_payment_identity")
                current_payment={role=part.role,unit_number=part.unit_number,
                    paid=part.paid}
            end
            if row.phase=="paid" then
                need(type(current_payment)=="table" and row.paid
                    and row.paid.role==current_payment.role
                    and row.paid.unit_number==current_payment.unit_number
                    and row.paid.paid==current_payment.paid
                    and integer(row.paid.tick,row.tick,game.tick),
                    "material_census_admission_paid_readback")
            else
                need(row.paid==nil,"material_census_admission_ambiguous_paid_field")
            end
            attempts[#attempts+1]={schema=row.schema,phase=row.phase,
                session_id=row.session_id,actor_unit=row.actor_unit,target=row.target,
                layout=row.layout,part=row.part,receipt=row.receipt,tick=row.tick,
                source_asset_sha256=row.source_asset_sha256,
                builder_asset_sha256=row.builder_asset_sha256,
                admission=row.admission,paid=row.paid or false,
                current_payment=current_payment}
        end
        local attempt_count=0
        for receipt in pairs(admission_journal.rows) do
            attempt_count=attempt_count+1
            need(attempt_seen[receipt],"material_census_admission_orphan_row")
        end
        need(attempt_count==#attempts,"material_census_admission_row_count")
        admission_history={status="observed",schema=admission_journal.schema,
            protocol=admission_journal.protocol,session_id=admission_journal.session_id,
            actor_unit=admission_journal.actor_unit,
            guard_asset_sha256=admission_journal.guard_asset_sha256,
            builder_asset_sha256=admission_journal.builder_asset_sha256,
            native_profile=admission_journal.native_profile,
            legacy_manual_history=admission_journal.legacy_manual_history,
            rows=attempts}
    end
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
            "cargo-wagon"},limit=2049}
    need(#stock_entities<2049,"material_census_alternate_bound")
    for _,e in ipairs(stock_entities) do
        need(e.valid and expected[e.unit_number] and seen[e.unit_number],
            "material_census_alternate_stock")
    end
    -- A bounded local alternative census supports an explicit collection-cost
    -- estimate. It does not claim that the rest of the generated surface is
    -- empty or complete, and future chunks remain unknown.
    local copper_role
    for _,target in ipairs(out.research_work and out.research_work.targets or {}) do
        if target.recipe=="copper-plate" then
            need(not copper_role,"material_census_consumer_alias")
            copper_role=target.role
        end
    end
    local copper_consumer=copper_role and c.entities[copper_role]
    if not copper_consumer then
        for _,role in ipairs(q.targets) do
            local candidate=c.entities[role]
            if candidate and candidate.name=="stone-furnace" then
                need(not copper_consumer,"material_census_consumer_alias")
                copper_consumer=candidate
            end
        end
    end
    need(copper_consumer and copper_consumer.valid
        and copper_consumer.surface==actor.surface
        and copper_consumer.force==actor.force,
        "material_census_consumer_identity")
    local actor_position=point(actor.position)
    local consumer_position=point(copper_consumer.position)
    local centers={actor_position,consumer_position}
    local function local_entities(kind,code)
        local by_unit={};local count=0
        for _,center in ipairs(centers) do
            local found_local=actor.surface.find_entities_filtered{
                type=kind,area={left_top={x=center.x-64,y=center.y-64},
                    right_bottom={x=center.x+64,y=center.y+64}},limit=257}
            need(type(found_local)=="table" and #found_local<=256,code)
            for _,entity in ipairs(found_local) do
                need(entity and entity.valid and entity.surface==actor.surface
                    and entity.unit_number
                    and integer(entity.unit_number,1,9007199254740991),code)
                local prior=by_unit[entity.unit_number]
                need(not prior or prior==entity,code)
                if not prior then
                    by_unit[entity.unit_number]=entity
                    count=count+1
                    need(count<=512,code)
                end
            end
        end
        local result={}
        for _,entity in pairs(by_unit) do result[#result+1]=entity end
        table.sort(result,function(a,b) return a.unit_number<b.unit_number end)
        return result
    end
    local ground=local_entities("item-entity","material_census_ground_bound")
    local trees=local_entities("tree","material_census_tree_bound")
    local function item_fuel(name)
        local p=prototypes.item[name]
        need(p and finite(p.fuel_value or 0,0,1000000000000),
            "material_census_item_prototype")
        local category=p.fuel_category
        need(category==nil or text(category),"material_census_fuel_category")
        return {name=name,fuel_value=p.fuel_value or 0,
            fuel_category=category or ""}
    end
    local ground_rows={};local ground_units={};local ground_total=0
    for _,e in ipairs(ground) do
        local stack=e.stack
        need(e.valid and e.surface==actor.surface and e.unit_number
            and integer(e.unit_number,1,9007199254740991)
            and not ground_units[e.unit_number]
            and stack and stack.valid_for_read
            and text(stack.name) and integer(stack.count,1,200000)
            and stack.quality and stack.quality.name=="normal",
            "material_census_ground_stack")
        ground_units[e.unit_number]=true;ground_total=ground_total+stack.count
        need(ground_total<=2000000,"material_census_ground_count")
        local fuel=item_fuel(stack.name)
        ground_rows[#ground_rows+1]={unit=e.unit_number,name=stack.name,
            count=stack.count,position=point(e.position),fuel_value=fuel.fuel_value,
            fuel_category=fuel.fuel_category}
    end
    table.sort(ground_rows,function(a,b) return a.unit<b.unit end)
    local base_mining_speed=actor.prototype and actor.prototype.mining_speed
    local force_mining_modifier=actor.force.manual_mining_speed_modifier
    local character_mining_modifier=player.character_mining_speed_modifier
    need(finite(base_mining_speed,.001,1000)
        and finite(force_mining_modifier,-.99,1000)
        and finite(character_mining_modifier,-.99,1000),
        "material_census_mining_modifier")
    local manual_mining_speed=base_mining_speed*(1+force_mining_modifier)
        *(1+character_mining_modifier)
    need(finite(manual_mining_speed,.001,1000),"material_census_mining_speed")
    local tree_rows={};local tree_units={};local tree_products={}
    for _,e in ipairs(trees) do
        need(e.valid and e.type=="tree" and e.surface==actor.surface
            and e.unit_number and integer(e.unit_number,1,9007199254740991)
            and not tree_units[e.unit_number],"material_census_tree_identity")
        tree_units[e.unit_number]=true
        local mine=e.prototype and e.prototype.mineable_properties
        local products={}
        local mining_time=0
        if mine and mine.minable then
            need(finite(mine.mining_time,.001,1000000)
                and not mine.required_fluid and type(mine.products)=="table"
                and #mine.products<=16,"material_census_tree_products")
            mining_time=mine.mining_time
            for _,product in ipairs(mine.products) do
                need(product.type=="item" and text(product.name)
                    and product.quality==nil and product.quality_min==nil
                    and product.quality_max==nil
                    and (product.probability==nil or finite(product.probability,0,1))
                    and (product.extra_count_fraction==nil
                        or product.extra_count_fraction==0),
                    "material_census_tree_product")
                local minimum=product.amount_min or product.amount or product.amount_max
                local maximum=product.amount_max or product.amount or product.amount_min
                need(integer(minimum,1,200000) and integer(maximum,minimum,200000),
                    "material_census_tree_yield")
                local probability=product.probability or 1
                local expected_amount=((minimum+maximum)/2)*probability
                local fuel=item_fuel(product.name)
                products[#products+1]={name=product.name,minimum=minimum,
                    maximum=maximum,expected_amount=expected_amount,
                    fuel_value=fuel.fuel_value,fuel_category=fuel.fuel_category,
                    probability=probability}
                tree_products[product.name]=(tree_products[product.name] or 0)+maximum
                need(tree_products[product.name]<=2000000,
                    "material_census_tree_yield_bound")
                need(#products<=16,"material_census_tree_products")
            end
        else
            need(not mine or mine.minable==false,
                "material_census_tree_mining_unknown")
        end
        table.sort(products,function(a,b) return a.name<b.name end)
        for i=2,#products do need(products[i-1].name~=products[i].name,
            "material_census_tree_product_alias") end
        tree_rows[#tree_rows+1]={unit=e.unit_number,name=e.name,
            position=point(e.position),mining_time=mining_time,products=products}
    end
    table.sort(tree_rows,function(a,b) return a.unit<b.unit end)
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
    -- Count each reachable owned inventory exactly once. The descriptive
    -- output above repeats fuel/output inventories; this separate map uses
    -- Lua object identity to prevent those overlapping views from inflating
    -- the available-stock bound used for current research demand.
    local owned_inventory_counts={};local counted_inventories={}
    local function count_inventory(inv)
        need(inv and inv.valid,"material_census_inventory")
        if counted_inventories[inv] then return end
        counted_inventories[inv]=true
        for _,stack in pairs(inv.get_contents()) do
            need(text(stack.name) and integer(stack.count,1,200000)
                and (not stack.quality or stack.quality=="normal"
                    or stack.quality.name=="normal"),"material_census_item")
            owned_inventory_counts[stack.name]=(owned_inventory_counts[stack.name] or 0)+stack.count
            need(owned_inventory_counts[stack.name]<=2000000,
                "material_census_owned_stock_bound")
        end
    end
    for index=1,actor_maximum do
        local inv=player.get_inventory(index)
        if inv then count_inventory(inv) end
    end
    for _,e in ipairs(found) do
        if e~=actor then
            local maximum=e.get_max_inventory_index()
            for index=1,maximum do
                local inv=e.get_inventory(index)
                if inv then count_inventory(inv) end
            end
            if e.type=="transport-belt" then
                for lane=1,2 do count_inventory(e.get_transport_line(lane)) end
            elseif e.type=="inserter" then
                local stack=e.held_stack
                need(stack,"material_census_hand")
                if stack.valid_for_read then
                    need(text(stack.name) and integer(stack.count,1,200000)
                        and stack.quality and stack.quality.name=="normal",
                        "material_census_hand")
                    owned_inventory_counts[stack.name]=(owned_inventory_counts[stack.name] or 0)+stack.count
                    need(owned_inventory_counts[stack.name]<=2000000,
                        "material_census_owned_stock_bound")
                end
            end
        end
    end
    local owned_inventory_rows={}
    for name,count in pairs(owned_inventory_counts) do
        owned_inventory_rows[#owned_inventory_rows+1]={name=name,count=count}
    end
    sorted(owned_inventory_rows,"name")
    local tree_product_rows={}
    for name,maximum in pairs(tree_products) do
        local fuel=item_fuel(name)
        tree_product_rows[#tree_product_rows+1]={name=name,maximum=maximum,
            fuel_value=fuel.fuel_value,fuel_category=fuel.fuel_category}
    end
    sorted(tree_product_rows,"name")
    out.material_census={status="observed",reason="bounded_owned_and_local_alternatives_v7",
        owned_surface_coverage_complete=true,actor_unit=actor.unit_number,
        native_profile=installation.profile,
        cycle_asset_sha256=cycle_v2_installed and
            installation.assets.coal_manual_cycle_v2 or false,
        admission_journal=admission_history,
        actor_items=inventory_items(player.get_main_inventory()),entities=rows,
        actor_inventories=actor_inventories,ground_items=#ground,trees=#trees,
        crafting_queue=0,actor_position=actor_position,
        manual_mining_speed=manual_mining_speed,
        alternative_stock={status="observed",
            scope="actor_and_copper_consumer_radius_64",radius_tiles=64,
            copper_consumer_position=consumer_position,
            observed_generated_entities_only=true,
            unobserved_chunks_inside_scope_unknown=true,
            unobserved_current_surface_outside_scope=true,
            future_generated_chunks_unknown=true,ground_item_entities=ground_rows,
            trees=tree_rows,tree_product_upper_bounds=tree_product_rows,
            ground_item_count=#ground,tree_count=#trees,
            owned_inventory_counts=owned_inventory_rows}}
