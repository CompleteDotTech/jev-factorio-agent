from copy import deepcopy
import math

import pytest

from jev_factorio import solid_routes as r
from jev_factorio.factory_contract import allowed, satisfied, validate_command
from jev_factorio.planning.solid_routes import candidates
from solid_routes_fixtures import ROUTE, SOURCE, TARGET, fixture, row, parameters, build, full, commission


def test_paid_prefix_not_placement_claim_and_stable_plan_identity():
    state = fixture()
    first = candidates(state, "rocket_launch")[0]
    assert first.steps[0].allowed(state) and not first.steps[0].satisfied(state)
    identity = first.id
    row(state)["layout"] = "changed-valid-offer"
    state.tick += 1; state.factory["solid_routes"]["tick"] = state.tick
    assert candidates(state, "rocket_launch")[0].id == identity
    assert not first.steps[0].allowed(state)
    current = candidates(state, "rocket_launch")[0]
    build(state, current.steps[0].parameters)
    assert current.steps[0].satisfied(state)
    assert r.remaining(row(state)) == {"transport-belt": 2, "inserter": 1}
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)


@pytest.mark.parametrize("field,value", [("extra", "bad"), ("part", "drill"), ("receipt", ""), ("layout", True), ("route", "x"*129)])
def test_invalid_commands(field, value):
    p = parameters(fixture()); p[field] = value
    with pytest.raises(ValueError):
        validate_command(r.COMMAND, p)


@pytest.mark.parametrize("mutate", [
    lambda s: s.factory["solid_routes"].update(tick=299),
    lambda s: s.factory["solid_routes"].update(protocol=True),
    lambda s: s.factory["solid_routes"].update(actor_index=0),
    lambda s: s.factory["solid_routes"].update(session_id="other"),
    lambda s: row(s)["steps"][1].update(direction=0),
    lambda s: row(s)["steps"][0].update(direction=4),
    lambda s: row(s)["steps"][0].update(name="burner-inserter"),
    lambda s: row(s)["steps"][0]["position"].update(x=5),
    lambda s: row(s)["steps"][0]["position"].update(x=math.nan),
    lambda s: row(s)["steps"][0]["position"].update(x=1_000_001),
    lambda s: row(s)["steps"].reverse(),
    lambda s: row(s)["source"].update(inventory="fuel"),
    lambda s: row(s)["target"].update(unit_number=5001),
    lambda s: row(s)["target"]["position"].update(x=100),
    lambda s: row(s)["source"]["bounds"]["right_bottom"].update(x=15),
    lambda s: row(s).update(topology=True),
    lambda s: row(s).update(state="ready"),
    lambda s: row(s).update(pending={"part":"send","receipt":"x","phase":"prepared"}),
])
def test_malformed_or_stale_observations_fail_closed(mutate):
    state = fixture(); p = parameters(state); mutate(state)
    with pytest.raises(ValueError): r.routes(state)
    assert not r.allowed(p, state)
    assert not r.component_complete(p, state)
    assert not r.permits("factory_wait", {}, state)


@pytest.mark.parametrize("mutation", ["unit", "role", "receipt", "paid", "prefix"])
def test_bad_paid_prefix(mutation):
    state = fixture(); build(state); build(state)
    parts = row(state)["parts"]
    if mutation == "prefix": parts["send"] = parts.pop("receive")
    elif mutation == "paid": parts["belt:2"]["paid"] = True
    else:
        key = "unit_number" if mutation == "unit" else mutation
        parts["belt:2"][key] = parts["receive"][key]
    with pytest.raises(ValueError): r.routes(state)


def test_current_rejects_replaced_or_aliased_entities():
    state = fixture(); assert r.current(row(state), state)
    state.factory["entities"]["alias"] = deepcopy(state.factory["entities"][SOURCE])
    assert not r.current(row(state), state)
    del state.factory["entities"]["alias"]
    state.factory["entities"][SOURCE]["unit_number"] = 999
    assert not r.current(row(state), state)


def test_complete_geometry_requires_sustained_flow_and_never_rebuilds_backpressure():
    state = fixture(); full(state)
    assert not candidates(state, "rocket_launch")
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)
    commission(state)
    assert r.flow_complete(ROUTE, row(state)["layout"], state)
    assert satisfied("solid_flow", row(state)["layout"], 3, {"role": ROUTE}, state, "factory_wait")
    row(state)["reason"] = "backpressure"
    assert candidates(state, "rocket_launch") == []
    # Already observed flow is historical within the coherent snapshot, not a
    # claim that blocked machinery is currently productive.
    row(state)["flow"]["last_tick"] -= 1
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)


