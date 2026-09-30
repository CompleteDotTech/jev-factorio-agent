"""Typed, same-observation evidence for native craft-item research triggers."""
from __future__ import annotations

import math


def current_trigger(snapshot, catalog, technology, outer_recipe):
    """Return a current native trigger bridge without treating it as a recipe edge.

    The outer recipe must remain locked. This evidence names its unlock
    technology and the exact cumulative craft-item counter separately from the
    trigger item's own enabled recipe and ingredients.
    """
    factory = snapshot.factory
    runtime = factory.get('acceptance_runtime') if isinstance(factory, dict) else None
    if (snapshot.world_kind != 'fle'
            or getattr(snapshot, '_coherent_observation_verified', None)
                != (snapshot.session_id, snapshot.tick)
            or factory.get('observation_snapshot_schema') != 2
            or type(factory.get('tick')) is not int or factory['tick'] != snapshot.tick
            or not isinstance(runtime, dict) or runtime.get('schema') != 1
            or runtime.get('session_id') != snapshot.session_id
            or runtime.get('speed') != 1 or runtime.get('tick_paused') is not False
            or any(type(runtime.get(key)) is not int or runtime[key] <= 0
                   for key in ('actor_unit', 'surface_index', 'force_index'))):
        return None
    if (not isinstance(technology, str) or not technology
            or not isinstance(outer_recipe, str) or not outer_recipe):
        return None
    locked = catalog.recipes.get(outer_recipe)
    tech = catalog.technologies.get(technology)
    unlocks = catalog.unlocks(outer_recipe)
    researched = snapshot.researched or []
    if (not isinstance(locked, dict) or locked.get('name') != outer_recipe
            or locked.get('hidden') or catalog.enabled(locked, researched)
            or unlocks != [technology]
            or not isinstance(tech, dict) or tech.get('enabled') is not True
            or technology in researched):
        return None
    prerequisites = tech.get('prerequisites', [])
    if prerequisites == {}:
        prerequisites = []
    if (not isinstance(prerequisites, list) or len(prerequisites) > 256
            or any(not isinstance(name, str) or not name for name in prerequisites)
            or any(name not in researched for name in prerequisites)):
        return None
    trigger = tech.get('trigger')
    if (not isinstance(trigger, dict) or trigger.get('type') != 'craft-item'
            or not isinstance(trigger.get('item'), dict)):
        return None
    item = trigger['item'].get('name')
    count = trigger.get('count')
    if (not isinstance(item, str) or not item or len(item) > 128
            or type(count) is not int or not 1 <= count <= 2**31 - 1):
        return None
    produced = factory.get('produced')
    produced = produced.get(item) if isinstance(produced, dict) else None
    if type(produced) is not int or not 0 <= produced < count:
        return None
    try:
        trigger_recipe = catalog.recipe_for(item)
    except (KeyError, ValueError, TypeError):
        return None
    if (trigger_recipe.get('hidden') or not catalog.enabled(trigger_recipe, researched)
            or trigger_recipe.get('name') not in catalog.recipes
            or not any(product.get('type') == 'item' and product.get('name') == item
                       and type(product.get('amount')) in {int, float}
                       and math.isfinite(product['amount']) and product['amount'] > 0
                       and product.get('probability', 1) == 1
                       for product in trigger_recipe.get('products', []))):
        return None
    return {
        'schema': 1,
        'observed_tick': snapshot.tick,
        'session_id': snapshot.session_id,
        'actor_unit': runtime['actor_unit'],
        'surface_index': runtime['surface_index'],
        'force_index': runtime['force_index'],
        'technology': technology,
        'technology_enabled_and_unresearched': True,
        'technology_prerequisites_satisfied': True,
        'outer_recipe': outer_recipe,
        'outer_recipe_locked_now': True,
        'trigger_type': 'craft-item',
        'trigger_item': item,
        'trigger_count': count,
        'trigger_produced_now': produced,
        'trigger_remaining_now': count - produced,
        'planner_trigger_batch_count': min(20, count - produced),
        'trigger_recipe': trigger_recipe['name'],
        'basis': 'same_tick_native_unlock_and_craft_item_counter',
        'trigger_progress_requires_later_native_observation': True,
    }


