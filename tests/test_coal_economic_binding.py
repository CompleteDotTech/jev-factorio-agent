"""The binding is arithmetic only; fixtures are not native provenance."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.coal_economic_observation import NativeEconomics, SourceFacts
from jev_factorio.planning.coal_economic_binding import evaluate_bound_forecast
from jev_factorio.planning.coal_economic_proof import EconomicInput
from test_coal_economic_proof import example


def facts(raw):
    parsed = EconomicInput.parse(raw)
    sources = tuple(SourceFacts(row.target_role, row.target_unit, row.layout,
                                row.remaining_ore, row.ore_mining_ticks,
                                row.consumer_max_joules_per_tick,
                                row.consumer_stored_fuel_joules_lower,
                                row.consumer_stored_fuel_joules_upper)
                    for row in parsed.sources)
    return NativeEconomics(parsed.epoch, 321, parsed.bundle_sha256, 'a' * 64,
                           parsed.power, sources)


def test_exact_typed_native_terms_allow_forecast_without_payment_authority():
    raw = example()
    native = facts(raw)
    original = deepcopy(raw)
    result = evaluate_bound_forecast(native, raw)
    assert result['forecast']['eligible'] is True
    assert result['mutation_authorized'] is False
    assert result['native_payback_proven'] is False
    assert result['native_actor_unit'] == 321
    assert raw == original


@pytest.mark.parametrize('change', [
    lambda value: value['epoch'].update(tick=9999),
    lambda value: value.update(bundle_sha256='b' * 64),
    lambda value: value['power'].update(buffer_capacity_joules_upper=0),
    lambda value: value['sources'][0].update(consumer_stored_fuel_joules_lower=1),
    lambda value: value['sources'][0].update(consumer_stored_fuel_joules_upper=1),
    lambda value: value['sources'][0].update(mining_speed_ppm=400_000),
    lambda value: value['sources'][0].update(baseline_coal_demand_forecast=-1),
])
def test_altered_native_or_declared_fields_fail_closed(change):
    raw = example()
    native = facts(raw)
    change(raw)
    with pytest.raises(ValueError):
        evaluate_bound_forecast(native, raw)


def test_lower_and_upper_stock_are_distinct_directions():
    raw = example()
    raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 1
    native = facts(raw)
    result = evaluate_bound_forecast(native, raw)
    assert result['forecast']['eligible'] is True
    raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 0
    with pytest.raises(ValueError):
        evaluate_bound_forecast(native, raw)
    raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 2
    with pytest.raises(ValueError):
        evaluate_bound_forecast(native, raw)


def test_forged_positive_native_permission_is_rejected():
    raw = example()
    with pytest.raises(ValueError):
        evaluate_bound_forecast(replace(facts(raw), mutation_authorized=True), raw)
