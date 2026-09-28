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
from ..production_sites import sources as production_site_sources


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


def _craft_start_evidence(snapshot, catalog, step):
    """Describe a planned handcraft start; never certify future output."""
    parameters = step.parameters or {}
    recipe_name, batches = parameters.get('recipe'), parameters.get('batches')
    recipe = catalog.recipes.get(recipe_name, {})
    if type(batches) is not int or batches < 1 or not recipe:
        return None
    ingredients, products = recipe.get('ingredients', []), recipe.get('products', [])
    if (not ingredients or not products
            or any(entry.get('type') != 'item' or not _finite(entry.get('amount'))
                   or entry['amount'] <= 0 for entry in ingredients + products)
            or any(product.get('probability', 1) != 1 for product in products)):
        return None
    inputs = {}
    for entry in ingredients:
        inputs[entry['name']] = inputs.get(entry['name'], 0) + entry['amount'] * batches
    expected = {}
    for product in products:
        expected[product['name']] = expected.get(product['name'], 0) + product['amount'] * batches
    if inputs != (step.costs or {}) or step.item not in expected:
        return None
    factory = snapshot.factory
    return {
        'observed_tick': snapshot.tick,
        'native_recipe': recipe_name,
        'input_costs_match_native_recipe': True,
        'inputs_in_inventory_now': all(snapshot.inventory.get(item, 0) >= count
                                       for item, count in inputs.items()),
        'recipe_unlocked_and_handcraftable': (
            bool(catalog.hand_categories.get(recipe.get('category')))
            and catalog.enabled(recipe, snapshot.researched or [])),
        'player_connected_and_bound': (factory.get('player_connected') is True
                                       and factory.get('player_bound') is True),
        'crafting_queue_empty': factory.get('crafting_queue') == 0,
        'craft_job_protocol_ready': (type(factory.get('craft_jobs_protocol')) is int
                                     and factory['craft_jobs_protocol'] == 1
                                     if step.action == 'factory_craft_job' else None),
        'expected_products_after_native_verification': expected,
        'native_receipt_required_for_completion': step.action == 'factory_craft_job',
    }


def _placement_start_evidence(snapshot, plan):
    if len(plan.steps) != 1 or plan.steps[0].action != 'factory_place':
        return None
    step = plan.steps[0]
    parameters = step.parameters or {}
    if not str(parameters.get('anchor', '')).startswith('cell-site:'):
        return None
    try:
        site = production_site_sources(snapshot).get(parameters.get('role'), {})
    except (ValueError, KeyError, TypeError):
        return None
    if (site.get('state') != 'proposed' or site.get('reason') != 'joint_layout_available'
            or site.get('anchor') != parameters.get('anchor')
            or parameters.get('name') != 'stone-furnace'
            or step.costs != {'stone-furnace': 1}
            or _position(site.get('position')) is None):
        return None
    return {
        'observed_tick': snapshot.tick,
        'source_role': parameters['role'],
        'site_anchor': parameters['anchor'],
        'site_position': deepcopy(site['position']),
        'site_state': 'proposed',
        'native_offer_checked_current_site_clearance': True,
        'paid_furnace_in_inventory_now': snapshot.inventory.get('stone-furnace', 0) >= 1,
        'no_source_owned_at_role_now': parameters['role'] not in snapshot.factory.get('entities', {}),
        'player_connected_and_bound_now': (
            snapshot.factory.get('player_connected') is True
            and snapshot.factory.get('player_bound') is True),
        'crafting_queue_empty_now': snapshot.factory.get('crafting_queue') == 0,
        'travel_is_lower_bound_not_arrival_proof': True,
        'native_preflight_rechecks_offer_and_actor': True,
        'later_transport_and_output_require_native_verification': True,
    }