@pytest.mark.parametrize("field,value", [("received", 4), ("sent", True), ("positive_samples", 2), ("last_tick", 0),
                                        ("unattributed_loss", 1), ("target_unit", 999), ("method", "screenshot")])
def test_invalid_or_insufficient_flow_is_not_accepted(field, value):
    state = fixture(); full(state); commission(state); row(state)["flow"][field] = value
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)


def test_whole_kit_and_fresh_actor_are_required():
    state = fixture(); p = parameters(state)
    state.inventory["transport-belt"] = 1
    assert not allowed(r.COMMAND, p, state)
    state.inventory["transport-belt"] = 2
    state.factory["crafting_queue"] = 1
    assert not allowed(r.COMMAND, p, state)
    state.factory["crafting_queue"] = 0
    state.factory["player_connected"] = False
    assert not allowed(r.COMMAND, p, state)


def test_connected_item_and_recipe_locks_preserve_other_ingredients():
    state = fixture(); full(state)
    assert not r.permits("factory_insert", {"role": TARGET, "item": "iron-gear-wheel"}, state)
    assert not r.permits("factory_extract", {"role": SOURCE, "item": "iron-gear-wheel"}, state)
    assert not r.permits("factory_configure", {"role": TARGET, "recipe": "different"}, state)
    assert r.permits("factory_insert", {"role": TARGET, "item": "copper-plate"}, state)


def test_actor_epoch_is_cross_checked_against_the_same_native_runtime():
    state = fixture()
    state.factory["acceptance_runtime"] = {"player_index": 2, "surface_index": 1, "force_index": 1}
    with pytest.raises(ValueError, match="epoch"): r.routes(state)


def test_commitments_detach_and_require_monotone_paid_prefix():
    state = fixture(); build(state); saved = r.commitment(row(state))
    r.validate_commitment(saved, ROUTE)
    build(state)
    assert r.reconciles(saved, row(state)) and len(saved["parts"]) == 1
    row(state)["parts"]["receive"]["receipt"] = "changed"
    assert not r.reconciles(saved, row(state))


@pytest.mark.parametrize("reason", ["backpressure", "no_power", "source_depleted"])
def test_blocked_routes_are_not_current_productive_flow(reason):
    state = fixture(); full(state); commission(state)
    assert r.flow_complete(ROUTE, row(state)["layout"], state)
    row(state)["reason"] = reason
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)


def test_refreshing_idle_snapshots_does_not_refresh_positive_delivery():
    state = fixture(); full(state); commission(state)
    flow = row(state)["flow"]
    last_positive = state.tick
    for gap, expected in [(r.MAX_FLOW_IDLE_TICKS, True), (r.MAX_FLOW_IDLE_TICKS+1, False)]:
        state.tick = last_positive+gap
        state.factory["solid_routes"]["tick"] = state.tick
        flow["last_tick"] = state.tick
        assert r.flow_complete(ROUTE, row(state)["layout"], state) is expected
    assert flow["received"] == 3


@pytest.mark.parametrize("stamp", [-1, True, 1000000])
def test_invalid_positive_delivery_stamp_is_rejected(stamp):
    state = fixture(); full(state); commission(state)
    row(state)["flow"]["last_positive_tick"] = stamp
    assert not r.flow_complete(ROUTE, row(state)["layout"], state)


def test_project_failure_identity_survives_uncommitted_endpoint_replacement():
    state = fixture()
    before = candidates(state, 'rocket_launch')[0]
    value = row(state)
    value['source']['unit_number'] = 9001
    state.factory['entities'][SOURCE]['unit_number'] = 9001
    new_route = 'solid:9001:5002:iron-gear-wheel:input'
    value['route'] = new_route
    value['layout'] = 'new-native-layout'
    state.factory['solid_routes']['routes'] = {new_route:value}
    after = candidates(state, 'rocket_launch')[0]
    assert before.id == after.id  # Failed project cannot buy two more tries by replacement.
    assert before.steps[0].parameters != after.steps[0].parameters
    assert not before.steps[0].allowed(state)
