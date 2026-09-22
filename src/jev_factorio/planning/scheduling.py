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
    """Describe each pack's refill deadline; quantities remain capped at twenty."""
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
        due = bool(amount and (early or available <= min(5, remaining)
                   or (coverage is not None and lead is not None and coverage <= lead + SAFETY_TICKS)))
        rows.append({'item': item, 'amount': amount, 'remaining': remaining,
                     'available': available, 'coverage_ticks': coverage, 'lead_ticks': lead,
                     'due': due, 'deadline_tick': snapshot.tick + max(0, math.floor(coverage - (lead or 0) - SAFETY_TICKS))
                     if coverage is not None else None,
                     'basis': 'catalog-and-policy-estimate', 'speed_assumed': 'research_speed' not in lab})
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
        return visit(target)
    except ValueError:
        return None


def future_research_demands(snapshot, catalog) -> list[tuple[str, int]]:
    """Prepare one next science batch only when current research is fully fed."""
    rows = research_schedule(snapshot, catalog)
    if not rows or any(row['available'] < row['remaining'] for row in rows):
        return []
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
        demands.append((item, target))
    return demands


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
            plan = planner._need(item, amount)
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
