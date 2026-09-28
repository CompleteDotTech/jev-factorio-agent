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
                site = snapshot.factory.get('fair_resource_targets', {}).get(ingredient, {})
                gather_start = {
                    'resource_in_current_observation': ingredient in snapshot.nearby_resources,
                    'fair_target_identity_observed': (
                        isinstance(site, dict) and isinstance(site.get('name'), str)
                        and bool(site['name'].strip())
                        and (ingredient == 'wood' or site['name'] == ingredient)
                        and type(site.get('surface_index')) is int and site['surface_index'] > 0
                        and _position(site.get('position')) is not None),
                    'resource_inventory_now': snapshot.inventory.get(ingredient, 0),
                    'target_inventory_after_this_step': step.threshold,
                    'travel_is_lower_bound_not_arrival_proof': True,
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
            'craft_start_evidence': craft_start,
            'craft_dependency': craft_dependency,
            'placement_start_evidence': placement_start,
            'placement_dependency': placement_dependency,
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
