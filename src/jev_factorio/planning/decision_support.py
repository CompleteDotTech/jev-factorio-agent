"""Local decision evidence and transparent ranking; never action authorization.

Durations are policy estimates. Euclidean travel is a lower bound, not a native
path or arrival promise. Missing geometry is unknown, never zero-cost travel.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy

from .scheduling import (RAW_TICKS_PER_ITEM, SAFETY_TICKS, SERVICE_TICKS,
                         TRAVEL_TICKS_PER_TILE, research_schedule)


def _finite(value):
    return type(value) in {int, float} and math.isfinite(value)


def _position(value):
    if isinstance(value, dict):
        value = [value.get('x'), value.get('y')]
    if isinstance(value, (tuple, list)) and len(value) == 2 and all(_finite(v) for v in value):
        return tuple(value)
    return None


def distinct_candidates(plans):
    """Collapse identical executable options without changing the retained ID.

    Apply after failure-budget filtering. Receipts are part of the signature:
    different verification identities must not be silently aliased.
    """
    result, seen = [], set()
    for plan in plans:
        signature = json.dumps([step.__dict__ for step in plan.steps], sort_keys=True,
                               allow_nan=False, separators=(',', ':'))
        if signature not in seen:
            seen.add(signature)
            result.append(plan)
    return result


def candidate_evidence(snapshot, catalog, plans) -> dict:
    """Describe the admitted frontier without inventing downstream output."""
    entities = snapshot.factory.get('entities', {})
    try:
        schedules = research_schedule(snapshot, catalog)
    except (ValueError, KeyError, TypeError):
        schedules = []  # Unsupported forecast is explicitly unknown below.
    due_packs = {row['item'] for row in schedules if row['due']}
    result = {}
    for index, plan in enumerate(plans):
        origin = _position(snapshot.player_position)
        travel, actor, unknown, reasons = 0.0, 0.0, [], []
        urgency, outputs, quantities, costs = 0, set(), 0.0, {}
        for step in plan.steps:
            parameters = step.parameters or {}
            role = parameters.get('role', '')
            entity = entities.get(role, {})
            item = parameters.get('item', parameters.get('resource', step.item))
            amount = parameters.get('quantity', 0)
            amount = amount if _finite(amount) and amount > 0 else 0
            for name, count in (step.costs or {}).items():
                costs[name] = costs.get(name, 0) + count
            passive = step.action in {'factory_wait', 'idle'}
            target = None
            if step.action == 'factory_gather':
                evidence = snapshot.factory.get('fair_resource_targets', {}).get(item, {})
                target = _position(evidence.get('position'))
            elif role:
                target = _position(entity.get('position'))
            if passive or step.action in {'factory_craft', 'factory_research', 'factory_bind'}:
                distance = 0.0
            elif origin is not None and target is not None:
                distance = math.dist(origin, target)
                origin = target
            else:
                distance = None
                origin = None
                unknown.append('travel:' + step.action)
            if distance is not None:
                travel += distance
                actor += distance * TRAVEL_TICKS_PER_TILE
            if not passive:
                actor += SERVICE_TICKS
            if step.action == 'factory_gather':
                actor += amount * RAW_TICKS_PER_ITEM
                quantities += amount
            elif step.action == 'factory_craft':
                recipe = catalog.recipes.get(parameters.get('recipe', ''), {})
                energy, batches = recipe.get('energy'), parameters.get('batches')
                if _finite(energy) and energy > 0 and type(batches) is int and batches > 0:
                    actor += energy * batches * 60
                    outputs.update(p['name'] for p in recipe.get('products', []) if p.get('type') == 'item')
                else:
                    unknown.append('craft_duration')
            elif step.action in {'factory_insert', 'factory_extract'}:
                quantities += amount
                outputs.add(item)
            if step.action == 'factory_insert':
                fuel = entity.get('fuel', {}).get('coal')
                if item == 'coal' and _finite(fuel) and fuel < 2:
                    urgency = max(urgency, 3)
                    reasons.append('observed_low_fuel:' + role)
                if role == 'utility:lab' and item in due_packs:
                    urgency = max(urgency, 3)
                    reasons.append('due_research_delivery:' + item)
                # A current, coverage-due refill outranks optional stockpiling.
                # This affects ranking only; native execution guards still apply.
                schedule = (plan.materials or {}).get('scheduling', {})
                coverage, lead = schedule.get('coverage_ticks'), schedule.get('lead_ticks')
                if (schedule.get('kind') == 'producer_resupply'
                        and schedule.get('observed_tick') == snapshot.tick
                        and schedule.get('role') == role and schedule.get('item') == item
                        and _finite(coverage) and coverage >= 0 and _finite(lead) and lead >= 0
                        and coverage <= lead + SAFETY_TICKS):
                    urgency = max(urgency, 2)
                    reasons.append('due_producer_refill:' + role)
                recipe = catalog.recipes.get(entity.get('recipe', ''), {})
                ingredients = recipe.get('ingredients', [])
                requirements = {i['name']: i['amount'] for i in ingredients if i.get('type') == 'item'}
                supplied = entity.get('input', {})
                powered = entity.get('energy', 0) > 0 or entity.get('fuel', {}).get('coal', 0) > 0
                if (item in requirements and powered and not entity.get('crafting')
                        and supplied.get(item, 0) < requirements[item]
                        and all(supplied.get(name, 0) + (amount if name == item else 0) >= count
                                for name, count in requirements.items())
                        and len(requirements) == len(ingredients)):
                    urgency = max(urgency, 2)
                    reasons.append('unblocks_supplied_recipe:' + role)
        target = (plan.materials or {}).get('local_objective')
        if target is not None:
            target = deepcopy(target)
        passive = all(s.action in {'factory_wait', 'idle'} for s in plan.steps)
        result[plan.id] = {
            'compiler_order': index, 'passive': passive, 'urgency': urgency,
            'reasons': sorted(set(reasons)), 'local_target': target,
            'travel_tiles_lower_bound': None if any(x.startswith('travel:') for x in unknown) else round(travel, 3),
            'actor_ticks_estimate': None if unknown else math.ceil(actor),
            'processed_units': quantities, 'material_costs': costs,
            'delivers_or_crafts': sorted(outputs), 'unknowns': sorted(set(unknown)),
            'research_deadline_tick': min((row['deadline_tick'] for row in schedules
                if row['item'] in outputs and row['deadline_tick'] is not None), default=None),
            'requires_investment': any(s.action in {'factory_place', 'factory_connect',
                                      'factory_buffer_build', 'factory_input_build'} for s in plan.steps),
            'estimate_basis': 'native_observation_and_catalog_with_declared_policy_heuristics',
        }
    return result


def ranking_key(row: dict) -> tuple:
    """Urgency and productive work precede known actor cost; ties stay stable.

    Unknown cost is never zero-cost work. Among equally urgent, fully estimated
    options prefer useful units per occupied actor tick; ties use compiler order.
    This is a scheduling heuristic, not a success probability or calibrated value.
    """
    duration = row['actor_ticks_estimate']
    amount = max(1, row['processed_units'])
    return (row['passive'], -row['urgency'], duration is None,
            (duration / amount) if duration is not None else 0,
            row['compiler_order'])


def scheduling_context(snapshot, catalog, plans, goal: str) -> dict:
    evidence = candidate_evidence(snapshot, catalog, plans)
    primary = (plans[0].materials or {}).get('local_objective') if plans else None
    return {
        'local_objective': {
            'kind': 'ready_production', 'ultimate_goal': goal,
            'primary_target': deepcopy(primary),
            'instruction': 'Prevent observed starvation, remove the next production blocker, '
                           'or do useful independent work while production runs. '
                           'A single useful action need not complete the ultimate goal.',
            'success_authority': 'unchanged native step and goal predicates, never model scores',
        },
        'candidate_evidence': evidence,
        'deterministic_ranking': sorted(evidence, key=lambda key: ranking_key(evidence[key])),
        'selection_contract': {'schema': 1, 'observed_tick': snapshot.tick,
                               'heuristics_are_not_native_timing_measurements': True},
    }
