"""Commit small same-cell service sequences using existing verified transfers."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import replace

from .service_policy import ServiceBudget, carried_stock

TRANSFER_ACTIONS = {'factory_insert', 'factory_extract'}


def service_visit(planner, plan, *, max_steps: int = 3):
    """Group only already feasible transfers; do not spend forecast collections.

    The ordinary controller persists and verifies each step, and checks fresh
    stock before dispatch. A lost acknowledgment does not replay the visit.
    Background work remains single-step under its existing lock contract.
    """
    if type(max_steps) is not int or not 1 <= max_steps <= 4:
        raise ValueError('Invalid service visit budget')
    snapshot = planner.snapshot
    if ((plan.materials or {}).get('capital_investment')
            or (plan.materials or {}).get('collection_only_lookahead')
            or planner.goal != 'rocket_launch' or len(plan.steps) != 1
            or plan.steps[0].action not in TRANSFER_ACTIONS
            or snapshot.factory.get('crafting_queue', 0)
            or not getattr(planner, 'allow_service_visits', True)):
        return plan
    first = plan.steps[0]
    entities = snapshot.factory.get('entities', {})
    role = first.parameters['role']
    cell = {role}
    source = role
    for row in snapshot.factory.get('output_buffers', {}).get('sources', {}).values():
        group = {row['source'], row.get('chest_role', '')}
        group.update(p['role'] for p in row.get('parts', {}).values())
        if role in group:
            cell, source = group - {''}, row['source']
            break
    producer = entities.get(source, {})
    recipe = planner.catalog.recipes.get(producer.get('recipe', ''), {})
    view = deepcopy(snapshot)
    spendable = carried_stock(planner)
    steps, signatures = [], set()
    budget = ServiceBudget(planner, first, cell)

    def add(step):
        parameters = step.parameters or {}
        if step.action not in TRANSFER_ACTIONS or parameters.get('role') not in cell:
            budget.rejections['outside_service_cell'] += 1
            return
        signature = step.action, parameters['role'], parameters['item']
        if signature in signatures or len(steps) >= max_steps:
            budget.rejections['duplicate_or_step_budget'] += 1
            return
        costs = step.costs or {}
        if any(count > spendable.get(item, 0) for item, count in costs.items()):
            budget.rejections['carried_stock_or_reservation'] += 1
            return
        # Fresh preconditions will still be rechecked by the real dispatcher.
        if not step.allowed(view) or step.satisfied(view):
            budget.rejections['fresh_precondition_or_already_satisfied'] += 1
            return
        if steps and not budget.admit(step):
            return
        steps.append(step)
        signatures.add(signature)
        for item, count in costs.items():
            spendable[item] = spendable.get(item, 0) - count
            view.inventory[item] = view.inventory.get(item, 0) - count
        # A collection is not made available for spending by later steps.
        machine = view.factory['entities'][parameters['role']]
        section = 'output' if step.action == 'factory_extract' else 'input'
        item, count = parameters['item'], parameters['quantity']
        if item == 'coal' and step.action == 'factory_insert':
            section = 'fuel'
        stock = machine.setdefault(section, {})
        stock[item] = max(0, stock.get(item, 0) + (-count if step.action == 'factory_extract' else count))

    add(first)
    if not steps:
        return plan
    # Science packs already carried can service one lab in one committed visit.
    # Keep the selected first transfer unchanged, including a one-pack tail.
    if source == 'utility:lab':
        for row in budget.science:
            item = row['item']
            count = min(row['amount'], spendable.get(item, 0),
                        max(0, planner.catalog.stack_sizes.get(item, 200)
                            - view.factory['entities'][source].get('input', {}).get(item, 0)))
            if count > 0:
                add(planner._transfer(source, item, count).steps[0])
    for target in sorted(cell):
        machine = entities.get(target, {})
        name = machine.get('name', '')
        if not (planner.catalog.machines.get(name, {}).get('burner') or name in {
                'boiler', 'burner-inserter', 'burner-mining-drill'}):
            continue
        low, maximum = (2, 5) if name in {'burner-inserter', 'burner-mining-drill'} else (5, 50)
        fuel = machine.get('fuel', {}).get('coal', 0)
        if fuel < low and spendable.get('coal', 0):
            count = min(maximum - fuel, spendable['coal'])
            add(planner._transfer(target, 'coal', count).steps[0])
    # Feed only real carried solids for an already configured dedicated recipe.
    # Route ownership guards reject hand-feeding an automated input cell.
    for ingredient in recipe.get('ingredients', []):
        if ingredient.get('type') != 'item':
            continue
        item, each = ingredient['name'], ingredient['amount']
        target = planner.targets.get(item, 0)
        if item in {'iron-ore', 'copper-ore', 'stone'}:
            target = max(target, min(20 * each, spendable.get(item, 0)))
        buffered = view.factory['entities'][source].get('input', {}).get(item, 0)
        need = max(0, math.ceil(target - buffered - (each if producer.get('crafting') else 0)))
        # Use observed free stack space; consumption can only increase it.
        stack_size = planner.catalog.stack_sizes.get(item, 200)
        free_stack = max(0, stack_size - buffered)
        count = min(200, need, spendable.get(item, 0), free_stack)
        if count:
            add(planner._transfer(source, item, count).steps[0])
    # Collect only a batch that the active planner already considers ready.
    for product in recipe.get('products', []):
        item = product['name']
        desired = max(planner.targets.get(item, 0), getattr(planner, 'demands', {}).get(item, 0))
        if not desired:
            continue
        try:
            pickup = planner._need(item, desired)
        except (ValueError, KeyError):
            continue
        if pickup and pickup.steps[0].action == 'factory_extract':
            add(pickup.steps[0])
    if len(steps) == 1:
        if getattr(snapshot, '_campaign_diagnostics', False):
            return replace(plan, materials={**(plan.materials or {}), 'service_visit': {
                **budget.summary(), 'steps': 1, 'unit_numbers': [entities[role]['unit_number']]}})
        return plan
    identity = [{k: v for k, v in s.parameters.items() if k != 'receipt'}
                | {'action': s.action} for s in steps]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    return replace(plan, id=f'service:{source}:{digest}', steps=tuple(steps),
                   materials={**(plan.materials or {}), 'service_visit': {
                       **budget.summary(), 'steps': len(steps),
                       'unit_numbers': [entities[s.parameters['role']]['unit_number'] for s in steps]}},
                   description=f'Service {source} in {len(steps)} individually verified transfers. '
                               + plan.description)
