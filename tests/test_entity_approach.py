import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.fair_actions import FairActions, NativePathNotFound


@pytest.fixture
def interaction(monkeypatch):
    lua = pytest.importorskip("lupa.lua54").LuaRuntime()
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    lua.execute("""
        target={x=12.5,y=51.5}
        entity={valid=true,unit_number=2627}
        prototypes={entity={['chemical-plant']={selection_box={
            left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}}}}
        player={position={x=37.6875,y=43.1484375},reach_distance=10,surface={}}
        storage={fair={actor=function() return player end}}
        helpers={json_to_table=function(_) return target end,
                 table_to_json=function(value) captured=value;return 'captured' end}
        rcon={print=function(_) end}
        player.surface.find_entity=function(name,position)
            assert(name=='chemical-plant' and position==target)
            return entity
        end
        reachable=false;reach_checks=0;searches=0
        player.can_reach_entity=function(e)
            assert(e==entity);reach_checks=reach_checks+1;return reachable
        end
        player.surface.find_non_colliding_position=function(name,near,radius,precision)
            assert(name=='character' and (radius==1 or (entity==nil and radius==8)) and precision==.25)
            searches=searches+1
            if candidates then return candidates[searches] end
            return near
        end
    """)
    fair = object.__new__(FairActions)
    def command(script):
        lua.execute(script)
        result = dict(lua.globals().captured)
        if "positions" in result:
            result["positions"] = [dict(point) for point in result["positions"].values()]
        return json.dumps(result)
    fair.command = command
    movements = []
    def move(position):
        movements.append((position.x, position.y))
        lua.globals().reachable = True
    fair.move_to = move
    return fair, lua, movements


def approach(fair):
    fair.approach(SimpleNamespace(x=12.5,y=51.5), "chemical-plant")


def candidates(lua, points):
    lua.globals().candidates = lua.table_from([lua.table_from(dict(zip(("x","y"), point)))
                                            for point in points])


def test_in_reach_interaction_does_not_walk_or_search(interaction):
    fair, lua, movements = interaction
    lua.globals().reachable = True
    approach(fair)
    assert movements == [] and lua.globals().searches == 0
    assert fair.metrics["approaches_skipped_in_reach"] == 1


def test_missing_entity_preserves_generic_construction_point_api(interaction):
    fair, lua, movements = interaction
    lua.execute('entity=nil')
    approach(fair)
    assert movements == [(15.5,51.5)]
    assert lua.globals().reach_checks == 0
    assert fair.metrics.get('approaches_skipped_in_reach', 0) == 0


def test_shoreline_pocket_failure_tries_other_side_and_rechecks_reach(interaction):
    fair, lua, movements = interaction
    candidates(lua, [(15.5,51.5), (12.5,43.5), (12.5,43.5)])
    def move(position):
        movements.append((position.x,position.y))
        if len(movements) == 1:
            raise NativePathNotFound("Native pathfinder could not find a route")
        lua.globals().reachable = True
    fair.move_to = move
    approach(fair)
    assert movements == [(15.5,51.5), (12.5,43.5)]
    assert lua.globals().reach_checks == 2
    assert lua.globals().searches == 16


def test_completed_walk_without_native_reach_does_not_authorize_interaction(interaction):
    fair, lua, movements = interaction
    candidates(lua, [(15.5,51.5), (12.5,43.5)])
    def move(position):
        movements.append((position.x,position.y))
        lua.globals().reachable = len(movements) == 2
    fair.move_to = move
    approach(fair)
    assert len(movements) == 2 and lua.globals().reach_checks == 3


class UnclassifiedPathFailure(NativePathNotFound):
    pass


@pytest.mark.parametrize("failure", [
    RuntimeError("Native pathfinder could not find a route"),
    RuntimeError("Native walking is obstructed"),
    TimeoutError("Native action exceeded its bounded observation window"),
    UnclassifiedPathFailure("Unclassified native failure"),
])
def test_uncertain_walk_failure_never_tries_another_candidate(interaction, failure):
    fair, lua, movements = interaction
    candidates(lua, [(15.5,51.5), (12.5,43.5)])
    def move(position):
        movements.append((position.x,position.y))
        raise failure
    fair.move_to = move
    with pytest.raises(type(failure)) as error:
        approach(fair)
    assert error.value is failure
    assert len(movements) == 1 and lua.globals().reach_checks == 1


@pytest.mark.parametrize("change,reason", [
    ("entity=nil", "target is missing"),
    ("entity.unit_number=9999", "identity changed"),
])
def test_target_identity_is_revalidated_after_walk(interaction, change, reason):
    fair, lua, movements = interaction
    candidates(lua, [(15.5,51.5), (12.5,43.5)])
    def move(position):
        movements.append((position.x,position.y))
        lua.execute(change)
    fair.move_to = move
    with pytest.raises(Exception, match=reason):
        approach(fair)
    assert len(movements) == 1


def test_all_candidate_no_route_failures_are_bounded_and_deduplicated(interaction):
    fair, lua, movements = interaction
    candidates(lua, [(15.5,51.5), (12.5,43.5), (12.5,43.5)])
    def move(position):
        movements.append((position.x,position.y))
        raise NativePathNotFound("Native pathfinder could not find a route")
    fair.move_to = move
    with pytest.raises(NativePathNotFound, match="reachable interaction"):
        approach(fair)
    assert len(movements) == 2


def test_candidates_outside_arrival_margin_never_start_a_walk(interaction):
    fair, lua, movements = interaction
    candidates(lua, [(40,51.5)] * 16)
    with pytest.raises(Exception, match="arrival margin"):
        approach(fair)
    assert not movements and lua.globals().searches == 16


def test_candidate_ring_is_bounded_inside_native_reach(interaction):
    fair, lua, movements = interaction
    approach(fair)
    assert lua.globals().searches == 16
    assert len(movements) == 1
    x,y = movements[0]
    assert (x-12.5)**2 + (y-51.5)**2 <= 9**2
    assert (x,y) != (15.5,51.5)
