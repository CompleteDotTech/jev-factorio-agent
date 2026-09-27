-- Read-only export of the technology tree and research state for the dashboard.
-- Works on Factorio 1.1 (game.*_prototypes, global) and 2.0+ (prototypes, storage,
-- helpers), with or without Space Age. It reads prototypes and force state only.
local env = _ENV or _G
local P = rawget(env, "prototypes")
local H = rawget(env, "helpers")
local S = rawget(env, "storage") or rawget(env, "global")

-- Missing members of LuaObjects raise errors, and members differ between versions.
local function get(object, key)
    local ok, value = pcall(function() return object[key] end)
    if ok then return value end
    return nil
end

local function id(value)
    if type(value) == "string" then return value end
    if value ~= nil then return get(value, "name") end
    return nil
end

local technologies = P and P.technology or game.technology_prototypes
local recipes = P and P.recipe or game.recipe_prototypes
local encode = H and H.table_to_json or game.table_to_json
local mods = {}
for name, version in pairs(script.active_mods) do mods[name] = version end

local out = {
    schema = "jev.research.v1",
    version = mods.base,
    mods = mods,
    technologies = {},
    science_packs = {}
}

local packs = {}
for name, technology in pairs(technologies) do
    local prerequisites = {}
    for key in pairs(get(technology, "prerequisites") or {}) do
        prerequisites[#prerequisites + 1] = key
    end
    local ingredients = {}
    for _, ingredient in pairs(get(technology, "research_unit_ingredients") or {}) do
        local pack = id(ingredient.name or ingredient[1])
        if pack then
            ingredients[#ingredients + 1] = {name = pack, amount = ingredient.amount or ingredient[2] or 1}
            packs[pack] = true
        end
    end
    local unlocks, locations = {}, {}
    for _, effect in pairs(get(technology, "effects") or {}) do
        if effect.type == "unlock-recipe" and effect.recipe then
            unlocks[#unlocks + 1] = id(effect.recipe)
        elseif effect.type == "unlock-space-location" and effect.space_location then
            locations[#locations + 1] = id(effect.space_location)
        end
    end
    local trigger = get(technology, "research_trigger")
    if trigger then
        trigger = {
            type = trigger.type,
            item = id(trigger.item),
            entity = id(trigger.entity),
            fluid = id(trigger.fluid),
            count = trigger.count
        }
    end
    out.technologies[name] = {
        prerequisites = prerequisites,
        ingredients = ingredients,
        count = get(technology, "research_unit_count"),
        count_formula = get(technology, "research_unit_count_formula"),
        energy = get(technology, "research_unit_energy"),
        trigger = trigger,
        unlocks = unlocks,
        locations = locations,
        hidden = get(technology, "hidden") == true,
        enabled = get(technology, "enabled") ~= false,
        essential = get(technology, "essential") == true,
        upgrade = get(technology, "upgrade") == true,
        max_level = get(technology, "max_level"),
        order = get(technology, "order")
    }
end

-- Which recipes make each science pack, and which technologies unlock them.
local makers = {}
for name, recipe in pairs(recipes) do
    for _, product in pairs(get(recipe, "products") or {}) do
        local item = id(product.name)
        if item and packs[item] then
            makers[name] = makers[name] or {}
            makers[name][#makers[name] + 1] = item
            out.science_packs[item] = out.science_packs[item] or {from_start = false, unlocked_by = {}}
            if get(recipe, "enabled") == true then
                out.science_packs[item].from_start = true
            end
        end
    end
end
for pack in pairs(packs) do
    out.science_packs[pack] = out.science_packs[pack] or {from_start = false, unlocked_by = {}}
end
for name, technology in pairs(out.technologies) do
    for _, recipe in ipairs(technology.unlocks) do
        for _, pack in ipairs(makers[recipe] or {}) do
            local list = out.science_packs[pack].unlocked_by
            list[#list + 1] = name
        end
    end
end

-- Research state of the agent's force (FLE keeps it on the agent character).
local force = game.forces.player
local agents = S and S.agent_characters
if agents and agents[1] and agents[1].valid then force = agents[1].force end
local researched = {}
for name, technology in pairs(force.technologies) do
    if technology.researched then researched[#researched + 1] = name end
end
out.state = {
    tick = game.tick,
    force = force.name,
    researched = researched,
    current = id(get(force, "current_research")),
    progress = get(force, "research_progress")
}

rcon.print(encode(out))
