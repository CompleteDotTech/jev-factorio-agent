"""Bounded economic production over the existing paid native action contract.

Estimates choose investments, never grant stock, prove flow, or replace native
identity checks. The serial planner and existing paid cells remain unchanged.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .capacity_evidence import capacity_evidence

RAW = {'coal', 'iron-ore', 'copper-ore', 'stone', 'wood'}
RECURRING = {'iron-gear-wheel', 'copper-cable', 'electronic-circuit', 'advanced-circuit',
             'processing-unit', 'transport-belt', 'inserter'}
MAX_RESEARCH_UNITS = 200
MAX_PRODUCT_HORIZON = 2000
MAX_EXTRA_CELLS = 1


def solid_recipe(recipe):
    ingredients, products = recipe.get('ingredients', []), recipe.get('products', [])
    return bool(ingredients and len(products) == 1
                and all(p.get('type') == 'item' and p.get('amount', 0) > 0
                        and p.get('probability', 1) == 1 for p in products)
                and all(i.get('type') == 'item' and i.get('amount', 0) > 0 for i in ingredients)
                and type(recipe.get('energy')) in {int, float}
                and math.isfinite(recipe['energy']) and recipe['energy'] > 0)


def capability_technology(catalog, researched):
    """Unlock the basic native assembler before recursively pursuing the silo."""
    name = 'assembling-machine-1'
    machine, recipe = catalog.machines.get(name, {}), catalog.recipes.get(name, {})
    if not machine.get('categories', {}).get('crafting') or not recipe:
        return None
    if catalog.enabled(recipe, researched):
        return None
    return next((tech for tech in catalog.unlocks(name)
                 if tech not in researched and catalog.technologies[tech].get('enabled')), None)


def investment_cost(catalog, name, researched):
    """Recipe-derived material/processing effort plus declared service allowance."""
    recipe = catalog.recipes.get(name)
    if not recipe or not catalog.enabled(recipe, researched) or not solid_recipe(recipe):
        return None
    try:
        bill = catalog.material_plan(name, 1, {}, researched)
    except (ValueError, KeyError):
        return None
    if set(bill.shortages) - RAW:
        return None
    effort = 600 + sum(count * 120 for count in bill.shortages.values())
    effort += sum(catalog.recipes[key]['energy'] * batches * 60 for key, batches in bill.batches.items())
    return math.ceil(effort)


def remaining_products(snapshot, catalog):
    """Bounded shared science workload; no unsupported future recipe unlocks."""
    factory = snapshot.factory
    tech = catalog.technologies.get(factory.get('research', ''), {})
    progress = factory.get('research_progress', 0)
    if (not tech or tech.get('trigger') or type(progress) not in {int, float}
            or not math.isfinite(progress) or not 0 <= progress <= 1):
        return {}
    lab = factory.get('entities', {}).get('utility:lab', {})
    demand = {i['name']: max(0, math.ceil(min(MAX_RESEARCH_UNITS, tech['count'] * (1 - progress))
                    * i['amount'] - lab.get('input', {}).get(i['name'], 0)))
              for i in tech.get('ingredients', [])[:8]}
    try:
        from .demand import SupplyLedger
        bill = catalog.material_demands(demand, SupplyLedger.capture(snapshot, catalog).forecast_stock(),
                                        snapshot.researched or [])
    except (ValueError, KeyError):
        return {}
    products = {}
    for name, batches in bill.batches.items():
        for product in catalog.recipes[name]['products']:
            item = product['name']
            products[item] = min(MAX_PRODUCT_HORIZON,
                                products.get(item, 0) + math.ceil(product['amount'] * batches))
    return products


class EconomicProduction:
    """Mixin used only by ready-work planners and their capability-aware subclasses."""

    def _economic_evidence(self, plan, **details):
        return replace(plan, materials={**(plan.materials or {}), 'economics': {
            'basis': 'catalog-and-policy-estimate', **details}})

    def plan(self):
        # Keep the existing client/binding, crafting, boiler, and active-research
        # barriers. Research startup itself still pays every native prerequisite.
        if (self.goal == 'rocket_launch' and self.factory.get('player_bound') is True
                and self.factory.get('player_connected') is True and not self.factory.get('crafting_queue', 0)
                and not self.factory.get('research')
                and not (self.entities.get('utility:boiler') and
                         self.entities['utility:boiler'].get('fuel', {}).get('coal', 0) < 5)):
            technology = capability_technology(self.catalog, self.researched)
            if technology:
                plan = self._research(technology)
                if plan:
                    return self._economic_evidence(plan, objective='unlock_basic_assembly', technology=technology)
        return super().plan()

    def _machine(self, role, name, path, anchor='factory'):
        previous = getattr(self, '_economic_acquiring', False)
        self._economic_acquiring = True
        try:
            return super()._machine(role, name, path, anchor)
        finally:
            self._economic_acquiring = previous

    def _production(self, recipe, role, batches, path):
        previous = getattr(self, '_economic_role', None)
        self._economic_role = role
        try:
            return super()._production(recipe, role, batches, path)
        finally:
            self._economic_role = previous

    def _machine_type(self, recipe):
        role = getattr(self, '_economic_role', '') or ''
        if self.goal == 'rocket_launch' and role in self.entities:
            name = self.entities[role]['name']
            if not self.catalog.machines.get(name, {}).get('categories', {}).get(recipe['category']):
                raise ValueError('Owned production machine no longer supports its recipe')
            return name  # Never replace a paid producer because a faster tier unlocked.
        if role.startswith('capacity:'):
            selected = self._investment_machine(recipe)
            if selected:
                return selected[0]
        return super()._machine_type(recipe)

    def _workload(self, item, immediate=0):
        if not hasattr(self, '_economic_products'):
            self._economic_products = remaining_products(self.snapshot, self.catalog)
        return min(MAX_PRODUCT_HORIZON, max(immediate, self._economic_products.get(item, 0),
                                           getattr(self, 'demands', {}).get(item, 0)))

    def _investment_machine(self, recipe):
        candidates = []
        for name, machine in sorted(self.catalog.machines.items()):
            speed = machine.get('speed', 0)
            if (not machine.get('categories', {}).get(recipe['category'])
                    or type(speed) not in {int, float} or not math.isfinite(speed) or speed <= 0):
                continue
            cost = investment_cost(self.catalog, name, self.researched)
            if cost is not None:
                if self.snapshot.inventory.get(name, 0) >= 1:
                    cost = 600  # Existing carried construction is a sunk acquisition cost.
                # More expensive/faster machines need to amortize their extra kit.
                output = recipe['products'][0]['amount']
                workload = self._workload(recipe['products'][0]['name'])
                candidates.append((cost + workload / output * recipe['energy'] * 60 / speed, name, cost))
        if not candidates:
            return None
        _, name, cost = min(candidates)
        return name, cost

    def _need(self, item, amount, path=()):
        have = self.snapshot.inventory.get(item, 0)
        # Preserve immediate stock collection and all special buffer ownership
        # paths. Investment applies only to recurring deterministic solid crafts.
        if (self.goal != 'rocket_launch' or getattr(self, '_economic_acquiring', False)
                or have >= amount or item not in RECURRING and not item.endswith('-science-pack')
                or any(m.get('output', {}).get(item, 0) for m in self.entities.values())):
            return super()._need(item, amount, path)
        try:
            recipe = self.catalog.recipe_for(item)
        except (ValueError, KeyError):
            return super()._need(item, amount, path)
        if not solid_recipe(recipe) or not self.catalog.enabled(recipe, self.researched):
            return super()._need(item, amount, path)
        role = 'recipe:' + recipe['name']
        existing = self.entities.get(role)
        if existing:
            name = existing['name']
            if not self.catalog.machines.get(name, {}).get('categories', {}).get(recipe['category']):
                raise ValueError('Recurring production role has incompatible native machine')
            cost = 0
        else:
            selected = self._investment_machine(recipe)
            if not selected:
                return super()._need(item, amount, path)
            name, cost = selected
            work = self._workload(item, math.ceil(amount - have))
            # This is avoided handcraft-queue occupancy, not a promised wall-time
            # speedup: native assembly may be slower but overlaps other crafts.
            queue_ticks = work / recipe['products'][0]['amount'] * recipe['energy'] * 60
            if work < 40 or queue_ticks < cost + 1200:
                return super()._need(item, amount, path)
            from .capital import proposal
            staged = proposal(self, recipe, name, cost, work, math.ceil(amount - have))
            if staged is not None:
                return staged
        path = self._visit('item:' + item, path)
        prerequisite = self._machine(role, name, path)
        if prerequisite:
            return self._economic_evidence(prerequisite, objective='recurring_machine_production',
                                           item=item, machine=name, investment_ticks=cost,
                                           workload=self._workload(item, math.ceil(amount - have)))
        batches = min(20, math.ceil((amount - have) / recipe['products'][0]['amount']))
        prerequisite = self._production(recipe, role, batches, path)
        if prerequisite:
            return prerequisite
        machine = self.entities[role]
        queued = min(math.floor(machine.get('input', {}).get(i['name'], 0) / i['amount'])
                     for i in recipe['ingredients'])
        potential = (queued + int(machine.get('crafting') is True)) * recipe['products'][0]['amount']
        target = max(1, min(getattr(self, 'collection_batch', 10), amount - have, potential))
        speed = self.catalog.machines[name].get('speed', 1)
        return self._wait('machine_output', item, target, role,
                         timeout=max(3600, math.ceil(recipe['energy'] * batches * 120 / speed)))

    def _capacity_work(self, primary):
        """Service a second paid producer or invest only at a supplied bottleneck.

        Extra generic roles do not inherit the main furnace's belt ownership.
        They remain explicitly batch-serviced and use ordinary native receipts.
        """
        if (not primary or (primary.materials or {}).get('capital_investment') or self.goal != 'rocket_launch' or self.factory.get('crafting_queue', 0)
                or getattr(self, '_buffer_service', False) or getattr(self, '_economic_acquiring', False)
                or primary.steps[0].action != 'factory_wait'
                or primary.steps[0].effect not in {'machine_output', 'research_progress'}):
            return primary
        if primary.steps[0].effect == 'research_progress':
            # Productive/refill work was considered first. Examine at most eight
            # already measured producers; never recurse through another research wait.
            measured = getattr(self.snapshot, '_capacity_evidence', {}).get('producers', {})
            for role in sorted(measured)[:8]:
                if not role.startswith('recipe:'):
                    continue
                recipe = self.catalog.recipes.get(role[7:], {})
                if not solid_recipe(recipe):
                    continue
                item = recipe['products'][0]['name']
                if self._workload(item) <= 0:
                    continue
                watch = self._wait('machine_output', item, min(10, self._workload(item)), role)
                candidate = self._capacity_work(watch)
                if candidate.steps[0].action != 'factory_wait' and candidate.steps[0].allowed(self.snapshot):
                    return candidate
            return primary
        item = primary.steps[0].item
        try:
            recipe = self.catalog.recipe_for(item)
        except (ValueError, KeyError):
            return primary
        if not solid_recipe(recipe) or recipe['category'] not in {'smelting', 'crafting'}:
            return primary
        output = recipe['products'][0]['amount']
        main_role = 'recipe:' + recipe['name']
        source = self.entities.get(main_role)
        if not source:
            return primary
        role = f'capacity:{recipe["name"]}:{MAX_EXTRA_CELLS + 1}'
        extra = self.entities.get(role)
        workload = self._workload(item)
        if extra:
            # Continue a paid investment even if the original forecast changed.
            available = extra.get('output', {}).get(item, 0)
            need = max(1, math.ceil(primary.steps[0].threshold))
            if available:
                plan = self._transfer(role, item, min(need, available), extracting=True)
                return self._batch_collection(plan, self.snapshot.inventory.get(item, 0) + need)
            # Feed enough to unblock the current wait, not an obsolete entire horizon.
            return self._production(recipe, role, min(20, math.ceil(need / output)), ()) or primary
        # A machine with no power/fuel/input is a supply problem, not capacity proof.
        prototype = self.catalog.machines.get(source['name'], {})
        if (workload < 80 or source.get('crafting') is not True or source.get('products_finished', 0) < 20
                or prototype.get('electric') and source.get('energy', 0) <= 0
                or prototype.get('burner') and source.get('fuel', {}).get('coal', 0) < 5
                or any(source.get('input', {}).get(i['name'], 0) < i['amount'] * 10
                       or self.snapshot.inventory.get(i['name'], 0) < i['amount'] * 10
                       for i in recipe['ingredients'])):
            return primary
        evidence = capacity_evidence(self.snapshot, self.catalog, main_role)
        if evidence is None:
            return primary
        selected = self._investment_machine(recipe)
        speed = prototype.get('speed', 0)
        if not selected or type(speed) not in {int, float} or speed <= 0:
            return primary
        name, cost = selected
        # Kit acquisition belongs to Stage 3's committed investment workflow.
        # Stage 4 does not start an uncommitted multi-step construction campaign.
        if self.snapshot.inventory.get(name, 0) < 1:
            return primary
        new_speed = self.catalog.machines[name]['speed']
        observed = evidence['observed_products_per_tick']
        added_rate = new_speed * output / (recipe['energy'] * 60)
        saved = workload * (1 / observed - 1 / (observed + added_rate))
        if saved <= cost + 1200:
            return primary
        plan = self._machine(role, name, (), anchor=main_role)
        return self._economic_evidence(plan, objective='supplied_production_bottleneck',
                  item=item, machine=name, investment_ticks=cost, estimated_saved_ticks=round(saved, 2),
                  workload=workload, measured_bottleneck=evidence,
                  service_mode='bounded_manual_transfers') if plan else primary
