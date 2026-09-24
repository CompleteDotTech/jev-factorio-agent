import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.fair_actions import FairActions, NativePathNotFound


@pytest.mark.parametrize("actor,target,reach,placeable,walk", [
    ((26.0195, 39.8555), (24.5, 41.5), 10, True, False),
    ((3, 4), (0, 0), 5, True, False),  # Exact same inclusive boundary as fair.place.
    ((-12, 0), (0, 0), 10, True, True),
    ((0, 12), (0, 0), 10, True, True),
    ((0, 0), (0, 0), 10, False, True),  # Actor obstructs the future building.
    ((-0.25, 0), (0, 0), 10, False, True),
    ((25.94921875, 43.4296875), (16.5, 48.5), 10, True, True),
])
def test_native_build_reach_and_actor_side_approach(monkeypatch, actor, target, reach, placeable, walk):
    lua = pytest.importorskip("lupa.lua54").LuaRuntime()
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    lua.execute("""
        player={position={},surface={},force={}}
        defines={build_check_type={manual=1}}
        storage={fair={actor=function() return player end}}
        helpers={json_to_table=function(_) return target end,
                 table_to_json=function(value) captured=value; return 'captured' end}
        rcon={print=function(_) end}
        player.surface.can_place_entity=function(parameters)
            assert(parameters.name=='pipe' and parameters.direction==4)
            assert(parameters.position==target and parameters.force==player.force)
            assert(parameters.build_check_type==defines.build_check_type.manual)
            return placeable
        end
        player.surface.find_non_colliding_position=function(name,near,radius,precision)
            searches=(searches or 0)+1
            assert(name=='character' and radius==1 and precision==0.25)
            if candidates then return candidates[searches] end
            return near
        end
    """)
    lua.globals().player.position = lua.table_from(dict(zip(("x", "y"), actor)))
    lua.globals().player.build_distance = reach
    lua.globals().placeable = placeable
    lua.globals().target = lua.table_from(dict(zip(("x", "y"), target)))
    live_geometry = actor == (25.94921875, 43.4296875)
    if live_geometry:
        # Native collision search can return outside its requested circle.
        # Reject that result and select a bounded inward candidate instead.
        lua.globals().candidates = lua.table_from([
            lua.table_from({"x": 25.3671875, "y": 43.2578125}),
            lua.table_from({"x": 23.046875, "y": 43.71484375}),
        ])
    fair = object.__new__(FairActions)
    def command(script):
        lua.execute(script)
        result = dict(lua.globals().captured)
        if "positions" in result:
            result["positions"] = [dict(point) for point in result["positions"].values()]
        return json.dumps(result)
    fair.command = command
    movements = []
    fair.move_to = movements.append
    fair.approach_build(SimpleNamespace(x=target[0], y=target[1]), "pipe", 4)
    assert bool(movements) is walk
    if walk:
        point = movements[0]
        # Chosen point stays between the actor and build center, never fixed right.
        assert (point.x-target[0])*(actor[0]-target[0]) >= 0
        assert (point.y-target[1])*(actor[1]-target[1]) >= 0
        assert (point.x-target[0])**2+(point.y-target[1])**2 <= (reach-1)**2
        assert (point.x, point.y) != actor
        if live_geometry:
            assert lua.globals().searches == 16
    else:
        assert lua.globals().searches is None
        assert fair.metrics["approaches_skipped_in_reach"] == 1


def test_build_approach_failure_is_not_preflight_rejection(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    fair = object.__new__(FairActions)
    fair.command = lambda script: '{"positions":[{"x":1,"y":2}]}'
    def failed(position):
        raise RuntimeError("native pathfinder failed")
    fair.move_to = failed
    with pytest.raises(RuntimeError, match="native pathfinder"):
        fair.approach_build(SimpleNamespace(x=1, y=2), "pipe", 0)


@pytest.mark.parametrize("reach", [0.5, 10])
def test_no_safe_arrival_margin_does_not_start_walking(monkeypatch, reach):
    lua = pytest.importorskip("lupa.lua54").LuaRuntime()
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    lua.execute("""
        player={position={x=20,y=0},force={},surface={}}
        storage={fair={actor=function() return player end}}
        defines={build_check_type={manual=1}}
        helpers={json_to_table=function(_) return {x=0,y=0} end}
        player.surface.can_place_entity=function(_) return true end
        searches=0
        player.surface.find_non_colliding_position=function(...)
            searches=searches+1; return {x=20,y=0}
        end
    """)
    lua.globals().player.build_distance = reach
    fair = object.__new__(FairActions)
    fair.command = lua.execute
    fair.move_to = lambda position: pytest.fail("unsafe approach started walking")
    with pytest.raises(Exception, match="margin"):
        fair.approach_build(SimpleNamespace(x=0, y=0), "pipe", 0)
    assert lua.globals().searches == (0 if reach <= 1 else 16)


@pytest.mark.parametrize("failure,retries", [
    (NativePathNotFound("Native pathfinder could not find a route"), True),
    (RuntimeError("Native pathfinder could not find a route"), False),
    (RuntimeError("Native walking is obstructed"), False),
    (TimeoutError("Native action exceeded its bounded observation window"), False),
])
def test_build_candidates_retry_only_typed_native_no_route(monkeypatch, failure, retries):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    fair = object.__new__(FairActions)
    fair.command = lambda script: '{"positions":[{"x":1,"y":2},{"x":3,"y":4}]}'
    moved = []
    def move(position):
        moved.append((position.x, position.y))
        if len(moved) == 1:
            raise failure
    fair.move_to = move
    if retries:
        fair.approach_build(SimpleNamespace(x=0, y=0), "pipe", 0)
        assert moved == [(1, 2), (3, 4)]
    else:
        with pytest.raises(type(failure)):
            fair.approach_build(SimpleNamespace(x=0, y=0), "pipe", 0)
        assert moved == [(1, 2)]


def test_wait_classifies_only_observed_native_no_route():
    fair = object.__new__(FairActions)
    fair.call = lambda function: {"status": "failed", "error": "Native pathfinder could not find a route"}
    cleanup = []
    fair.command = cleanup.append
    with pytest.raises(NativePathNotFound):
        fair.wait()
    assert cleanup == ["storage.fair.stop()"]
