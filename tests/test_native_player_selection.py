"""Execute native player selection against two distinct Lua player objects."""
from importlib.resources import files

import pytest


def source(name):
    return files("jev_factorio").joinpath("lua", name).read_text()


def runtime_for(index=None):
    runtime = pytest.importorskip("lupa.lua54").LuaRuntime()
    runtime.execute("""
        handlers = {}
        script = {
            on_nth_tick = function() end,
            on_event = function(event, callback) handlers[event] = callback end,
            get_event_handler = function(event) return handlers[event] end
        }
        defines = {
            controllers = {character = 1},
            events = {on_tick = 1, on_script_path_request_finished = 2},
            direction = {north = 0, northeast = 2, east = 4, southeast = 6,
                         south = 8, southwest = 10, west = 12, northwest = 14}
        }
        prototypes = {item = {}, entity = {character = {crafting_categories = {crafting = true}}}}
        force = {
            rockets_launched = 0, technologies = {}, research_progress = 0,
            recipes = {gear = {enabled = true, category = "crafting",
                ingredients = {{type = "item", name = "iron-plate", amount = 2}}}},
            get_item_production_statistics = function()
                return {get_input_count = function() return 0 end}
            end
        }
        surface = {
            find_entities_filtered = function() return {} end,
            request_path = function() return 17 end
        }
        players = {}
        for index = 1, 2 do
            local character = {valid = true, unit_number = 100 + index,
                force = force, surface = surface,
                prototype = {collision_box = {}, collision_mask = {}}}
            local player = {index = index, character = character,
                connected = true, cheat_mode = false, crafting_queue_size = 0,
                position = {x = 0, y = 0}, surface = surface, force = force,
                walking_state = {walking = false}, mining_state = {mining = false},
                iron = 20, crafted = 0, controller_assignments = 0}
            player.get_item_count = function() return player.iron end
            player.begin_crafting = function(request)
                player.iron = player.iron - request.count * 2
                player.crafted = player.crafted + request.count
                return request.count
            end
            player.set_controller = function(request)
                player.controller_assignments = player.controller_assignments + 1
                player.character = request.character
            end
            players[index] = player
        end
        game = {tick = 0, speed = 1, get_player = function(index) return players[index] end}
        storage = {agent_characters = {players[1].character}}
    """)
    if index is not None:
        runtime.globals().storage.jev_player_index = index
        if index in (1, 2):
            runtime.execute(f"storage.agent_characters[1] = players[{index}].character")
    return runtime


@pytest.mark.parametrize("configured,selected", [(None, 1), (1, 1), (2, 2)])
def test_binding_and_native_walk_use_selected_player_only(configured, selected):
    lua = runtime_for(configured)
    other = 3 - selected
    lua.execute(f"players[{other}].connected = false")
    lua.execute(source("fair_actions.lua"))
    lua.execute(f"""
        storage.fair.bind()
        assert(storage.fair.actor().index == {selected})
        assert(storage.agent_characters[1] == players[{selected}].character)
        storage.fair.begin_move{{x = 2, y = 0}}
        handlers[2]{{id = 17, path = {{{{position = {{x = 2, y = 0}}}}}}}}
        handlers[1]{{}}
        assert(players[{selected}].walking_state.walking)
        assert(not players[{other}].walking_state.walking)
        players[{selected}].position = {{x = 2, y = 0}}
        handlers[1]{{}}
        assert(storage.fair.job.status == "completed")
        assert(not players[{selected}].walking_state.walking)
        assert(players[1].controller_assignments == 0 and players[2].controller_assignments == 0)
    """)


@pytest.mark.parametrize("name", ["fair_actions.lua", "factory.lua"])
@pytest.mark.parametrize("index", [0, -1, 1.5, "2", False, float("inf"), float("nan")])
def test_invalid_selection_is_rejected_before_installing_handlers(name, index):
    lua = runtime_for()
    lua.globals().storage.jev_player_index = index
    with pytest.raises(Exception, match="positive integer"):
        lua.execute(source(name))
    assert lua.eval("next(handlers)") is None
    assert lua.eval("storage.jev_bound_player_index") is None