def _recipe_input_transfer_start_evidence(snapshot, catalog, plan):
    """Bind a paid recipe input transfer to current native facts, not future output."""
    if len(plan.steps) != 1:
        return None
    step = plan.steps[0]
    provenance = (plan.materials or {}).get('recipe_input_transfer')
    local = (plan.materials or {}).get('local_objective')
    parameters = step.parameters or {}
    if (step.action != 'factory_insert' or step.effect != 'transfer'
            or not isinstance(provenance, dict) or not isinstance(local, dict)):
        return None
    role, item, recipe_name = (provenance.get('source_role'),
                               provenance.get('ingredient'), provenance.get('recipe'))
    if (not isinstance(role, str) or not role.startswith('recipe:')
            or not isinstance(item, str) or not item
            or not isinstance(recipe_name, str) or role != 'recipe:' + recipe_name):
        return None
    factory = snapshot.factory
    machine = factory.get('entities', {}).get(role, {})
    source = (factory.get('production_sites', {}).get('sources', {}).get(role, {}))
    recipe = catalog.recipes.get(recipe_name, {})
    prototype = catalog.machines.get(machine.get('name'), {})
    ingredients = recipe.get('ingredients', [])
    matches = [row for row in ingredients if row.get('type') == 'item'
               and row.get('name') == item] if isinstance(ingredients, list) else []
    path = provenance.get('planner_item_path')
    batches = provenance.get('planned_batches')
    input_bag = machine.get('input')
    buffered = input_bag.get(item, 0) if isinstance(input_bag, dict) else None
    crafting = machine.get('crafting')
    fuel_bag = machine.get('fuel')
    burner_fuel = fuel_bag.get('coal', 0) if isinstance(fuel_bag, dict) else None
    carried = snapshot.inventory.get(item)
    if (len(matches) != 1 or type(batches) is not int or batches < 1
            or not _finite(matches[0].get('amount')) or matches[0]['amount'] <= 0
            or type(buffered) is not int or buffered < 0 or type(crafting) is not bool):
        return None
    required = max(0, math.ceil(matches[0]['amount'] * batches - buffered
                                 - (matches[0]['amount'] if crafting else 0)))
    if (provenance.get('observed_tick') != snapshot.tick
            or type(machine.get('unit_number')) is not int or machine['unit_number'] <= 0
            or provenance.get('source_unit') != machine['unit_number']
            or source.get('state') != 'owned'
            or source.get('source_unit') != machine['unit_number']
            or recipe.get('name') != recipe_name or recipe.get('hidden')
            or not catalog.enabled(recipe, snapshot.researched or [])
            or not bool(prototype.get('categories', {}).get(recipe.get('category')))
            or (prototype.get('burner') is True
                and (not _finite(burner_fuel) or burner_fuel <= 0))
            or (prototype.get('burner') is not True
                and machine.get('recipe') != recipe_name)
            or provenance.get('observed_input') != buffered
            or provenance.get('observed_crafting') is not crafting
            or not isinstance(path, list) or not 2 <= len(path) <= 32
            or any(not isinstance(part, str) or not part for part in path)
            or path[0] != local.get('item') or path[-2:] != [recipe_name, item]
            or type(required) is not int or required < 1
            or parameters.get('role') != role or parameters.get('item') != item
            or parameters.get('quantity') != required
            or parameters.get('receipt') != f'{snapshot.tick}:factory_insert:{role}:{item}'
            or step.costs != {item: required}
            or type(carried) is not int or carried < required
            or factory.get('player_connected') is not True
            or factory.get('player_bound') is not True):
        return None
    return {
        'observed_tick': snapshot.tick,
        'planner_item_path': list(path),
        'direct_native_recipe': recipe_name,
        'owned_source_role': role,
        'owned_source_unit': machine['unit_number'],
        'ingredient': item,
        'ingredient_in_machine_now': buffered,
        'ingredient_in_inventory_now': carried,
        'burner_fuel_coal_now': burner_fuel if prototype.get('burner') is True else None,
        'paid_quantity_to_transfer': required,
        'planned_native_receipt_id': parameters['receipt'],
        'basis': 'current_planner_recipe_input_and_owned_native_machine',
        'native_transfer_and_later_output_require_verification': True,
    }


