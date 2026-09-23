storage.fair = storage.fair or {}
local fair = storage.fair
fair.quarantined = true
script.on_nth_tick(5, nil)
script.on_nth_tick(15, nil)
script.on_nth_tick(60, nil)

if storage.actions and storage.actions.inspect_inventory
    and storage.actions.inspect_inventory ~= fair.inspect_inventory then
    local inspect_inventory = storage.actions.inspect_inventory
    fair.inspect_inventory = function(...)
        local result = table.pack(pcall(inspect_inventory, ...))
        script.on_nth_tick(60, nil)
        if not result[1] then error(result[2]) end
        return table.unpack(result, 2, result.n)
    end
    storage.actions.inspect_inventory = fair.inspect_inventory
end

fair.actor = function()
    local player = game.get_player(1)
    local character = storage.agent_characters and storage.agent_characters[1]
    assert(player and player.connected and character and character.valid,
        "Fair play requires the original connected character")
    assert(player.character == character, "Fair player binding changed")
    assert(game.speed == 1 and not player.cheat_mode, "Fair play requires normal game speed")
    return player
end

fair.stop = function(reason)
    local player = game.get_player(1)
    if player and player.character == (storage.agent_characters or {})[1] then
        player.walking_state = {walking = false}
        player.mining_state = {mining = false}
    end
    if fair.job and (fair.job.status ~= "failed" or reason) then
        fair.job.status = reason and "failed" or "completed"
        fair.job.error = reason
    end
end

fair.bind = function()
    fair.quarantined = true
    script.on_nth_tick(5, nil)
    script.on_nth_tick(15, nil)
    script.on_nth_tick(60, nil)
    for _, name in pairs({"crafting_queue", "harvest_queues", "walking_queues"}) do
        assert(not storage[name] or next(storage[name]) == nil,
            "Legacy scripted work must be reconciled: " .. name)
    end
    local player = game.get_player(1)
    local character = (storage.agent_characters or {})[1]
    assert(player and player.connected and character and character.valid,
        "Fair play requires the original connected character")
    assert(not player.character or player.character == character,
        "Refusing to replace a different character")
    if not player.character then
        player.set_controller{type = defines.controllers.character, character = character}
    end
    local player = fair.actor()
    storage.fast = false
    fair.stop("Controls stopped on adapter attachment")
    fair.quarantined = false
    return {position = player.position}
end

fair.begin_move = function(position)
    local player = fair.actor()
    fair.stop()
    local character = player.character
    fair.job = {
        kind = "walk", status = "path_pending", lease = game.tick + 180,
        unit = character.unit_number, last_progress = game.tick,
        last_position = player.position, goal = position
    }
    fair.job.request = player.surface.request_path{
        bounding_box = character.prototype.collision_box,
        collision_mask = character.prototype.collision_mask,
        start = player.position, goal = position, force = player.force,
        radius = 0.2, entity_to_ignore = character, can_open_gates = true,
        pathfind_flags = {cache = false, allow_paths_through_own_entities = false}
    }
    return {request = fair.job.request}
end

local function mining_entity(player, position, item)
    local filter = {position = position, radius = 0.75}
    if item == "wood" then filter.type = "tree" else filter.name = item end
    local best, best_distance
    for _, entity in pairs(player.surface.find_entities_filtered(filter)) do
        if entity.valid and entity.minable then
            local distance = (entity.position.x - position.x)^2
                + (entity.position.y - position.y)^2
            if not best or distance < best_distance then
                best, best_distance = entity, distance
            end
        end
    end
    assert(best, "No mineable resource at target")
    return best
end

fair.mine_approach = function(position, item)
    local player = fair.actor()
    local entity = mining_entity(player, position, item)
    if player.can_reach_entity(entity) then return {reachable = true} end
    local horizontal = player.position.x - entity.position.x
    local vertical = player.position.y - entity.position.y
    local distance = math.sqrt(horizontal * horizontal + vertical * vertical)
    assert(distance > 0, "Unreachable mining target overlaps player")
    local target = {
        x = entity.position.x + horizontal / distance * 1.5,
        y = entity.position.y + vertical / distance * 1.5
    }
    local approach = player.surface.find_non_colliding_position("character", target, 2, 0.25)
    assert(approach, "No collision-free mining approach")
    return {reachable = false, position = approach}
end

fair.begin_mine = function(position, item, quantity)
    local player = fair.actor()
    fair.stop()
    local entity = mining_entity(player, position, item)
    assert(player.can_reach_entity(entity), "Mining target is outside normal reach")
    player.update_selected_entity(entity.position)
    assert(player.selected == entity, "Mining target is obscured by another entity")
    fair.job = {
        kind = "mine", status = "mining", lease = game.tick + 180,
        unit = player.character.unit_number, entity = entity, item = item,
        baseline = player.get_item_count(item), quantity = quantity,
        last_progress = game.tick, last_count = player.get_item_count(item)
    }
    player.mining_state = {mining = true, position = entity.position}
    return {baseline = fair.job.baseline}
