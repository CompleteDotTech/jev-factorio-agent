"""Inventory conservation in bounded downstream forecasts; no native game claims."""
from copy import deepcopy

import pytest

from jev_factorio import solid_routes as contract
from jev_factorio.planning import solid_investment as policy
from jev_factorio.planning.demand import SupplyLedger
from solid_routes_fixtures import ROUTE, SOURCE, TARGET, row
from test_solid_investment import scenario, service_history, offers, make_loop


def valued(state, data, history):
    demand, reason = policy.requirements(state, data)
    assert reason == 'current_research_recipe_bill'
    return policy.offer_value(row(state), state, data, demand, history)


def assert_deficit(value, expected):
    if expected:
        assert value.get('demand_units') == expected, value
    else:
        assert not value['eligible'], value
        assert value['reason'] == 'no_current_recipe_deficit', value


@pytest.mark.parametrize('gears,copper', [(0, 120), (40, 40), (60, 60), (40, 120),
    (120, 40), (119, 119), (120, 120), (20, 0), (0, 0), (20, 80), (80, 20)])
def test_queued_science_inputs_are_not_credited_twice(gears, copper):
    state, data = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': gears, 'copper-plate': copper}
    before = deepcopy(state)
    value = valued(state, data, history)
    assert_deficit(value, max(0, 120 - gears))
    assert state == before  # Forecasting is read-only, even when stock is paired.


@pytest.mark.parametrize('carried,reserved', [(0, 0), (10, 0), (10, 10), (40, 0), (80, 20)])
def test_carried_and_reserved_gears_do_not_change_queue_credit(carried, reserved):
    state, data = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 40, 'copper-plate': 40}
    state.inventory['iron-gear-wheel'] = carried
    demand, _ = policy.requirements(state, data, reserved={'iron-gear-wheel': reserved})
    value = policy.offer_value(row(state), state, data, demand, history)
    assert_deficit(value, max(0, 120 - 40 - (carried - reserved)))


@pytest.mark.parametrize('finished,running', [(0, False), (0, True), (1, False), (20, False), (20, True)])
def test_completed_and_inflight_science_are_each_credited_once(finished, running):
    state, data = scenario(); history = service_history(state)
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 40, 'copper-plate': 40}
    target['output'] = {'automation-science-pack': finished}
    target['crafting'] = running
    assert_deficit(valued(state, data, history), 120 - 40 - finished - int(running))


@pytest.mark.parametrize('progress,lab_packs', [(0.5, 0), (0, 20), (0.25, 10), (0.75, 30)])
def test_current_progress_and_lab_stock_shorten_the_same_horizon(progress, lab_packs):
    state, data = scenario(); history = service_history(state)
    state.factory['research_progress'] = progress
    state.factory['entities']['utility:lab']['input'] = {'automation-science-pack': lab_packs}
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 20, 'copper-plate': 20}
    assert_deficit(valued(state, data, history), max(0, int(120 * (1 - progress)) - lab_packs - 20))


def test_one_production_step_preserves_outstanding_haul_demand():
    state, data = scenario(); history = service_history(state)
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 60, 'copper-plate': 60}
    prior = valued(state, data, history)
    target['input']['iron-gear-wheel'] -= 20
    target['input']['copper-plate'] -= 20
    target['output']['automation-science-pack'] = 20
    state.tick += 10; state.factory['solid_routes']['tick'] = state.tick
    after = valued(state, data, history)
    assert_deficit(prior, 60)
    assert_deficit(after, 60)
    assert prior['manual_trips_estimate'] == after['manual_trips_estimate'] == 3


def test_queue_credit_is_not_a_blanket_removal_of_destination_stock():
    state, data = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 120, 'copper-plate': 40}
    ledger = SupplyLedger.capture(state, data)
    assert ledger.queued_output['automation-science-pack'] == 40
    assert_deficit(valued(state, data, history), 0)
    assert offers(state, data, history)[0] == []