def _output_pickup_start_evidence(snapshot, catalog, plan):
    """Describe ready output at an owned native source, never a completed pickup."""
    if len(plan.steps) != 1:
        return None
    step = plan.steps[0]
    materials = plan.materials or {}
    provenance = materials.get('output_pickup')
    local = materials.get('local_objective')
    intent = materials.get('work_intent')
    p = step.parameters or {}
    if (step.action != 'factory_extract' or step.effect != 'transfer'
            or step.costs != {} or not isinstance(provenance, dict)
            or not isinstance(local, dict) or not isinstance(intent, dict)
            or intent.get('scope') != 'immediate'
            or intent.get('observed_tick') != snapshot.tick
            or provenance.get('observed_tick') != snapshot.tick):
        return None
    role, item, quantity = p.get('role'), p.get('item'), p.get('quantity')
    if (not isinstance(role, str) or not role.startswith('recipe:')
            or not isinstance(item, str) or not item
            or type(quantity) is not int or not 1 <= quantity <= 200):
        return None
    factory = snapshot.factory
    entities = factory.get('entities')
    sites = factory.get('production_sites')
    if not isinstance(entities, dict) or not isinstance(sites, dict):
        return None
    machine = entities.get(role)
    sources = sites.get('sources')
    source = sources.get(role) if isinstance(sources, dict) else None
    if not isinstance(machine, dict) or not isinstance(source, dict):
        return None
    unit = machine.get('unit_number')
    output = machine.get('output')
    available = output.get(item) if isinstance(output, dict) else None
    recipe_name = role.removeprefix('recipe:')
    recipe = catalog.recipes.get(recipe_name)
    path = provenance.get('planner_item_path')
    if (type(unit) is not int or unit <= 0
            or source.get('state') != 'owned' or source.get('source_unit') != unit
            or not isinstance(recipe, dict) or recipe.get('name') != recipe_name
            or recipe.get('hidden') or not catalog.enabled(recipe, snapshot.researched or [])
            or not any(product.get('type') == 'item' and product.get('name') == item
                       for product in recipe.get('products', []))
            or not isinstance(path, list) or not 1 <= len(path) <= 32
            or any(not isinstance(part, str) or not part for part in path)
            or path[0] != local.get('item') or path[-1] != item
            or provenance.get('source_role') != role
            or provenance.get('source_unit') != unit
            or provenance.get('item') != item
            or type(available) is not int or available < quantity
            or provenance.get('observed_output') != available
            or p.get('receipt') != f'{snapshot.tick}:factory_extract:{role}:{item}'
            or factory.get('player_connected') is not True
            or factory.get('player_bound') is not True):
        return None
    return {
        'observed_tick': snapshot.tick,
        'planner_item_path': list(path),
        'owned_source_role': role,
        'owned_source_unit': unit,
        'ready_output_item': item,
        'ready_output_quantity_now': available,
        'planned_pickup_quantity': quantity,
        'planned_native_receipt_id': p['receipt'],
        'player_connected_and_bound_now': True,
        'basis': 'current_planner_output_and_owned_native_machine',
        'native_pickup_and_inventory_delta_require_verification': True,
    }