end

fair.next_mine_target = function(item, radius)
    local player = fair.actor()
    assert(type(item) == "string", "Mining item must be a string")
    assert(type(radius) == "number" and radius > 0 and radius <= 128,
        "Mining search radius is invalid")
    local filter = {position = player.position, radius = radius}
    if item == "wood" then filter.type = "tree" else filter.name = item end
    local best, best_distance
    for _, entity in pairs(player.surface.find_entities_filtered(filter)) do
        if entity.valid and entity.minable then
            local horizontal = entity.position.x - player.position.x
            local vertical = entity.position.y - player.position.y
            local distance = horizontal * horizontal + vertical * vertical
            if not best or distance < best_distance then
                -- Reject targets obscured by another entity before committing
                -- an observation.  This only updates the normal cursor; it
                -- does not walk, mine, transfer, or alter game speed.  The
                -- later begin_mine call independently enforces normal reach.
                player.update_selected_entity(entity.position)
                if player.selected == entity then
                    best, best_distance = entity, distance
                end
            end
        end
    end
    if not best then return {} end
    return {
        position = {x = best.position.x, y = best.position.y},
        unit_number = best.unit_number,
        name = best.name,
        surface_index = best.surface.index,
    }
end

fair.discover_mine_target = function(item, center, radius)
    -- Discovery is deliberately read-only.  It may inspect only terrain the
    -- campaign already generated, but it never changes the player's cursor or
    -- controls.  The later fair harvesting path walks to the returned entity
    -- and independently verifies normal reach and cursor selection.
    local player = fair.actor()
    assert(type(item) == "string", "Mining item must be a string")
    assert(type(center) == "table" and type(center.x) == "number"
        and type(center.y) == "number", "Mining search center is invalid")
    assert(type(radius) == "number" and radius > 0 and radius <= 1024,
        "Mining discovery radius is invalid")
    local filter = {position = center, radius = radius}
    if item == "wood" then filter.type = "tree" else filter.name = item end
    local best, best_distance
    for _, entity in pairs(player.surface.find_entities_filtered(filter)) do
        if entity.valid and entity.minable then
            local horizontal = entity.position.x - center.x
            local vertical = entity.position.y - center.y
            local distance = horizontal * horizontal + vertical * vertical
            if not best or distance < best_distance then
                best, best_distance = entity, distance
            end
        end
    end
    if not best then return {} end
    return {
        position = {x = best.position.x, y = best.position.y},
        unit_number = best.unit_number,
        name = best.name,
        surface_index = best.surface.index,
    }
end

fair.observe = function()
    local player = fair.actor()
    local job = fair.job or {}
    if job.status ~= "failed" and job.status ~= "completed" then
        job.lease = game.tick + 180
    end
    return {
        position = player.position, tick = game.tick, status = job.status or "idle",
        error = job.error, gained = job.item and player.get_item_count(job.item) - job.baseline or 0
    }
end

fair.find_build_site = function(name, center, radius)
    local player = fair.actor()
    assert(type(name) == "string" and prototypes.entity[name], "Unknown building prototype")
    assert(type(center) == "table" and type(center.x) == "number"
        and type(center.y) == "number", "Invalid build-site center")
    assert(type(radius) == "number" and radius >= 0 and radius <= 32
        and radius % 0.5 == 0, "Invalid build-site radius")
    local directions = {
        defines.direction.north, defines.direction.east,
        defines.direction.south, defines.direction.west
    }
    local best, best_distance
    local half_steps = radius * 2
    for horizontal = -half_steps, half_steps do
        for vertical = -half_steps, half_steps do
            local position = {
                x = center.x + horizontal / 2,
                y = center.y + vertical / 2
            }
            local distance = horizontal * horizontal + vertical * vertical
            for _, direction in ipairs(directions) do
                if player.surface.can_place_entity{
                    name = name, position = position, direction = direction,
                    force = player.force,
                    build_check_type = defines.build_check_type.manual
                } and (not storage.campaign or not storage.campaign.production_reserved
                    or not storage.campaign.production_reserved(name, position, direction))
                    and (not storage.campaign or not storage.campaign.mining_outpost_reserved
                    or not storage.campaign.mining_outpost_reserved(name, position, direction))
                    and (not best or distance < best_distance) then
                    best = {position = position, direction = direction}
                    best_distance = distance
                end
            end
        end
    end
    assert(best, "No ordinary build site")
    return best
end

