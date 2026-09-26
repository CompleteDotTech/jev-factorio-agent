from importlib.resources import files

import pytest


@pytest.fixture
def fair_runtime():
    runtime = pytest.importorskip("lupa.lua54").LuaRuntime()
    runtime.execute("""
        handlers = {}
        defines = {
            controllers = {character = 1},
            events = {on_tick = 1, on_script_path_request_finished = 2},
            build_check_type = {manual = 1},
            direction = {north = 0, northeast = 2, east = 4, southeast = 6,
                         south = 8, southwest = 10, west = 12, northwest = 14}
        }
        prototypes = {entity = {pipe = {}, ["offshore-pump"] = {}}}
        script = {
            get_event_handler = function(event) return handlers[event] end,
            on_event = function(event, callback) handlers[event] = callback end,
            on_nth_tick = function(interval, callback)
                handlers["nth" .. interval] = callback
            end
        }
        ticks_forwarded, paths_forwarded = 0, 0
        handlers[1] = function() ticks_forwarded = ticks_forwarded + 1 end
        handlers[2] = function() paths_forwarded = paths_forwarded + 1 end
        force = {}
        character = {
            valid = true, unit_number = 9,
            prototype = {collision_box = {}, collision_mask = {}}
        }
        resource = {
            valid = true, minable = true, name = "coal", surface = {index = 1},
            position = {x = 2, y = 0}
        }
        quantities = {coal = 0, pipe = 4}
        surface = {
            index = 1,
            request_path = function(parameters)
                requested_path = parameters
                return 17
            end,
            find_entities_filtered = function() return {resource} end,
            find_entity = function() return built_entity end,
            can_place_entity = function(parameters)
                return not site_filter or site_filter(parameters)
            end
        }
        cursor = {count = 0}
        inventory = {
            find_item_stack = function(name)
                if (quantities[name] or 0) == 0 then return nil end
                return {name = name, count = quantities[name], valid_for_read = true}
            end,
            get_item_count = function(name) return quantities[name] or 0 end,
            remove = function(stack)
                quantities[stack.name] = quantities[stack.name] - stack.count
                return stack.count
            end,
            insert = function(stack)
                quantities[stack.name] = (quantities[stack.name] or 0) + stack.count
                return stack.count
            end
        }
        cursor.transfer_stack = function(stack)
            cursor.name, cursor.count = stack.name, stack.count
            quantities[stack.name] = 0
            return true
        end
        reachable, buildable = true, true
        player = {
            connected = true, character = character, cheat_mode = false,
            position = {x = 0, y = 0}, surface = surface, force = force,
            cursor_stack = cursor, build_distance = 10,
            get_item_count = inventory.get_item_count,
            get_main_inventory = function() return inventory end,
            can_reach_entity = function() return reachable end,
            can_build_from_cursor = function() return buildable end,
            clear_cursor = function()
                if cursor.name then
                    quantities[cursor.name] = quantities[cursor.name] + cursor.count
                end
                cursor.count, cursor.name = 0, nil
                return true
            end,
            build_from_cursor = function(parameters)
                cursor.count = cursor.count - 1
                built_entity = {
                    name = cursor.name, valid = true, force = force,
                    position = parameters.position, unit_number = 19, type = "pipe"
                }
                return true
            end
        }
        game = {tick = 0, speed = 1, get_player = function() return player end}
        player.update_selected_entity = function() player.selected = resource end
        storage = {agent_characters = {character}}
    """)
    runtime.execute(files("jev_factorio").joinpath("lua/fair_actions.lua").read_text())
    runtime.execute("storage.fair.bind()")
    return runtime