def candidate_evidence(snapshot, catalog, plans) -> dict:
    """Describe the admitted frontier without inventing downstream output."""
    entities = snapshot.factory.get('entities', {})
    try:
        schedules = research_schedule(snapshot, catalog)
    except (ValueError, KeyError, TypeError):
        schedules = []  # Unsupported forecast is explicitly unknown below.
    due_packs = {row['item'] for row in schedules if row['due']}
    result = {}
    from .solid_investment import ranking_marker as solid_marker
    from .coal_funding import ranking_marker as coal_marker
    has_solid_offer = any(solid_marker(plan, snapshot) for plan in plans)
    has_coal_offer = any(coal_marker(plan, snapshot) for plan in plans)
    for index, plan in enumerate(plans):
        placement_start = _placement_start_evidence(snapshot, plan)
        recipe_input_transfer_start = _recipe_input_transfer_start_evidence(snapshot, catalog, plan)
        output_pickup_start = _output_pickup_start_evidence(snapshot, catalog, plan)
        origin = _position(snapshot.player_position)
        travel, actor, unknown, reasons = 0.0, 0.0, [], []
        harvest_thresholds = {}
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
            elif step.action == 'factory_place' and placement_start is not None:
                target = _position(placement_start['site_position'])
            elif step.action in {'walk_to_coal', 'mine_coal', 'walk_to_iron', 'mine_iron'}:
                resource = 'coal' if step.action.endswith('coal') else 'iron-ore'
                evidence = snapshot.factory.get('fair_resource_targets', {}).get(resource, {})
                target = _position(evidence.get('position'))
            elif role:
                target = _position(entity.get('position'))
            if passive or step.action in {'factory_craft', 'factory_craft_job',
                                          'factory_research', 'factory_bind'}:
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
            elif step.action in {'mine_coal', 'mine_iron'}:
                # Legacy harvest steps verify an inventory threshold, not an
                # additional amount. Count only the unmet observed threshold.
                prior = max(snapshot.inventory.get(step.item, 0),
                            harvest_thresholds.get(step.item, 0))
                remaining = max(0, step.threshold - prior)
                harvest_thresholds[step.item] = max(prior, step.threshold)
                quantities += remaining
                outputs.add(step.item)
            elif step.action in {'factory_craft', 'factory_craft_job'}:
                recipe = catalog.recipes.get(parameters.get('recipe', ''), {})
                energy, batches = recipe.get('energy'), parameters.get('batches')
                if _finite(energy) and energy > 0 and type(batches) is int and batches > 0:
                    if step.action == 'factory_craft':
                        actor += energy * batches * 60
                    if step.action == 'factory_craft':
                        outputs.update(p['name'] for p in recipe.get('products', [])
                                       if p.get('type') == 'item')
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
        capital = (plan.materials or {}).get('capital_investment')
        if isinstance(capital, dict) and capital.get('observed_tick') == snapshot.tick:
            from .capital import validate_spec, STAGES
            try:
                validate_spec(capital.get('spec'), catalog, snapshot.researched or [])
                if capital.get('stage') in STAGES and plan.id.startswith(capital['spec']['key'] + ':'):
                    urgency = max(urgency, 1)  # Below due supply and emergency maintenance.
                    reasons.append('justified_capital_stage:' + capital['stage'])
            except (ValueError, KeyError, TypeError):
                pass  # Invalid or stale annotations cannot buy priority.
        if has_solid_offer:
            if outputs & due_packs:
                urgency = max(urgency, 2)
                reasons.append('ready_science_before_solid_investment')
            if solid_marker(plan, snapshot):
                urgency = max(urgency, 1)
                reasons.append('justified_downstream_investment')
        if has_coal_offer:
            if outputs & due_packs:
                urgency = max(urgency, 2)
                reasons.append('ready_science_before_coal_kit')
            if coal_marker(plan, snapshot):
                urgency = max(urgency, 1)
                reasons.append('explicit_coal_kit_investment')
        target = (plan.materials or {}).get('local_objective')
        if target is not None:
            target = deepcopy(target)
        intent = (plan.materials or {}).get('work_intent', {})
        scope = (intent.get('scope') if isinstance(intent, dict)
                 and intent.get('observed_tick') == snapshot.tick else None)
        scope = scope if scope in {'immediate', 'lookahead'} else 'unclassified'
        prerequisite = (plan.materials or {}).get('raw_prerequisite')
        prerequisite_evidence = None
        gather_start = None
        if len(plan.steps) == 1 and plan.steps[0].action == 'factory_gather':
            step = plan.steps[0]
            resource = (step.parameters or {}).get('resource')
            site = snapshot.factory.get('fair_resource_targets', {}).get(resource, {})
            gather_start = {
                'resource_in_current_observation': resource in snapshot.nearby_resources,
                'fair_target_identity_observed': (
                    isinstance(site, dict) and isinstance(site.get('name'), str)
                    and bool(site['name'].strip())
                    and (resource == 'wood' or site['name'] == resource)
                    and type(site.get('surface_index')) is int and site['surface_index'] > 0
                    and _position(site.get('position')) is not None),
                'resource_inventory_now': snapshot.inventory.get(resource, 0),
                'target_inventory_after_this_step': step.threshold,
                'travel_is_lower_bound_not_arrival_proof': True,
            }
        intent = (plan.materials or {}).get('work_intent')
        craft_start = (_craft_start_evidence(snapshot, catalog, plan.steps[0])
                       if len(plan.steps) == 1 and plan.steps[0].action in {
                           'factory_craft', 'factory_craft_job'}
                       and (intent is None or (isinstance(intent, dict)
                            and intent.get('observed_tick') == snapshot.tick))
                       else None)
        craft_dependency = None
        provenance = (plan.materials or {}).get('craft_dependency')
        if (craft_start is not None
                and craft_start['recipe_unlocked_and_handcraftable'] is True
                and isinstance(provenance, dict)):
            path = provenance.get('planner_item_path')
            local = (plan.materials or {}).get('local_objective')
            target = local.get('item') if isinstance(local, dict) else None
            step = plan.steps[0]
            if (provenance.get('observed_tick') == snapshot.tick
                    and provenance.get('recipe') == craft_start['native_recipe']
                    and provenance.get('product') == step.item
                    and isinstance(path, list) and 1 <= len(path) <= 32
                    and all(isinstance(item, str) and item for item in path)
                    and path[-1] == step.item
                    and isinstance(target, str) and bool(target)
                    and path[0] == target):
                craft_dependency = {
                    'observed_tick': snapshot.tick,
                    'planner_item_path': list(path),
                    'current_craft_product': step.item,
                    'basis': 'current_recursive_planner_provenance_and_native_recipe',
                    'later_steps_require_fresh_native_preconditions': True,
                }
        placement_dependency = None
        provenance = (plan.materials or {}).get('placement_dependency')
        local = (plan.materials or {}).get('local_objective')
        target_item = local.get('item') if isinstance(local, dict) else None
        if (placement_start is not None
                and placement_start['paid_furnace_in_inventory_now'] is True
                and placement_start['no_source_owned_at_role_now'] is True
                and placement_start['player_connected_and_bound_now'] is True
                and placement_start['crafting_queue_empty_now'] is True
                and isinstance(provenance, dict)):
            path = provenance.get('planner_item_path')
            role = placement_start['source_role']
            product = role.removeprefix('recipe:')
            recipe = catalog.recipes.get(product, {})
            furnace = catalog.machines.get('stone-furnace', {})
            if (provenance.get('observed_tick') == snapshot.tick
                    and provenance.get('machine') == 'stone-furnace'
                    and provenance.get('source_role') == role
                    and provenance.get('site_anchor') == placement_start['site_anchor']
                    and isinstance(target_item, str) and bool(target_item)
                    and isinstance(path, list) and 1 <= len(path) <= 32
                    and all(isinstance(item, str) and item for item in path)
                    and path[0] == target_item and path[-1] == product
                    and recipe.get('name') == product and not recipe.get('hidden')
                    and catalog.enabled(recipe, snapshot.researched or [])
                    and bool(furnace.get('categories', {}).get(recipe.get('category')))
                    and any(row.get('type') == 'item' and row.get('name') == product
                            and row.get('amount', 0) > 0
                            for row in recipe.get('products', []))):
                placement_dependency = {
                    'observed_tick': snapshot.tick,
                    'planner_item_path': list(path),
                    'machine_for_recipe': role,
                    'basis': 'current_recursive_planner_and_validated_native_site',
                    'later_flow_and_output_require_fresh_native_preconditions': True,
                }
        if (isinstance(prerequisite, dict) and prerequisite.get('observed_tick') == snapshot.tick
                and len(plan.steps) == 1 and plan.steps[0].action == 'factory_gather'):
            step = plan.steps[0]
            ingredient = (step.parameters or {}).get('resource')
            parent = prerequisite.get('direct_product')
            recipe_name = prerequisite.get('recipe')
            path = prerequisite.get('planner_item_path')
            recipe = catalog.recipes.get(recipe_name, {})
            if (ingredient == prerequisite.get('ingredient')
                    and isinstance(parent, str) and parent
                    and isinstance(path, list) and 2 <= len(path) <= 32
                    and path[-2:] == [parent, ingredient]
                    and any(product.get('type') == 'item' and product.get('name') == parent
                            and product.get('amount', 0) > 0 for product in recipe.get('products', []))
                    and any(entry.get('type') == 'item' and entry.get('name') == ingredient
                            and entry.get('amount', 0) > 0 for entry in recipe.get('ingredients', []))):
                prerequisite_evidence = {
                    'observed_tick': snapshot.tick,
                    'direct_recipe': recipe_name,
                    'direct_product': parent,
                    'planner_item_path': list(path),
                    'basis': 'current_planner_dependency_and_native_catalog_recipe',
                    'later_steps_require_fresh_native_preconditions': True,
                }
        fuel_prerequisite = None
        fuel_transfer_start = None
        fuel = (plan.materials or {}).get('fuel_prerequisite')
        local = (plan.materials or {}).get('local_objective')
        local_item = local.get('item') if isinstance(local, dict) else None
        if isinstance(fuel, dict) and len(plan.steps) == 1:
            step = plan.steps[0]
            parameters = step.parameters or {}
            role = fuel.get('source_role')
            machine = entities.get(role, {}) if isinstance(role, str) else {}
            fuel_bag = machine.get('fuel')
            current = fuel_bag.get('coal', 0) if isinstance(fuel_bag, dict) else None
            recipe_name = role.removeprefix('recipe:') if isinstance(role, str) else ''
            recipe = catalog.recipes.get(recipe_name, {})
            prototype = catalog.machines.get(machine.get('name'), {})
            path = fuel.get('planner_item_path')
            carried = snapshot.inventory.get('coal')
            startup = current == 0 and machine.get('products_finished', 0) == 0
            expected_target = min(5 if startup else 50, catalog.stack_sizes.get('coal', 50))
            required = expected_target - current if _finite(current) else None
            if (step.action == 'factory_insert' and step.effect == 'transfer'
                    and parameters.get('item') == 'coal' and parameters.get('role') == role
                    and type(required) is int and required > 0
                    and parameters.get('quantity') == required
                    and parameters.get('receipt') == f'{snapshot.tick}:factory_insert:{role}:coal'
                    and step.costs == {'coal': required}
                    and type(carried) is int and carried >= required
                    and fuel.get('observed_tick') == snapshot.tick
                    and isinstance(role, str) and role.startswith('recipe:')
                    and type(machine.get('unit_number')) is int
                    and machine['unit_number'] == fuel.get('source_unit')
                    and current == fuel.get('observed_fuel')
                    and fuel.get('target_fuel') == expected_target
                    and fuel.get('startup') is startup
                    and isinstance(fuel_bag, dict)
                    and prototype.get('burner') is True
                    and recipe.get('name') == recipe_name and not recipe.get('hidden')
                    and catalog.enabled(recipe, snapshot.researched or [])
                    and bool(prototype.get('categories', {}).get(recipe.get('category')))
                    and isinstance(local_item, str) and bool(local_item)
                    and isinstance(path, list) and 1 <= len(path) <= 32
                    and all(isinstance(item, str) and item for item in path)
                    and path[0] == local_item and path[-1] == recipe_name
                    and snapshot.factory.get('player_connected') is True
                    and snapshot.factory.get('player_bound') is True):
                fuel_transfer_start = {
                    'observed_tick': snapshot.tick,
                    'planner_item_path': list(path),
                    'burner_role': role,
                    'burner_unit': machine['unit_number'],
                    'fuel_now': current,
                    'coal_in_inventory_now': carried,
                    'coal_to_transfer': required,
                    'native_receipt': parameters['receipt'],
                    'basis': 'current_planner_need_owned_burner_and_paid_inventory',
                    'native_transfer_and_later_output_require_verification': True,
                }
        if (isinstance(fuel, dict) and len(plan.steps) == 1
                and plan.steps[0].action == 'factory_gather'
                and (plan.steps[0].parameters or {}).get('resource') == 'coal'
                and gather_start is not None
                and gather_start['resource_in_current_observation'] is True
                and gather_start['fair_target_identity_observed'] is True):
            role = fuel.get('source_role')
            machine = entities.get(role, {})
            recipe_name = role.removeprefix('recipe:') if isinstance(role, str) else ''
            recipe = catalog.recipes.get(recipe_name, {})
            prototype = catalog.machines.get(machine.get('name'), {})
            path = fuel.get('planner_item_path')
            fuel_bag = machine.get('fuel')
            current = fuel_bag.get('coal', 0) if isinstance(fuel_bag, dict) else None
            startup = current == 0 and machine.get('products_finished', 0) == 0
            expected_target = min(5 if startup else 50, catalog.stack_sizes.get('coal', 50))
            carried = snapshot.inventory.get('coal', 0)
            gather_quantity = (min(50, max(0, math.ceil(expected_target - current - carried)))
                               if _finite(current) and type(carried) is int else 0)
            if (fuel.get('observed_tick') == snapshot.tick
                    and isinstance(role, str) and role.startswith('recipe:')
                    and type(machine.get('unit_number')) is int
                    and machine['unit_number'] == fuel.get('source_unit')
                    and _finite(current) and current == fuel.get('observed_fuel')
                    and type(expected_target) is int and expected_target >= 1
                    and fuel.get('target_fuel') == expected_target
                    and fuel.get('startup') is startup
                    and current < min(5, expected_target)
                    and type(carried) is int and carried >= 0 and gather_quantity > 0
                    and (plan.steps[0].parameters or {}).get('quantity') == gather_quantity
                    and plan.steps[0].threshold == carried + gather_quantity
                    and prototype.get('burner') is True
                    and recipe.get('name') == recipe_name and not recipe.get('hidden')
                    and catalog.enabled(recipe, snapshot.researched or [])
                    and bool(prototype.get('categories', {}).get(recipe.get('category')))
                    and isinstance(local_item, str) and bool(local_item)
                    and isinstance(path, list) and 1 <= len(path) <= 32
                    and all(isinstance(item, str) and item for item in path)
                    and path[0] == local_item and path[-1] == recipe_name):
                fuel_prerequisite = {
                    'observed_tick': snapshot.tick,
                    'planner_item_path': list(path),
                    'burner_role': role,
                    'burner_unit': machine['unit_number'],
                    'fuel_now': current,
                    'startup_target': expected_target if startup else None,
                    'current_required_units': min(5, expected_target) - current,
                    'basis': 'current_planner_fuel_need_and_owned_native_burner',
                    'later_fuel_transfer_and_output_require_fresh_native_preconditions': True,
                }
        if plan.goal == 'stockpile_fuel' and all(
                step.action in {'walk_to_coal', 'mine_coal'} for step in plan.steps):
            highest = max((step.threshold for step in plan.steps
                           if step.action == 'mine_coal'), default=0)
            scope = 'immediate' if highest <= 5 else 'lookahead'
        passive = all(s.action in {'factory_wait', 'idle'} for s in plan.steps)
        result[plan.id] = {
            'work_scope': scope,
            'processed_units_basis': 'handling_volume_not_useful_production',
            'compiler_order': index, 'passive': passive, 'urgency': urgency,
            'reasons': sorted(set(reasons)), 'local_target': target,
            'travel_tiles_lower_bound': None if any(x.startswith('travel:') for x in unknown) else round(travel, 3),
            'actor_ticks_estimate': None if unknown else math.ceil(actor),
            'processed_units': quantities, 'material_costs': costs,
            'delivers_or_crafts': sorted(outputs), 'unknowns': sorted(set(unknown)),
            'raw_prerequisite': prerequisite_evidence,
            'gather_start_evidence': gather_start,
            'fuel_prerequisite': fuel_prerequisite,
            'fuel_transfer_start_evidence': fuel_transfer_start,
            'craft_start_evidence': craft_start,
            'craft_dependency': craft_dependency,
            'placement_start_evidence': placement_start,
            'placement_dependency': placement_dependency,
            'recipe_input_transfer_start_evidence': recipe_input_transfer_start,
            'output_pickup_start_evidence': output_pickup_start,
            'research_deadline_tick': min((row['deadline_tick'] for row in schedules
                if row['item'] in outputs and row['deadline_tick'] is not None), default=None),
            'requires_investment': any(s.action in {'factory_place', 'factory_connect',
                                      'factory_buffer_build', 'factory_input_build', 'factory_solid_build'} for s in plan.steps),
            'estimate_basis': 'native_observation_and_catalog_with_declared_policy_heuristics',
        }
        if scope == 'lookahead' and plan.goal == 'stockpile_fuel':
            result[plan.id]['current_prerequisite_units'] = max(
                0, min(quantities, 5 - snapshot.inventory.get('coal', 0)))
    # A nearer bulk pickup of the same currently needed material is not
    # discretionary stockpiling. Compare its cost using only the current need,
    # not all extra handled units. This is evidence/ranking, never permission.
    def acquired_item(plan):
        if len(plan.steps) != 1 or plan.steps[0].action not in {'factory_extract', 'factory_gather'}:
            return None
        step = plan.steps[0]
        parameters = step.parameters or {}
        item = parameters.get('item', parameters.get('resource', step.item))
        return item if isinstance(item, str) and item else None

    required = {}
    for plan in plans:
        item, row = acquired_item(plan), result[plan.id]
        if item and row['work_scope'] == 'immediate' and row['processed_units'] > 0:
            required[item] = max(required.get(item, 0), row['processed_units'])
    for plan in plans:
        item, row = acquired_item(plan), result[plan.id]
        if item in required and row['work_scope'] == 'lookahead' and row['processed_units'] > 0:
            row['work_scope'] = 'shared_prerequisite'
            row['current_prerequisite_units'] = min(required[item], row['processed_units'])
            row['reasons'].append('same_item_current_prerequisite')
    return result


