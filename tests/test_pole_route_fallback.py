"""A wider, paid pole route can recover from disconnected narrow corridors."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jev_factorio.backends.errors import ConnectionPreflightRejected
from jev_factorio.backends.fair_actions import FairActions
from jev_factorio.planning.connections import (
    shortest_wire_path, shortest_wire_path_between_regions,
)


START = {"x": 53.5, "y": 13.5}
END = {"x": 60.5, "y": 80.5}


@pytest.fixture
def position(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    env.Direction = SimpleNamespace(UP=0)
    monkeypatch.setitem(sys.modules, "fle", ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", env)
    return SimpleNamespace


def cells():
    # A route exists only through ordinary placement cells outside both
    # eight-cell L corridors, with several valid cells near each endpoint.
    path = {(x + .5, 11.5) for x in (51, 45, 39, 37)}
    path |= {(37.5, y + .5) for y in range(17, 78, 6)}
    path |= {(x + .5, 77.5) for x in (43, 49, 55, 59)}
    return path | {(53.5, 13.5), (60.5, 80.5)}


def adapter(positions, count):
    fair = object.__new__(FairActions)
    queries, placements = [], []

    def discover(name, fluid, rectangles):
        assert (name, fluid) == ("small-electric-pole", "electricity")
        queries.append(rectangles)
        return {p for p in positions if any(left <= p[0] - .5 <= right
                                            and top <= p[1] - .5 <= bottom
                                            for left, right, top, bottom in rectangles)}, set()

    fair._connection_cells = discover
    fair.command = lambda script: json.dumps({"count": count})
    fair.place_entity = lambda prototype, position, **kwargs: placements.append(
        ((position.x, position.y), kwargs))
    return fair, queries, placements


def connect(fair, position):
    fair.connect(position(**START), position(**END),
                 SimpleNamespace(value=("small-electric-pole",)), "electricity")


def test_wide_pole_route_finds_detour_and_uses_ordinary_placements(position):
    fair, queries, placements = adapter(cells(), 20)
    connect(fair, position)
    assert len(queries) == 3  # Two fast corridors and one bounded wide survey.
    assert placements[0][0] == (51.5, 11.5)
    assert placements[-1][0] == (59.5, 77.5)
    assert all(options == {"direction": 0, "exact": True} for _, options in placements)
    assert all((left <= point[0] - .5 <= right and top <= point[1] - .5 <= bottom)
               for point, _ in placements
               for left, right, top, bottom in queries[-1])
    assert max(sum((r-l+1)*(b-t+1) for l, r, t, b in group)
               for group in queries) <= 16_384
    assert sum(sum((r-l+1)*(b-t+1) for l, r, t, b in group)
               for group in queries) <= 65_536


def test_wide_route_preserves_inventory_preflight(position):
    fair, _, placements = adapter(cells(), 0)
    with pytest.raises(ConnectionPreflightRejected) as error:
        connect(fair, position)
    assert error.value.code == "insufficient_connection_materials"
    assert placements == []


def test_wide_route_unavailable_still_rejects_before_placement(position):
    fair, _, placements = adapter({(53.5, 13.5), (60.5, 80.5)}, 20)
    with pytest.raises(ConnectionPreflightRejected) as error:
        connect(fair, position)
    assert error.value.code == "no_connection_route"
    assert placements == []


def test_region_search_retains_exact_endpoint_validation():
    with pytest.raises(ValueError, match="endpoints must be passable"):
        shortest_wire_path((0.5, 0.5), (6.5, 0.5), {(0.5, 0.5)})


def test_region_search_chooses_reachable_verified_endpoint():
    cells = {(0.5, 0.5), (10.5, 0.5), (15.5, 0.5), (20.5, 0.5)}
    assert shortest_wire_path_between_regions(
        {(0.5, 0.5), (10.5, 0.5)}, {(20.5, 0.5)}, cells,
        max_wire_distance=6,
    ) == [(10.5, 0.5), (15.5, 0.5), (20.5, 0.5)]
    with pytest.raises(ValueError, match="endpoints must be passable"):
        shortest_wire_path_between_regions(
            {(10.5, 0.5)}, {(21.5, 0.5)}, cells, max_wire_distance=6,
        )