@pytest.mark.parametrize("fails", [False, True])
def test_inventory_inspection_removes_unsafe_gui_callback(fair_runtime, fails):
    fair_runtime.globals().inspection_fails = fails
    fair_runtime.execute("""
        storage.actions = {inspect_inventory = function(value)
            script.on_nth_tick(60, function() error("invalid disconnected character") end)
            if inspection_fails then error("inventory unavailable") end
            return value, nil, 7
        end}
        handlers.nth60 = function() error("stale callback") end
    """)
    source = files("jev_factorio").joinpath("lua/fair_actions.lua").read_text()
    fair_runtime.execute(source)
    fair_runtime.execute(source)
    fair_runtime.execute("""
        assert(handlers.nth60 == nil)
        local result = table.pack(pcall(storage.actions.inspect_inventory, "contents"))
        assert(handlers.nth60 == nil)
        if inspection_fails then
            assert(not result[1])
            assert(string.find(result[2], "inventory unavailable"))
        else
            assert(result.n == 4 and result[1] and result[2] == "contents")
            assert(result[3] == nil and result[4] == 7)
        end
    """)


def test_generated_terrain_discovery_probes_selection_without_starting_controls(fair_runtime):
    fair_runtime.execute("""
        resource.position = {x = 512, y = 0}
        selection_updates = 0
        native_player = player
        native_player.update_selected_entity = function()
            selection_updates = selection_updates + 1
            native_player.selected = resource
        end
        game.get_player = function() return native_player end
        player = nil
        discovered = storage.fair.discover_mine_target("coal", {x = 0, y = 0}, 1024)
        assert(discovered.name == "coal")
        assert(discovered.position.x == 512 and discovered.position.y == 0)
        assert(selection_updates == 1)
        assert(storage.fair.job == nil)
        assert(not native_player.walking_state.walking and not native_player.mining_state.mining)
        assert(quantities.coal == 0)
    """)


@pytest.mark.parametrize('depleted', [False, True])
def test_generated_discovery_skips_unusable_nearest_node_before_plan_commit(fair_runtime, depleted):
    fair_runtime.globals().depleted = depleted
    fair_runtime.execute("""
        local blocked = resource
        blocked.type = "resource"
        blocked.amount = depleted and 0 or 50
        local visible = {valid=true,minable=true,name="coal",surface=surface,
                         position={x=3,y=0},type="resource",amount=50}
        surface.index = 1
        local chest = {valid=true,name="wooden-chest"}
        surface.find_entities_filtered = function() return {blocked, visible} end
        player.update_selected_entity = function(position)
            if position == blocked.position then
                player.selected = depleted and blocked or chest
            else player.selected = visible end
        end
        local found = storage.fair.discover_mine_target("coal", {x=0,y=0}, 256)
        assert(found.position.x == 3 and found.name == "coal")
        assert(storage.fair.job == nil and quantities.coal == 0)
        player.update_selected_entity = function() player.selected = chest end
        assert(next(storage.fair.discover_mine_target("coal", {x=0,y=0}, 256)) == nil)
        assert(storage.fair.job == nil and quantities.coal == 0)
    """)


def test_native_walk_controls_player_and_stops_at_destination(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 2, y = 0}
        assert(storage.fair.job.status == "path_pending")
        handlers[2]{id = 17, path = {
            {position = {x = 0, y = 0}}, {position = {x = 2, y = 0}}
        }}
        handlers[1]{}
        assert(player.walking_state.walking)
        assert(player.walking_state.direction == defines.direction.east)
        assert(character.walking_state == nil)
        assert(player.position.x == 0)
        player.position = {x = 2, y = 0}
        handlers[1]{}
        assert(storage.fair.job.status == "completed")
        assert(not player.walking_state.walking)
    """)


@pytest.mark.parametrize("queue", ["crafting_queue", "harvest_queues", "walking_queues"])
def test_retained_legacy_work_is_quarantined_not_discarded(fair_runtime, queue):
    fair_runtime.execute("""
        handlers.nth5 = function() error("unsafe legacy walking") end
        handlers.nth15 = function() error("unsafe legacy mining") end
    """)
    fair_runtime.execute(f"storage.{queue} = {{ retained = true }}")
    with pytest.raises(Exception, match="Legacy scripted work must be reconciled"):
        fair_runtime.execute("storage.fair.bind()")
    assert fair_runtime.eval(f"storage.{queue}.retained") is True
    assert fair_runtime.eval("handlers.nth5") is None
    assert fair_runtime.eval("handlers.nth15") is None


def test_native_mining_observes_yield_without_inserting_resources(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_mine({x = 2, y = 0}, "coal", 2)
        handlers[1]{}
        assert(player.mining_state.mining)
        assert(quantities.coal == 0)
        assert(character.mining_state == nil)
        quantities.coal = 1
        handlers[1]{}
        assert(storage.fair.job.status == "mining")
        quantities.coal = 2
        handlers[1]{}
        assert(storage.fair.job.status == "completed")
        assert(not player.mining_state.mining)
        assert(storage.fair.observe().gained == 2)
    """)