def ranking_key(row: dict) -> tuple:
    """Urgency and productive work precede known actor cost; ties stay stable.

    Unknown cost is never zero-cost work. Among equally urgent options, a
    current prerequisite precedes discretionary lookahead. Handling volume is
    only a tie-breaker within a demand class, not proof of useful production.
    This is a scheduling heuristic, not a success probability or calibrated value.
    """
    duration = row['actor_ticks_estimate']
    amount = max(1, row.get('current_prerequisite_units', row['processed_units']))
    return (row['passive'], -row['urgency'], row.get('work_scope') == 'lookahead', duration is None,
            (duration / amount) if duration is not None else 0,
            row['compiler_order'])


def scheduling_context(snapshot, catalog, plans, goal: str) -> dict:
    evidence = candidate_evidence(snapshot, catalog, plans)
    primary = (plans[0].materials or {}).get('local_objective') if plans else None
    if primary is None and goal == 'stockpile_fuel':
        primary = {'item': 'coal', 'inventory_target': 5, 'ultimate_goal': goal}
    instruction = ('Gather the observed five-coal construction buffer; a bounded '
                   'coal harvest advances this prerequisite.' if goal == 'stockpile_fuel' else
                   'Prevent observed starvation, remove the next production blocker, '
                   'or do useful independent work while production runs. '
                   'A single useful action need not complete the ultimate goal. '
                   'Immediate prerequisites precede discretionary lookahead at equal urgency; '
                   'moving more items is not evidence of more useful production.')
    return {
        'local_objective': {
            'kind': 'stockpile_fuel' if goal == 'stockpile_fuel' else 'ready_production',
            'ultimate_goal': goal,
            'primary_target': deepcopy(primary),
            'instruction': instruction,
            'success_authority': 'unchanged native step and goal predicates, never model scores',
        },
        'candidate_evidence': evidence,
        'deterministic_ranking': sorted(evidence, key=lambda key: ranking_key(evidence[key])),
        'selection_contract': {'schema': 1, 'observed_tick': snapshot.tick,
                               'heuristics_are_not_native_timing_measurements': True},
    }
