"""Bounded manual fuel acquisition for demanded, owned production cells.

This is not coal automation. Unknown burn rate/capacity stays unknown; a
combined existing-target deficit is not evidence of measured native payback.
"""
from __future__ import annotations

import math
from dataclasses import replace
from collections import Counter

from .service_policy import position
from .scheduling import RAW_TICKS_PER_ITEM, SERVICE_TICKS, TRAVEL_TICKS_PER_TILE

MAX_CONSUMERS = 16
MAX_COAL_TARGET = 50
MAX_RESERVE = 20
MAX_SERVICE_LEG_TILES = 64
MAX_GROUP_TRAVEL_TICKS = 7200


def _quantity(value):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid fuel service quantity')
    return math.floor(value)


def service_plan(planner, primary: str, source: str, path, acquire):
    """One receipt-verified action; recompute the group after every observation.

    Only the currently needed cell and other *demanded, uncovered* cells enter
    the group. The legacy five-coal target for small burners is unchanged.
    Acquisition groups deficits, not forecasts or locked inventory. A partial
    carried reserve is useful immediately instead of requiring a full load.
    """
    entities, snapshot = planner.entities, planner.snapshot
    requested = {source}
    demands = dict(getattr(planner, 'targets', {}))
    for item, amount in getattr(planner, 'demands', {}).items():
        demands[item] = max(demands.get(item, 0), amount)
    if planner.focus:
        item, amount = planner.focus
        demands[item] = max(demands.get(item, 0), amount)
    outputs = snapshot.factory.get('output_buffers', {}).get('sources', {})
    for name, row in outputs.items():
        item = row.get('item', '')
        needed = demands.get(item, 0) - planner.ledger.carried.get(item, 0)
        ready = entities.get(row.get('chest_role', ''), {}).get('output', {}).get(item, 0)
        if needed > 0 and _quantity(ready) < needed and row.get('state') != 'fault':
            requested.add(name)

    due, observed = {}, {}
    deferred = Counter()
    failures = getattr(snapshot, '_planner_failure_budgets', {})
    def add(role):
        machine = entities.get(role, {})
        unit = machine.get('unit_number')
        if type(unit) is not int or unit <= 0:
            raise ValueError('Fuel consumer has no native identity')
        current = _quantity(machine.get('fuel', {}).get('coal', 0))
        name = machine.get('name', '')
        identity = (name, current, position(machine.get('position')))
        if unit in observed and observed[unit] != identity:
            raise ValueError('Aliased fuel observations disagree')
        observed[unit] = identity
        if name not in {'burner-inserter', 'burner-mining-drill'}:
            return
        if current >= 2:
            return
        proposal = {'role': role, 'unit_number': unit, 'fuel': current,
                    'target': 5, 'deficit': 5-current}
        old = due.get(unit)
        # Preserve the requested endpoint's identity when it has an alias.
        if old is None or role == primary:
            due[unit] = proposal

    add(primary)
    for name in sorted(requested):
        output = outputs.get(name, {})
        if output.get('state') == 'ready' and output.get('topology') is True:
            role = output.get('parts', {}).get('inserter', {}).get('role')
            if role:
                add(role)
        route = snapshot.factory.get('input_routes', {}).get('sources', {}).get(name, {})
        if (route.get('state') == 'ready' and route.get('topology') is True
                and len(route.get('parts', {})) == len(route.get('steps', []))):
            for part in ('inserter', 'drill'):
                role = route.get('parts', {}).get(part, {}).get('role')
                if role:
                    add(role)
    if not due or len(due) > MAX_CONSUMERS:
        raise ValueError('Fuel service group unavailable or over budget')
    origin = position(snapshot.player_position)
    primary_point = position(entities[primary].get('position'))
    candidates = []
    for row in due.values():
        role = row['role']
        trial = planner._transfer(role, 'coal', row['deficit'])
        if failures.get(trial.id, 0) >= 2:
            if role == primary:
                raise ValueError('Fuel primary transfer failure budget exhausted')
            deferred['plan_failure_budget'] += 1
            continue
        target_point = position(entities[role].get('position'))
        if (role != primary and primary_point is not None and target_point is not None
                and sum(abs(a-b) for a,b in zip(primary_point, target_point)) > MAX_SERVICE_LEG_TILES):
            deferred['service_leg_budget'] += 1
            continue
        candidates.append(row)
    # A bounded nearest-neighbor order, not a claim that native pathfinding succeeds.
    # The required primary cannot be dropped to make an optional group cheaper.
    first = next((r for r in candidates if r['role'] == primary), None)
    if first is None:
        raise ValueError('Fuel primary consumer is no longer due')
    consumers = [first]
    candidates.remove(first)
    cursor, extra_ticks = primary_point, 0
    while candidates:
        def distance(row):
            point = position(entities[row['role']].get('position'))
            return None if cursor is None or point is None else sum(abs(a-b) for a,b in zip(cursor, point))
        selected = min(candidates, key=lambda r: (distance(r) is None, distance(r) or 0, r['role']))
        candidates.remove(selected)
        leg = distance(selected)
        if leg is not None and (leg > MAX_SERVICE_LEG_TILES
                or extra_ticks + math.ceil(leg * TRAVEL_TICKS_PER_TILE) + SERVICE_TICKS > MAX_GROUP_TRAVEL_TICKS):
            deferred['service_time_budget'] += 1
            continue
        if sum(r['deficit'] for r in consumers) + selected['deficit'] > MAX_COAL_TARGET:
            deferred['acquisition_budget'] += 1
            continue
        consumers.append(selected)
        if leg is not None:
            extra_ticks += math.ceil(leg * TRAVEL_TICKS_PER_TILE) + SERVICE_TICKS
        cursor = position(entities[selected['role']].get('position'))
    deficit = sum(row['deficit'] for row in consumers)
    carried = _quantity(snapshot.inventory.get('coal', 0))
    spendable = min(carried, _quantity(planner.ledger.carried.get('coal', 0)))
    reserved = carried-spendable
    target = min(MAX_COAL_TARGET, deficit)
    capacity = snapshot.factory.get('inventory_insertable', {}).get('coal')
    if capacity is not None:
        capacity = _quantity(capacity)
    coal_site = position(snapshot.factory.get('fair_resource_targets', {}).get('coal', {}).get('position'))
    travel = None
    if origin is not None and coal_site is not None:
        points = [origin, coal_site]
        for row in consumers:
            point = position(entities[row['role']].get('position'))
            if point is None:
                break
            points.append(point)
        else:
            travel = sum(sum(abs(a-b) for a,b in zip(left,right))
                         for left,right in zip(points,points[1:]))
    lead = (None if travel is None else math.ceil(travel*TRAVEL_TICKS_PER_TILE)
            + len(consumers)*SERVICE_TICKS + target*RAW_TICKS_PER_ITEM)
    from .fuel_history import estimated_rate, BASIS
    rates = [estimated_rate(snapshot, row['unit_number']) for row in consumers]
    reserve, reserve_basis = 0, 'unknown_burn_rate_combined_due_deficits_only'
    if lead is None:
        reserve_basis = 'unknown_geometry_combined_due_deficits_only'
    elif all(rate is not None for rate in rates):
        rate = sum(rates)
        # Include the time required to gather the reserve itself. An unstable
        # estimate must not turn into an endless max-sized acquisition loop.
        if rate * RAW_TICKS_PER_ITEM >= 1:
            reserve_basis = 'unstable_lead_model_combined_due_deficits_only'
        else:
            reserve = min(MAX_RESERVE, MAX_COAL_TARGET - deficit,
                          math.ceil(rate * lead / (1 - rate * RAW_TICKS_PER_ITEM)))
            reserve_basis = BASIS + '_with_bounded_estimated_service_lead'
            from .scheduling import research_schedule
            rows = research_schedule(snapshot, planner.catalog)
            slack = [row['deadline_tick'] - snapshot.tick for row in rows
                     if row.get('amount', 0) and row.get('deadline_tick') is not None]
            unknown_deadline = any(row.get('amount', 0) and row.get('deadline_tick') is None for row in rows)
            boiler = entities.get('utility:boiler')
            urgent_power = bool(boiler and _quantity(boiler.get('fuel', {}).get('coal', 0)) < 5)
            if (unknown_deadline or urgent_power
                    or slack and lead + reserve * RAW_TICKS_PER_ITEM >= min(slack)):
                reserve, reserve_basis = 0, 'science_or_power_deadline_defers_optional_reserve'
    target = min(MAX_COAL_TARGET, deficit + reserve)
    if capacity is not None:
        target = min(target, capacity)
    reserve = max(0, target - deficit)
    if lead is not None:
        lead += (target - deficit) * RAW_TICKS_PER_ITEM
    selected = consumers[0]
    if spendable:
        count = min(selected['deficit'], spendable)
        plan = planner._transfer(selected['role'], 'coal', count)
    else:
        acquire_count = target if capacity is None else min(target, capacity)
        if acquire_count <= 0:
            raise ValueError('No observed inventory capacity for fuel acquisition')
        # _need compares against observed inventory. Include held coal in the
        # inventory target, without ever treating it as spendable service fuel.
        plan = acquire('coal', reserved + acquire_count, path)
        if plan is None:
            raise ValueError('Fuel acquisition did not produce a safe prerequisite')
    return replace(plan, materials={**(plan.materials or {}), 'fuel_service': {
        'schema': 2, 'observed_tick': snapshot.tick, 'consumer_count': len(consumers),
        'consumers': [{key: row[key] for key in ('role','fuel','target','deficit')} for row in consumers],
        'combined_deficit': deficit, 'acquisition_target': target,
        'acquisition_performed_by_this_plan': plan.steps[0].action == 'factory_gather',
        'carried_spendable': spendable, 'carried_held': reserved,
        'inventory_insertable': capacity, 'reserve': reserve,
        'reserve_basis': reserve_basis,
        'max_reserve': MAX_RESERVE, 'deferred': dict(deferred),
        'visit_order_basis': 'required_primary_then_bounded_nearest_neighbor_not_path_proof',
        'lead_ticks_estimate': lead,
        'lead_basis': 'catalog_policy_and_Manhattan_geometry_not_native_timing',
        'max_target': MAX_COAL_TARGET, 'sequential_replan_after_receipt': True,
    }})
