"""Deadline-aware scheduling estimates; never execution or success evidence."""
from __future__ import annotations

import math
from dataclasses import replace

from .demand import SupplyLedger

# Explicit conservative policy estimates, not measured native speed constants.
RAW_TICKS_PER_ITEM = 120
TRAVEL_TICKS_PER_TILE = 20
SERVICE_TICKS = 300
SAFETY_TICKS = 600
MAX_LEAD_TICKS = 216000


def positive(value, default=None):
    return value if type(value) in {int, float} and math.isfinite(value) and value > 0 else default


def replenishment_ticks(snapshot, catalog, item: str, amount: int) -> int | None:
    """Bound a serial refill estimate using actual recipes and explicit heuristics.

    Known output may be collected, but machine inputs are not treated as free
    material. Summing process time is deliberately conservative about overlap.
    Missing recipes or resource locations yield unknown, not zero lead time.
    """
    if amount <= 0:
        return 0
    ledger = SupplyLedger.capture(snapshot, catalog)
    stock = dict(ledger.carried)
    for material, count in ledger.collectible.items():
        stock[material] = stock.get(material, 0) + count
    try:
        bill = catalog.material_plan(item, amount, stock, snapshot.researched or [])
    except (ValueError, KeyError):
        return None
    total = SERVICE_TICKS
    for material, count in bill.shortages.items():
        if count <= 0:
            continue
        distance = snapshot.nearby_resources.get(material)
        if material not in {'coal', 'iron-ore', 'copper-ore', 'stone', 'wood'} or (
                type(distance) not in {int, float} or not math.isfinite(distance) or distance < 0):
            return None
        trips = math.ceil(count / (1 if material == 'wood' else 50))
        total += count * RAW_TICKS_PER_ITEM + trips * 2 * distance * TRAVEL_TICKS_PER_TILE
    for name, batches in bill.batches.items():
        recipe = catalog.recipes[name]
        energy = positive(recipe.get('energy'))
        if energy is None:
            return None
        speed = 1.0
        if not catalog.hand_categories.get(recipe['category']):
            speeds = {}
            for role, machine in snapshot.factory.get('entities', {}).items():
                if machine.get('recipe') != name:
                    continue
                value = positive(catalog.machines.get(machine['name'], {}).get('speed'))
                if value is not None:
                    speeds[machine.get('unit_number', role)] = value
            if not speeds:
                return None  # Building missing production is not an instantaneous refill.
            speed = min(speeds.values())  # Do not presume parallel dispatch or balanced supply.
        total += batches * energy * 60 / speed
    lab = snapshot.factory.get('entities', {}).get('utility:lab', {})
    position = lab.get('position', {})
    if all(type(position.get(axis, 0)) in {int, float} and math.isfinite(position.get(axis, 0))
           for axis in ('x', 'y')):
        x, y = snapshot.player_position
        total += (abs(position.get('x', x) - x) + abs(position.get('y', y) - y)) * TRAVEL_TICKS_PER_TILE
    return min(MAX_LEAD_TICKS, math.ceil(total))


