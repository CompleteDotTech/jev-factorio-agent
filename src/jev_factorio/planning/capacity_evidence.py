"""Process-local evidence for bounded extra capacity, not a flow certificate.

Only existing observations are sampled. Point-sampled activity is not continuous
utilization; long gaps, identity changes and missing facts invalidate history.
The private snapshot attribute is derived planner context, not native telemetry.
"""
from __future__ import annotations

import math
from collections import Counter, deque
from copy import deepcopy

MAX_PRODUCERS = 64
MAX_SAMPLES = 32
SAMPLE_TICKS = 120
MIN_SPAN = 1200
MAX_GAP = 900


def finite(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _sample(snapshot, catalog, role: str, machine: dict) -> dict | None:
    from .economics import solid_recipe
    recipe = catalog.recipes.get(machine.get('recipe', ''), {})
    prototype = catalog.machines.get(machine.get('name', ''), {})
    unit, counter, crafting = (machine.get('unit_number'), machine.get('products_finished'),
                               machine.get('crafting'))
    # Restrict counter-to-output conversion to deterministic one-unit recipes.
    if (not solid_recipe(recipe) or recipe['products'][0]['amount'] != 1
            or not catalog.enabled(recipe, snapshot.researched or [])
            or not prototype.get('categories', {}).get(recipe['category'])
            or type(unit) is not int or unit <= 0
            or type(counter) is not int or counter < 0 or type(crafting) is not bool):
        return None
    speed = prototype.get('speed')
    inputs, outputs, fuel = machine.get('input'), machine.get('output'), machine.get('fuel')
    if (not finite(speed) or speed <= 0 or not all(isinstance(v, dict) for v in (inputs, outputs, fuel))
            or not all(finite(inputs.get(i['name'], 0)) and inputs.get(i['name'], 0) >= 0 for i in recipe['ingredients'])):
        return None
    item = recipe['products'][0]['name']
    stock = outputs.get(item, 0)
    buffer = snapshot.factory.get('output_buffers', {}).get('sources', {}).get(role)
    if buffer:
        if buffer.get('source_unit') != unit or buffer.get('state') != 'ready':
            return None
        chest = snapshot.factory.get('entities', {}).get(buffer.get('chest_role'), {})
        chest_stock = chest.get('output', {}).get(item)
        if not finite(chest_stock) or chest_stock < 0:
            return None
        stock += chest_stock
    if not finite(stock) or stock < 0:
        return None
    if prototype.get('electric'):
        energy = machine.get('energy')
        if not finite(energy):
            return None
        powered = energy > 0
    elif prototype.get('burner'):
        coal = fuel.get('coal')
        if not finite(coal):
            return None
        powered = coal >= 5
    else:
        return None
    supplied = all(inputs.get(i['name'], 0) >= i['amount'] for i in recipe['ingredients'])
    state = ('power_or_fuel' if not powered else 'input_starved' if not supplied else
             'active' if crafting else 'stopped')
    return {'tick': snapshot.tick, 'unit_number': unit, 'name': machine['name'],
            'recipe': machine['recipe'], 'products_finished': counter, 'stock': stock,
            'state': state, 'nominal_rate': speed / (recipe['energy'] * 60)}


class CapacityHistory:
    def __init__(self) -> None:
        self.session = None
        self.last_tick = -1
        self.rows: dict[str, deque] = {}
        self.latest: dict[str, dict] = {}

    def observe(self, snapshot, catalog) -> dict:
        if snapshot.session_id != self.session or snapshot.tick < self.last_tick:
            self.rows.clear()
            self.latest.clear()
        self.session, self.last_tick = snapshot.session_id, snapshot.tick
        entities = snapshot.factory.get('entities', {})
        roles = sorted(role for role in entities if role.startswith(('recipe:', 'capacity:')))[:MAX_PRODUCERS]
        units = Counter(entities[role].get('unit_number') for role in roles
                        if isinstance(entities[role], dict) and type(entities[role].get('unit_number')) is int)
        result = {}
        for role in roles:
            machine = entities[role]
            try:
                current = _sample(snapshot, catalog, role, machine)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                current = None
            if current is None or units[current['unit_number']] != 1:
                self.rows.pop(role, None)
                self.latest.pop(role, None)
                result[role] = {'eligible': False, 'reason': 'missing_or_unsupported_evidence'}
                continue
            samples = self.rows.setdefault(role, deque(maxlen=MAX_SAMPLES))
            previous = self.latest.get(role)
            if previous and (any(current[k] != previous[k] for k in ('unit_number', 'name', 'recipe', 'nominal_rate'))
                             or current['products_finished'] < previous['products_finished']
                             or snapshot.tick - previous['tick'] > MAX_GAP):
                samples.clear()
            # Even a brief observed outage invalidates a healthy window. Otherwise
            # a sub-interval bad observation could disappear before the next sample.
            if current['state'] != 'active' or current['stock'] >= 10:
                samples.clear()
            self.latest[role] = current
            if not samples or snapshot.tick - samples[-1]['tick'] >= SAMPLE_TICKS:
                samples.append(current)
            # Current failures are not hidden until the next sampling interval.
            result[role] = self._summary(samples, current)
        self.rows = {role: rows for role, rows in self.rows.items() if role in roles}
        self.latest = {role: row for role, row in self.latest.items() if role in roles}
        evidence = {'schema': 1, 'session_id': snapshot.session_id, 'tick': snapshot.tick,
                    'basis': 'sampled_observations_not_continuous_utilization', 'producers': result}
        snapshot._capacity_evidence = evidence
        return deepcopy(evidence)

    @staticmethod
    def _summary(samples, current):
        first, last = samples[0], samples[-1]
        span = last['tick'] - first['tick']
        produced = last['products_finished'] - first['products_finished']
        removed = produced - (last['stock'] - first['stock'])
        states = dict(Counter(s['state'] for s in samples))
        active = states.get('active', 0) / len(samples)
        rate = produced / span if span > 0 else None
        reason = 'sustained_supplied_production'
        if len(samples) < 3 or span < MIN_SPAN:
            reason = 'insufficient_history'
        elif current['state'] != 'active' or active < 0.8 or states.get('input_starved') or states.get('power_or_fuel'):
            reason = 'supply_or_activity_constraint'
        elif produced < 2 or removed < 2 or current['stock'] >= 10 or last['stock'] - first['stock'] > 1:
            reason = 'output_or_transport_constraint'
        elif rate < 0.75 * current['nominal_rate'] or rate > 1.25 * current['nominal_rate']:
            reason = 'rate_outside_supported_band'
        return {'eligible': reason == 'sustained_supplied_production', 'reason': reason,
                'unit_number': current['unit_number'], 'name': current['name'], 'recipe': current['recipe'],
                'first_tick': first['tick'], 'last_tick': last['tick'], 'samples': len(samples),
                'span_ticks': span, 'sample_states': states, 'sample_active_fraction': active,
                'products_delta': produced, 'removed_from_cell': removed, 'output_stock': current['stock'],
                'observed_products_per_tick': rate, 'last_products_finished': current['products_finished']}


def capacity_evidence(snapshot, catalog, role: str) -> dict | None:
    """Recheck freshness and live identity before accepting an optional investment."""
    data = getattr(snapshot, '_capacity_evidence', None)
    if (not isinstance(data, dict) or data.get('schema') != 1 or data.get('session_id') != snapshot.session_id
            or data.get('tick') != snapshot.tick):
        return None
    row = data.get('producers', {}).get(role, {})
    try:
        current = _sample(snapshot, catalog, role, snapshot.factory['entities'][role])
        if (not current or row.get('eligible') is not True or current['state'] != 'active'
                or any(row.get(k) != current[k] for k in ('unit_number', 'name', 'recipe'))
                or row.get('last_products_finished') != current['products_finished']
                or row.get('output_stock') != current['stock'] or current['stock'] >= 10):
            return None
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None
    return deepcopy(row)


def expansion_ready(snapshot, catalog, source_role: str) -> bool:
    """Fresh demand and spare supply must still support a new capacity placement."""
    from .economics import remaining_products
    if capacity_evidence(snapshot, catalog, source_role) is None:
        return False
    try:
        machine = snapshot.factory['entities'][source_role]
        recipe = catalog.recipes[machine['recipe']]
        item = recipe['products'][0]['name']
        demand = remaining_products(snapshot, catalog).get(item, 0)
        return (demand >= 80 and all(
            machine['input'].get(i['name'], 0) >= i['amount'] * 10
            and snapshot.inventory.get(i['name'], 0) >= i['amount'] * 10
            for i in recipe['ingredients']))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