def test_next_mining_target_uses_actor_position_and_skips_depleted_entities(fair_runtime):
    fair_runtime.execute("""
        local depleted = {valid = true, minable = false, position = {x = 30, y = 0}}
        local distant = {valid = true, minable = true, position = {x = 45, y = 0}}
        local nearby = {
            valid = true, minable = true, name = "tree-01", surface = {index = 1},
            position = {x = 31, y = 0}
        }
        player.position = {x = 30, y = 0}
        surface.find_entities_filtered = function(filter)
            assert(filter.type == "tree")
            assert(filter.position.x == 30 and filter.position.y == 0)
            assert(filter.radius == 64)
            return {depleted, distant, nearby}
        end
        player.update_selected_entity = function(position)
            player.selected = position == nearby.position and nearby or distant
        end
        local target = storage.fair.next_mine_target("wood", 64)
        assert(target.position.x == 31 and target.position.y == 0)
        assert(target.unit_number == nil)
        assert(target.name == "tree-01" and target.surface_index == 1)
        assert(player.position.x == 30 and player.position.y == 0)
        assert(not player.walking_state or not player.walking_state.walking)
        assert(not player.mining_state or not player.mining_state.mining)
        assert(quantities.coal == 0)
    """)


def test_next_mining_target_enforces_fair_actor_invariants(fair_runtime):
    fair_runtime.execute("game.speed = 2")
    with pytest.raises(Exception, match="normal game speed"):
        fair_runtime.execute('storage.fair.next_mine_target("wood", 64)')
    assert fair_runtime.eval("player.position.x") == 0
    assert fair_runtime.eval("quantities.coal") == 0


def test_raw_target_skips_ore_obscured_by_another_selection_box(fair_runtime):
    fair_runtime.execute("""
        local hidden = {
            valid = true, minable = true, name = "iron-ore",
            surface = {index = 1}, position = {x = 1, y = 0}
        }
        local visible = {
            valid = true, minable = true, name = "iron-ore",
            surface = {index = 1}, position = {x = 2, y = 0}
        }
        surface.find_entities_filtered = function(filter)
            assert(filter.name == "iron-ore")
            assert(filter.radius == 128)
            return {hidden, visible}
        end
        player.update_selected_entity = function(position)
            player.selected = position == visible.position and visible or nil
        end
        local result = storage.fair.next_mine_target("iron-ore", 128)
        assert(result.position.x == 2 and result.name == "iron-ore")
        assert(requested_path == nil and quantities.coal == 0)
        assert(not player.walking_state or not player.walking_state.walking)
        assert(not player.mining_state or not player.mining_state.mining)
    """)


def test_wood_target_skips_trunks_obscured_by_other_selection_boxes(fair_runtime):
    fair_runtime.execute("""
        local hidden = {
            valid = true, minable = true, name = "dead-grey-trunk",
            surface = {index = 1}, position = {x = 1, y = 0}
        }
        local visible = {
            valid = true, minable = true, name = "tree-03",
            surface = {index = 1}, position = {x = 2, y = 0}
        }
        surface.find_entities_filtered = function() return {hidden, visible} end
        player.update_selected_entity = function() player.selected = visible end
        local result = storage.fair.next_mine_target("wood", 64)
        assert(result.position.x == 2 and result.name == "tree-03")
        assert(requested_path == nil and quantities.coal == 0)
        assert(not player.walking_state or not player.walking_state.walking)
        assert(not player.mining_state or not player.mining_state.mining)
        player.update_selected_entity = function() player.selected = nil end
        assert(next(storage.fair.next_mine_target("wood", 64)) == nil)
    """)


