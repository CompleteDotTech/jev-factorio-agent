"""Actual Lua execution with synthetic engine callbacks, not a native Factorio pilot."""
from test_fair_actions import fair_runtime
import pytest


def test_both_detour_legs_are_checked_before_any_walking(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 10, y = 0}
        handlers[2]{id = 17, path = {{position = {x = 5, y = 0}, needs_destroy_to_reach = true}}}
        assert(storage.fair.job.path_requests == 2)
        assert(storage.fair.job.status == "path_pending")
        assert(not player.walking_state.walking and quantities.coal == 0)
        local midpoint = storage.fair.job.detour_goal
        handlers[2]{id = 17, path = {{position = midpoint, needs_destroy_to_reach = false}}}
        assert(storage.fair.job.path_requests == 3)
        assert(not player.walking_state.walking)
        handlers[2]{id = 17, path = {{position = {x = 10, y = 0}, needs_destroy_to_reach = false}}}
        assert(storage.fair.job.status == "walking")
        assert(#storage.fair.job.path == 2)
        assert(not player.walking_state.walking)
        handlers[1]{tick = 1}
        assert(player.walking_state.walking)
        assert(storage.fair.job.movement_started)
        assert(requested_path.pathfind_flags.allow_destroy_friendly_entities == false)
    """)


def test_blocked_second_legs_exhaust_budget_without_partial_movement(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 10, y = 0}
        handlers[2]{id = 17, path = {{position = {x = 5, y = 0}, needs_destroy_to_reach = true}}}
        for _ = 1, 4 do
            local midpoint = storage.fair.job.detour_goal
            handlers[2]{id = 17, path = {{position = midpoint}}}
            handlers[2]{id = 17, path = {{position = {x = 10, y = 0}, needs_destroy_to_reach = true}}}
            assert(not player.walking_state.walking)
        end
        assert(storage.fair.job.status == "failed")
        assert(storage.fair.job.failure_code == "destruction_required")
        assert(storage.fair.job.path_requests == 9)
        assert(not storage.fair.job.movement_started)
        local result = storage.fair.begin_move{x = 10, y = 0}
        assert(result.rejected and result.failure_code == "blocked_route_cooldown")
        assert(storage.fair.job.path_requests == 0)
    """)


def test_partial_movement_has_distinct_failure_evidence(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 10, y = 0}
        handlers[2]{id = 17, path = {{position = {x = 10, y = 0}}}}
        handlers[1]{tick = 1}
        player.position = {x = 1, y = 0}
        game.tick = 2
        storage.fair.observe()
        handlers[1]{tick = 2}
        game.tick = 303
        storage.fair.observe()
        handlers[1]{tick = 303}
        assert(storage.fair.job.status == "failed")
        assert(storage.fair.job.failure_code == "movement_obstructed")
        assert(storage.fair.job.movement_started)
        assert(not player.walking_state.walking)
        assert(player.position.x == 1)
    """)


def test_path_planning_deadline_is_bounded_even_when_polled(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 10, y = 0}
        game.tick = 601
        storage.fair.observe()
        handlers[1]{tick = 601}
        assert(storage.fair.job.status == "failed")
        assert(storage.fair.job.failure_code == "path_deadline")
        assert(not player.walking_state.walking)
    """)


def test_stale_callback_cannot_replace_owned_route(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x = 10, y = 0}
        handlers[2]{id = 900, path = {{position = {x = 999, y = 0}}}}
        assert(storage.fair.job.status == "path_pending")
        assert(storage.fair.job.path == nil)
        assert(not player.walking_state.walking)
    """)


def test_path_planning_time_does_not_consume_the_walking_watchdog(fair_runtime):
    fair_runtime.execute("""
        storage.fair.begin_move{x=20,y=0}
        game.tick=400
        storage.fair.job.lease=580
        handlers[2]{id=17, path={{position={x=20,y=0}, needs_destroy_to_reach=false}}}
        storage.fair.tick_handler{tick=400}
        assert(storage.fair.job.status == "walking")
        assert(storage.fair.job.last_progress == 400)
    """)


def test_python_wait_distinguishes_pre_walk_rejection_from_partial_movement():
    from jev_factorio.backends.fair_actions import FairActions, NativePathNotFound
    import pytest
    for started in (False, True):
        fair = FairActions.__new__(FairActions)
        fair.call = lambda *args: {"status": "failed", "failure_code": "destruction_required",
                                  "movement_started": started, "error": "blocked"}
        fair.command = lambda *args: ""
        with pytest.raises(RuntimeError) as error:
            fair.wait()
        assert (type(error.value) is NativePathNotFound) is (not started)

@pytest.mark.parametrize("change", ["replacement", "surface", "actor", "position", "invalid"])
def test_mining_approach_identity_survives_walk_without_retargeting(fair_runtime, change):
    fair_runtime.globals().change = change
    fair_runtime.execute("""
        local observed = storage.fair.mine_approach(resource.position, "coal")
        local old = resource
        storage.fair.begin_move{x=1,y=0}
        storage.fair.stop()
        if change == "replacement" then
            resource = {valid=true,minable=true,name="coal",surface={index=1},
                        position={x=2,y=0}}
        elseif change == "surface" then surface.index=2
        elseif change == "actor" then character.unit_number=10
        elseif change == "position" then resource.position.x=2.1
        elseif change == "invalid" then old.valid=false end
        local ok = pcall(storage.fair.begin_mine, {x=2,y=0}, "coal", 1, observed.identity)
        assert(not ok)
        assert(not player.mining_state.mining and quantities.coal==0)
    """)


def test_mining_approach_identity_is_single_use_after_walk(fair_runtime):
    fair_runtime.execute("""
        local observed = storage.fair.mine_approach(resource.position, "coal")
        storage.fair.begin_move{x=1,y=0}
        storage.fair.stop()
        storage.fair.begin_mine({x=2,y=0}, "coal", 1, observed.identity)
        assert(player.mining_state.mining)
        storage.fair.stop()
        assert(not pcall(storage.fair.begin_mine, {x=2,y=0}, "coal", 1, observed.identity))
        assert(not player.mining_state.mining)
    """)
