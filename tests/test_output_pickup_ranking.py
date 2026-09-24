"""Offline pickup choices; policy estimates are not native travel measurements."""
from copy import deepcopy

import pytest

from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_factory import catalog, machine, snapshot


def pickup(planner_type, stocks, need=20, *, carried=0, position=(0, 0)):
    state = snapshot(inventory={"iron-plate": carried}, player_position=position)
    state.factory["entities"] = {
        role: machine(output={"iron-plate": count}, position=location, crafting=False)
        for role, count, location in stocks
    }
    before = deepcopy(state)
    plan = planner_type(catalog(), state, "rocket_launch")._need("iron-plate", need)
    assert state == before
    return plan.steps[0], state


@pytest.mark.parametrize("planner_type", [FactoryPlanner, ReadyWorkPlanner])
def test_stocked_neighbor_avoids_a_one_plate_pickup(planner_type):
    step, state = pickup(planner_type, [
        ("capacity:iron-plate:2", 1, {"x": 0, "y": 0}),
        ("recipe:iron-plate", 20, {"x": 8, "y": 0}),
    ])
    assert step.action == "factory_extract"
    assert step.parameters["role"] == "recipe:iron-plate"
    assert step.parameters["quantity"] == 20
    assert step.allowed(state) and not step.satisfied(state)
    # A stale quantity is still refused at the ordinary execution boundary.
    state.factory["entities"]["recipe:iron-plate"]["output"]["iron-plate"] = 19
    assert not step.allowed(state)


def test_tiny_deficit_prefers_nearby_stock_not_largest_stockpile():
    step, _ = pickup(FactoryPlanner, [
        ("a-far", 200, {"x": 100, "y": 0}),
        ("z-near", 2, {"x": 1, "y": 0}),
    ], need=11, carried=10)
    assert step.parameters["role"] == "z-near"
    assert step.parameters["quantity"] == 1


def test_larger_stock_does_not_force_an_excessive_detour():
    step, _ = pickup(FactoryPlanner, [
        ("a-far", 20, {"x": 1000, "y": 0}),
        ("z-near", 10, {"x": 0, "y": 0}),
    ])
    assert step.parameters["role"] == "z-near"
    assert step.parameters["quantity"] == 10


def test_ranking_and_transfer_both_cap_useful_stock_at_native_limit():
    step, _ = pickup(FactoryPlanner, [
        ("a-far", 1000, {"x": 10, "y": 0}),
        ("z-near", 200, {"x": 0, "y": 0}),
    ], need=500)
    assert step.parameters["role"] == "z-near"
    assert step.parameters["quantity"] == 200


@pytest.mark.parametrize("unknown", [None, {"x": float("nan"), "y": 0}, {"x": True, "y": 0}])
def test_unknown_geometry_preserves_role_order(unknown):
    step, _ = pickup(FactoryPlanner, [
        ("a-existing", 1, unknown),
        ("z-stocked", 20, {"x": 0, "y": 0}),
    ])
    assert step.parameters["role"] == "a-existing"


def test_equal_choices_use_stable_role_order():
    step, _ = pickup(FactoryPlanner, [
        ("z", 20, {"x": 1, "y": 0}),
        ("a", 20, {"x": -1, "y": 0}),
    ])
    assert step.parameters["role"] == "a"


def test_restricted_output_cannot_outrank_an_allowed_pickup(monkeypatch):
    from jev_factorio import factory_contract
    allowed = factory_contract.allowed
    monkeypatch.setattr(factory_contract, "allowed", lambda action, parameters, state:
                        parameters.get("role") != "a-restricted" and allowed(action, parameters, state))
    step, state = pickup(FactoryPlanner, [
        ("a-restricted", 200, {"x": 0, "y": 0}),
        ("z-allowed", 2, {"x": 0, "y": 0}),
    ])
    assert step.parameters["role"] == "z-allowed" and step.allowed(state)


def test_successor_private_output_cannot_win_pickup_ranking():
    state = snapshot()
    state.factory.update(successors={}, output_buffers={"sources": {
        "growth:iron-plate": {"chest_role": "output-chest:private"},
    }})
    state.factory["entities"] = {
        "growth:iron-plate": machine(output={"iron-plate": 200}),
        "output-chest:private": machine("wooden-chest", output={"iron-plate": 200}),
        "recipe:iron-plate": machine(output={"iron-plate": 2}),
    }
    step = FactoryPlanner(catalog(), state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.parameters["role"] == "recipe:iron-plate"
    assert step.parameters["quantity"] == 2
