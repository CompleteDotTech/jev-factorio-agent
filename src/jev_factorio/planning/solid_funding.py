"""Bounded kit funding from owned stock; never mining, placement, or forecast credit.

Only the selected next ordinary extraction/craft is executable. The simulated
bill is a feasibility/cost calculation, not authoritative inventory. Rebuild it
from fresh facts before every mutation. No new native command is introduced.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import Catalog
    from ..state import GameSnapshot

from .. import solid_routes as routes
from ..skills import Plan, Step
from .demand import SupplyLedger
from .economics import solid_recipe
from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE
from .service_policy import position

MAX_ACTIONS = 32
MAX_TICKS = 216000
MAX_EXPANSIONS = 128
STATE_FIELDS = {'schema', 'key', 'route', 'layout', 'intent', 'source_unit', 'target_unit',
                'catalog_sha256', 'started_tick', 'deadline_tick', 'actions'}


class KitBudgetExhausted(ValueError):
    """Existing authoritative failure counts forbid this optional bill."""


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False, ensure_ascii=True).encode('ascii')).hexdigest()


def project_key(row: dict) -> str:
    # Same failure identity as construction. Geometry/tick/replacements do not
    # buy a fresh budget; exact endpoint/layout binding is enforced separately.
    binding = [row['source']['role'], row['target']['role'], row['item'], row['target']['inventory']]
    return 'solid-project:' + hashlib.sha256(json.dumps(binding, separators=(',', ':'),
                                               ensure_ascii=True).encode('ascii')).hexdigest()



def failure_count(plan: Plan, failures: dict) -> int:
    """Read existing budgets without migrating, resetting or duplicating counts."""
    key = plan.id[:-4] if plan.id.endswith(':kit') else plan.id
    parts = ['receive', 'send', *[f'belt:{i}' for i in range(1, routes.MAX_BELTS + 1)]]
    project = max((failures.get(f'{key}:{part}', 0) for part in parts), default=0)
    step = plan.steps[0]
    p = step.parameters or {}
    ordinary = f"factory:{step.action}:{p.get('role', p.get('recipe', ''))}"
    # Kit failures and earlier ordinary-plan failures are separate attempts;
    # this feature never writes one failure into both counters.
    return max(project, failures.get(plan.id, 0) + failures.get(ordinary, 0))


def intent(row: dict) -> dict:
    return {'source': row['source']['role'], 'target': row['target']['role'],
            'item': row['item'], 'destination': row['target']['inventory']}


def catalog_digest(row: dict, snapshot: GameSnapshot, catalog: Catalog) -> str:
    bill = catalog.material_demands(routes.remaining(row), {}, snapshot.researched or [])
    if len(bill.batches) > MAX_EXPANSIONS:
        raise ValueError('Kit catalog graph exceeds bound')
    return digest({'recipes': {name: catalog.recipes[name] for name in sorted(bill.batches)},
                   'hand_categories': catalog.hand_categories, 'version': catalog.version})


def _positive(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0 and value == int(value)


def acquire(row: dict, snapshot: GameSnapshot, catalog: Catalog, *, reserved=None, job=None, failures=None) -> tuple[Plan | None, dict]:
    """Prove a finite complete kit bill, then offer just its first legal action.

    Hand crafting is restricted to enabled deterministic single-output recipes.
    Missing materials may come from already observed, exclusively identified,
    permitted owned output. No future production, machine input, research unlock,
    exploration, fuel service, optional capital or manual native edit is credited.
    """
    boiler = snapshot.factory.get('entities', {}).get('utility:boiler', {})
    if (row['state'] != 'proposed' or row['pending'] or job is not None
            or boiler and boiler.get('fuel', {}).get('coal', 0) < 5):
        raise ValueError('Kit acquisition requires an unstarted proposal')
    ledger = SupplyLedger.capture(snapshot, catalog, reserved=reserved, job=job)
    stock = dict(ledger.carried)
    actions, collected = [], Counter()
    entities = snapshot.factory.get('entities', {})
    if not isinstance(entities, dict) or len(entities) > 256:
        raise ValueError('Kit owned-output visibility exceeds bound')
    ids = Counter(m.get('unit_number') for m in entities.values() if isinstance(m, dict)
                  and routes.integer(m.get('unit_number'), 1))
    outputs = {role: dict(machine.get('output', {})) for role, machine in entities.items()
               if isinstance(machine, dict) and routes.integer(machine.get('unit_number'), 1)
               and ids[machine['unit_number']] == 1 and isinstance(machine.get('output', {}), dict)}
    expansions, craft_ticks, service_ticks = 0, 0.0, 0.0
    actor = position(snapshot.player_position)

    def append(step, estimate):
        nonlocal service_ticks
        if len(actions) >= MAX_ACTIONS:
            raise ValueError('Kit acquisition action bound exceeded')
        probe = Plan(project_key(row) + ':kit', 'rocket_launch', 'Budget check', (step,))
        if failure_count(probe, failures or {}) >= 2:
            raise KitBudgetExhausted('Existing kit, action or construction failure budget exhausted')
        actions.append(step)
        service_ticks += estimate

    def consume(item, amount, path=()):
        nonlocal expansions, craft_ticks, actor
        expansions += 1
        if expansions > MAX_EXPANSIONS or item in path or not _positive(amount) or amount > 200:
            raise ValueError('Kit recipe cycle, quantity or expansion bound')
        amount = int(amount)
        use = min(stock.get(item, 0), amount)
        stock[item] = stock.get(item, 0) - use
        missing = amount - use
        if not missing:
            return
        for role, available in sorted(outputs.items()):
            quantity = min(missing, available.get(item, 0))
            if not quantity:
                continue
            if not _positive(quantity):
                raise ValueError('Invalid observed kit source quantity')
            machine = entities[role]
            if 'successors' in snapshot.factory:
                from ..successors import private_output
                if private_output(role, snapshot):
                    continue
            destination = position(machine.get('position'))
            if actor is None or destination is None:
                continue  # Unknown distance cannot be priced as free travel.
            p = {'role': role, 'item': item, 'quantity': int(quantity),
                 'receipt': f'{snapshot.tick}:factory_extract:{role}:{item}'}
            if len(p['receipt']) > 128:
                continue
            step = Step('factory_extract', 'transfer', parameters=p, timeout_ticks=1800)
            if not step.allowed(snapshot):
                continue
            distance = sum(abs(a-b) for a,b in zip(actor, destination))
            append(step, SERVICE_TICKS + distance * TRAVEL_TICKS_PER_TILE)
            actor = destination
            available[item] -= quantity
            collected[(role, item)] += quantity
            missing -= quantity
            if not missing:
                return
        recipe = catalog.recipe_for(item)
        if (not solid_recipe(recipe) or len(recipe.get('products', [])) != 1
                or recipe['products'][0]['name'] != item
                or not _positive(recipe['products'][0].get('amount'))
                or not catalog.hand_categories.get(recipe.get('category'))
                or not catalog.enabled(recipe, snapshot.researched or [])
                or not recipe.get('ingredients')
                or len(recipe['ingredients']) > 16
                or len({v['name'] for v in recipe['ingredients']}) != len(recipe['ingredients'])
                or any(not _positive(v.get('amount')) for v in recipe['ingredients'])
                or type(recipe.get('energy')) not in {int, float}
                or not math.isfinite(recipe['energy']) or recipe['energy'] <= 0):
            raise ValueError('Kit ingredient lacks a supported enabled hand recipe or owned output')
        output = int(recipe['products'][0]['amount'])
        batches = math.ceil(missing / output)
        if not 1 <= batches <= 20:
            raise ValueError('Kit hand-craft batch bound exceeded')
        costs = {v['name']: int(v['amount']) * batches for v in recipe['ingredients']}
        for name, count in sorted(costs.items()):
            consume(name, count, (*path, item))
        duration = math.ceil(recipe['energy'] * batches * 60)
        if duration > MAX_TICKS:
            raise ValueError('Kit processing horizon exceeded')
        # Absolute inventory threshold uses the real inventory at dispatch for
        # the first step only. Later simulated steps are not executable plans.
        step = Step('factory_craft', 'inventory', item,
                    snapshot.inventory.get(item, 0) + output * batches, costs=costs,
                    parameters={'recipe': recipe['name'], 'batches': batches},
                    timeout_ticks=max(1800, duration * 2))
        append(step, SERVICE_TICKS)
        craft_ticks += duration
        stock[item] = stock.get(item, 0) + output * batches - missing

    kit = routes.remaining(row)
    for item, amount in sorted(kit.items()):
        consume(item, amount)
    if not actions:
        return None, {'reason': 'complete_carried_kit', 'acquisition_actions_estimate': 0}
    if craft_ticks + service_ticks > MAX_TICKS:
        raise ValueError('Complete kit acquisition horizon exceeded')
    first = actions[0]
    headroom = snapshot.factory.get('inventory_insertable', {})
    if not isinstance(headroom, dict):
        raise ValueError('Invalid kit headroom evidence')
    product = first.parameters['item'] if first.action == 'factory_extract' else first.item
    produced = first.parameters['quantity'] if first.action == 'factory_extract' else first.threshold - snapshot.inventory.get(product, 0)
    if product in headroom and (not routes.integer(headroom[product], 0, 2**32 - 1)
                               or headroom[product] < produced):
        raise ValueError('Observed inventory cannot receive the next kit output')
    if not first.allowed(snapshot) or any(ledger.carried.get(k, 0) < v for k,v in (first.costs or {}).items()):
        raise ValueError('First kit action is not currently spendable')
    source_draw = collected.get((row['source']['role'], row['item']), 0)
    estimates = {'acquisition_actions_estimate': len(actions),
                 'acquisition_game_ticks_estimate': math.ceil(craft_ticks + service_ticks),
                 'acquisition_basis': 'owned_output_geometry_and_catalog_hand_craft_estimates',
                 'source_draw_for_kit': int(source_draw),
                 'next_output_headroom_basis': ('observed_item_headroom' if product in headroom
                                                else 'unknown_native_must_validate'),
                 'catalog_sha256': catalog_digest(row, snapshot, catalog),
                 'acquisition_service_ticks_estimate': math.ceil(service_ticks),
                 'source_units_after_kit': max(0, snapshot.factory['entities'][row['source']['role']]
                                              .get('output', {}).get(row['item'], 0) - source_draw)}
    return Plan(project_key(row) + ':kit', 'rocket_launch',
                'Acquire the next paid downstream kit prerequisite', (first,),
                materials={'solid_kit': estimates}), estimates


def validate_state(state: dict, last_tick: int, intents: list) -> None:
    if not isinstance(state, dict) or set(state) != STATE_FIELDS:
        raise ValueError('Invalid solid funding checkpoint fields')
    if (not routes.integer(state['schema'], 1, 1) or state['intent'] not in intents
            or not isinstance(state['key'], str) or len(state['key']) != 78
            or not state['key'].startswith('solid-project:')
            or any(c not in '0123456789abcdef' for c in state['key'][14:])
            or any(not isinstance(state[k], str) or not 0 < len(state[k]) <= 128 for k in ('route', 'layout'))
            or not routes.integer(state['source_unit'], 1) or not routes.integer(state['target_unit'], 1)
            or state['source_unit'] == state['target_unit']
            or not isinstance(state['catalog_sha256'], str) or len(state['catalog_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in state['catalog_sha256'])
            or not routes.integer(state['started_tick'], 0, last_tick)
            or not routes.integer(state['deadline_tick'], state['started_tick'] + 1,
                                  state['started_tick'] + MAX_TICKS)
            or not routes.integer(state['actions'], 1, MAX_ACTIONS)):
        raise ValueError('Invalid solid funding checkpoint binding or bounds')
    i = state['intent']
    stub = {'source': {'role': i['source']}, 'target': {'role': i['target'], 'inventory': i['destination']},
            'item': i['item']}
    if state['key'] != project_key(stub):
        raise ValueError('Solid funding project identity changed')


def bound(state: dict, row: dict) -> bool:
    return (state['key'] == project_key(row) and state['route'] == row['route']
            and state['layout'] == row['layout'] and state['intent'] == intent(row)
            and state['source_unit'] == row['source']['unit_number']
            and state['target_unit'] == row['target']['unit_number'])


def start(row: dict, marker: dict, tick: int) -> dict:
    return {'schema': 1, 'key': project_key(row), 'route': row['route'], 'layout': row['layout'],
            'intent': intent(row), 'source_unit': row['source']['unit_number'],
            'target_unit': row['target']['unit_number'], 'catalog_sha256': marker['catalog_sha256'],
            'started_tick': tick, 'deadline_tick': tick + MAX_TICKS, 'actions': 1}