def research_schedule(snapshot, catalog, *, early: bool = False) -> list[dict]:
    """Refill deadlines; opt-in lead-time reserves stay stack/remaining bounded."""
    factory = snapshot.factory
    name = factory.get('research', '')
    tech = catalog.technologies.get(name)
    lab = factory.get('entities', {}).get('utility:lab')
    progress = factory.get('research_progress', 0)
    if (not tech or not lab or name in (snapshot.researched or []) or tech.get('trigger')
            or type(progress) not in {int, float} or not math.isfinite(progress) or not 0 <= progress <= 1):
        return []
    energy = positive(tech.get('energy_ticks'))
    speed = positive(lab.get('research_speed'), 1)
    rows = []
    for ingredient in tech.get('ingredients', [])[:8]:
        item, each = ingredient['name'], positive(ingredient.get('amount'))
        if each is None:
            continue
        available = lab.get('input', {}).get(item, 0)
        remaining = max(0, math.ceil(tech['count'] * (1 - progress) * each))
        amount = max(0, math.ceil(min(20, remaining) - available))
        coverage = available / each * energy / speed if energy else None
        lead = replenishment_ticks(snapshot, catalog, item, amount) if amount else 0
        measured = measured_lead(snapshot, item)
        target = min(20, remaining)
        if getattr(snapshot, '_lead_time_supply', False):
            known = [value for value in (lead, measured) if value is not None]
            lead = max(known) if known else None
            if energy and lead is not None:
                # Cover the next measured/estimated refill opportunity, not a rocket-sized BOM.
                stack = catalog.stack_sizes.get(item, 200)
                stack = stack if type(stack) is int and stack > 0 else 200
                target = min(200, stack, remaining,
                             max(target, math.ceil((lead + SAFETY_TICKS) * speed / energy * each)))
            amount = max(0, math.ceil(target - available))
        due = bool(amount and (early or available <= min(5, remaining)
                   or (coverage is not None and lead is not None and coverage <= lead + SAFETY_TICKS)))
        rows.append({'item': item, 'amount': amount, 'remaining': remaining,
                     'available': available, 'coverage_ticks': coverage, 'lead_ticks': lead,
                     'due': due, 'deadline_tick': snapshot.tick + max(0, math.floor(coverage - (lead or 0) - SAFETY_TICKS))
                     if coverage is not None else None,
                     'basis': 'catalog-and-policy-estimate', 'speed_assumed': 'research_speed' not in lab,
                     **({'target': target, 'measured_lead_ticks': measured,
                         'quantity_basis': 'lead_time_coverage_with_stack_and_remaining_caps'}
                        if getattr(snapshot, '_lead_time_supply', False) else {})})
    return sorted(rows, key=lambda row: (row['deadline_tick'] if row['deadline_tick'] is not None
                                         else snapshot.tick, row['item']))


def next_technology(catalog, researched, current='', target='rocket-silo') -> str | None:
    """Bounded dependency traversal; preview only, never cancels current research."""
    done, visiting, count = set(researched) | ({current} if current else set()), set(), 0
    def visit(name):
        nonlocal count
        if name in done:
            return None
        count += 1
        if count > 512 or name in visiting:
            raise ValueError('Research lookahead cycle or budget exceeded')
        tech = catalog.technologies.get(name)
        if not tech or not tech.get('enabled'):
            raise ValueError('Research lookahead unavailable')
        visiting.add(name)
        for parent in sorted(tech.get('prerequisites', [])):
            found = visit(parent)
            if found:
                return found
        visiting.remove(name)
        return name
    try:
        from .economics import capability_technology
        capability = capability_technology(catalog, list(done))
        return visit(capability or target)
    except ValueError:
        return None


def measured_lead(snapshot, item, *, minimum_samples=1):
    row = getattr(snapshot, '_measured_replenishment', {}).get(item, {})
    if (not isinstance(row, dict) or type(row.get('schema')) is not int or row['schema'] != 1
            or row.get('session_id') != snapshot.session_id
            or type(row.get('samples')) is not int or row['samples'] < minimum_samples
            or type(row.get('lead_ticks')) is not int or not 0 < row['lead_ticks'] <= MAX_LEAD_TICKS
            or type(row.get('observed_tick')) is not int
            or not 0 <= snapshot.tick - row['observed_tick'] <= MAX_LEAD_TICKS
            or row.get('basis') != 'demand_to_verified_lab_receipt'):
        return None
    return row['lead_ticks']


def future_research_eligibility(snapshot, catalog):
    """Explain the conservative default and the explicitly enabled pilot gate.

    Partial-coverage lookahead permits only collection of already produced next
    science. It cannot consume shared ingredients, start research, or construct
    speculative infrastructure. This is intentionally narrower than full feeding.
    """
    rows = research_schedule(snapshot, catalog)
    if not rows:
        return {'eligible': False, 'reason': 'no_current_research', 'supplies': []}
    if all(row['available'] >= row['remaining'] for row in rows):
        return {'eligible': True, 'reason': 'current_research_fully_fed', 'supplies': rows,
                'collection_only': False}
    if not getattr(snapshot, '_coverage_margin_lookahead', False):
        return {'eligible': False, 'reason': 'current_research_not_fully_fed', 'supplies': rows}
    for row in rows:
        lead = measured_lead(snapshot, row['item'], minimum_samples=2)
        if lead is None:
            return {'eligible': False, 'reason': 'insufficient_verified_replenishment_history', 'supplies': rows}
        # A bounded optional service visit must fit *in addition to* protected current refill lead.
        if row['coverage_ticks'] is None or row['coverage_ticks'] < lead + SAFETY_TICKS + 900:
            return {'eligible': False, 'reason': 'current_coverage_margin', 'supplies': rows}
    return {'eligible': True, 'reason': 'measured_coverage_margin_collection_only',
            'collection_only': True, 'supplies': rows, 'max_extra_service_ticks': 900}


