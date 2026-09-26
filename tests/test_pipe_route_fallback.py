"""Offline regressions for a detour excluded by both narrow L corridors."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.errors import ConnectionPreflightRejected
from jev_factorio.backends.fair_actions import FairActions
from jev_factorio.planning.connections import shortest_pipe_path


START = {"x": 16.5, "y": 381.5}
END = {"x": 61.5, "y": 71.5}


@pytest.fixture
def position(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    env.Direction = SimpleNamespace(UP=0)
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    return SimpleNamespace


def detour():
    # Reproduce the geometry of the native route, not its private world data.
    return ({(x + .5, 381.5) for x in range(16, 24)}
            | {(23.5, y + .5) for y in range(357, 382)}
            | {(x + .5, 357.5) for x in range(23, 62)}
            | {(61.5, y + .5) for y in range(71, 358)})


def within(cells, rectangles):
    return {p for p in cells if any(left <= p[0] - .5 <= right
                                   and top <= p[1] - .5 <= bottom
                                   for left, right, top, bottom in rectangles)}


def adapter(cells, *, existing=(), count=390):
    fair = object.__new__(FairActions)
    queries, placements = [], []
    existing = set(existing)

    def discover(name, fluid, rectangles):
        assert name == "pipe" and fluid == "crude-oil"
        queries.append(rectangles)
        return within(cells - existing, rectangles), within(existing, rectangles)

    fair._connection_cells = discover
    fair.command = lambda script: json.dumps({"count": count})
    fair.place_entity = lambda prototype, position, **kwargs: placements.append(
        ((position.x, position.y), kwargs))
    return fair, queries, placements


def connect(fair, position):
    fair.connect(position(**START), position(**END),
                 SimpleNamespace(value=("pipe",)), "crude-oil")


def test_complete_rectangle_finds_detour_missing_from_both_corridors(position):
    cells = detour()
    for horizontal in (True, False):
        restricted = within(cells, FairActions._connection_corridor(
            START, END, horizontal_first=horizontal))
        with pytest.raises(ValueError, match="No passable connection path"):
            shortest_pipe_path(tuple(START.values()), tuple(END.values()), restricted)
    fair, queries, placements = adapter(cells)
    connect(fair, position)
    assert len(queries) == 4  # Two fast corridors, then two bounded chunks.
    assert len(placements) == 356
    assert {point for point, _ in placements} == cells
    assert all(options == {"direction": 0, "exact": True} for _, options in placements)
    areas = [sum((r-l+1)*(b-t+1) for l, r, t, b in rectangles)
             for rectangles in queries]
    assert max(areas) <= 16_384
    assert sum(areas) == 33_500
    assert placements[0][0] == tuple(START.values())
    assert placements[-1][0] == tuple(END.values())


def test_fallback_reuses_existing_pipes_without_spending_inventory(position):
    cells = detour()
    existing = {(23.5, y + .5) for y in range(357, 382)}
    fair, _, placements = adapter(cells, existing=existing, count=len(cells-existing))
    connect(fair, position)
    assert {point for point, _ in placements} == cells-existing


@pytest.mark.parametrize("missing,count,reason", [
    (set(), 355, "insufficient_connection_materials"),
    ({(40.5, 357.5)}, 390, "no_connection_route"),
    ({tuple(START.values())}, 390, "no_connection_route"),
])
def test_fallback_preserves_preflight_and_never_places_on_rejection(position, missing, count, reason):
    fair, _, placements = adapter(detour()-missing, count=count)
    with pytest.raises(ConnectionPreflightRejected) as error:
        connect(fair, position)
    assert error.value.code == reason
    assert placements == []


def test_fallback_budget_is_inclusive_and_chunks_do_not_overlap():
    area = 20_274
    rectangles = FairActions._pipe_fallback_rectangles(START, END, searched=65_536-area)
    assert sum((r-l+1)*(b-t+1) for l, r, t, b in rectangles) == area
    assert all(rectangles[i][3]+1 == rectangles[i+1][2]
               for i in range(len(rectangles)-1))
    assert FairActions._pipe_fallback_rectangles(START, END, searched=65_537-area) == []


def test_fast_corridor_success_does_not_scan_fallback(position):
    cells = ({(x + .5, 381.5) for x in range(16, 62)}
             | {(61.5, y + .5) for y in range(71, 382)})
    fair, queries, placements = adapter(cells)
    connect(fair, position)
    assert len(queries) == 1
    assert len(placements) == 356


def test_over_budget_fallback_performs_no_extra_queries_or_placements(position):
    fair, queries, placements = adapter(set())
    with pytest.raises(ConnectionPreflightRejected) as error:
        fair.connect(position(x=.5, y=.5), position(x=350.5, y=350.5),
                     SimpleNamespace(value=("pipe",)), "crude-oil")
    assert error.value.code == "no_connection_route"
    assert len(queries) == 2
    assert placements == []


def test_fallback_retains_maximum_route_length(position):
    cells = ({(.5, y+.5) for y in range(301)}
             | {(x+.5, 300.5) for x in range(21)}
             | {(20.5, y+.5) for y in range(301)}
             | {(x+.5, .5) for x in range(20, 31)}
             | {(30.5, y+.5) for y in range(301)})
    with pytest.raises(ValueError, match="maximum length"):
        shortest_pipe_path((.5, .5), (30.5, 300.5), cells)
    fair, queries, placements = adapter(cells, count=1000)
    with pytest.raises(ConnectionPreflightRejected) as error:
        fair.connect(position(x=.5, y=.5), position(x=30.5, y=300.5),
                     SimpleNamespace(value=("pipe",)), "crude-oil")
    assert error.value.code == "no_connection_route"
    assert len(queries) > 2
    assert placements == []


def test_failed_pole_route_uses_its_own_bounded_fallback(position):
    fair = object.__new__(FairActions)
    queries = []
    fair._connection_cells = lambda *args: queries.append(args) or (set(), set())
    fair.place_entity = lambda *args, **kwargs: pytest.fail("unexpected placement")
    with pytest.raises(ConnectionPreflightRejected) as error:
        fair.connect(position(**START), position(**END),
                     SimpleNamespace(value=("small-electric-pole",)), "electricity")
    assert error.value.code == "no_connection_route"
    assert len(queries) == 4
    assert all(sum((right-left+1)*(bottom-top+1)
                   for left, right, top, bottom in rectangles) <= 16_384
               for _, _, rectangles in queries)