def test_actual_controller_admits_paid_route_with_partly_stocked_science(tmp_path):
    loop, backend = make_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 60, 'copper-plate': 60}
    result = loop.step()
    assert result['verified'], result
    assert result['action'] == contract.COMMAND
    assert len(backend.calls) == 1
    assert len(loop.memory.solid_commitments[ROUTE]['parts']) == 1
    assert loop.memory.failures == {}


def test_fresh_queue_completion_preserves_valid_route_permission():
    state, data = scenario(); history = service_history(state)
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 40, 'copper-plate': 40}
    selected = offers(state, data, history)[0][0]
    target['input']['iron-gear-wheel'] -= 20; target['input']['copper-plate'] -= 20
    target['output']['automation-science-pack'] = 20
    state.tick += 10; state.factory['solid_routes']['tick'] = state.tick
    assert policy.fresh_permission(selected, selected.steps[0], state, data, outcomes=history)
    assert valued(state, data, history)['demand_units'] == 80


def test_fresh_full_supply_still_prevents_payment(tmp_path):
    loop, backend = make_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 40, 'copper-plate': 40}
    def replenish():
        if backend.observations == 2:
            backend.state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 120, 'copper-plate': 120}
    backend.before_observe = replenish
    result = loop.step()
    assert not result['verified'] and not backend.calls
    assert not loop.memory.pending and not loop.memory.solid_commitments


@pytest.mark.parametrize('change', ['source', 'power', 'alias', 'reserved_kit'])
def test_corrected_demand_never_bypasses_other_investment_gates(change):
    state, data = scenario(); history = service_history(state); options = {}
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 60, 'copper-plate': 60}
    if change == 'source': state.factory['entities'][SOURCE]['output'].clear()
    elif change == 'power': state.factory['entities'][TARGET]['energy'] = 0
    elif change == 'alias': state.factory['entities']['alias'] = deepcopy(state.factory['entities'][TARGET])
    else: options['reserved'] = {'inserter': 1}
    assert offers(state, data, history, **options)[0] == []


@pytest.mark.parametrize('gear_cost,yield_count,gears,copper', [
    (2, 1, 100, 60), (2, 2, 100, 40), (3, 2, 100, 40), (1, 2, 40, 40),
    (2, 1, 239, 120), (3, 2, 179, 60), (2, 2, 120, 40), (3, 2, 180, 60)])
def test_recipe_coefficients_and_batch_yield_preserve_net_units(gear_cost, yield_count, gears, copper):
    # Catalog arithmetic only: changed recipe yields do NOT qualify a native route.
    from math import ceil
    state, data = scenario(); history = service_history(state)
    recipe = data.recipes['automation-science-pack']
    for ingredient in recipe['ingredients']:
        if ingredient['name'] == 'iron-gear-wheel': ingredient['amount'] = gear_cost
    recipe['products'][0]['amount'] = yield_count
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': gears, 'copper-plate': copper}
    assert_deficit(valued(state, data, history), max(0, ceil(120 / yield_count) * gear_cost - gears))


def test_conservation_grid_for_paired_and_unpaired_destination_stock():
    # Fixed, exhaustive fixture; no randomized seed or noisy time assertion.
    for gears in (0, 1, 19, 20, 39, 40, 60, 100, 120):
        for copper in (0, 1, 20, 40, 60, 100, 120):
            for carried in (0, 10, 30):
                state, data = scenario(); history = service_history(state)
                state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': gears, 'copper-plate': copper}
                state.inventory['iron-gear-wheel'] = carried
                assert_deficit(valued(state, data, history), max(0, 120 - gears - carried))


def test_diagnostics_explain_pledged_and_unpaired_stock():
    state, data = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 60, 'copper-plate': 40}
    value = valued(state, data, history)
    assert value['demand_basis'] == 'net_recipe_bill_less_uncommitted_input'
    assert value['destination_input_units'] == 60
    assert value['queued_input_units'] == 40
    assert value['uncommitted_input_units'] == 20
    assert value['demand_units'] == 60 and value['flow_proven'] is False


