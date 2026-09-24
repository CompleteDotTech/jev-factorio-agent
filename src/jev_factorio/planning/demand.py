"""Bounded material forecasts; only carried, unreserved stock is spendable.

Machine inputs are not a bag of free raw material. For supported deterministic
solid recipes they are credited as future products, separately from the one
in-flight batch. Forecasts never authorize a native action or its verifier.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .materials import quantities


@dataclass
class SupplyLedger:
    carried: dict[str, float] = field(default_factory=dict)
    reserved: dict[str, float] = field(default_factory=dict)
    collectible: dict[str, float] = field(default_factory=dict)
    machine_inputs: dict[str, float] = field(default_factory=dict)
    queued_output: dict[str, float] = field(default_factory=dict)
    in_flight_output: dict[str, float] = field(default_factory=dict)
    acknowledged_output: dict[str, float] = field(default_factory=dict)
    locked_inventory: dict[str, float] = field(default_factory=dict)

    @classmethod
    def capture(cls, snapshot, catalog, *, reserved=None, job=None) -> SupplyLedger:
        from ..launch_readiness import reserved as payload_reserve
        commitments = quantities(reserved or {})
        for item, amount in payload_reserve(snapshot).items():
            commitments[item] = commitments.get(item, 0) + amount
        ledger = cls(carried=quantities(snapshot.inventory), reserved=commitments)
        for item, count in ledger.reserved.items():
            if count > ledger.carried.get(item, 0):
                raise ValueError('Reservation exceeds carried supply')
            ledger.carried[item] -= count
        if job is not None:
            for item, count in job.outputs.items():
                ledger.locked_inventory[item] = ledger.carried.pop(item, 0)
                ledger.acknowledged_output[item] = job.baseline[item] + count
        # Aliases for one native inventory must not multiply forecast supply.
        buckets = {}
        def credit(category, identity, item, count):
            count = quantities({item: count})[item]
            key = category, identity, item
            buckets[key] = max(buckets.get(key, 0), count)
        for role, machine in sorted(snapshot.factory.get('entities', {}).items()):
            if 'successors' in snapshot.factory:
                from ..successors import private_output
                if private_output(role, snapshot):
                    continue  # Uncollected qualification/trial stock is not general forecast supply.
            unit = machine.get('unit_number')
            identity = ('unit', unit) if type(unit) is int and unit > 0 else ('role', role)
            for item, count in machine.get('output', {}).items():
                credit('collectible', identity, item, count)
            for item, count in machine.get('input', {}).items():
                credit('machine_inputs', identity, item, count)
            recipe = catalog.recipes.get(machine.get('recipe', ''), {})
            ingredients, products = recipe.get('ingredients', []), recipe.get('products', [])
            if (not ingredients or len(products) != 1 or products[0].get('type') != 'item'
                    or products[0].get('probability', 1) != 1
                    or any(i.get('type') != 'item' or i.get('amount', 0) <= 0 for i in ingredients)):
                continue
            product, output = products[0]['name'], products[0]['amount']
            quantities({product: output})
            queued = min(math.floor(machine.get('input', {}).get(i['name'], 0) / i['amount'])
                         for i in ingredients)
            credit('queued_output', identity, product, queued * output)
            if machine.get('crafting') is True:
                credit('in_flight_output', identity, product, output)
        for (category, _, item), count in buckets.items():
            target = getattr(ledger, category)
            target[item] = target.get(item, 0) + count
        return ledger

    def forecast_stock(self) -> dict[str, float]:
        result = dict(self.carried)
        for category in (self.collectible, self.queued_output, self.in_flight_output,
                         self.acknowledged_output):
            for item, count in category.items():
                result[item] = result.get(item, 0) + count
        return result

    def summary(self) -> dict:
        return {name: dict(getattr(self, name)) for name in self.__dataclass_fields__}


def horizon_demands(snapshot, catalog, goal: str, item: str, amount: int) -> dict[str, int]:
    """Current task plus at most two upcoming science batches, never a rocket BOM."""
    result = {item: math.ceil(amount)}
    if goal != 'rocket_launch':
        return result
    factory = snapshot.factory
    tech = catalog.technologies.get(factory.get('research', ''), {})
    progress = factory.get('research_progress', 0)
    if (tech and not tech.get('trigger') and type(progress) in {int, float}
            and math.isfinite(progress) and 0 <= progress <= 1):
        lab = factory.get('entities', {}).get('utility:lab', {})
        for ingredient in tech.get('ingredients', [])[:8]:
            name = ingredient['name']
            remaining = math.ceil(tech['count'] * (1 - progress) * ingredient['amount'])
            wanted = min(40, max(0, remaining - lab.get('input', {}).get(name, 0)))
            if wanted:
                result[name] = max(result.get(name, 0), wanted)
    if item == 'coal':
        due = {}
        for role, machine in factory.get('entities', {}).items():
            name = machine.get('name', '')
            if (catalog.machines.get(name, {}).get('burner') or name in {
                    'boiler', 'burner-inserter', 'burner-mining-drill'}):
                fuel = machine.get('fuel', {}).get('coal', 0)
                target = 5 if name in {'burner-inserter', 'burner-mining-drill'} else 50
                if fuel < (2 if target == 5 else 5):
                    key = machine.get('unit_number', role)
                    due[key] = max(due.get(key, 0), target - fuel)
        result[item] = max(result[item], min(50, sum(due.values())))
    return result