def trigger_input_requirement(snapshot, catalog, trigger, resource, path):
    """Validate and size the direct recipe suffix below a technology edge."""
    if (not isinstance(trigger, dict) or trigger.get('schema') != 1
            or trigger.get('observed_tick') != snapshot.tick
            or trigger.get('session_id') != snapshot.session_id
            or not isinstance(resource, str) or not resource
            or not isinstance(path, list) or len(path) != 3
            or path != [trigger.get('outer_recipe'), trigger.get('trigger_item'), resource]):
        return None
    recipe_name, item = trigger.get('trigger_recipe'), trigger.get('trigger_item')
    recipe = catalog.recipes.get(recipe_name)
    if (not isinstance(recipe, dict) or recipe.get('name') != recipe_name
            or not catalog.enabled(recipe, snapshot.researched or [])
            or not any(product.get('type') == 'item' and product.get('name') == item
                       and product.get('probability', 1) == 1
                       and type(product.get('amount')) in {int, float}
                       and math.isfinite(product['amount']) and product['amount'] > 0
                       for product in recipe.get('products', []))):
        return None
    ingredients = [entry for entry in recipe.get('ingredients', [])
                   if entry.get('type') == 'item' and entry.get('name') == resource]
    if not ingredients or any(type(row.get('amount')) not in {int, float}
                              or not math.isfinite(row['amount']) or row['amount'] <= 0
                              for row in ingredients):
        return None
    output = sum(product['amount'] for product in recipe['products']
                 if product.get('type') == 'item' and product.get('name') == item
                 and product.get('probability', 1) == 1)
    ingredient = sum(row['amount'] for row in ingredients)
    batch_count = trigger.get('planner_trigger_batch_count')
    if type(batch_count) is not int or not 1 <= batch_count <= min(20, trigger['trigger_remaining_now']):
        return None
    planned_batches = math.ceil(batch_count / output)
    required = math.ceil(ingredient * planned_batches)
    return {
        'schema': 1,
        'observed_tick': snapshot.tick,
        'outer_recipe': trigger['outer_recipe'],
        'trigger_item': item,
        'trigger_recipe': recipe_name,
        'resource': resource,
        'planned_recipe_input_units': required,
        'trigger_remaining_now': trigger['trigger_remaining_now'],
        'basis': 'current_trigger_count_and_direct_enabled_recipe_ingredient',
        'does_not_establish_trigger_item_output_or_unlock': True,
    }


