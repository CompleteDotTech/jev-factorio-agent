"""Synthetic arithmetic contracts; no engine, paid campaign or payback claim."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from jev_factorio.planning.coal_economic_proof import EconomicInput, INPUT_SCHEMA, POLICY, validate_proof
from jev_factorio.planning.coal_economics import evaluate


def example():
    """Declared synthetic native facts, never a qualified real-engine witness."""
    source = {'target_role': 'furnace', 'target_unit': 21, 'layout': 'bundle:1',
        'remaining_ore': 5000, 'mining_speed_ppm': 500_000, 'ore_mining_ticks': 60,
        'consumer_max_joules_per_tick': 1500, 'consumer_stored_fuel_joules_lower': 0,
        'consumer_stored_fuel_joules_upper': 0,
        'baseline_coal_demand_forecast': 30, 'demand_basis': 'current_required_recipe_forecast',
        'demand_evidence_sha256': 'd' * 64}
    boiler = {**source, 'target_role': 'utility:boiler', 'target_unit': 11,
        'consumer_max_joules_per_tick': 30_000, 'consumer_stored_fuel_joules_lower': 40_000_000,
        'consumer_stored_fuel_joules_upper': 40_000_000,
        'baseline_coal_demand_forecast': 10, 'demand_basis': 'observed_burn_projection'}
    return {'schema': INPUT_SCHEMA, 'policy': POLICY, 'base_version': '2.0.77',
        'mods': {'base': '2.0.77'},
        'epoch': {'session_id': 'synthetic-qualification-fixture', 'tick': 10_000,
                  'actor_index': 1, 'surface_index': 1, 'force_index': 1},
        'bundle_sha256': 'a' * 64, 'catalog_sha256': 'b' * 64, 'horizon_ticks': 100_000,
        'sources': [source, boiler],
        'kit': {'electric-mining-drill': 2, 'wooden-chest': 2, 'inserter': 4, 'transport-belt': 8},
        'acquisition_actor_ticks_forecast': 1000, 'acquisition_evidence_sha256': 'c' * 64,
        'construction_walk_tiles_forecast': 20, 'recurring_service_actor_ticks_forecast': 1000,
        'startup_elapsed_game_ticks_forecast': 6200,
        'power': {'network_id': 7, 'boiler_role': 'utility:boiler', 'boiler_unit': 11,
            'generator_units': [12], 'topology_sha256': '1' * 64,
            'unit_qualification_sha256': '2' * 64, 'prototype_sha256': '3' * 64,
            'coal_fuel_joules': 4_000_000, 'boiler_efficiency_ppm': 1_000_000,
            'generator_efficiency_ppm': 1_000_000, 'boiler_max_joules_per_tick': 30_000,
            'generator_max_joules_per_tick': 15_000, 'buffer_capacity_joules_upper': 1_000_000,
            'boiler_stored_fuel_joules': 40_000_000,
            'existing_loads': [{'name': 'assembling-machine-1', 'max_joules_per_tick': 1000,
                                'drain_joules_per_tick': 0, 'units': [13]}],
            'drill_max_joules_per_tick': 1500, 'drill_drain_joules_per_tick': 0,
            'inserter_max_joules_per_tick': 246, 'inserter_drain_joules_per_tick': 7},
        'manual_cycle': {'first_tick': 0, 'last_tick': 9000, 'coal_delivered': 10,
            'actor_ticks': 4000, 'receipts_sha256': 'e' * 64, 'bundle_sha256': 'a' * 64,
            'deliveries': [{'target_role': 'furnace', 'target_unit': 21, 'coal': 8},
                           {'target_role': 'utility:boiler', 'target_unit': 11, 'coal': 2}]}}


def test_positive_forecast_before_new_network_exists_is_not_mutation_permission():
    raw = example()
    original = deepcopy(raw)
    result = evaluate(raw)
    assert result['eligible'] is True
    assert result['native_payback_proven'] is False and result['mutation_authorized'] is False
    assert raw == original
    assert validate_proof(json.loads(json.dumps(result))) == result
    terms = result['terms']
    assert terms['net_external_coal_forecast'] == 30
    assert terms['total_coal_forecast'] - terms['operating_coal_upper'] == 30
    assert terms['manual_actor_ticks_avoided_forecast'] == 12_000
    assert terms['total_actor_cost_ticks_forecast'] == 7200
    assert terms['material_opportunity_points'] == 72  # Not added to 7200 ticks.
    assert terms['operating_coal_upper'] > 0


def test_immutable_typed_roundtrip_and_independent_snapshot():
    raw = example()
    typed = EconomicInput.parse(raw)
    assert typed.to_dict() == raw
    raw['sources'][0]['remaining_ore'] = 0
    assert typed.sources[0].remaining_ore == 5000
    with pytest.raises(FrozenInstanceError):
        typed.epoch.tick = 0


def test_fractional_stock_uses_lower_for_bootstrap_and_upper_for_demand_ceiling():
    raw = example()
    bootstrap = evaluate(raw)['terms']['bootstrap_fuel_joules_required_forecast']
    raw['power']['boiler_stored_fuel_joules'] = bootstrap - 1
    raw['sources'][1]['consumer_stored_fuel_joules_lower'] = bootstrap - 1
    raw['sources'][1]['consumer_stored_fuel_joules_upper'] = bootstrap
    assert evaluate(raw)['reason'] == 'bootstrap_fuel_shortfall'

    raw = example()
    raw['sources'][0]['consumer_stored_fuel_joules_lower'] = 29_999_999
    raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 30_000_000
    raw['sources'][0]['baseline_coal_demand_forecast'] = 31
    result = evaluate(raw)
    assert result['terms']['branches'][0]['additional_demand_coal_ceiling'] == 30
    assert result['reason'] == 'demand_exceeds_native_burn_and_stock_bound'


def test_stock_bounds_and_legacy_single_stock_are_not_interchangeable():
    raw = example()
    raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 2
    with pytest.raises(ValueError):
        EconomicInput.parse(raw)
    raw = example()
    raw['sources'][0]['consumer_stored_fuel_joules'] = raw['sources'][0].pop('consumer_stored_fuel_joules_lower')
    with pytest.raises(ValueError):
        EconomicInput.parse(raw)


@pytest.mark.parametrize('case,reason', [
    ('cheap_manual', 'actor_payback_margin_not_met'),
    ('horizon', 'construction_exceeds_horizon'),
    ('bootstrap', 'bootstrap_fuel_shortfall'),
    ('ore', 'source_cannot_cover_bounded_demand_and_power'),
    ('mining_rate', 'source_cannot_cover_bounded_demand_and_power'),
    ('generation', 'insufficient_qualified_generation_capacity'),
    ('idle', 'no_required_external_coal_demand'),
    ('overspecified_demand', 'demand_exceeds_native_burn_and_stock_bound'),
    ('stock_covers_demand', 'demand_exceeds_native_burn_and_stock_bound'),
])
def test_bounded_unprofitable_or_infeasible_inputs_defer(case, reason):
    raw = example()
    if case == 'cheap_manual': raw['manual_cycle']['actor_ticks'] = 100
    if case == 'horizon': raw['horizon_ticks'] = 600
    if case == 'bootstrap':
        raw['power']['boiler_stored_fuel_joules'] = 0
        raw['sources'][1]['consumer_stored_fuel_joules_lower'] = 0
        raw['sources'][1]['consumer_stored_fuel_joules_upper'] = 0
    if case == 'ore': raw['sources'][0]['remaining_ore'] = 5
    if case == 'mining_rate': raw['sources'][1]['mining_speed_ppm'] = 1
    if case == 'generation': raw['power']['generator_max_joules_per_tick'] = 1000
    if case == 'idle': raw['sources'][0]['baseline_coal_demand_forecast'] = 0
    if case == 'overspecified_demand': raw['sources'][0]['baseline_coal_demand_forecast'] = 1000
    if case == 'stock_covers_demand':
        raw['sources'][0]['consumer_stored_fuel_joules_lower'] = 400_000_000
        raw['sources'][0]['consumer_stored_fuel_joules_upper'] = 400_000_000
    result = evaluate(raw)
    assert result['eligible'] is False and result['reason'] == reason


def test_higher_network_cost_never_invents_more_avoided_manual_service():
    raw = example()
    first = evaluate(raw)
    raw['power']['drill_max_joules_per_tick'] *= 2
    second = evaluate(raw)
    assert second['terms']['operating_coal_upper'] > first['terms']['operating_coal_upper']
    assert second['terms']['manual_actor_ticks_avoided_forecast'] == first['terms']['manual_actor_ticks_avoided_forecast']


def test_partial_manual_cycle_is_not_an_avoided_trip():
    raw = example()
    raw['manual_cycle']['coal_delivered'] = 50
    raw['manual_cycle']['deliveries'][0]['coal'] = 40
    raw['manual_cycle']['deliveries'][1]['coal'] = 10
    result = evaluate(raw)
    assert result['terms']['baseline_manual_coal_demand_forecast'] == 40
    assert result['terms']['manual_cycles_avoided_forecast'] == 0
    assert result['eligible'] is False


@pytest.mark.parametrize('field', ['tick', 'actor_index', 'surface_index', 'force_index'])
@pytest.mark.parametrize('value', [True, -1, 1.5, None, '1'])
def test_epoch_requires_native_integer_identities(field, value):
    raw = example()
    raw['epoch'][field] = value
    with pytest.raises(ValueError): evaluate(raw)


@pytest.mark.parametrize('case', ['unknown_key', 'shortcut', 'unqualified_units', 'zero_drill_energy',
    'unknown_prototype', 'zero_efficiency', 'super_efficiency', 'alias', 'missing_power_target',
    'mismatched_boiler_fuel', 'different_manual_bundle', 'future_receipt', 'kit', 'mods', 'version',
    'generator_alias', 'load_alias', 'omitted_delivery', 'wrong_delivery_unit', 'wrong_delivery_total'])
def test_unknown_or_mismatched_evidence_cannot_force_positive(case):
    raw = example()
    if case == 'unknown_key': raw['eligible'] = True
    if case == 'shortcut': raw['power']['operating_coal_upper'] = 0
    if case == 'unqualified_units': raw['power']['unit_qualification_sha256'] = None
    if case == 'zero_drill_energy': raw['power']['drill_max_joules_per_tick'] = 0
    if case == 'unknown_prototype': raw['power']['existing_loads'][0]['name'] = 'electric-energy-interface'
    if case == 'zero_efficiency': raw['power']['boiler_efficiency_ppm'] = 0
    if case == 'super_efficiency': raw['power']['generator_efficiency_ppm'] = 1_000_001
    if case == 'alias': raw['sources'][0]['target_unit'] = 11
    if case == 'missing_power_target': raw['sources'][1]['target_role'] = 'wrong'
    if case == 'mismatched_boiler_fuel': raw['power']['boiler_stored_fuel_joules'] += 1
    if case == 'different_manual_bundle': raw['manual_cycle']['bundle_sha256'] = 'f' * 64
    if case == 'future_receipt': raw['manual_cycle']['last_tick'] = 10_001
    if case == 'kit': raw['kit']['electric-mining-drill'] = 1
    if case == 'mods': raw['mods']['foreign'] = '1.0'
    if case == 'version': raw['base_version'] = '2.1.20'
    if case == 'generator_alias': raw['sources'][0]['target_unit'] = 12
    if case == 'load_alias': raw['sources'][0]['target_unit'] = 13
    if case == 'omitted_delivery': raw['manual_cycle']['deliveries'].pop()
    if case == 'wrong_delivery_unit': raw['manual_cycle']['deliveries'][0]['target_unit'] = 22
    if case == 'wrong_delivery_total': raw['manual_cycle']['coal_delivered'] = 11
    with pytest.raises(ValueError): evaluate(raw)


@pytest.mark.parametrize('case', ['tick', 'unit', 'power', 'cost', 'elapsed', 'result', 'authorization', 'bool_alias'])
def test_proof_binds_all_inputs_and_derived_terms(case):
    result = evaluate(example())
    if case == 'tick': result['input']['epoch']['tick'] += 1
    if case == 'unit': result['input']['sources'][0]['target_unit'] += 1
    if case == 'power': result['input']['power']['topology_sha256'] = 'f' * 64
    if case == 'cost': result['input']['acquisition_actor_ticks_forecast'] += 1
    if case == 'elapsed': result['input']['startup_elapsed_game_ticks_forecast'] += 1
    if case == 'result': result['terms']['operating_coal_upper'] = 0
    if case == 'authorization': result['mutation_authorized'] = True
    if case == 'bool_alias': result['eligible'] = 1
    with pytest.raises(ValueError): validate_proof(result)


def test_asymmetric_demand_cannot_credit_an_unneeded_grouped_visit():
    raw = example()
    raw['manual_cycle']['deliveries'][0]['coal'] = 1
    raw['manual_cycle']['deliveries'][1]['coal'] = 9
    result = evaluate(raw)
    assert result['terms']['baseline_manual_coal_demand_forecast'] == 40
    assert result['terms']['manual_cycles_avoided_forecast'] == 1
    assert result['terms']['manual_actor_ticks_avoided_forecast'] == 4000
    assert result['eligible'] is False
    raw['sources'][1]['baseline_coal_demand_forecast'] = 0
    result = evaluate(raw)
    assert result['terms']['manual_cycles_avoided_forecast'] == 0
    assert result['eligible'] is False


def test_material_opportunity_budget_is_separate_from_time_payback():
    raw = example()
    for suffix, unit in (('b', 22), ('c', 23)):
        raw['sources'].insert(-1, {**raw['sources'][0], 'target_role': 'furnace_' + suffix,
                                   'target_unit': unit})
        raw['manual_cycle']['deliveries'].insert(-1, {'target_role': 'furnace_' + suffix,
                                                      'target_unit': unit, 'coal': 2})
    raw['manual_cycle']['coal_delivered'] += 4
    raw['kit'].update({'electric-mining-drill': 4, 'wooden-chest': 4, 'inserter': 8,
                       'transport-belt': 96})
    raw['startup_elapsed_game_ticks_forecast'] = 35_000
    result = evaluate(raw)
    assert result['terms']['material_opportunity_points'] == 224
    assert result['reason'] == 'material_opportunity_budget'


def test_actor_payback_margin_uses_exact_integer_comparison():
    raw = example()
    raw['manual_cycle']['actor_ticks'] = 3000
    result = evaluate(raw)
    assert result['terms']['manual_actor_ticks_avoided_forecast'] == 9000
    assert result['reason'] == 'actor_payback_margin_not_met'
    raw['manual_cycle']['actor_ticks'] += 1
    assert evaluate(raw)['eligible'] is True


def test_carried_kit_still_prices_placement_and_operating_fuel():
    raw = example()
    raw['acquisition_actor_ticks_forecast'] = 0
    raw['construction_walk_tiles_forecast'] = 0
    result = evaluate(raw)
    assert result['terms']['startup_actor_ticks_forecast'] == 4800
    assert result['terms']['operating_coal_upper'] > 0


def test_inter_call_gaps_cost_game_time_and_fuel_without_inventing_actor_work():
    raw = example()
    baseline = evaluate(raw)
    raw['startup_elapsed_game_ticks_forecast'] = 20_000
    delayed = evaluate(raw)
    assert baseline['eligible'] is True
    assert delayed['reason'] == 'bootstrap_fuel_shortfall'
    before, after = baseline['terms'], delayed['terms']
    assert before['total_actor_cost_ticks_forecast'] == after['total_actor_cost_ticks_forecast'] == 7200
    assert before['manual_actor_ticks_avoided_forecast'] == after['manual_actor_ticks_avoided_forecast']
    assert after['active_ticks_forecast'] == 80_000 < before['active_ticks_forecast']
    assert after['bootstrap_fuel_joules_required_forecast'] > before['bootstrap_fuel_joules_required_forecast']
    assert after['branches'][0]['capacity_coal_forecast'] < before['branches'][0]['capacity_coal_forecast']
    assert delayed['mutation_authorized'] is False and delayed['native_payback_proven'] is False
    assert validate_proof(delayed) == delayed


@pytest.mark.parametrize('elapsed', [None, True, 6200.0, '6200', -1, 6199, 216_001])
def test_elapsed_startup_requires_bounded_integer_and_serial_actor_time(elapsed):
    raw = example()
    raw['startup_elapsed_game_ticks_forecast'] = elapsed
    with pytest.raises(ValueError):
        EconomicInput.parse(raw)
