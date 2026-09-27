"""Bounded manual fuel acquisition for demanded, owned production cells.

This is not coal automation. Unknown burn rate/capacity stays unknown; a
combined existing-target deficit is not evidence of measured native payback.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .service_policy import position
from .scheduling import RAW_TICKS_PER_ITEM, SERVICE_TICKS, TRAVEL_TICKS_PER_TILE

MAX_CONSUMERS = 16
MAX_COAL_TARGET = 50


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

    due = {}
    observed = {}
    def add(role):
        machine = entities.get(role, {})
        unit = machine.get('unit_number')
        if type(unit) is not int or unit <= 0:
            raise ValueError('Fuel consumer has no native identity')
        current = _quantity(machine.get('fuel', {}).get('coal', 0))
        name = machine.get('name', '')
        identity = (name, current)
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
    def distance(role):
        target = position(entities[role].get('position'))
        return (sum(abs(a-b) for a,b in zip(origin,target))
                if origin is not None and target is not None else None)
    # Nearest eligible service first; unknown geometry retains the requested
    # endpoint first, not a fictitious zero-distance advantage.
    consumers = sorted(due.values(), key=lambda r: (
        distance(r['role']) is None,
        distance(r['role']) if distance(r['role']) is not None else (0 if r['role']==primary else 1),
        r['role']))
    deficit = sum(row['deficit'] for row in consumers)
    carried = _quantity(snapshot.inventory.get('coal', 0))
    spendable = min(carried, _quantity(planner.ledger.carried.get('coal', 0)))
    reserved = carried-spendable
    target = min(MAX_COAL_TARGET, deficit)
    capacity = snapshot.factory.get('inventory_insertable', {}).get('coal')
    if capacity is not None:
        capacity = _quantity(capacity)
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
    return replace(plan, materials={**(plan.materials or {}), 'fuel_service': {
        'schema': 1, 'observed_tick': snapshot.tick, 'consumer_count': len(consumers),
        'consumers': [{key: row[key] for key in ('role','fuel','target','deficit')} for row in consumers],
        'combined_deficit': deficit, 'acquisition_target': target,
        'carried_spendable': spendable, 'carried_held': reserved,
        'inventory_insertable': capacity, 'reserve': 0,
        'reserve_basis': 'unknown_burn_rate_combined_due_deficits_only',
        'lead_ticks_estimate': lead,
        'lead_basis': 'catalog_policy_and_Manhattan_geometry_not_native_timing',
        'max_target': MAX_COAL_TARGET, 'sequential_replan_after_receipt': True,
    }})