fair.place = function(name, position, direction)
    local player = fair.actor()
    assert(not player.surface.find_entity(name, position), "Building already exists")
    assert((player.position.x - position.x)^2 + (player.position.y - position.y)^2
        <= player.build_distance^2, "Building is outside normal reach")
    assert(player.clear_cursor(), "Cannot clear the cursor without losing items")
    local before = player.get_item_count(name)
    local stack = player.get_main_inventory().find_item_stack(name)
    assert(stack and stack.valid_for_read, "Missing building item")
    assert(player.cursor_stack.transfer_stack(stack), "Cannot move existing item into cursor")
    local ok, failure = pcall(function()
        assert(player.can_build_from_cursor{position = position, direction = direction},
            "Building is obstructed or outside normal reach")
        player.build_from_cursor{position = position, direction = direction}
    end)
    local cleared = player.clear_cursor()
    assert(ok, failure)
    assert(cleared, "Could not return cursor items")
    assert(player.get_item_count(name) == before - 1, "Native build did not consume one item")
    local entity = player.surface.find_entity(name, position)
    assert(entity and entity.valid and entity.force == player.force, "Native build did not create entity")
    return {name = entity.name, position = entity.position, unit_number = entity.unit_number,
        drop_position = entity.type == "mining-drill" and entity.drop_position or nil}
end

fair.insert = function(name, position, item, quantity)
    local player = fair.actor()
    local entity = player.surface.find_entity(name, position)
    assert(entity and entity.valid and player.can_reach_entity(entity),
        "Interaction target is outside normal reach")
    local inventory = player.get_main_inventory()
    assert(inventory.get_item_count(item) >= quantity, "Missing transfer items")
    assert(entity.can_insert{name = item, count = quantity}, "Transfer destination is full")
    local removed = inventory.remove{name = item, count = quantity}
    local inserted = entity.insert{name = item, count = removed}
    if inserted < removed then
        assert(inventory.insert{name = item, count = removed - inserted} == removed - inserted)
    end
    assert(inserted == quantity, "Partial transfer requires reconciliation")
    return {quantity = inserted}
end

local previous_path = script.get_event_handler(defines.events.on_script_path_request_finished)
if previous_path ~= fair.path_handler then fair.previous_path = previous_path end
fair.path_handler = function(event)
    local job = fair.job
    if job and job.status == "path_pending" and event.id == job.request then
        if not event.path then fair.stop("Native pathfinder could not find a route"); return end
        job.path, job.index, job.status = event.path, 1, "walking"
    elseif fair.previous_path then fair.previous_path(event) end
end
script.on_event(defines.events.on_script_path_request_finished, fair.path_handler)

local previous_tick = script.get_event_handler(defines.events.on_tick)
if previous_tick ~= fair.tick_handler then fair.previous_tick = previous_tick end
fair.tick_handler = function(event)
    if fair.quarantined then fair.stop("Adapter attachment is not validated"); return end
    local job = fair.job
    if not job or job.status == "failed" or job.status == "completed" then return end
    local ok, player = pcall(fair.actor)
    if not ok then fair.stop("Fair player/session invariant failed"); return end
    if player.character.unit_number ~= job.unit or game.tick > job.lease then
        fair.stop("Control lease expired or character changed"); return
    end
    if job.status == "mining" then
        local count = player.get_item_count(job.item)
        if count - job.baseline >= job.quantity then fair.stop(); return end
        if not job.entity.valid then fair.stop("Resource depleted before requested amount"); return end
        if not player.can_reach_entity(job.entity) then fair.stop("Mining target left reach"); return end
        player.update_selected_entity(job.entity.position)
        if player.selected ~= job.entity then fair.stop("Mining target became obscured"); return end
        if count ~= job.last_count then job.last_count, job.last_progress = count, game.tick end
        if game.tick - job.last_progress > 600 then fair.stop("Native mining made no progress"); return end
        player.mining_state = {mining = true, position = job.entity.position}
    elseif job.status == "walking" then
        local point = job.path[job.index]
        while point and (player.position.x - point.position.x)^2
            + (player.position.y - point.position.y)^2 < 0.0625 do
            job.index = job.index + 1
            point = job.path[job.index]
        end
        if not point then fair.stop(); return end
        local horizontal = point.position.x - player.position.x
        local vertical = point.position.y - player.position.y
        local direction
        if math.abs(horizontal) > 2 * math.abs(vertical) then
            direction = horizontal > 0 and defines.direction.east or defines.direction.west
        elseif math.abs(vertical) > 2 * math.abs(horizontal) then
            direction = vertical > 0 and defines.direction.south or defines.direction.north
        elseif horizontal > 0 then
            direction = vertical > 0 and defines.direction.southeast or defines.direction.northeast
        else
            direction = vertical > 0 and defines.direction.southwest or defines.direction.northwest
        end
        if (player.position.x - job.last_position.x)^2
            + (player.position.y - job.last_position.y)^2 > 0.25 then
            job.last_progress, job.last_position = game.tick, player.position
        end
        if game.tick - job.last_progress > 300 then fair.stop("Native walking is obstructed"); return end
        player.walking_state = {walking = true, direction = direction}
    end
end
script.on_event(defines.events.on_tick, fair.tick_handler)
