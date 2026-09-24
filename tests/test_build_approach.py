import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.fair_actions import FairActions


@pytest.mark.parametrize("actor,target,reach,placeable,walk", [
    ((26.0195, 39.8555), (24.5, 41.5), 10, True, False),
    ((3, 4), (0, 0), 5, True, False),  # Exact same inclusive boundary as fair.place.
    ((-12, 0), (0, 0), 10, True, True),
    ((0, 12), (0, 0), 10, True, True),
    ((0, 0), (0, 0), 10, False, True),  # Actor obstructs the future building.
    ((-0.25, 0), (0, 0), 10, False, True),
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
            return near
        end
    """)
    lua.globals().player.position = lua.table_from(dict(zip(("x", "y"), actor)))
    lua.globals().player.build_distance = reach
    lua.globals().placeable = placeable
    lua.globals().target = lua.table_from(dict(zip(("x", "y"), target)))
    fair = object.__new__(FairActions)
    def command(script):
        lua.execute(script)
        return json.dumps(dict(lua.globals().captured))
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
        assert (point.x-target[0])**2+(point.y-target[1])**2 <= reach**2
        assert (point.x, point.y) != actor
    else:
        assert lua.globals().searches is None
        assert fair.metrics["approaches_skipped_in_reach"] == 1


def test_build_approach_failure_is_not_preflight_rejection(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    fair = object.__new__(FairActions)
    fair.command = lambda script: '{"x":1,"y":2}'
    def failed(position):
        raise RuntimeError("native pathfinder failed")
    fair.move_to = failed
    with pytest.raises(RuntimeError, match="native pathfinder"):
        fair.approach_build(SimpleNamespace(x=1, y=2), "pipe", 0)