def current_machine_input_requirement(snapshot, catalog, trigger, resource, path):
    """Recompute the exact remaining input for the current owned trigger recipe.

    The technology counter determines how much new trigger-item production is
    useful. The current owned machine's buffered and in-flight inputs reduce
    what this step must supply; they never count as produced trigger progress.
    """
    requirement = trigger_input_requirement(snapshot, catalog, trigger, resource, path)
    if not isinstance(requirement, dict):
        return None
    role = 'recipe:' + requirement['trigger_recipe']
    machine = snapshot.factory.get('entities', {}).get(role)
    production = snapshot.factory.get('production_sites')
    source = (production.get('sources', {}).get(role)
              if isinstance(production, dict)
              and isinstance(production.get('sources'), dict) else None)
    recipe = catalog.recipes.get(requirement['trigger_recipe'])
    prototype = catalog.machines.get(machine.get('name')) if isinstance(machine, dict) else None
    ingredients = ([row for row in recipe.get('ingredients', [])
                    if row.get('type') == 'item' and row.get('name') == resource]
                   if isinstance(recipe, dict) else [])
    if (len(ingredients) != 1 or not isinstance(machine, dict)
            or not isinstance(prototype, dict)
            or recipe.get('hidden') or not catalog.enabled(recipe, snapshot.researched or [])
            or recipe.get('category') != 'smelting'
            or not prototype.get('categories', {}).get(recipe.get('category'))
            or type(machine.get('unit_number')) is not int or machine['unit_number'] <= 0
            or not isinstance(source, dict) or source.get('state') != 'owned'
            or source.get('source_unit') != machine['unit_number']
            or not isinstance(machine.get('input'), dict)
            or type(machine.get('crafting')) is not bool):
        return None
    observed_recipe = machine.get('recipe')
    if observed_recipe != requirement['trigger_recipe']:
        # Factorio reports an idle furnace's get_recipe() as the empty string.
        # Keep that raw observation intact and qualify it only through the
        # owned recipe role, enabled smelting recipe, idle state, and exact
        # same-RPC native receiver identity/capacity below.
        if (observed_recipe != '' or prototype.get('burner') is not True
                or machine['crafting'] is not False
                or machine.get('name') not in {
                    'stone-furnace', 'steel-furnace', 'electric-furnace'}):
            return None
    from ..production_sites import sources as production_site_sources
    try:
        qualified_sources = production_site_sources(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if qualified_sources.get(role) != source:
        return None
    if prototype.get('burner') is True:
        fuel = machine.get('fuel')
        if not isinstance(fuel, dict) or type(fuel.get('coal')) is not int or fuel['coal'] < 1:
            return None
    ingredient = ingredients[0].get('amount')
    buffered = machine['input'].get(resource, 0)
    if (type(ingredient) not in {int, float} or not math.isfinite(ingredient)
            or ingredient <= 0 or type(buffered) is not int or buffered < 0):
        return None
    product = next((row for row in recipe['products']
                    if row.get('type') == 'item'
                    and row.get('name') == requirement['trigger_item']
                    and row.get('probability', 1) == 1), None)
    if (not isinstance(product, dict) or type(product.get('amount')) not in {int, float}
            or not math.isfinite(product['amount']) or product['amount'] <= 0):
        return None
    requested_units = trigger.get('planner_trigger_batch_count')
    if type(requested_units) is not int or requested_units < 1:
        return None
    batches = math.ceil(requested_units / product['amount'])
    # Match the native planner's per-ingredient stack cap so evidence does not
    # claim a larger single machine fill than its selected batch can require.
    for row in recipe['ingredients']:
        if row.get('type') == 'item':
            amount = row.get('amount')
            if type(amount) not in {int, float} or not math.isfinite(amount) or amount <= 0:
                return None
            stack = catalog.stack_sizes.get(row.get('name'), 200)
            if type(stack) is not int or stack < 1:
                return None
            batches = min(batches, max(1, math.floor(stack / amount)))
    planned_input = math.ceil(ingredient * batches)
    in_flight = ingredient if machine['crafting'] else 0
    needed = max(0, planned_input - buffered - in_flight)
    if needed < 1:
        return None
    # A gather is useful only if this same owned machine can accept the
    # eventual input. For gathering, one carried unit is enough to query the
    # identity-bound sidecar; the observed insertable count must cover the
    # remaining machine shortfall. Dispatch repeats the capacity check before
    # removing any inventory.
    from .decision_support import _receiver_capacity_start_evidence
    receiver_capacity = _receiver_capacity_start_evidence(
        snapshot, catalog, role, resource, 1, machine['unit_number'])
    if (not isinstance(receiver_capacity, dict)
            or receiver_capacity.get('receiver_type') != 'furnace'
            or receiver_capacity.get('insertable_count_now', 0) < needed):
        return None
    return {
        **requirement,
        'source_role': role,
        'source_unit': machine['unit_number'],
        'machine_recipe_observed': observed_recipe,
        'machine_recipe_identity_basis': (
            'exact_owned_recipe_role_and_enabled_smelting_recipe'
            if observed_recipe == '' else 'native_machine_recipe_matches_enabled_recipe'),
        'planned_recipe_batches': batches,
        'planned_recipe_input_units': planned_input,
        'machine_input_now': buffered,
        'machine_input_in_flight': in_flight,
        'machine_input_units_required_now': needed,
        'receiver_capacity': receiver_capacity,
        'basis': 'same_tick_trigger_counter_and_owned_machine_input_shortfall',
    }


def direct_trigger_input(snapshot, catalog, trigger, resource, amount, path):
    """Require an exact current recipe-input quantity below a typed tech edge."""
    evidence = trigger_input_requirement(snapshot, catalog, trigger, resource, path)
    if type(amount) is not int or amount < 1 or not isinstance(evidence, dict):
        return None
    return evidence if amount == evidence['planned_recipe_input_units'] else None
