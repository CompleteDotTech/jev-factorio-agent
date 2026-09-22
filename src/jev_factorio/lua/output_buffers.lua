-- Native controls build every component. Sampling below changes only telemetry.
local campaign, fair = storage.campaign, storage.fair
assert(campaign and fair, "Output buffers require native factory and fair controls")
local b = storage.output_buffers or {cells = {}, offers = {}, protocol = 1}
assert(b.protocol == 1, "Unsupported output-buffer runtime")
storage.output_buffers = b
local routes = storage.input_routes
if routes and campaign.observe == routes.observer and campaign.transfer == routes.transfer
    and routes.previous_observe == b.observer and routes.previous_transfer == b.transfer then
    assert(script.get_event_handler(defines.events.on_tick) == b.tick_handler,
        "Unexpected tick handler; refusing to replace it")
    return
end
local supported = {"iron-plate", "copper-plate", "steel-plate"}
local names = {chest = "wooden-chest", inserter = "burner-inserter"}
local function point(p) return {x = p.x or p[1], y = p.y or p[2]} end
local function same(a, c) return math.abs(a.x-c.x) < 0.01 and math.abs(a.y-c.y) < 0.01 end
local function rotate(p, turns)
    p = point(p)
    for _ = 1, turns do p = {x = -p.y, y = p.x} end
    return p
end
local function count(entity, kind, item)
    local inv = entity.get_inventory(kind)
    return inv and inv.get_item_count(item) or 0
end
local function source_for(role)
    local player = fair.actor()
    local source = campaign.entities[role]
    assert(source and source.valid and source.type == "furnace"
        and source.name == "stone-furnace" and source.force == player.force
        and source.surface == player.surface, "Output-buffer source changed")
    local recipe = source.get_recipe()
    assert(not recipe or "recipe:" .. recipe.name == role, "Output-buffer source recipe changed")
    return source
end
local function geometry(cell)
    local source = source_for(cell.source)
    assert(source == cell.entity and source.unit_number == cell.source_unit
        and same(source.position, cell.source_position), "Output-buffer source identity changed")
    for part, entry in pairs(cell.parts) do
        local entity = entry.entity
        assert(entity and entity.valid and entity.name == names[part]
            and entity.unit_number == entry.unit_number
            and campaign.entities[entry.role] == entity
            and entity.force == source.force and entity.surface == source.surface
            and same(entity.position, cell[part .. "_position"]), "Output-buffer component changed")
        if part == "inserter" then
            assert(entity.direction == cell.direction, "Output-buffer inserter rotated")
        end
    end
    return source
end
local function topology(cell)
    local arm = cell.parts.inserter and cell.parts.inserter.entity
    local chest = cell.parts.chest and cell.parts.chest.entity
    return arm and chest and arm.pickup_target == cell.entity and arm.drop_target == chest
end
local function offer(role, item)
    if campaign.production_output_offer then
        local managed, cell = campaign.production_output_offer(role, item)
        if managed then return cell end
    end
    local source = source_for(role)
    local prototype = prototypes.entity["burner-inserter"]
    assert(prototype and prototype.inserter_pickup_position and prototype.inserter_drop_position,
        "Missing native inserter vectors")
    local best, distance
    local box = source.bounding_box
    -- A fixed local search; no placement probes, ghosts, terrain edits or rotation writes.
    for dx = -3, 3 do for dy = -3, 3 do for turns = 0, 3 do
        local position = {x = math.floor(source.position.x) + dx + 0.5,
                          y = math.floor(source.position.y) + dy + 0.5}
        local pickup = rotate(prototype.inserter_pickup_position, turns)
        local drop = rotate(prototype.inserter_drop_position, turns)
        pickup = {x = position.x + pickup.x, y = position.y + pickup.y}
        local chest = {x = math.floor(position.x + drop.x) + 0.5,
                       y = math.floor(position.y + drop.y) + 0.5}
        local direction = turns * 4
        if pickup.x > box.left_top.x and pickup.x < box.right_bottom.x
            and pickup.y > box.left_top.y and pickup.y < box.right_bottom.y
            and not same(chest, position)
            and source.surface.can_place_entity{name = "burner-inserter", position = position,
                direction = direction, force = source.force, build_check_type = defines.build_check_type.manual}
            and source.surface.can_place_entity{name = "wooden-chest", position = chest,
                direction = 0, force = source.force, build_check_type = defines.build_check_type.manual} then
            local player = fair.actor()
            local cost = (player.position.x-position.x)^2 + (player.position.y-position.y)^2
            if not best or cost < distance then
                local layout = string.format("output:%d:%d:%d:%d", source.unit_number, dx, dy, direction)
                best = {source = role, item = item, source_unit = source.unit_number,
                    source_position = point(source.position), entity = source, layout = layout,
                    chest_position = chest, inserter_position = position, direction = direction,
                    chest_role = "output-chest:" .. source.unit_number,
                    inserter_role = "output-arm:" .. source.unit_number, parts = {}}
                distance = cost
            end
        end
    end end end
    return best