def current_research_supply(planner):
    """Maintain the current dependency chain before optional capital/lookahead.

    Prefer immediately usable carried/ready science, including small urgent
    deficits, before acquiring larger reserves. Existing _need recursively
    supplies steel, cable, circuits and engines; no parallel recipe planner.
    """
    snapshot = planner.snapshot
    if (not getattr(snapshot, '_lead_time_supply', False) or planner.goal != 'rocket_launch'
            or snapshot.factory.get('player_bound') is not True
            or snapshot.factory.get('player_connected') is not True
            or snapshot.factory.get('crafting_queue', 0)):
        return None
    lab = snapshot.factory.get('entities', {}).get('utility:lab', {})
    boiler = snapshot.factory.get('entities', {}).get('utility:boiler', {})
    fuel = positive(boiler.get('fuel', {}).get('coal')) if boiler else None
    if not positive(lab.get('energy')) or boiler and (fuel is None or fuel < 5):
        return None  # Preserve existing binding, power and fuel safety priority.
    stock = planner.ledger
    rows = [row for row in research_schedule(snapshot, planner.catalog) if row['due']]
    rows.sort(key=lambda row: (row['available'] > 0,
        not (stock.carried.get(row['item'], 0) or stock.collectible.get(row['item'], 0)),
        row['coverage_ticks'] if row['coverage_ticks'] is not None else 0,
        -(row['lead_ticks'] or 0), row['item']))
    original = (planner.focus, dict(planner.targets), dict(planner.raw_targets), dict(planner.demands))
    for row in rows:
        planner.focus, planner.targets, planner.raw_targets, planner.demands = (original[0], *[dict(v) for v in original[1:]])
        item, amount = row['item'], row['amount']
        have = math.floor(stock.carried.get(item, 0))
        plan = (planner._transfer('utility:lab', item, min(have, amount)) if have
                else planner._need(item, amount))
        if plan and plan.steps[0].action != 'factory_wait':
            return replace(plan, materials={**(plan.materials or {}),
                'scheduling': {'kind': 'resupply', **row}},
                description='Protect current science supply. ' + plan.description)
    planner.focus, planner.targets, planner.raw_targets, planner.demands = original
    return None


def future_research_demands(snapshot, catalog) -> list[tuple[str, int]]:
    """Prepare one next science batch only when current research is fully fed."""
    eligibility = future_research_eligibility(snapshot, catalog)
    if not eligibility['eligible']:
        return []
    rows = eligibility['supplies']
    name = next_technology(catalog, snapshot.researched or [], snapshot.factory.get('research', ''))
    tech = catalog.technologies.get(name, {})
    if not tech or tech.get('trigger'):
        return []
    surplus = {row['item']: max(0, row['available'] - row['remaining']) for row in rows}
    demands = []
    for ingredient in tech.get('ingredients', [])[:8]:
        item = ingredient['name']
        target = math.ceil(min(20, tech['count'] * ingredient['amount']) - surplus.get(item, 0))
        if target <= snapshot.inventory.get(item, 0):
            continue
        try:
            if not catalog.enabled(catalog.recipe_for(item), snapshot.researched or []):
                continue
        except (KeyError, ValueError):
            continue
        if eligibility.get('collection_only'):
            # Current research inputs stay untouched; no spending forecasts or job outputs.
            ledger = SupplyLedger.capture(snapshot, catalog)
            available = math.floor(ledger.collectible.get(item, 0))
            if available <= 0:
                continue
            entities = snapshot.factory.get('entities', {})
            matching = [entity for entity in entities.values() if entity.get('output', {}).get(item, 0)]
            origin = snapshot.player_position
            if not matching or any(not isinstance(entity.get('position'), dict) for entity in matching):
                continue
            if (len(origin) != 2 or any(type(value) not in {int, float} or not math.isfinite(value) for value in origin)
                    or any(type(entity['position'].get(axis)) not in {int, float}
                           or not math.isfinite(entity['position'][axis])
                           for entity in matching for axis in ('x', 'y'))):
                continue
            distances = [sum(abs(entity['position'][axis] - origin[index])
                             for index, axis in enumerate(('x', 'y'))) for entity in matching]
            # Include return/service slack; unknown or distant geometry is not safe slack.
            if any(not math.isfinite(distance) or SERVICE_TICKS + 2 * distance * TRAVEL_TICKS_PER_TILE > 900
                   for distance in distances):
                continue
            target = min(target, snapshot.inventory.get(item, 0) + available)
        demands.append((item, target))
    return demands


