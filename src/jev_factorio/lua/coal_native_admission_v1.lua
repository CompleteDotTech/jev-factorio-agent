-- Native, current-goal admission for the first paid coal source component.
-- This code is composed into the fixed v7 query and runs in the same RCON
-- request as build_coal_source for the first placement. It reads current game
-- facts only; no caller boolean or forecast can authorize the payment.
local function coal_native_admission_v1(out,actor,player,c,q,prepared_request,source_asset_sha256)
    local function reject(reason,details)
        return {schema="jev.coal-native-admission.v1",qualified=false,reason=reason,
            source_asset_sha256=source_asset_sha256,
            session_id=out.epoch and out.epoch.session_id or "",
            tick=out.epoch and out.epoch.tick or -1,
            actor_unit=out.epoch and out.epoch.actor_unit or 0,
            details=details or {}}
    end
    local function evaluate()
        need(type(source_asset_sha256)=="string" and #source_asset_sha256==64
            and source_asset_sha256:find("[^0-9a-f]")==nil,
            "native_admission_asset_unqualified")
        need(out.query_status=="observed" and out.reason=="bounded_native_projection",
            "native_query_unqualified")
        need(out.epoch and out.epoch.session_id==rt.jev_session_id
            and out.epoch.tick==game.tick and out.epoch.actor_unit==actor.unit_number
            and out.epoch.actor_index==player.index and player.character==actor
            and player.connected and #game.connected_players==1
            and game.connected_players[1]==player and game.speed==1 and not game.tick_paused,
            "native_epoch_changed")
        need(q.revision==4 and q.admission_evidence==true,"coal_treatment_unqualified")
        local pending_count=0
        for target,row in pairs(q.rows) do
            need(type(row)=="table" and not row.fault and not row.manual_pending,
                "coal_source_fault")
            if row.pending then
                pending_count=pending_count+1
                need(prepared_request and target==prepared_request.target
                    and row.pending.phase=="prepared"
                    and row.pending.part==prepared_request.part
                    and row.pending.receipt==prepared_request.receipt
                    and row.layout==prepared_request.layout
                    and row.parts and next(row.parts)==nil,
                    "coal_prepared_identity_changed")
            end
            if prepared_request then
                need(row.parts and next(row.parts)==nil,
                    "coal_first_payment_has_existing_parts")
            end
        end
        if prepared_request then
            need(q.committed==true and pending_count==1
                and prepared_request.part=="chest"
                and type(prepared_request.target)=="string"
                and type(prepared_request.receipt)=="string",
                "coal_first_payment_not_prepared")
            local installation=jev_fle_runtime.native_installation
            local solid=rt.solid_routes
            need(installation and installation.assets
                and installation.assets.coal_supply=="__COAL_SUPPLY_ASSET_SHA256__"
                and solid and solid.implementation_revision==4
                and solid.coal==q and solid.coal_api
                and type(q.prepare)=="function" and type(q.build)=="function"
                and jev_fle_runtime.campaign.prepare_coal_source==q.prepare
                and jev_fle_runtime.campaign.build_coal_source==q.build,
                "native_coal_builder_unqualified")
        else
            need(q.committed==false and pending_count==0,"coal_proposal_state_changed")
        end

        local attempt_journal=rt.coal_native_admission_journal_v1
        if attempt_journal then
            need(attempt_journal.schema=="jev.native-coal-admission-journal.v1"
                and attempt_journal.protocol==1
                and attempt_journal.session_id==out.epoch.session_id
                and attempt_journal.actor_unit==actor.unit_number
                and attempt_journal.guard_asset_sha256==source_asset_sha256
                and type(attempt_journal.order)=="table"
                and type(attempt_journal.rows)=="table",
                "coal_admission_journal_identity")
            sequence(attempt_journal.order,32);bounded(attempt_journal.rows,32)
            local ordered={};for _,id in ipairs(attempt_journal.order) do
                need(type(id)=="string" and not ordered[id]
                    and attempt_journal.rows[id]
                    and (attempt_journal.rows[id].phase=="paid"),
                    "coal_admission_journal_unresolved")
                ordered[id]=true
            end
            local count=0;for id,row in pairs(attempt_journal.rows) do
                count=count+1
                need(ordered[id] and row.receipt==id,"coal_admission_journal_rows")
            end
            need(count==#attempt_journal.order and count<=32,
                "coal_admission_journal_bound")
            if prepared_request then
                need(attempt_journal.rows[prepared_request.receipt]==nil,
                    "coal_admission_receipt_reused")
            end
        end

        local target_set={}
        for _,target in ipairs(q.targets) do
            need(type(target)=="string" and not target_set[target],"coal_target_alias")
            target_set[target]=true
        end
        need(target_set["utility:boiler"] and target_set["recipe:copper-plate"]
            and #q.targets==2,"unsupported_direct_coal_targets")
        local research=out.research_work
        need(research and research.status=="observed"
            and research.reason=="current_research_activity"
            and actor.force.current_research
            and actor.force.current_research.name==research.technology
            and actor.force.current_research.researched~=true,
            "current_research_unavailable")

        -- Require a native prerequisite path from the selected research to the
        -- rocket-silo goal. The Python checkpoint separately requires the
        -- campaign's active goal to be rocket launch.
        local technologies=actor.force.technologies
        local selected=technologies[research.technology]
        local goal=technologies["rocket-silo"]
        need(selected and goal and selected.enabled==true and selected.researched~=true,
            "current_research_not_enabled")
        local visiting,visited,path={},0,nil
        local function path_to(node,trail,depth)
            need(node and depth<=64,"technology_path_bound")
            if node.name==selected.name then path=trail;return true end
            need(not visiting[node.name],"technology_graph_cycle")
            visiting[node.name]=true;visited=visited+1
            need(visited<=512,"technology_graph_bound")
            local parents={}
            for name in pairs(node.prerequisites or {}) do parents[#parents+1]=name end
            table.sort(parents)
            for _,name in ipairs(parents) do
                local parent=technologies[name]
                need(parent,"technology_prerequisite_missing")
                local next_trail={};for _,item in ipairs(trail) do next_trail[#next_trail+1]=item end
                next_trail[#next_trail+1]=name
                if path_to(parent,next_trail,depth+1) then visiting[node.name]=nil;return true end
            end
            visiting[node.name]=nil
            return false
        end
        need(path_to(goal,{goal.name},0) and type(path)=="table",
            "research_not_on_rocket_goal_path")

        local census=out.material_census
        local alternative=census and census.alternative_stock
        need(alternative and alternative.status=="observed"
            and alternative.scope=="actor_and_copper_consumer_radius_64"
            and alternative.radius_tiles==64
            and alternative.observed_generated_entities_only==true
            and alternative.unobserved_chunks_inside_scope_unknown==true
            and alternative.unobserved_current_surface_outside_scope==true
            and alternative.future_generated_chunks_unknown==true,
            "current_stock_scope_unqualified")
        local stock={}
        for _,row in ipairs(alternative.owned_inventory_counts) do
            need(type(row.name)=="string" and integer(row.count,1,2000000),
                "owned_stock_invalid")
            need(not stock[row.name],"owned_stock_duplicate")
            stock[row.name]=row.count
        end
        local function add_stock(name,count)
            need(type(name)=="string" and integer(count,1,2000000),"alternative_stock_invalid")
            stock[name]=(stock[name] or 0)+count
            need(stock[name]<=9007199254740991,"alternative_stock_overflow")
        end
        local gross_stock={}
        for name,count in pairs(stock) do gross_stock[name]=count end

        -- The complete future source/corridor bill must already be carried and
        -- is reserved in full before calculating remaining research demand.
        local bill={}
        local placement_positions={}
        local function add_bill(name,count)
            need(type(name)=="string" and integer(count,1,4096),"coal_kit_invalid")
            bill[name]=(bill[name] or 0)+count
        end
        local function add_missing(specs,parts)
            for _,spec in ipairs(specs) do
                if not parts[spec.part] then
                    add_bill(spec.name,1)
                    need(type(spec.position)=="table"
                        and finite(spec.position.x,-1000000,1000000)
                        and finite(spec.position.y,-1000000,1000000),
                        "coal_placement_position_unavailable")
                    placement_positions[#placement_positions+1]=spec.position
                end
            end
        end
        local function network_source(role)
            for target in pairs(q.rows) do
                if role=="coal:"..target..":chest" then return true end
            end
            return false
        end
        local cells=rt.solid_routes and rt.solid_routes.cells or {}
        local target_names={}
        for target in pairs(q.rows) do target_names[#target_names+1]=target end
        table.sort(target_names)
        for _,target in ipairs(target_names) do
            local row=q.rows[target]
            add_missing(row.steps,row.parts)
            local source_role="coal:"..target..":chest"
            local cell
            for _,candidate in pairs(cells) do
                if candidate.source and candidate.source.role==source_role then
                    need(not cell,"coal_corridor_alias")
                    cell=candidate
                end
            end
            for _,spec in ipairs(row.corridor) do
                if not cell or not cell.parts[spec.part] then
                    add_bill(spec.name,1)
                    need(type(spec.position)=="table"
                        and finite(spec.position.x,-1000000,1000000)
                        and finite(spec.position.y,-1000000,1000000),
                        "coal_placement_position_unavailable")
                    placement_positions[#placement_positions+1]=spec.position
                end
            end
        end
        local other_cells={}
        for key,cell in pairs(cells) do
            need(cell.source and type(cell.source.role)=="string",
                "coal_corridor_source_identity")
            if not network_source(cell.source.role) then
                other_cells[#other_cells+1]={key=tostring(key),cell=cell,
                    role=cell.source.role}
            end
        end
        table.sort(other_cells,function(a,b)
            if a.role~=b.role then return a.role<b.role end
            return a.key<b.key
        end)
        for _,entry in ipairs(other_cells) do
            add_missing(entry.cell.steps,entry.cell.parts)
        end
        need(#placement_positions>0 and #placement_positions<=512,
            "coal_placement_scope_unavailable")
        need(type(actor.position)=="table"
            and finite(actor.position.x,-1000000,1000000)
            and finite(actor.position.y,-1000000,1000000),
            "coal_actor_position_unavailable")
        local placement_distance=0
        local previous_x,previous_y=actor.position.x,actor.position.y
        for _,position in ipairs(placement_positions) do
            placement_distance=placement_distance+math.abs(previous_x-position.x)
                +math.abs(previous_y-position.y)
            previous_x,previous_y=position.x,position.y
            need(finite(placement_distance,0,2000000000),
                "coal_placement_distance_bound")
        end
        local placement_service_ticks=#placement_positions*300
        local placement_travel_ticks=math.ceil(placement_distance*20)
        local remaining_setup_ticks=placement_service_ticks+placement_travel_ticks
        need(integer(remaining_setup_ticks,1,1000000000000),
            "coal_placement_setup_cost_unavailable")
        local main=player.get_main_inventory()
        need(main and main.valid,"coal_kit_inventory_unavailable")
        local whole_kit_carried=true
        for name,count in pairs(bill) do
            local carried=main.get_item_count(name)
            if carried<count then whole_kit_carried=false end
            if prepared_request then
                need(carried>=count,"whole_coal_kit_not_carried")
                need((stock[name] or 0)>=count,"whole_coal_kit_stock_mismatch")
                stock[name]=stock[name]-count
            else
                -- Read-only eligibility may precede the existing deterministic
                -- kit-funding lane. Reserve only the matching current stock;
                -- the funding planner must separately prove how missing kit
                -- items can be acquired. First payment still requires all of it.
                local available=math.min(stock[name] or 0,count)
                stock[name]=math.max(0,(stock[name] or 0)-available)
            end
        end

        -- Expand only the currently selected, finite science bill through
        -- unique enabled item recipes. Ambiguous, fluid, probabilistic,
        -- coproduct, or cyclic chains fail closed. No future technology or
        -- funding is credited.
        local multiplier=research.ignore_cost_multiplier and 1 or research.cost_multiplier
        need(finite(multiplier,0.001,100000)
            and finite(research.progress,0,1)
            and integer(research.unit_count,1,1000000),"research_bill_invalid")
        local units=math.ceil(research.unit_count*multiplier*(1-research.progress))
        need(integer(units,1,1000000000),"research_remainder_invalid")
        local shortages={};local research_consumed={};local recipe_batches={}
        local expansions=0;local stack_path={}
        local function recipe_for(item)
            local matches={}
            for name,recipe in pairs(actor.force.recipes) do
                if recipe.enabled and not recipe.hidden then
                    local products=recipe.products
                    if type(products)=="table" and #products==1 then
                        local product=products[1]
                        if product.type=="item" and product.name==item then
                            need(integer(product.amount,1,1000000)
                                and (product.probability==nil or product.probability==1)
                                and product.amount_min==nil and product.amount_max==nil
                                and product.quality==nil and product.quality_min==nil
                                and product.quality_max==nil,"research_product_unsupported")
                            matches[#matches+1]={name=name,recipe=recipe,product=product}
                        end
                    end
                end
            end
            if #matches==0 then return nil end
            need(#matches==1,"research_recipe_ambiguous")
            return matches[1]
        end
        local function expand(item,amount,depth)
            need(depth<=32 and integer(amount,0,1000000000),"research_expansion_bound")
            if amount==0 then return end
            local available=math.min(stock[item] or 0,amount)
            stock[item]=(stock[item] or 0)-available
            if available>0 then
                research_consumed[item]=(research_consumed[item] or 0)+available
            end
            local remaining=amount-available
            if remaining==0 then return end
            expansions=expansions+1
            need(expansions<=512 and not stack_path[item],"research_recipe_cycle_or_bound")
            local match=recipe_for(item)
            if not match then
                shortages[item]=(shortages[item] or 0)+remaining
                return
            end
            local recipe=match.recipe
            need(finite(recipe.energy,0.001,100000)
                and type(recipe.ingredients)=="table" and #recipe.ingredients<=16,
                "research_recipe_unsupported")
            local batches=math.ceil(remaining/match.product.amount)
            need(integer(batches,1,1000000000),"research_batch_bound")
            if item=="copper-plate" then
                need(match.name=="copper-plate","copper_recipe_not_direct")
                recipe_batches[match.name]=(recipe_batches[match.name] or 0)+batches
            end
            stack_path[item]=true
            for _,ingredient in ipairs(recipe.ingredients) do
                need(ingredient.type=="item" and type(ingredient.name)=="string"
                    and integer(ingredient.amount,1,1000000),"research_ingredient_unsupported")
                expand(ingredient.name,ingredient.amount*batches,depth+1)
            end
            stack_path[item]=nil
            local produced=match.product.amount*batches-remaining
            need(integer(produced,0,1000000000),"research_recipe_rounding")
            stock[item]=(stock[item] or 0)+produced
        end
        local research_demand={}
        for _,ingredient in ipairs(research.ingredients) do
            need(type(ingredient.name)=="string" and integer(ingredient.amount,1,1000),
                "research_ingredient_invalid")
            research_demand[ingredient.name]=(research_demand[ingredient.name] or 0)
                +ingredient.amount*units
        end
        for _,name in ipairs((function()
            local names={};for item in pairs(research_demand) do names[#names+1]=item end
            table.sort(names);return names
        end)()) do
            expand(name,research_demand[name],0)
        end
        local copper_batches=recipe_batches["copper-plate"] or 0
        need(integer(copper_batches,1,1000000000),"current_goal_has_no_copper_plate_demand")
        need(next(shortages)==nil,"current_research_bill_unfunded")

        local target
        for _,row in ipairs(research.targets) do
            if row.role=="recipe:copper-plate" then target=row end
        end
        need(target and target.recipe=="copper-plate" and target.crafting==false,
            "copper_target_not_current")
        local copper_entity=c.entities["recipe:copper-plate"]
        local boiler_entity=c.entities["utility:boiler"]
        need(copper_entity and copper_entity.valid and copper_entity.name=="stone-furnace"
            and boiler_entity and boiler_entity.valid and boiler_entity.name=="boiler",
            "coal_consumer_identity_changed")
        local copper_recipe=actor.force.recipes["copper-plate"]
        need(copper_recipe and copper_recipe.enabled and #copper_recipe.products==1
            and copper_recipe.products[1].type=="item"
            and copper_recipe.products[1].name=="copper-plate"
            and integer(copper_recipe.products[1].amount,1,1000000)
            and copper_recipe.products[1].amount_min==nil
            and copper_recipe.products[1].amount_max==nil
            and (copper_recipe.products[1].probability==nil
                or copper_recipe.products[1].probability==1)
            and target.recipe_energy==copper_recipe.energy,
            "copper_target_recipe_changed")
        local input_names={}
        for _,item in ipairs(copper_recipe.ingredients) do
            need(item.type=="item" and type(item.name)=="string"
                and integer(item.amount,1,1000000),"copper_input_unsupported")
            input_names[#input_names+1]=item.name
        end
        table.sort(input_names)
        need(#input_names==1 and input_names[1]=="copper-ore",
            "copper_recipe_not_supported_direct_chain")
        local copper_input={}
        for _,item in ipairs(target.input) do
            need(type(item.name)=="string" and integer(item.count,1,200000)
                and not copper_input[item.name],"copper_input_inventory_invalid")
            copper_input[item.name]=item.count
        end
        local ore_per_batch=copper_recipe.ingredients[1].amount
        need(integer(ore_per_batch,1,1000000),"copper_ore_recipe_amount_invalid")
        local copper_ore_required=copper_batches*ore_per_batch
        local copper_ore_input=copper_input["copper-ore"] or 0
        need(integer(copper_ore_required,1,1000000000)
            and copper_ore_input>=copper_ore_required,
            "copper_target_input_does_not_cover_current_goal")

        local protos={}
        for _,row in ipairs(out.prototypes) do protos[row.name]=row end
        local coal_fuel=out.coal_fuel_joules
        need(finite(coal_fuel,1,1000000000000),"coal_fuel_unavailable")
        local source_rows={}
        for _,row in ipairs(out.sources) do source_rows[row.target]=row end
        local function source_capacity(role)
            local row=source_rows[role]
            need(row and type(row.resources)=="table" and #row.resources>0,
                "coal_source_patch_unavailable")
            local ore=0;local slowest=0;local fastest=math.huge
            for _,resource in ipairs(row.resources) do
                need(resource.name=="coal" and integer(resource.amount,1,1000000000)
                    and finite(resource.mining_time,0.001,1000000),"coal_resource_unqualified")
                ore=ore+resource.amount;slowest=math.max(slowest,resource.mining_time)
                fastest=math.min(fastest,resource.mining_time)
                need(ore<=1000000000,"coal_resource_bound")
            end
            need(fastest<math.huge,"coal_resource_time_unknown")
            return ore,slowest,fastest
        end
        local drill=protos["electric-mining-drill"]
        local drill_witness,inserter_witness
        for _,row in ipairs(out.buffer_witnesses) do
            if row.name=="electric-mining-drill" then drill_witness=row.capacity end
            if row.name=="inserter" then inserter_witness=row.capacity end
        end
        need(drill and finite(drill.mining_speed,0.001,1000)
            and drill_witness and finite(drill_witness,0,1000000)
            and inserter_witness and finite(inserter_witness,0,1000000),
            "coal_power_prototype_unavailable")
        local kit_drills=bill["electric-mining-drill"] or 0
        local kit_inserters=bill.inserter or 0
        need(kit_drills==2 and kit_inserters>=4,
            "unsupported_whole_network_load")

        local existing_load=0;local generator_count=0
        local registry={}
        for _,row in ipairs(out.registry) do registry[row.unit]=row end
        for _,member in ipairs(out.electric_members) do
            local owned=registry[member.unit]
            need(owned,"electric_member_unowned")
            local proto=protos[owned.name]
            need(proto and finite(proto.max_usage,0,1000000000)
                and finite(proto.drain,0,1000000000),"electric_load_unknown")
            if owned.name=="steam-engine" then generator_count=generator_count+1
            else existing_load=existing_load+proto.max_usage+proto.drain end
        end
        need(generator_count>=1,"steam_generator_missing")
        existing_load=math.ceil(existing_load)
        local planned_load=math.ceil((bill["electric-mining-drill"] or 0)
                *((drill.max_usage or 0)+(drill.drain or 0))
            +(bill.inserter or 0)*((protos.inserter.max_usage or 0)
                +(protos.inserter.drain or 0)))
        local total_load=existing_load+planned_load
        local boiler_proto=protos.boiler;local engine_proto=protos["steam-engine"]
        local boiler_eff=boiler_proto and boiler_proto.burner_efficiency
        local engine_eff=engine_proto and engine_proto.generator_efficiency
        need(finite(boiler_eff,0.001,1) and finite(engine_eff,0.001,1)
            and finite(boiler_proto.max_usage,1,1000000000)
            and finite(engine_proto.max_production,1,1000000000),
            "steam_conversion_unknown")
        local generation=math.min(boiler_proto.max_usage,
            generator_count*engine_proto.max_production)
        need(total_load<generation,"strict_power_headroom_missing")
        local conversion=boiler_eff*engine_eff
        need(finite(conversion,0.001,1),"steam_conversion_invalid")
        local function accepted_energy(categories)
            local total=0
            local function add(name,count,fuel_value,fuel_category)
                if fuel_value>0 and categories[fuel_category] then
                    total=total+count*fuel_value
                    need(total<=9007199254740991,"alternative_fuel_overflow")
                end
            end
            local target_coal={}
            for _,row in ipairs(out.fuel_targets) do target_coal[row.role]=row.coal end
            for name,count in pairs(gross_stock) do
                local reserved=(bill[name] or 0)+(research_consumed[name] or 0)
                local available=math.max(0,count-reserved)
                if name=="coal" then
                    for _,coal_count in pairs(target_coal) do available=math.max(0,available-coal_count) end
                end
                local item=prototypes.item[name]
                if item and item.fuel_category and item.fuel_value then
                    add(name,available,item.fuel_value,item.fuel_category)
                end
            end
            return total
        end
        local boiler_categories=boiler_entity.burner and boiler_entity.burner.fuel_categories
        local copper_categories=copper_entity.burner and copper_entity.burner.fuel_categories
        need(type(boiler_categories)=="table" and type(copper_categories)=="table",
            "burner_categories_unknown")
        local boiler_alternatives=accepted_energy(boiler_categories)
        local copper_alternatives=accepted_energy(copper_categories)
        local stored={}
        for _,row in ipairs(out.fuel_targets) do
            stored[row.role]=row.coal*coal_fuel
                +math.floor(row.remaining_burning_fuel+row.heat)
        end
        local copper_ore,copper_slowest,copper_fastest=source_capacity("recipe:copper-plate")
        local boiler_ore,boiler_slowest=source_capacity("utility:boiler")
        need(copper_slowest>0 and boiler_slowest>0,"coal_resource_time_unknown")
        local copper_rate=drill.mining_speed/(copper_slowest*60)
        local boiler_rate=drill.mining_speed/(boiler_slowest*60)
        local copper_eff=protos["stone-furnace"].burner_efficiency
        need(finite(copper_eff,0.001,1),"copper_burner_efficiency_unknown")
        local work_ticks=math.ceil(copper_batches*target.recipe_energy*60
            /target.crafting_speed)
        need(integer(work_ticks,1,1000000000),"research_service_work_bound")
        local copper_fuel_need=math.ceil(work_ticks
            *protos["stone-furnace"].max_usage)
        local copper_net_need=math.max(0,copper_fuel_need
            -stored["recipe:copper-plate"]-copper_alternatives)
        need(copper_net_need>0,"copper_coal_net_margin_missing")
        local manual_coal_units=math.ceil(copper_net_need/(coal_fuel*copper_eff))
        need(integer(manual_coal_units,1,1000000000)
            and manual_coal_units<=copper_ore,
            "manual_copper_fuel_alternative_unavailable")
        local manual_coal_ticks=math.ceil(manual_coal_units*copper_fastest*60
            /census.manual_mining_speed)
        need(integer(manual_coal_ticks,1,1000000000000),
            "manual_copper_fuel_time_unavailable")
        local function local_collection_forecast(categories,needed_energy)
            local candidates={}
            for _,row in ipairs(alternative.ground_item_entities) do
                if row.fuel_value>0 and categories[row.fuel_category] then
                    candidates[#candidates+1]={kind="ground",unit=row.unit,
                        position=row.position,energy=row.count*row.fuel_value,
                        service_ticks=60}
                end
            end
            for _,tree in ipairs(alternative.trees) do
                if tree.mining_time>0 then
                    local energy=0
                    for _,product in ipairs(tree.products) do
                        if product.fuel_value>0 and categories[product.fuel_category] then
                            energy=energy+product.expected_amount
                                *product.fuel_value
                        end
                    end
                    energy=math.floor(energy)
                    if energy>0 then
                        candidates[#candidates+1]={kind="tree",unit=tree.unit,
                            position=tree.position,energy=energy,
                            service_ticks=math.ceil(tree.mining_time*60
                                /census.manual_mining_speed)}
                    end
                end
            end
            table.sort(candidates,function(a,b)
                local left=a.service_ticks/a.energy
                local right=b.service_ticks/b.energy
                if left~=right then return left<right end
                if a.kind~=b.kind then return a.kind<b.kind end
                return a.unit<b.unit
            end)
            local remaining=needed_energy
            local collection_ticks=0;local collected_energy=0;local used=0
            local x,y=census.actor_position.x,census.actor_position.y
            for _,candidate in ipairs(candidates) do
                if remaining<=0 then break end
                local distance=math.abs(x-candidate.position.x)
                    +math.abs(y-candidate.position.y)
                need(finite(distance,0,2000000),"manual_alternative_distance_invalid")
                collection_ticks=collection_ticks+math.ceil(distance*20)
                    +candidate.service_ticks
                local amount=math.min(remaining,candidate.energy)
                collected_energy=collected_energy+amount
                remaining=math.max(0,remaining-amount)
                x,y=candidate.position.x,candidate.position.y
                used=used+1
            end
            local remaining_coal=math.ceil(remaining/(coal_fuel*copper_eff))
            local remaining_coal_ticks=math.ceil(remaining_coal*copper_fastest*60
                /census.manual_mining_speed)
            return collection_ticks+remaining_coal_ticks,collection_ticks,
                collected_energy,used
        end
        local collection_ticks,collected_energy,collected_sources
        local local_service_ticks,local_collection_ticks,local_collected_energy,
            local_source_count=local_collection_forecast(copper_categories,copper_net_need)
        local manual_service_ticks=math.min(manual_coal_ticks,local_service_ticks)
        if local_service_ticks<=manual_coal_ticks then
            collection_ticks=local_collection_ticks
            collected_energy=local_collected_energy
            collected_sources=local_source_count
        else
            collection_ticks=0;collected_energy=0;collected_sources=0
        end
        need(integer(manual_service_ticks,1,1000000000000)
            and manual_service_ticks>remaining_setup_ticks,
            "manual_fuel_service_forecast_not_positive")
        local copper_mined=math.min(copper_ore,math.floor(copper_rate*work_ticks))
        local copper_output=copper_mined*coal_fuel*copper_eff
        need(copper_rate*coal_fuel*copper_eff>protos["stone-furnace"].max_usage
            and copper_output>copper_net_need,
            "copper_coal_net_margin_missing")

        local electric_joules=math.ceil(total_load*work_ticks)
        local buffer=0
        for _,member in ipairs(out.electric_members) do
            buffer=buffer+(member.buffer_capacity or 0)
        end
        local segment_capacities={}
        for _,member in ipairs(out.fluid_members) do
            for _,box in ipairs(member.boxes) do
                if box.fluid=="steam" or box.filter=="steam" then
                    local old=segment_capacities[box.segment_id]
                    need(not old or old==box.capacity,"steam_buffer_segment_changed")
                    segment_capacities[box.segment_id]=box.capacity
                end
            end
        end
        local steam={};for _,row in ipairs(out.fluid_prototypes) do if row.name=="steam" then steam=row end end
        need(finite(steam.heat_capacity,1,1000000),"steam_buffer_capacity_unknown")
        for _,capacity in pairs(segment_capacities) do
            buffer=buffer+capacity*steam.heat_capacity*(165-15)
        end
        buffer=buffer+(bill["electric-mining-drill"] or 0)*drill_witness
            +(bill.inserter or 0)*inserter_witness
        local required_electric=electric_joules+buffer
        local boiler_fuel_need=math.ceil(required_electric/conversion)
        local boiler_net_need=math.max(0,boiler_fuel_need
            -stored["utility:boiler"]-boiler_alternatives)
        local boiler_mined=math.min(boiler_ore,math.floor(boiler_rate*work_ticks))
        local boiler_output=boiler_mined*coal_fuel
        need(boiler_net_need>0 and boiler_rate*coal_fuel*conversion>total_load
            and boiler_output>boiler_net_need,
            "boiler_coal_net_margin_missing")

        return {schema="jev.coal-native-admission.v1",qualified=true,
            reason="current_research_direct_copper_fuel_and_setup_estimate_margin",
            source_asset_sha256=source_asset_sha256,
            session_id=out.epoch.session_id,tick=out.epoch.tick,
            actor_unit=out.epoch.actor_unit,technology=research.technology,
            rocket_goal_path=path,direct_target="recipe:copper-plate",
            remaining_research_units=units,research_demand=research_demand,
            copper_plate_recipe_batches=copper_batches,
            current_copper_ore_available=stock["copper-ore"] or 0,
            productive_copper_recipe_ticks=work_ticks,
            whole_construction_kit=bill,
            copper_plate_ore_input_required=copper_ore_required,
            copper_plate_ore_input_available=copper_ore_input,
            whole_construction_kit_carried=whole_kit_carried,
            current_placement_count=#placement_positions,
            current_placement_manhattan_distance_tiles_estimate=placement_distance,
            placement_service_ticks_estimate=placement_service_ticks,
            placement_travel_ticks_estimate=placement_travel_ticks,
            remaining_project_setup_ticks_estimate=remaining_setup_ticks,
            manual_copper_coal_units_estimate=manual_coal_units,
            manual_copper_mining_ticks_estimate_no_walk=manual_coal_ticks,
            manual_fuel_service_ticks_estimate=manual_service_ticks,
            manual_local_collection_ticks_estimate=collection_ticks,
            manual_local_collection_fuel_joules_estimate=collected_energy,
            manual_local_collection_source_count_estimate=collected_sources,
            manual_fuel_service_basis="local_expected_tree_yield_60_tick_pickup_20_tick_tiles_direct_coal_no_walk_v1",
            copper_fuel_categories=(function()
                local values={};for name,enabled in pairs(copper_categories) do
                    if enabled then values[#values+1]=name end
                end;table.sort(values);return values end)(),
            copper_coal_fuel_joules_per_item_estimate=coal_fuel*copper_eff,
            copper_fastest_coal_mining_time_seconds_estimate=copper_fastest,
            existing_load_joules_per_tick=existing_load,
            planned_load_joules_per_tick=planned_load,
            total_load_joules_per_tick=total_load,
            generation_capacity_joules_per_tick=generation,
            boiler_conversion_efficiency=boiler_eff,
            generator_conversion_efficiency=engine_eff,
            boiler_source_coal_per_tick_lower=boiler_rate,
            copper_source_coal_per_tick_lower=copper_rate,
            boiler_source_finite_ore=boiler_ore,copper_source_finite_ore=copper_ore,
            boiler_source_energy_joules_lower=boiler_output,
            copper_source_energy_joules_lower=copper_output,
            whole_project_electric_joules_upper=electric_joules,
            buffer_energy_joules_upper=buffer,
            current_boiler_fuel_joules_lower=stored["utility:boiler"],
            current_copper_fuel_joules_lower=stored["recipe:copper-plate"],
            current_boiler_alternative_fuel_joules_upper=boiler_alternatives,
            current_copper_alternative_fuel_joules_upper=copper_alternatives,
            boiler_residual_demand_joules_upper=boiler_net_need,
            copper_residual_demand_joules_upper=copper_net_need,
            current_surface_only=true,local_scope_only=true,
            local_alternative_scope="actor_and_copper_consumer_radius_64",
            observed_generated_entities_only=true,
            unobserved_chunks_inside_scope_unknown=true,
            unobserved_current_surface_outside_scope=true,
            future_generated_chunks_unknown=true,
            time_to_completion_claimed=false,mutation_authorized=false}
    end
    local ok,result=pcall(evaluate)
    if ok then return result end
    local reason=type(result)=="string" and result:match("^[a-z_]+$")
        and result or "native_admission_unavailable"
    return reject(reason)
end