def test_mining_rejects_remote_target_and_stops_if_reach_changes(fair_runtime):
    fair_runtime.execute("""
        reachable = false
        assert(not pcall(storage.fair.begin_mine, {x = 2, y = 0}, "coal", 1))
        assert(quantities.coal == 0)
        reachable = true
        storage.fair.begin_mine({x = 2, y = 0}, "coal", 1)
        reachable = false
        handlers[1]{}
        assert(storage.fair.job.status == "failed")
        assert(not player.mining_state.mining)
    """)


@pytest.mark.parametrize("change", [
    "game.tick = 181",
    "player.connected = false",
    "game.speed = 2",
    "player.cheat_mode = true",
    "character.unit_number = 10",
])
def test_control_lease_and_actor_invariants_stop_native_controls(fair_runtime, change):
    fair_runtime.execute("""
        storage.fair.begin_mine({x = 2, y = 0}, "coal", 5)
    """)
    fair_runtime.execute(change)
    fair_runtime.execute("""
        handlers[1]{}
        assert(storage.fair.job.status == "failed")
        assert(not player.walking_state.walking)
        assert(not player.mining_state.mining)
    """)


def test_observation_renews_lease_and_explicit_stop_cancels(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_mine({x = 2, y = 0}, "coal", 5)
        game.tick = 170
        storage.fair.observe()
        game.tick = 190
        handlers[1]{}
        assert(storage.fair.job.status == "mining")
        storage.fair.stop("Cancelled")
        assert(storage.fair.job.status == "failed")
        assert(not player.mining_state.mining)
    """)


def test_place_consumes_existing_stack_and_returns_cursor_remainder(fair_runtime):
    fair_runtime.execute("""
        local result = storage.fair.place("pipe", {x = 1, y = 0}, 0)
        assert(result.name == "pipe")
        assert(quantities.pipe == 3)
        assert(cursor.count == 0)
        assert(player.position.x == 0 and player.position.y == 0)
    """)


def test_build_site_search_checks_direction_without_moving_or_granting_items(fair_runtime):
    fair_runtime.execute("""
        site_filter = function(parameters)
            return parameters.name == "offshore-pump"
                and parameters.position.x == 1.5 and parameters.position.y == 0
                and parameters.direction == defines.direction.east
        end
        local before = quantities.pipe
        local result = storage.fair.find_build_site("offshore-pump", {x = 0, y = 0}, 2)
        assert(result.position.x == 1.5 and result.position.y == 0)
        assert(result.direction == defines.direction.east)
        assert(quantities.pipe == before)
        assert(cursor.count == 0 and built_entity == nil)
        assert(player.position.x == 0 and player.position.y == 0)
    """)


def test_place_entity_uses_selected_direction_and_preserves_exact_direction(monkeypatch):
    import sys
    import types
    from dataclasses import dataclass
    from types import SimpleNamespace

    @dataclass
    class Position:
        x: float
        y: float

    Direction = SimpleNamespace(
        UP=SimpleNamespace(value=0), RIGHT=SimpleNamespace(value=4),
        LEFT=SimpleNamespace(value=12),
    )
    Prototype = SimpleNamespace(
        OffshorePump=SimpleNamespace(value=("offshore-pump", object))
    )
    fle = types.ModuleType("fle")
    fle_env = types.ModuleType("fle.env")
    fle_env.Direction, fle_env.Position, fle_env.Prototype = Direction, Position, Prototype
    monkeypatch.setitem(sys.modules, "fle", fle)
    monkeypatch.setitem(sys.modules, "fle.env", fle_env)

    from jev_factorio.backends.fair_actions import FairActions

    fair = object.__new__(FairActions)
    calls = []
    approaches = []

    def call(function, *arguments):
        calls.append((function, arguments))
        if function == "find_build_site":
            return {"position": {"x": 1.5, "y": 0}, "direction": Direction.RIGHT.value}
        return {"name": "offshore-pump", "position": arguments[1]}

    monkeypatch.setattr(fair, "call", call)
    monkeypatch.setattr(fair, "approach_build", lambda position, name, direction: approaches.append((position, "build")))

    entity = fair.place_entity(
        Prototype.OffshorePump, Position(x=0, y=0), Direction.UP, exact=False
    )

    assert calls[0] == ("find_build_site", ("offshore-pump", {"x": 0.0, "y": 0.0}, 8))
    assert calls[1] == (
        "place", ("offshore-pump", {"x": 1.5, "y": 0}, Direction.RIGHT.value)
    )
    assert approaches[0][0] == Position(x=1.5, y=0)
    assert entity.position == Position(x=1.5, y=0)

    calls.clear()
    approaches.clear()
    fair.place_entity(
        Prototype.OffshorePump, Position(x=3, y=4), Direction.LEFT, exact=True
    )
    assert calls == [
        ("place", ("offshore-pump", {"x": 3.0, "y": 4.0}, Direction.LEFT.value))
    ]
    assert approaches[0][0] == Position(x=3, y=4)


def test_distant_pole_connection_uses_bounded_corridor_and_fair_placements(monkeypatch):
    import sys
    import types
    from dataclasses import dataclass
    from types import SimpleNamespace

    @dataclass
    class Position:
        x: float
        y: float

    Direction = SimpleNamespace(UP=SimpleNamespace(value=0))
    fle = types.ModuleType("fle")
    fle_env = types.ModuleType("fle.env")
    fle_env.Direction, fle_env.Position = Direction, Position
    monkeypatch.setitem(sys.modules, "fle", fle)
    monkeypatch.setitem(sys.modules, "fle.env", fle_env)

    from jev_factorio.backends.fair_actions import FairActions

    fair = object.__new__(FairActions)
    commands, placements = [], []
    source, target = Position(x=43.5, y=41.5), Position(x=15.5, y=383.5)
    path = (
        [(x + 0.5, 41.5) for x in range(15, 44)]
        + [(15.5, y + 0.5) for y in range(42, 384)]
    )

    def command(script):
        commands.append(script)
        if len(commands) == 1:
            assert "for horizontal=7,51 do for vertical=33,49 do" in script
            assert "for horizontal=7,23 do for vertical=33,391 do" in script
            assert "for horizontal=7,51 do for vertical=33,391 do" not in script
            return __import__("json").dumps({
                "buildable": [{"x": x, "y": y} for x, y in path],
                "existing": [],
            })
        return '{"count":104}'

    monkeypatch.setattr(fair, "command", command)
    monkeypatch.setattr(
        fair, "place_entity",
        lambda prototype, position, direction, exact: placements.append(
            (position, direction, exact)
        ),
    )
    prototype = SimpleNamespace(value=("small-electric-pole", object()))

    fair.connect(source, target, prototype, "electricity")

    assert len(commands) == 2
    assert placements
    assert all(direction is Direction.UP and exact for _, direction, exact in placements)
    assert all(placements[index][0] != placements[index + 1][0]
               for index in range(len(placements) - 1))


def test_connection_discovery_lua_is_read_only_and_executable(monkeypatch):
    """The fair corridor query must parse before it can select placement cells."""
    runtime = pytest.importorskip("lupa.lua54").LuaRuntime()
    runtime.execute("""
        discovery_calls = 0
        defines = {build_check_type = {manual = "manual"}}
        surface = {
            find_entity = function(name, position)
                discovery_calls = discovery_calls + 1
                assert(name == "small-electric-pole")
                assert(position.x == 0.5 and position.y == 0.5)
                return nil
            end,
            can_place_entity = function(parameters)
                assert(parameters.name == "small-electric-pole")
                assert(parameters.force == force)
                assert(parameters.build_check_type == defines.build_check_type.manual)
                return true
            end
        }
        force = {}
        player = {force = force, surface = surface}
        storage = {fair = {actor = function() return player end}}
        helpers = {table_to_json = function(result)
            assert(#result.buildable == 1 and #result.existing == 0)
            return '{"buildable":[{"x":0.5,"y":0.5}],"existing":[]}'
        end}
        rcon = {print = function(value) response = value end}
    """)

    from jev_factorio.backends.fair_actions import FairActions

    fair = object.__new__(FairActions)

    def command(script):
        runtime.execute(script)
        return runtime.eval("response")

    monkeypatch.setattr(fair, "command", command)
    buildable, existing = fair._connection_cells(
        "small-electric-pole", "", [(0, 0, 0, 0)]
    )

    assert buildable == {(0.5, 0.5)}
    assert existing == set()
    assert runtime.eval("discovery_calls") == 1


@pytest.mark.parametrize("reachable", [True, False])
def test_harvest_reacquires_live_target_after_a_partial_native_yield(monkeypatch, reachable):
    import sys
    import types
    from dataclasses import dataclass

    @dataclass
    class Position:
        x: float
        y: float

    fle = types.ModuleType("fle")
    fle_env = types.ModuleType("fle.env")
    fle_env.Position = Position
    monkeypatch.setitem(sys.modules, "fle", fle)
    monkeypatch.setitem(sys.modules, "fle.env", fle_env)

    from jev_factorio.backends.fair_actions import FairActions

    fair = object.__new__(FairActions)
    calls = []
    approaches = []
    targets = iter(({"x": 3, "y": 4}, {"x": 5, "y": 6}))

    def call(function, *arguments):
        calls.append((function, arguments))
        if function == "next_mine_target":
            return {"position": next(targets)}
        if function == "mine_approach":
            return {"reachable": reachable, "position": arguments[0], "identity": 71}
        if function == "begin_mine":
            return {}
        raise AssertionError(function)

    monkeypatch.setattr(fair, "call", call)
    monkeypatch.setattr(fair, "move_to",
                        lambda position: approaches.append(position))
    yields = iter(({"gained": 1}, {"gained": 1}))
    monkeypatch.setattr(fair, "wait", lambda: next(yields))

    assert fair.harvest("wood", Position(x=-999, y=-999), 2) == 2
    assert calls == [
        ("mine_approach", ({"x": -999.0, "y": -999.0}, "wood")),
        ("begin_mine", ({"x": -999.0, "y": -999.0}, "wood", 2, 71)),
        ("next_mine_target", ("wood", 64)),
        ("mine_approach", ({"x": 3, "y": 4}, "wood")),
        ("begin_mine", ({"x": 3, "y": 4}, "wood", 1, 71)),
    ]
    assert approaches == ([] if reachable else [
        Position(x=-999.0, y=-999.0), Position(x=3, y=4),
    ])


def test_reachable_mining_target_does_not_request_movement(fair_runtime):
    fair_runtime.execute("""
        local result = storage.fair.mine_approach({x = 2, y = 0}, "wood")
        assert(result.reachable == true and result.position == nil)
        assert(requested_path == nil)
        assert(player.position.x == 0 and player.position.y == 0)
        assert(quantities.coal == 0)
    """)


def test_mining_approach_stays_on_near_side_of_tree(fair_runtime):
    fair_runtime.execute("""
        reachable = false
        resource.position = {x = 10, y = 0}
        player.surface.find_non_colliding_position = function(name, position)
            assert(name == "character")
            assert(position.x == 8.5 and position.y == 0)
            return position
        end
        local result = storage.fair.mine_approach(resource.position, "wood")
        assert(not result.reachable and result.position.x == 8.5)
        assert(requested_path == nil and player.position.x == 0)
    """)


def test_failed_build_returns_all_cursor_items(fair_runtime):
    fair_runtime.execute("""
        buildable = false
        assert(not pcall(storage.fair.place, "pipe", {x = 100, y = 0}, 0))
        assert(quantities.pipe == 4)
        assert(cursor.count == 0)
        assert(built_entity == nil)
        assert(player.position.x == 0 and player.position.y == 0)
    """)


def test_callback_reload_preserves_previous_handlers_without_recursion(fair_runtime):
    source = files("jev_factorio").joinpath("lua/fair_actions.lua").read_text()
    fair_runtime.execute("""
        handlers.nth5 = function() error("unsafe movement") end
        handlers.nth15 = function() error("unsafe mining") end
    """)
    fair_runtime.execute(source)
    assert fair_runtime.eval("handlers.nth5") is None
    assert fair_runtime.eval("handlers.nth15") is None
    fair_runtime.execute(source)
    fair_runtime.execute("storage.fair.bind()")
    fair_runtime.execute("""
        handlers[1]{}
        handlers[2]{id = 500}
        assert(ticks_forwarded == 0)
        assert(storage.fair.previous_tick ~= nil)
        assert(paths_forwarded == 1)
    """)


@pytest.mark.parametrize("terminal", ["idle", "completed", "failed", "expired"])
def test_inherited_tick_cannot_restart_cancelled_controls(fair_runtime, terminal):
    source = files("jev_factorio").joinpath("lua/fair_actions.lua").read_text()
    fair_runtime.execute("""
        handlers[1] = function()
            player.walking_state = {walking = true}
            player.mining_state = {mining = true}
        end
    """)
    fair_runtime.execute(source)
    fair_runtime.execute("storage.fair.bind()")
    if terminal != "idle":
        fair_runtime.execute('storage.fair.begin_mine({x = 2, y = 0}, "coal", 2)')
        if terminal == "expired":
            fair_runtime.execute("game.tick = 181; handlers[1]{}")
        elif terminal == "failed":
            fair_runtime.execute('storage.fair.stop("cancelled")')
        else:
            fair_runtime.execute("storage.fair.stop()")
    fair_runtime.execute("""
        handlers[1]{}
        assert(not player.walking_state.walking)
        assert(not player.mining_state.mining)
        assert(quantities.coal == 0)
    """)


def test_unsafe_inherited_tick_is_quarantined_during_native_mining(fair_runtime):
    source = files("jev_factorio").joinpath("lua/fair_actions.lua").read_text()
    fair_runtime.execute("""
        handlers[1] = function() error("stale FLE tick must not control mining") end
    """)
    fair_runtime.execute(source)
    fair_runtime.execute("""
        storage.fair.bind()
        storage.fair.begin_mine({x = 2, y = 0}, "coal", 2)
        handlers[1]{}
        assert(storage.fair.job.status == "mining")
        assert(player.mining_state.mining)
        assert(quantities.coal == 0)
    """)


def test_unsafe_inherited_tick_is_quarantined_during_native_walking(fair_runtime):
    source = files("jev_factorio").joinpath("lua/fair_actions.lua").read_text()
    fair_runtime.execute("""
        handlers[1] = function() error("stale FLE tick must not control walking") end
    """)
    fair_runtime.execute(source)
    fair_runtime.execute("""
        storage.fair.bind()
        storage.fair.begin_move{x = 2, y = 0}
        handlers[2]{id = 17, path = {
            {position = {x = 0, y = 0}}, {position = {x = 2, y = 0}}
        }}
        handlers[1]{}
        assert(storage.fair.job.status == "walking")
        assert(player.walking_state.walking)
        assert(player.position.x == 0)
    """)


def test_failed_path_reports_failure_without_moving(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 2, y = 0}
        -- A missing path now receives bounded alternate planning, never walking.
        for _ = 1, 5 do handlers[2]{id = 17} end
        assert(storage.fair.job.status == "failed")
        assert(storage.fair.job.path_requests <= 9)
        assert(not player.walking_state.walking)
        assert(player.position.x == 0)
    """)