def future_research_plan(planner, item, amount):
    """Do not let a collection-only gate expand into construction or ingredient work."""
    eligibility = future_research_eligibility(planner.snapshot, planner.catalog)
    if not eligibility['eligible']:
        return None
    plan = planner._need(item, amount)
    if plan and eligibility.get('collection_only'):
        step = plan.steps[0]
        if (len(plan.steps) != 1 or step.action != 'factory_extract'
                or (step.parameters or {}).get('item') != item):
            return None
        return replace(plan, materials={**(plan.materials or {}), 'collection_only_lookahead': True})
    return plan


def ready_research_work(planner, primary):
    """The same deadline policy applies with or without background crafting."""
    if (not primary or planner.goal != 'rocket_launch'
            or primary.steps[0].action != 'factory_wait'
            or primary.steps[0].effect != 'research_progress'):
        return primary
    snapshot = planner.snapshot
    for row in research_schedule(snapshot, planner.catalog):
        if not row['due']:
            continue
        item, amount = row['item'], row['amount']
        plan = (planner._transfer('utility:lab', item, amount)
                if snapshot.inventory.get(item, 0) >= amount else planner._need(item, amount))
        if plan and plan.steps[0].action != 'factory_wait':
            return replace(plan, description=f'Refill before lab starvation: {amount} {item}. ' + plan.description,
                           materials={**(plan.materials or {}), 'scheduling': {'kind': 'resupply', **row}})
    for item, amount in future_research_demands(snapshot, planner.catalog):
        try:
            plan = future_research_plan(planner, item, amount)
        except (ValueError, KeyError):
            continue
        if plan and plan.steps[0].action not in {'factory_wait', 'factory_research'}:
            return replace(plan, description=f'Prepare next research batch: {amount} {item}. ' + plan.description)
    return primary


def scheduled_research_wait(planner, plan):
    """Coalesce small progress watches without replacing native verification."""
    step = plan.steps[0]
    if step.effect != 'research_progress' or step.action != 'factory_wait':
        return plan
    snapshot = planner.snapshot
    rows = research_schedule(snapshot, planner.catalog)
    tech = planner.catalog.technologies.get(step.item, {})
    energy, count = positive(tech.get('energy_ticks')), positive(tech.get('count'))
    if not energy or not count:
        return plan
    # Keep a bounded maintenance heartbeat. Due work is discovered on each
    # normal observation and may yield this passive wait under existing guards.
    interval = min(1800, max(1, step.timeout_ticks // 2))
    future = [row['deadline_tick'] - snapshot.tick for row in rows
              if row['amount'] and row['deadline_tick'] is not None and row['deadline_tick'] > snapshot.tick]
    if future:
        interval = min(interval, max(120, min(future)))
    progress = snapshot.factory.get('research_progress', 0)
    speed = positive(snapshot.factory.get('entities', {}).get('utility:lab', {}).get('research_speed'), 1)
    threshold = min(1, max(step.threshold, progress + interval * speed / (count * energy)))
    actual_interval = math.ceil((threshold - progress) * count * energy / speed)
    schedule = {'kind': 'research', 'technology': step.item, 'checked_tick': snapshot.tick,
                'next_check_tick': snapshot.tick + actual_interval,
                'reason': 'native_progress_watch_with_refill_deadline', 'supplies': rows}
    return replace(plan, id=plan.id + ':scheduled', steps=(replace(step, threshold=threshold),),
                   materials={**(plan.materials or {}), 'scheduling': schedule})


def poll_delay(loop) -> float:
    """Back off only acknowledged nonmutating research waits, never ambiguity."""
    base = loop.tick_seconds
    memory = getattr(loop, 'memory', None)
    if (base <= 0 or getattr(loop, 'factory_scheduling', '') != 'ready-work' or not memory
            or memory.status != 'running' or not memory.pending or not memory.active_plan
            or memory.pending.get('dispatch') != 'returned'
            or memory.pending.get('action') != 'factory_wait'):
        return base
    schedule = (memory.active_plan.get('materials') or {}).get('scheduling', {})
    next_tick = schedule.get('next_check_tick')
    if schedule.get('kind') != 'research' or type(next_tick) is not int:
        return base
    remaining = max(0, next_tick - memory.last_tick)
    return max(base, min(15.0, remaining / 240))