end
local function parameters(p)
    assert(type(p) == "table", "Invalid buffer command")
    local n = 0
    for key, value in pairs(p) do
        assert((key == "source" or key == "layout" or key == "part" or key == "receipt")
            and type(value) == "string" and #value > 0 and #value <= 128, "Invalid buffer field")
        n = n + 1
    end
    assert(n == 4 and names[p.part], "Invalid buffer component")
end
campaign.prepare_output_buffer = function(p)
    parameters(p)
    local cell = b.cells[p.source] or b.offers[p.source]
    assert(cell and cell.layout == p.layout and not cell.fault, "Stale buffer layout")
    geometry(cell)
    assert(not cell.parts[p.part] and (p.part == "chest" or cell.parts.chest),
        "Buffer component exists or predecessor missing")
    assert(fair.actor().crafting_queue_size == 0, "Do not build during native crafting")
    for _, part in ipairs({"chest", "inserter"}) do
        if not cell.parts[part] then
            assert(cell.entity.surface.can_place_entity{name = names[part],
                position = cell[part .. "_position"], direction = part == "inserter" and cell.direction or 0,
                force = cell.entity.force, build_check_type = defines.build_check_type.manual},
                "Buffer route obstructed")
        end
    end
    b.cells[p.source] = cell
    rcon.print(helpers.table_to_json({position = cell[p.part .. "_position"]}))
end
campaign.build_output_buffer = function(p)
    parameters(p)
    local cell = b.cells[p.source]
    assert(cell and cell.layout == p.layout and not cell.fault, "Unprepared buffer layout")
    geometry(cell)
    assert(not cell.parts[p.part] and (p.part == "chest" or cell.parts.chest),
        "Buffer component already exists or predecessor missing")
    local player = fair.actor()
    assert(player.crafting_queue_size == 0, "Do not build during native crafting")
    local role = cell[p.part .. "_role"]
    assert(not campaign.entities[role], "Output-buffer role already occupied")
    -- One atomic RPC performs the ordinary item-paid placement and registration.
    -- An error after partial native mutation is ambiguous, not retry permission.
    local before = player.get_item_count(names[p.part])
    local position = cell[p.part .. "_position"]
    fair.place(names[p.part], position, p.part == "inserter" and cell.direction or 0)
    local entity = player.surface.find_entity(names[p.part], position)
    assert(entity and entity.valid and entity.unit_number
        and player.get_item_count(names[p.part]) == before - 1, "Native buffer build not paid")
    campaign.entities[role] = entity
    cell.parts[p.part] = {entity = entity, role = role, unit_number = entity.unit_number,
                          receipt = p.receipt, paid = 1}
    cell.built_tick = game.tick
    geometry(cell)
    rcon.print(helpers.table_to_json({unit_number = entity.unit_number}))
end
local function sample(cell)
    if cell.fault or cell.flow then return end
    geometry(cell)
    if not cell.parts.inserter or not topology(cell) then return end
    local source, arm, chest = cell.entity, cell.parts.inserter.entity, cell.parts.chest.entity
    local recipe = source.get_recipe()
    if recipe then assert(recipe.name == cell.item, "Smelting recipe changed during flow observation") end
    local held = arm.held_stack
    assert(not held.valid_for_read or held.name == cell.item or held.name == "coal",
        "Unexpected inserter contents")
    local h = held.valid_for_read and held.name == cell.item and held.count or 0
    local out = count(source, defines.inventory.furnace_result, cell.item)
    local stored = count(chest, defines.inventory.chest, cell.item)
    local total, produced = out + h + stored, source.products_finished
    local previous = cell.previous
    if previous then
        assert(game.tick >= previous.tick and produced >= previous.produced, "Flow counter regressed")
        assert(total - previous.total == produced - previous.produced, "Flow conservation failed")
        assert(stored >= previous.stored, "Unverified output removed from buffer")
        if stored > previous.stored then
            cell.positive = cell.positive + 1
            cell.received = cell.received + stored - previous.stored
        end
        if cell.positive >= 3 and cell.received >= 3 and game.tick-cell.first_tick >= 120 then
            cell.flow = {layout = cell.layout, source_unit = cell.source_unit,
                first_tick = cell.first_tick, last_tick = game.tick, positive_samples = cell.positive,
                received = cell.received, conservation = true}
        end
    else
        cell.first_tick, cell.positive, cell.received = game.tick, 0, 0
    end
    cell.previous = {tick = game.tick, total = total, produced = produced, stored = stored}
end
local previous_tick = script.get_event_handler(defines.events.on_tick)
assert(previous_tick == fair.tick_handler or previous_tick == b.tick_handler,
    "Unexpected tick handler; refusing to replace it")
-- FairActions remains the only owner of controls. No retained arbitrary callback is invoked.
local fair_tick = fair.tick_handler
b.tick_handler = function(event)
    fair_tick(event)
    if game.tick % 30 ~= 0 then return end
    for _, cell in pairs(b.cells) do
        local ok = pcall(sample, cell)
        if not ok then cell.fault = "flow_or_identity_mismatch" end
    end
end
script.on_event(defines.events.on_tick, b.tick_handler)
local previous_observe = campaign.observe
if previous_observe == b.observer then previous_observe = b.previous_observe end
b.previous_observe = previous_observe
b.observer = function()
    local result = previous_observe()
    local rows = {}
    b.offers = {}
    local handler_ok = script.get_event_handler(defines.events.on_tick) == b.tick_handler
    for _, item in ipairs(supported) do
        local role = "recipe:" .. item
        local cell = b.cells[role]
        local source = campaign.entities[role]
        if cell or (source and source.valid and source.name == "stone-furnace") then
            if not cell then
                local ok, candidate = pcall(offer, role, item)
                if ok then cell = candidate; b.offers[role] = candidate end
            end
            if cell then
                local ok = pcall(geometry, cell)
                if not ok or not handler_ok then cell.fault = "buffer_identity_or_handler_changed" end
                local linked = ok and topology(cell) or false
                if ok and cell.parts.inserter and not linked
                    and game.tick - (cell.built_tick or 0) > 120 then cell.fault = "buffer_topology_changed" end
                local parts = {}
                for name, entry in pairs(cell.parts) do
                    parts[name] = {role = entry.role, unit_number = entry.unit_number,
                                   receipt = entry.receipt, paid = entry.paid}
                end
                local arm = cell.parts.inserter and cell.parts.inserter.entity
                local hand = arm and arm.valid and arm.held_stack
                rows[role] = {source = role, source_unit = cell.source_unit, item = cell.item,
                    layout = cell.layout, chest_role = cell.chest_role, parts = parts,
                    state = cell.fault and "fault" or (cell.parts.inserter and linked and "ready"
                        or (next(cell.parts) and "building" or "proposed")),
                    topology = linked == true, fault = cell.fault,
                    held = hand and hand.valid_for_read and hand.name == item and hand.count or 0,
                    flow = cell.flow or {}}
            end
        end
    end
    result.output_buffers = {protocol = 1, session_id = storage.jev_session_id,
                              tick = result.tick, sources = rows}
    return result
end
campaign.observe = b.observer
local previous_transfer = campaign.transfer
if previous_transfer == b.transfer then previous_transfer = b.previous_transfer end
b.previous_transfer = previous_transfer
b.transfer = function(role, item, quantity, receipt, extracting)
    for source, cell in pairs(b.cells) do
        assert(not cell.fault, "Output-buffer reconciliation required")
        if role == cell.chest_role then
            assert(extracting and cell.flow, "Do not seed or prematurely drain output evidence")
        end
        assert(not (role == source and cell.parts.inserter and extracting),
            "Do not race the output inserter")
    end
    return previous_transfer(role, item, quantity, receipt, extracting)
end
campaign.transfer = b.transfer
