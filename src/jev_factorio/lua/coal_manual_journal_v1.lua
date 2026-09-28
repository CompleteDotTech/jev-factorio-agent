-- Explicitly installed, diagnostic-only native actor-work journal.
-- This source is never part of the pinned live installation. A future
-- versioned treatment must qualify its installed source before using rows.
local rt = assert(storage)
assert(rt.coal_manual_journal_v1 == nil, "Coal journal installation already exists")
local fair = assert(rt.fair)
local actor = assert(rt.agent_characters and rt.agent_characters[1])
local bound_player = assert(fair.actor())
assert(actor.valid and actor.unit_number and type(rt.jev_session_id) == "string"
    and #rt.jev_session_id > 0 and bound_player.character == actor
    and type(bound_player.index) == "number" and bound_player.index > 0,
    "Coal journal actor/session is unavailable")

local journal = {protocol = 1, session_id = rt.jev_session_id,
    actor_index = bound_player.index, actor_unit = actor.unit_number,
    surface_index = actor.surface.index,
    force_index = actor.force.index, pending = nil, rows = {}, order = {}}
rt.coal_manual_journal_v1 = journal

local function checked_actor()
    local player = fair.actor()
    assert(player == bound_player and player.index == journal.actor_index
        and player.character == actor and actor.valid
        and actor.unit_number == journal.actor_unit
        and actor.surface.index == journal.surface_index
        and actor.force.index == journal.force_index
        and rt.jev_session_id == journal.session_id,
        "Coal journal actor epoch changed")
    return player
end

local function identifier(value)
    return type(value) == "string" and #value >= 1 and #value <= 128
        and not string.find(value, "[^%g]")
end

function journal.begin(receipt)
    local player = checked_actor()
    assert(identifier(receipt), "Invalid coal journal identity")
    assert(journal.pending == nil and journal.rows[receipt] == nil,
        "Coal journal receipt is pending or already used")
    local job = fair.job
    assert(not job or job.status == "completed" or job.status == "failed",
        "Coal journal cannot begin during another fair action")
    journal.pending = {schema = "jev.coal-manual-gather.v1", status = "pending",
        session_id = journal.session_id, actor_index = journal.actor_index,
        actor_unit = journal.actor_unit,
        surface_index = journal.surface_index, force_index = journal.force_index,
        receipt = receipt, resource = "coal",
        started_tick = game.tick, finished_tick = game.tick,
        coal_before = player.get_item_count("coal"), coal_after = player.get_item_count("coal"),
        walking_ticks = 0, mining_ticks = 0, pending = true,
        overflow = false, fault = false, last_tick = game.tick,
        last_position = {x = player.position.x, y = player.position.y}}
    return {receipt = receipt, started_tick = game.tick}
end

function journal.tick_handler(event)
    local row = journal.pending
    if not row or row.fault then return end
    if game.tick == row.started_tick then return end
    if not event or event.tick ~= game.tick or game.tick <= row.last_tick
        or game.tick - row.started_tick > 216000 then
        row.fault = "tick_window"; return
    end
    row.last_tick = game.tick
    local ok, player = pcall(checked_actor)
    if not ok then row.fault = "actor_epoch"; return end
    local job = fair.job
    if job and job.status ~= "completed" and job.status ~= "failed" then
        if job.unit ~= journal.actor_unit or (job.kind ~= "walk" and job.kind ~= "mine")
            or (job.kind == "mine" and job.item ~= "coal") then
            row.fault = "foreign_fair_job"; return
        end
        -- An actual position change is a conservative walking-work sample.
        -- Path-request waits and idle API gaps do not count.
        if job.kind == "walk" and job.status == "walking"
            and player.walking_state.walking
            and (player.position.x ~= row.last_position.x
                or player.position.y ~= row.last_position.y) then
            row.walking_ticks = row.walking_ticks + 1
        elseif job.kind == "mine" and job.status == "mining"
            and player.mining_state.mining and job.entity and job.entity.valid
            and player.selected == job.entity then
            row.mining_ticks = row.mining_ticks + 1
        end
    end
    row.last_position = {x = player.position.x, y = player.position.y}
    if row.walking_ticks + row.mining_ticks > 216000 then row.overflow = true end
end

function journal.finish(receipt)
    local row = journal.pending
    assert(row and row.receipt == receipt,
        "Coal journal pending receipt changed")
    local ok, player = pcall(checked_actor)
    if not ok then row.fault = "actor_epoch" end
    row.finished_tick = game.tick
    if ok then row.coal_after = player.get_item_count("coal") end
    row.pending = false
    row.status = "complete"
    if row.fault or row.overflow or row.finished_tick <= row.started_tick
        or not fair.job or fair.job.kind ~= "mine" or fair.job.status ~= "completed"
        or row.coal_after - row.coal_before < 1
        or row.coal_after - row.coal_before > 200
        or row.mining_ticks < 1
        or row.walking_ticks + row.mining_ticks > row.finished_tick - row.started_tick then
        row.status = "failed"
    end
    row.last_position = nil
    row.last_tick = nil
    if #journal.order >= 64 then
        -- Do not silently discard an unresolved receipt from a baseline cycle.
        row.overflow = true; row.status = "failed"
    else
        journal.order[#journal.order + 1] = receipt
        journal.rows[receipt] = row
    end
    journal.pending = nil
    return row
end

function journal.observe(receipt)
    assert(identifier(receipt), "Invalid coal journal receipt")
    local row = journal.rows[receipt]
    assert(row, "Coal journal receipt is unavailable")
    return row
end

-- A separate nth-tick slot preserves the pinned fair/output-buffer on_tick
-- callback chain. Installation itself is explicit and source-receipted.
script.on_nth_tick(1, journal.tick_handler)
