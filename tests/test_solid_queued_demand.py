"""Count destination inputs once across queued output and route payback."""
from copy import deepcopy

import pytest

from jev_factorio.planning.demand import SupplyLedger
from solid_routes_fixtures import TARGET, SOURCE, ROUTE, row
from test_solid_investment import scenario, service_history, policy, offers, make_loop


@pytest.mark.parametrize("gears,copper,carried,expected", [
    (0, 120, 0, 120), (20, 120, 0, 100), (60, 120, 0, 60),
    (80, 120, 0, 40), (120, 120, 0, 0), (150, 120, 0, 0),
    (60, 20, 0, 60), (60, 0, 0, 60), (60, 20, 20, 40),
    (60, 120, 20, 40), (60, 120, 60, 0), (0, 120, 20, 100),
])
def test_queued_batches_and_leftover_inputs_are_each_credited_once(gears, copper, carried, expected):
    state, data = scenario()
    state.factory["entities"][TARGET]["input"] = {"iron-gear-wheel": gears, "copper-plate": copper}
    state.inventory["iron-gear-wheel"] = carried
    history = service_history(state)
    before = deepcopy(state)
    demand, reason = policy.requirements(state, data)
    assert reason == "current_research_recipe_bill"
    value = policy.offer_value(row(state), state, data, demand, history)
    if expected:
        assert value["demand_units"] == expected
        assert value["valued_units"] == expected
        assert value["flow_proven"] is False
    else:
        assert value == {"eligible": False, "reason": "no_current_recipe_deficit"}
    assert state == before


def test_multiple_product_units_and_partial_recipe_stock():
    state, data = scenario()
    recipe = data.recipes["automation-science-pack"]
    recipe["ingredients"][0]["amount"] = 2  # The fixture first ingredient is gears.
    assert recipe["ingredients"][0]["name"] == "iron-gear-wheel"
    recipe["products"][0]["amount"] = 3
    state.factory["entities"][TARGET]["input"] = {"iron-gear-wheel": 61, "copper-plate": 20}
    ledger = SupplyLedger.capture(state, data)
    assert ledger.queued_output["automation-science-pack"] == 60
    value = policy.offer_value(row(state), state, data, policy.requirements(state, data)[0], service_history(state))
    # 20 more batches require 40 gears; 21 gears remain after the 20 queued batches.
    assert value["demand_units"] == 19


def test_another_queued_producer_does_not_consume_this_destinations_stock():
    state, data = scenario()
    target = state.factory["entities"][TARGET]
    other = deepcopy(target)
    other.update(unit_number=9876, position={"x": 50, "y": 50})
    other["input"] = {"iron-gear-wheel": 30, "copper-plate": 30}
    state.factory["entities"]["other-science"] = other
    target["input"] = {"iron-gear-wheel": 20, "copper-plate": 0}
    value = policy.offer_value(row(state), state, data, policy.requirements(state, data)[0], service_history(state))
    assert value["demand_units"] == 70


def test_partial_queued_science_still_admits_the_actual_paid_controller_offer(tmp_path):
    loop, backend = make_loop(tmp_path)
    backend.state.factory["entities"][TARGET]["input"]["iron-gear-wheel"] = 60
    result = loop.step()
    assert result["verified"] and len(backend.calls) == 1
    assert len(loop.memory.solid_commitments[ROUTE]["parts"]) == 1


def test_fresh_observation_with_partial_queue_retains_needed_investment(tmp_path):
    loop, backend = make_loop(tmp_path)
    def partial_queue():
        if backend.observations == 2:
            backend.state.factory["entities"][TARGET]["input"]["iron-gear-wheel"] = 60
    backend.before_observe = partial_queue
    result = loop.step()
    assert result["verified"] and len(backend.calls) == 1


def test_complete_queued_science_invalidates_the_fresh_investment(tmp_path):
    loop, backend = make_loop(tmp_path)
    def complete_queue():
        if backend.observations == 2:
            backend.state.factory["entities"][TARGET]["input"]["iron-gear-wheel"] = 120
    backend.before_observe = complete_queue
    result = loop.step()
    assert not result["verified"] and backend.calls == []
