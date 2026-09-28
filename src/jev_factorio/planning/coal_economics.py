"""Unit-safe bounded coal investment forecasts, not native action permission.

Native facts and receipt evidence must come from a separately qualified decoder.
Positive forecasts do not require prior operation of the proposed coal network.
This module is deliberately not connected to production admission yet.
"""
from __future__ import annotations

from .coal_economic_proof import EconomicInput, MILLION, POLICY, PROOF_SCHEMA, digest

# Points are a separate versioned material opportunity budget; never add them
# to game ticks, joules, coal units or native measurements.
MATERIAL_POINTS = {'electric-mining-drill': 20, 'wooden-chest': 2,
                   'inserter': 5, 'transport-belt': 1}
MAX_MATERIAL_POINTS = 200
PAYBACK_NUMERATOR, PAYBACK_DENOMINATOR = 5, 4
SOURCE_UTILIZATION_NUMERATOR, SOURCE_UTILIZATION_DENOMINATOR = 3, 4


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def evaluate(raw: dict) -> dict:
    evidence = EconomicInput.parse(raw)
    power = evidence.power
    kit = dict(evidence.kit)
    material_points = sum(MATERIAL_POINTS[name] * count for name, count in evidence.kit)
    startup_actor_ticks = evidence.startup_actor_ticks_forecast
    # API waits/crafting gaps consume game time and fuel, but are not actor work.
    # This explicit estimate is not a guaranteed scheduling upper bound.
    startup_elapsed_ticks = evidence.startup_elapsed_game_ticks_forecast
    recurring_ticks = evidence.recurring_service_actor_ticks_forecast
    active_ticks = max(0, evidence.horizon_ticks - startup_elapsed_ticks)
    existing_rate = sum((row.max_joules_per_tick + row.drain_joules_per_tick) * len(row.units)
                        for row in power.existing_loads)
    planned_rate = (kit['electric-mining-drill'] * (power.drill_max_joules_per_tick + power.drill_drain_joules_per_tick)
                    + kit['inserter'] * (power.inserter_max_joules_per_tick + power.inserter_drain_joules_per_tick))
    # Charge every qualified existing/proposed load for the entire horizon plus
    # buffer capacity. This overestimates startup/part-built operation and never
    # credits initial stored steam as free coal or future mined return.
    electric_joules_upper = (existing_rate + planned_rate) * evidence.horizon_ticks + power.buffer_capacity_joules_upper
    conversion = power.boiler_efficiency_ppm * power.generator_efficiency_ppm
    operating_coal_upper = _ceil_div(electric_joules_upper * MILLION**2,
                                    conversion * power.coal_fuel_joules)
    bootstrap_joules = _ceil_div(((existing_rate + planned_rate) * startup_elapsed_ticks + power.buffer_capacity_joules_upper)
                                * MILLION**2, conversion)
    generation_capacity = min(power.generator_max_joules_per_tick * len(power.generator_units),
                              power.boiler_max_joules_per_tick * power.generator_efficiency_ppm // MILLION)
    branch_terms = []
    for source in evidence.sources:
        forecast = (active_ticks * source.mining_speed_ppm * SOURCE_UTILIZATION_NUMERATOR
                    // (source.ore_mining_ticks * MILLION * SOURCE_UTILIZATION_DENOMINATOR))
        capacity = min(source.remaining_ore, forecast)
        internal = source.target_role == power.boiler_role
        required = operating_coal_upper if internal else source.baseline_coal_demand_forecast
        # A demand forecast cannot value more additional fuel than this consumer
        # could burn in the horizon. The boiler baseline excludes the new loads.
        burn_upper = (_ceil_div(existing_rate * evidence.horizon_ticks * MILLION**2, conversion)
                      if internal else source.consumer_max_joules_per_tick * evidence.horizon_ticks)
        additional_demand_ceiling = _ceil_div(max(0, burn_upper - source.consumer_stored_fuel_joules_upper),
                                              power.coal_fuel_joules)
        branch_terms.append({'target_role': source.target_role, 'target_unit': source.target_unit,
            'capacity_coal_forecast': capacity, 'required_coal_forecast': required,
            'useful_external_coal_forecast': 0 if internal else required,
            'baseline_coal_demand_forecast': source.baseline_coal_demand_forecast,
            'additional_demand_coal_ceiling': additional_demand_ceiling,
            'demand_basis': source.demand_basis})
    external = sum(row['useful_external_coal_forecast'] for row in branch_terms)
    total = external + operating_coal_upper
    # Only complete comparable manual cycles are valued. No fractional trip or
    # per-item multiplication of one distant trip's walking cost is claimed.
    # New drill/arm consumption is a cost, never an invented baseline haul saving.
    baseline_manual_coal = sum(source.baseline_coal_demand_forecast for source in evidence.sources)
    cycles = min(source.baseline_coal_demand_forecast // delivered.coal
                 for source, delivered in zip(evidence.sources, evidence.manual_cycle.deliveries))
    avoided_ticks = cycles * evidence.manual_cycle.actor_ticks
    priced_ticks = startup_actor_ticks + recurring_ticks
    terms = {'horizon_ticks': evidence.horizon_ticks,
        'active_ticks_forecast': active_ticks, 'startup_actor_ticks_forecast': startup_actor_ticks,
        'startup_elapsed_game_ticks_forecast': startup_elapsed_ticks,
        'recurring_service_actor_ticks_forecast': recurring_ticks,
        'total_actor_cost_ticks_forecast': priced_ticks,
        'manual_cycles_avoided_forecast': cycles, 'manual_actor_ticks_avoided_forecast': avoided_ticks,
        'material_opportunity_points': material_points, 'material_opportunity_limit': MAX_MATERIAL_POINTS,
        'existing_power_joules_per_tick_upper': existing_rate,
        'planned_power_joules_per_tick_upper': planned_rate,
        'generation_capacity_joules_per_tick': generation_capacity,
        'electric_joules_upper': electric_joules_upper, 'operating_coal_upper': operating_coal_upper,
        'bootstrap_fuel_joules_required_forecast': bootstrap_joules,
        'total_coal_forecast': total, 'net_external_coal_forecast': external,
        'baseline_manual_coal_demand_forecast': baseline_manual_coal,
        'branches': branch_terms,
        'power_basis': 'qualified_owned_steam_prototype_full_load_and_buffer_upper_bound',
        'source_basis': 'native_mining_prototypes_discounted_forecast_capped_by_finite_ore',
        'actor_basis': 'receipt_bound_manual_cycles_vs_paid_solver_and_policy_estimates',
        'startup_elapsed_basis': 'declared_game_tick_forecast_including_gaps_not_scheduling_upper_bound',
        'material_basis': 'separate_versioned_opportunity_points_not_time_or_fuel',
        'payback_margin': {'numerator': PAYBACK_NUMERATOR, 'denominator': PAYBACK_DENOMINATOR}}
    reason = 'eligible_conservative_forecast'
    if material_points > MAX_MATERIAL_POINTS:
        reason = 'material_opportunity_budget'
    elif not active_ticks:
        reason = 'construction_exceeds_horizon'
    elif existing_rate + planned_rate > generation_capacity:
        reason = 'insufficient_qualified_generation_capacity'
    elif power.boiler_stored_fuel_joules < bootstrap_joules:
        reason = 'bootstrap_fuel_shortfall'
    elif external <= 0:
        reason = 'no_required_external_coal_demand'
    elif any(row['baseline_coal_demand_forecast'] > row['additional_demand_coal_ceiling'] for row in branch_terms):
        reason = 'demand_exceeds_native_burn_and_stock_bound'
    elif any(row['capacity_coal_forecast'] < row['required_coal_forecast'] for row in branch_terms):
        reason = 'source_cannot_cover_bounded_demand_and_power'
    elif avoided_ticks * PAYBACK_DENOMINATOR <= priced_ticks * PAYBACK_NUMERATOR:
        reason = 'actor_payback_margin_not_met'
    result = {'schema': PROOF_SCHEMA, 'policy': POLICY, 'input': evidence.to_dict(),
        'input_sha256': digest(evidence.to_dict()), 'eligible': reason == 'eligible_conservative_forecast',
        'reason': reason, 'terms': terms, 'native_payback_proven': False, 'mutation_authorized': False}
    result['proof_sha256'] = digest(result)
    return result