def test_forecast_credit_does_not_become_spendable_inventory():
    from jev_factorio.planning.demand import uncommitted_input
    state, data = scenario()
    state.inventory.clear()
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 60, 'copper-plate': 40}
    ledger = SupplyLedger.capture(state, data)
    assert ledger.carried == {}
    assert ledger.queued_output['automation-science-pack'] == 40
    assert uncommitted_input(target, data, 'iron-gear-wheel') == 20
    assert 'uncommitted_input' not in ledger.summary()  # No checkpoint/schema field.
    assert state.inventory == {}


@pytest.mark.parametrize('kind', ['fluid', 'probabilistic', 'multiple_products', 'unknown_recipe'])
def test_unsupported_queue_does_not_pledge_input_stock(kind):
    from jev_factorio.planning.demand import uncommitted_input
    state, data = scenario()
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 60, 'copper-plate': 40}
    if kind == 'fluid': data.recipes[target['recipe']]['ingredients'][0]['type'] = 'fluid'
    elif kind == 'probabilistic': data.recipes[target['recipe']]['products'][0]['probability'] = 0.5
    elif kind == 'multiple_products': data.recipes[target['recipe']]['products'].append(
        {'name': 'waste', 'type': 'item', 'amount': 1})
    else: target['recipe'] = 'unknown'
    assert SupplyLedger.capture(state, data).queued_output.get('automation-science-pack', 0) == 0
    assert uncommitted_input(target, data, 'iron-gear-wheel') == 60


@pytest.mark.parametrize('stock', [-1, True, float('nan'), float('inf')])
def test_invalid_queue_stock_is_never_a_credit(stock):
    from jev_factorio.planning.demand import uncommitted_input
    state, data = scenario()
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 60, 'copper-plate': stock}
    with pytest.raises(ValueError): uncommitted_input(target, data, 'iron-gear-wheel')
    with pytest.raises(ValueError): SupplyLedger.capture(state, data)


def test_catalog_and_inventory_updates_are_not_cached_between_decisions():
    from jev_factorio.planning.demand import uncommitted_input
    state, data = scenario()
    target = state.factory['entities'][TARGET]
    target['input'] = {'iron-gear-wheel': 60, 'copper-plate': 40}
    assert uncommitted_input(target, data, 'iron-gear-wheel') == 20
    target['input']['copper-plate'] = 60
    assert uncommitted_input(target, data, 'iron-gear-wheel') == 0
    data.recipes[target['recipe']]['ingredients'][0]['amount'] = 3
    ledger = SupplyLedger.capture(state, data)
    assert ledger.queued_output['automation-science-pack'] == 20
    assert uncommitted_input(target, data, 'copper-plate') == 40


@pytest.mark.parametrize('fuel', [2, 1, 0])
def test_corrected_deficit_preserves_ready_science_in_composed_controller(tmp_path, monkeypatch, fuel):
    import test_solid_investment as fixtures
    original = fixtures.scenario
    def partly_stocked():
        state, data = original()
        state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 60, 'copper-plate': 60}
        return state, data
    monkeypatch.setattr(fixtures, 'scenario', partly_stocked)
    fixtures.test_real_composed_frontier_keeps_ready_science_with_justified_policy(tmp_path, fuel)


def test_cheap_manual_service_still_defers_a_corrected_deficit():
    state, data = scenario()
    state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': 60, 'copper-plate': 60}
    value = valued(state, data, ())
    assert value['demand_units'] == 60
    assert not value['eligible'] and value['reason'] == 'manual_service_cheaper'
    assert offers(state, data)[0] == []


def test_corrected_quantity_cannot_create_a_new_failure_budget_identity():
    state, data = scenario(); history = service_history(state)
    ids = set()
    for amount in (0, 40, 60):
        state.factory['entities'][TARGET]['input'] = {'iron-gear-wheel': amount, 'copper-plate': amount}
        selected = offers(state, data, history)[0][0]
        ids.add(selected.id)
    assert len(ids) == 1
