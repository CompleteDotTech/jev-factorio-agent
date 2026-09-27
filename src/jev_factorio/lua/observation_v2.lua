-- One ordered server command: fresh actor/lease, inventory, campaign and bootstrap.
-- Positive discovery alone is cached. This is not a mutation or receipt cache.
local campaign = assert(storage.campaign)
assert(storage.fair and storage.fair.actor and storage.fair.observe)
local cache, epoch = {}, nil
local function integer(value, low, high)
    return type(value) == "number" and value % 1 == 0 and value >= low and value <= high
end
local function point(value)
    assert(type(value) == "table" and type(value.x) == "number" and type(value.y) == "number")
    assert(value.x == value.x and value.y == value.y and math.abs(value.x) <= 1000000 and math.abs(value.y) <= 1000000)
    return {x=value.x,y=value.y}
end
local function contents(inv)
    assert(inv and inv.valid ~= false, "Missing native inventory")
    local result, count = {}, 0
    for key, value in pairs(inv.get_contents()) do
        local name, amount, quality
        if type(key) == "string" and type(value) == "number" then
            name, amount = key, value -- older API inventory shape; other guards still apply
        else
            assert(type(value) == "table", "Invalid native inventory stack")
            name, amount, quality = value.name, value.count, value.quality
        end
        quality = type(quality) == "table" and quality.name or quality
        assert(quality == nil or quality == "normal", "Unsupported inventory quality")
        assert(type(name) == "string" and #name > 0 and #name <= 128 and integer(amount,0,9007199254740991))
        count=count+1;assert(count<=4096,"Inventory content budget exceeded")
        result[name] = (result[name] or 0) + amount
    end
    return result
end
local function profiler()
    if helpers.create_profiler then return helpers.create_profiler() end
    if game.create_profiler then return game.create_profiler() end
end
local function finish(timer, name)
    if timer then timer.stop(); rcon.print({"", "JEV_NATIVE_PROFILE|" .. name .. "|", timer}) end
end
local function valid_target(player, entry)
    if not entry or game.tick < entry.tick or game.tick-entry.tick > 1800 then return false end
    local value=entry.value
    local entity=player.surface.find_entity(value.name,value.position)
    if not (entity and entity.valid and entity.minable) then return false end
    if entity.type=="resource" and entity.amount<=0 then return false end
    if value.unit_number and entity.unit_number~=value.unit_number then return false end
    if entity.surface.index~=player.surface.index then return false end
    player.update_selected_entity(entity.position)
    return player.selected==entity
end
local function bootstrap(player, expected_unit)
    if expected_unit ~= nil then assert(integer(expected_unit,1,9007199254740991),"Invalid bootstrap binding") end
    local found=player.surface.find_entities_filtered{
        force=player.force, name={"burner-mining-drill","wooden-chest"},
        position=player.position, radius=1000, limit=129}
    assert(#found<=128,"Bootstrap observation budget exceeded")
    table.sort(found,function(a,b) return (a.unit_number or 0)<(b.unit_number or 0) end)
    local names, selected={},nil
    for _, entity in ipairs(found) do
        assert(entity.valid and entity.surface.index==player.surface.index and entity.force.index==player.force.index)
        assert(integer(entity.unit_number,1,9007199254740991),"Missing bootstrap identity")
        names[#names+1]=entity.name
        if entity.name=="burner-mining-drill" and
            ((expected_unit and entity.unit_number==expected_unit) or (not expected_unit and not selected)) then selected=entity end
    end
    if expected_unit then assert(selected,"Bound bootstrap drill missing or replaced") end
    local result={placed_entities=names,drill=false,output_connected=false,iron_ore_collected=0,query_limit=129}
    if selected then
        local status="unknown"
        for name, value in pairs(defines.entity_status) do if selected.status==value then status=name;break end end
        result.drill={name=selected.name,unit_number=selected.unit_number,position=point(selected.position),
            drop_position=point(selected.drop_position),status=status,
            fuel=contents(assert(selected.get_fuel_inventory(),"Missing burner fuel inventory"))}
        for _, entity in ipairs(found) do
            if entity.name=="wooden-chest" and math.abs(entity.position.x-selected.drop_position.x)<=.1
                and math.abs(entity.position.y-selected.drop_position.y)<=.1 then
                local items=contents(assert(entity.get_inventory(defines.inventory.chest)))
                result.output_connected=true
                result.iron_ore_collected=result.iron_ore_collected+(items["iron-ore"] or 0)
            end
        end
    end
    return result
end
local function anchors(player)
    -- Bounded witnesses, not globally nearest FLE results. Absence/overflow is
    -- unknown; no mining, placement, generation or resource credit occurs here.
    local result={}
    local oil=player.surface.find_entities_filtered{name="crude-oil",position=player.position,radius=256,limit=129}
    if #oil<=128 then
        local best,distance
        for _, entity in ipairs(oil) do
            if entity.valid and entity.amount>0 and entity.surface.index==player.surface.index then
                local d=(entity.position.x-player.position.x)^2+(entity.position.y-player.position.y)^2
                if not distance or d<distance then best,distance=entity,d end
            end
        end
        if best then result["crude-oil"]={name=best.name,position=point(best.position),surface_index=player.surface.index} end
    end
    -- At most 129 tiles returned. A large lake is not silently labeled absent:
    -- its first bounded witness suffices for the normal placement preflight.
    -- Only vanilla water/deepwater are in this deliberately narrow contract.
    local water=player.surface.find_tiles_filtered{name={"water","deepwater"},position=player.position,radius=256,limit=129}
    local best,distance
    for _, tile in ipairs(water) do
        if tile.valid and (tile.name=="water" or tile.name=="deepwater") and tile.surface.index==player.surface.index then
            local d=(tile.position.x+.5-player.position.x)^2+(tile.position.y+.5-player.position.y)^2
            if not distance or d<distance then best,distance=tile,d end
        end
    end
    if best then result.water={name=best.name,position={x=best.position.x+.5,y=best.position.y+.5},surface_index=player.surface.index} end
    return result
end
campaign.observation_snapshot_v2=function(generation,expected_drill)
    assert(integer(generation,0,9007199254740991))
    local player=storage.fair.actor()
    local controls=storage.fair.observe() -- preserve the existing lease heartbeat
    local tick=game.tick
    local timer=profiler()
    local factory=campaign.observe() -- all installed capability wrappers, never cached
    local inventory=contents(player.get_main_inventory())
    local initial=bootstrap(player,expected_drill)
    finish(timer,"campaign_snapshot")
    local radius=factory.exploration_radius
    assert(integer(radius,1,32),"Invalid exploration bound")
    local identity=storage.jev_session_id..":"..player.character.unit_number..":"..player.surface.index
        ..":"..player.force.index..":"..radius..":"..generation..":"
        ..math.floor(player.position.x/16)..":"..math.floor(player.position.y/16)
    if identity~=epoch then cache,epoch={},identity end
    timer=profiler()
    local targets,hits,misses={},0,0
    for _, item in ipairs({"wood","coal","iron-ore","copper-ore","stone"}) do
        if valid_target(player,cache[item]) then targets[item]=cache[item].value;hits=hits+1
        else
            cache[item]=nil
            local value=storage.fair.discover_mine_target(item,{x=0,y=0},radius*32)
            if value.position then
                targets[item]=value
                cache[item]={value=value,tick=tick}
            end
            misses=misses+1
        end
    end
    local witness=anchors(player)
    finish(timer,"discovery")
    assert(game.tick==tick and factory.tick==tick and controls.tick==tick,"Native snapshot tick changed")
    timer=profiler()
    local encoded=helpers.table_to_json({schema=2,tick=tick,factory=factory,inventory=inventory,
        controls=controls,position=point(player.position),bootstrap=initial,targets=targets,anchors=witness,
        session_id=storage.jev_session_id,actor_unit=player.character.unit_number,
        surface_index=player.surface.index,force_index=player.force.index,cache={hits=hits,misses=misses},
        bounds={anchor_radius=256,anchor_limit=129,bootstrap_radius=1000,bootstrap_limit=129}})
    assert(#encoded<=8*1024*1024,"Native observation payload budget exceeded")
    finish(timer,"serialize")
    rcon.print("JEV_SNAPSHOT|"..encoded)
end
rcon.print("JEV_ATOMIC_READY|2")