@pytest.mark.parametrize("name", ["fair_actions.lua", "factory.lua"])
def test_player_selection_cannot_change_on_reattachment(name):
    lua = runtime_for(2)
    lua.execute(source(name))
    lua.execute("storage.jev_player_index = 1")
    with pytest.raises(Exception, match="cannot change"):
        lua.execute(source(name))
    assert lua.eval("storage.jev_bound_player_index") == 2


def test_selection_drift_fails_closed_and_stops_the_original_player():
    lua = runtime_for(2)
    lua.execute(source("fair_actions.lua"))
    lua.execute("""
        storage.fair.bind()
        storage.fair.begin_move{x = 2, y = 0}
        handlers[2]{id = 17, path = {{position = {x = 2, y = 0}}}}
        handlers[1]{}
        assert(players[2].walking_state.walking)
        players[1].walking_state = {walking = true}
        players[1].mining_state = {mining = true}
        storage.jev_player_index = 1
    """)
    with pytest.raises(Exception, match="cannot change"):
        lua.execute("storage.fair.actor()")
    lua.execute("""
        handlers[1]{}
        assert(storage.fair.job.status == "failed")
        assert(not players[2].walking_state.walking and not players[2].mining_state.mining)
        assert(players[1].walking_state.walking and players[1].mining_state.mining)
    """)


@pytest.mark.parametrize("failure", ["disconnected", "different_character", "cheat", "speed"])
def test_selected_player_keeps_existing_fair_play_guards(failure):
    lua = runtime_for(2)
    lua.execute(source("fair_actions.lua"))
    modifications = {
        "disconnected": "players[2].connected = false",
        "different_character": "players[2].character = players[1].character",
        "cheat": "players[2].cheat_mode = true",
        "speed": "game.speed = 2",
    }
    lua.execute(modifications[failure])
    with pytest.raises(Exception):
        lua.execute("storage.fair.bind()")
    assert lua.eval("players[1].controller_assignments + players[2].controller_assignments") == 0


@pytest.mark.parametrize("configured,selected", [(None, 1), (2, 2)])
def test_factory_observation_binding_and_paid_crafting_follow_selection(configured, selected):
    lua = runtime_for(configured)
    other = 3 - selected
    lua.execute(f"players[{other}].connected = false")
    lua.execute(source("fair_actions.lua"))
    lua.execute("storage.fair.bind()")
    lua.execute(source("factory.lua"))
    lua.execute(f"""
        players[{selected}].crafting_queue_size = 7
        players[{other}].crafting_queue_size = 13
        local observed = storage.campaign.observe()
        assert(observed.player_connected and observed.player_bound and observed.crafting_queue == 7)
        players[{selected}].crafting_queue_size = 0
        storage.campaign.bind_player()
        storage.campaign.craft("gear", 3)
        assert(players[{selected}].crafted == 3 and players[{selected}].iron == 14)
        assert(players[{other}].crafted == 0 and players[{other}].iron == 20)
        assert(players[1].controller_assignments == 0 and players[2].controller_assignments == 0)
    """)
    lua.execute(f"storage.jev_player_index = {other}")
    with pytest.raises(Exception, match="cannot change"):
        lua.execute("storage.campaign.observe()")
    with pytest.raises(Exception, match="cannot change"):
        lua.execute('storage.campaign.craft("gear", 1)')
    assert lua.eval(f"players[{selected}].crafted") == 3


def test_modules_cannot_install_conflicting_player_selections():
    lua = runtime_for(2)
    lua.execute(source("fair_actions.lua"))
    lua.execute("storage.jev_player_index = 1")
    with pytest.raises(Exception, match="cannot change"):
        lua.execute(source("factory.lua"))
