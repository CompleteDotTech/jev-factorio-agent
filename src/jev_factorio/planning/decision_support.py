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
from ..input_routes import sources as input_route_sources
from ..mining_outposts import (PARTS as OUTPOST_PARTS,
                               RESOURCES as OUTPOST_RESOURCES,
                               current as outpost_current,
                               remaining_kit as outpost_remaining_kit,
                               sources as outpost_sources)


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


def _local_target_completion_evidence(snapshot, catalog, plan, craft_start,
                                      craft_dependency):
    """Assess whether a receipt-conditional direct craft covers the local target.

    This is a forecast from the current native recipe and complete inventory
    observation. It never reports the craft as completed; the native receipt
    remains the only completion authority.
    """
    if (snapshot.world_kind != 'fle' or len(plan.steps) != 1
            or not isinstance(craft_start, dict)
            or not isinstance(craft_dependency, dict)):
        return None
    materials = plan.materials or {}
    local = materials.get('local_objective')
    intent = materials.get('work_intent')
    if not isinstance(local, dict) or not isinstance(intent, dict):
        return None
    item, target = local.get('item'), local.get('inventory_target')
    step = plan.steps[0]
    parameters = step.parameters or {}
    if (not isinstance(item, str) or not item or type(target) is not int or target < 1
            or step.action != 'factory_craft_job' or step.effect != 'craft_job_complete'
            or step.item != item or type(parameters.get('batches')) is not int
            or not 1 <= parameters['batches'] <= 200
            or not isinstance(parameters.get('recipe'), str)
            or not parameters['recipe']
            or not isinstance(parameters.get('receipt'), str)
            or not parameters['receipt']
            or intent.get('scope') != 'immediate'
            or intent.get('observed_tick') != snapshot.tick
            or craft_start.get('observed_tick') != snapshot.tick
            or craft_start.get('native_recipe') != parameters['recipe']
            or craft_start.get('native_receipt_required_for_completion') is not True
            or craft_dependency.get('observed_tick') != snapshot.tick
            or craft_dependency.get('current_craft_product') != item
            or craft_dependency.get('planner_item_path') != [item]
            or craft_dependency.get('basis') !=
                'current_recursive_planner_provenance_and_native_recipe'):
        return None
    if any(craft_start.get(key) is not True for key in (
            'input_costs_match_native_recipe', 'inputs_in_inventory_now',
            'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
            'crafting_queue_empty', 'craft_job_protocol_ready')):
        return None
    try:
        if not step.allowed(snapshot):
            return None
    except (KeyError, TypeError, ValueError):
        return None

    session, tick = snapshot.session_id, snapshot.tick
    identity = (session, tick)
    if (not isinstance(session, str) or not session or type(tick) is not int
            or getattr(snapshot, '_coherent_observation_verified', None) != identity
            or getattr(snapshot, '_atomic_inventory_verified', None) != identity):
        return None
    inventory = snapshot.inventory
    if (not isinstance(inventory, dict) or len(inventory) > 4096
            or any(not isinstance(name, str) or not name or len(name) > 128
                   or type(amount) is not int or amount < 0
                   for name, amount in inventory.items())):
        return None
    current = inventory.get(item, 0)

    # Recompute the target item's output from the version-bound native catalog;
    # do not trust planner annotations or fractional/bool quantities.
    recipe = catalog.recipes.get(parameters['recipe'])
    outputs = craft_start.get('expected_products_after_native_verification')
    stack_size = catalog.stack_sizes.get(item)
    output_value = outputs.get(item) if isinstance(outputs, dict) else None
    if (not isinstance(recipe, dict) or recipe.get('name') != parameters['recipe']
            or recipe.get('hidden') or not catalog.enabled(recipe, snapshot.researched or [])
            or not isinstance(recipe.get('products'), list)
            or len(recipe['products']) != 1
            or not isinstance(outputs, dict) or not outputs
            or any(not isinstance(name, str) or not name
                   or type(amount) not in {int, float} or not _finite(amount)
                   or amount < 1 or not float(amount).is_integer()
                   for name, amount in outputs.items())
            or type(stack_size) is not int or stack_size < 1
            or type(output_value) not in {int, float} or not _finite(output_value)
            or output_value < 1 or not float(output_value).is_integer()):
        return None
    output = int(output_value)
    native_outputs = {}
    for product in recipe.get('products', []):
        probability = product.get('probability', 1) if isinstance(product, dict) else None
        if (not isinstance(product, dict) or product.get('type') != 'item'
                or type(probability) not in {int, float} or probability != 1
                or type(product.get('amount')) not in {int, float}
                or not _finite(product['amount']) or product['amount'] < 1
                or not float(product['amount']).is_integer()):
            return None
        name = product.get('name')
        if not isinstance(name, str) or not name:
            return None
        native_outputs[name] = (native_outputs.get(name, 0)
                                + int(product['amount']) * parameters['batches'])
    normalized_outputs = {name: int(amount) for name, amount in outputs.items()}
    if native_outputs != normalized_outputs or native_outputs.get(item) != output:
        return None

    shortfall = max(0, target - current)
    return {
        'observed_tick': tick,
        'session_id': session,
        'target_item': item,
        'target_inventory': target,
        'inventory_now': current,
        'shortfall_now': shortfall,
        'expected_output_after_native_receipt': output,
        'shortfall_after_expected_output': max(0, shortfall - output),
        'would_close_current_shortfall_if_native_receipt_verifies': (
            shortfall > 0 and output >= shortfall),
        'native_recipe': parameters['recipe'],
        'native_batches': parameters['batches'],
        'target_item_stack_size': stack_size,
        'inventory_basis': 'coherent_snapshot_and_atomic_craft_inventory',
        'native_receipt_required_for_completion': True,
        'forecast_is_not_completed_output': True,
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


def _utility_lab_research_dependency(snapshot, catalog, plan):
    """Qualify a paid lab as the current capability-research prerequisite.

    This is deliberately not placement-site evidence. The ordinary native
    placement action searches for a site and rechecks manual build conditions
    at dispatch; until then, collision clearance, travel, and arrival remain
    unknown.
    """
    if (plan.goal != 'rocket_launch' or len(plan.steps) != 1
            or plan.steps[0].action != 'factory_place'):
        return None
    step = plan.steps[0]
    parameters = step.parameters or {}
    materials = plan.materials or {}
    dependency = materials.get('utility_lab_research_dependency')
    local = materials.get('local_objective')
    intent = materials.get('work_intent')
    economics = materials.get('economics')
    technology = dependency.get('technology') if isinstance(dependency, dict) else None
    if (parameters != {'role': 'utility:lab', 'name': 'lab', 'anchor': 'factory'}
            or step.costs != {'lab': 1}
            or not isinstance(dependency, dict) or not isinstance(local, dict)
            or not isinstance(intent, dict) or not isinstance(economics, dict)
            or dependency.get('observed_tick') != snapshot.tick
            or dependency.get('objective') != 'unlock_basic_assembly'
            or dependency.get('required_role') != 'utility:lab'
            or dependency.get('basis') !=
                'current_capability_technology_and_native_research_planner'
            or dependency.get('power_and_research_are_not_established') is not True
            or economics.get('observed_tick') != snapshot.tick
            or economics.get('objective') != 'unlock_basic_assembly'
            or economics.get('technology') != technology
            or local.get('kind') != 'research_prerequisite'
            or local.get('ultimate_goal') != plan.goal
            or local.get('immediate_prerequisite') != 'utility:lab'
            or local.get('observed_tick') != snapshot.tick
            or local.get('basis') != 'current_capability_research_plan'
            or local.get('later_power_and_research_need_native_verification') is not True
            or local.get('primary_target') != {
                'kind': 'native_technology', 'technology': technology}
            or intent.get('observed_tick') != snapshot.tick
            or intent.get('scope') != 'immediate'
            or intent.get('basis') != 'current_selected_capability_research_prerequisite'
            or not isinstance(technology, str) or not technology
            or type(snapshot.factory.get('player_connected')) is not bool
            or snapshot.factory.get('player_connected') is not True
            or snapshot.factory.get('player_bound') is not True
            or snapshot.factory.get('crafting_queue') != 0
            or snapshot.factory.get('research') not in ('', None)
            or 'utility:lab' in snapshot.factory.get('entities', {})
            or type(snapshot.inventory.get('lab')) is not int
            or snapshot.inventory['lab'] < 1):
        return None
    from .economics import capability_technology
    if capability_technology(catalog, snapshot.researched or []) != technology:
        return None
    tech = catalog.technologies.get(technology, {})
    assembler = catalog.recipes.get('assembling-machine-1', {})
    if (assembler.get('name') != 'assembling-machine-1'
            or not tech.get('enabled') or tech.get('trigger')
            or any(parent not in (snapshot.researched or [])
                   for parent in tech.get('prerequisites', []))
            or catalog.enabled(assembler, snapshot.researched or [])
            or technology not in catalog.unlocks('assembling-machine-1')):
        return None
    return {
        'observed_tick': snapshot.tick,
        'technology': technology,
        'technology_not_researched_now': technology not in (snapshot.researched or []),
        'technology_unlocks_basic_assembler': True,
        'current_research_idle': True,
        'current_technology_prerequisites_satisfied': True,
        'lab_required_by_native_research_walk': True,
        'utility_lab_absent_now': True,
        'paid_lab_in_inventory_now': snapshot.inventory['lab'],
        'player_connected_and_bound_now': True,
        'crafting_queue_empty_now': True,
        'native_placement_site_preflight_performed': False,
        'placement_site_clearance_unknown_until_dispatch': True,
        'travel_and_arrival_unverified': True,
        'existing_native_action_performs_bounded_search_and_fresh_build_checks': True,
        'native_build_result_and_fresh_role_postcondition_required': True,
        'lab_power_and_research_require_later_native_verification': True,
        'basis': 'same_tick_capability_research_plan_and_paid_lab_prerequisite',
    }


def _receiver_capacity_start_evidence(snapshot, catalog, role, item, quantity, source_unit):
    """Require an identity- and item-bound native receiver read from this RPC."""
    from ..backends.native_input_capacity import source_sha256

    factory = snapshot.factory
    value = getattr(snapshot, '_receiver_input_capacity', None)
    runtime = factory.get('acceptance_runtime')
    entity = factory.get('entities', {}).get(role)
    entity_name = entity.get('name') if isinstance(entity, dict) else None
    receiver = (value.get('receivers', {}).get(role)
                if isinstance(value, dict) and isinstance(value.get('receivers'), dict)
                else None)
    sample = (receiver.get('items', {}).get(item)
              if isinstance(receiver, dict) and isinstance(receiver.get('items'), dict)
              else None)
    actor_count = snapshot.inventory.get(item)
    if (snapshot.world_kind != 'fle'
            or getattr(snapshot, '_coherent_observation_verified', None)
                != (snapshot.session_id, snapshot.tick)
            or factory.get('observation_snapshot_schema') != 2
            or not isinstance(runtime, dict)
            or not isinstance(value, dict) or value.get('schema') != 1
            or type(value.get('schema')) is not int or value.get('complete') is not True
            or value.get('basis') != 'same_rpc_owned_campaign_receiver_capacity'
            or value.get('method') != 'get_insertable_count'
            or value.get('query_source_sha256') != source_sha256()
            or value.get('tick') != snapshot.tick
            or value.get('session_id') != snapshot.session_id
            or value.get('actor_unit') != runtime.get('actor_unit')
            or value.get('surface_index') != runtime.get('surface_index')
            or value.get('force_index') != runtime.get('force_index')
            or not isinstance(receiver, dict)
            or receiver.get('surface_index') != runtime.get('surface_index')
            or receiver.get('force_index') != runtime.get('force_index')
            or receiver.get('unit_number') != source_unit
            or type(receiver.get('unit_number')) is not int
            or receiver.get('name') != entity_name
            or receiver.get('type') not in {'furnace', 'assembling-machine', 'rocket-silo'}
            or type(receiver.get('burner')) is not bool
            or type(actor_count) is not int
            or type(quantity) is not int or quantity < 1
            or actor_count < quantity
            or type(sample) is not int
            or sample < quantity):
        return None
    machine = catalog.machines.get(receiver['name'], {})
    expected_type = ({'stone-furnace': 'furnace', 'steel-furnace': 'furnace',
                      'electric-furnace': 'furnace',
                      'assembling-machine-1': 'assembling-machine',
                      'assembling-machine-2': 'assembling-machine',
                      'assembling-machine-3': 'assembling-machine',
                      'rocket-silo': 'rocket-silo'}.get(receiver['name']))
    expected_inventory = ('fuel' if item == 'coal' and receiver['burner']
                          else 'furnace_source' if receiver['type'] == 'furnace'
                          else 'assembling_machine_input')
    source = (factory.get('production_sites', {}).get('sources', {}).get(role)
              if isinstance(factory.get('production_sites'), dict) else None)
    try:
        owned_sources = production_site_sources(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if (receiver.get('type') != expected_type
            or receiver.get('burner') is not (machine.get('burner') is True)
            or not isinstance(source, dict) or source.get('state') != 'owned'
            or source.get('source_unit') != source_unit
            or owned_sources.get(role) != source):
        return None
    return {
        'observed_tick': snapshot.tick, 'session_id': snapshot.session_id,
        'actor_unit': value['actor_unit'], 'surface_index': value['surface_index'],
        'force_index': value['force_index'], 'source_role': role,
        'source_unit': source_unit, 'receiver_name': receiver['name'],
        'receiver_type': receiver['type'], 'inventory': expected_inventory,
        'item': item, 'actor_count_now': actor_count,
        'insertable_count_now': sample,
        'method': value['method'],
        'query_source_sha256': value['query_source_sha256'],
        'basis': 'same_rpc_owned_campaign_receiver_capacity',
        'native_dispatch_rechecks_insertable_count_before_removal': True,
    }


def _recipe_input_transfer_start_evidence(snapshot, catalog, plan, *, path_root=None,
                                          require_receiver_capacity=False):
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
    expected_path_root = local.get('item') if path_root is None else path_root
    if (not isinstance(expected_path_root, str) or not expected_path_root
            or provenance.get('observed_tick') != snapshot.tick
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
            or path[0] != expected_path_root or path[-2:] != [recipe_name, item]
            or type(required) is not int or required < 1
            or parameters.get('role') != role or parameters.get('item') != item
            or parameters.get('quantity') != required
            or parameters.get('receipt') != f'{snapshot.tick}:factory_insert:{role}:{item}'
            or step.costs != {item: required}
            or type(carried) is not int or carried < required
            or factory.get('player_connected') is not True
            or factory.get('player_bound') is not True):
        return None
    evidence = {
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
    if require_receiver_capacity:
        receiver_capacity = _receiver_capacity_start_evidence(
            snapshot, catalog, role, item, required, machine['unit_number'])
        if receiver_capacity is None:
            return None
        evidence['receiver_capacity'] = receiver_capacity
    return evidence


def _output_pickup_start_evidence(snapshot, catalog, plan, *, path_root=None):
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
    expected_path_root = local.get('item') if path_root is None else path_root
    if (not isinstance(expected_path_root, str) or not expected_path_root
            or type(unit) is not int or unit <= 0
            or source.get('state') != 'owned' or source.get('source_unit') != unit
            or not isinstance(recipe, dict) or recipe.get('name') != recipe_name
            or recipe.get('hidden') or not catalog.enabled(recipe, snapshot.researched or [])
            or not any(product.get('type') == 'item' and product.get('name') == item
                       for product in recipe.get('products', []))
            or not isinstance(path, list) or not 1 <= len(path) <= 32
            or any(not isinstance(part, str) or not part for part in path)
            or path[0] != expected_path_root or path[-1] != item
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


def _current_item_dependency_path(snapshot, catalog, path, root, tail):
    """Validate a same-tick catalog path without conflating parent and child roots."""
    if (not isinstance(path, list) or not 1 <= len(path) <= 32
            or any(not isinstance(item, str) or not item for item in path)
            or path[0] != root or path[-1] != tail or len(set(path)) != len(path)):
        return False
    for product, ingredient in zip(path, path[1:]):
        try:
            recipe = catalog.recipe_for(product)
        except (KeyError, ValueError, TypeError):
            return False
        if (not isinstance(recipe, dict) or recipe.get('hidden')
                or not catalog.enabled(recipe, snapshot.researched or [])
                or not any(isinstance(row, dict) and row.get('type') == 'item'
                           and row.get('name') == product
                           and _finite(row.get('amount')) and row['amount'] > 0
                           and row.get('probability', 1) == 1
                           for row in recipe.get('products', []))
                or not any(isinstance(row, dict) and row.get('type') == 'item'
                           and row.get('name') == ingredient
                           and _finite(row.get('amount')) and row['amount'] > 0
                           for row in recipe.get('ingredients', []))):
            return False
    return True


def _current_native_fair_resource_target(snapshot, item):
    """Reuse the atomic observer's decoded, same-session resource identity."""
    if (snapshot.world_kind != 'fle' or not isinstance(item, str)
            or item not in {'wood', 'coal', 'iron-ore', 'copper-ore', 'stone'}):
        return None
    session, tick = snapshot.session_id, snapshot.tick
    identity = (session, tick)
    factory = snapshot.factory
    runtime = factory.get('acceptance_runtime')
    if (not isinstance(session, str) or not session or type(tick) is not int
            or getattr(snapshot, '_coherent_observation_verified', None) != identity
            or factory.get('observation_snapshot_schema') != 2
            or factory.get('tick') != tick
            or factory.get('player_connected') is not True
            or factory.get('player_bound') is not True
            or not isinstance(runtime, dict) or runtime.get('schema') != 1
            or runtime.get('session_id') != session
            or runtime.get('speed') != 1 or runtime.get('tick_paused') is not False
            or any(type(runtime.get(key)) is not int or runtime[key] <= 0
                   for key in ('actor_unit', 'player_index', 'surface_index', 'force_index'))):
        return None
    targets = factory.get('fair_resource_targets')
    target = targets.get(item) if isinstance(targets, dict) else None
    if (not isinstance(target, dict) or not isinstance(target.get('name'), str)
            or not target['name'].strip()
            or (item != 'wood' and target['name'] != item)
            or type(target.get('surface_index')) is not int
            or target['surface_index'] != runtime['surface_index']
            or _position(target.get('position')) is None
            or item not in snapshot.nearby_resources):
        return None
    return target


def _outpost_kit_prerequisite_start_evidence(snapshot, catalog, plan):
    """Qualify one current child-kit step while keeping its outer demand distinct.

    The current outpost admission rule is represented as a planner policy
    heuristic. This evidence does not forecast native payback or claim that the
    outpost arrived, flowed, produced output, or completed the outer target.
    """
    materials = plan.materials or {}
    provenance = materials.get('outpost_kit_prerequisite')
    local = materials.get('local_objective')
    intent = materials.get('work_intent')
    if (plan.goal != 'rocket_launch' or len(plan.steps) != 1
            or not isinstance(provenance, dict) or not isinstance(local, dict)
            or not isinstance(intent, dict) or provenance.get('schema') != 1
            or provenance.get('observed_tick') != snapshot.tick
            or intent.get('observed_tick') != snapshot.tick
            or intent.get('scope') != 'immediate'):
        return None

    resource, layout = provenance.get('outpost_resource'), provenance.get('outpost_layout')
    parent, child, admission = (provenance.get('parent_request'),
                                provenance.get('child_request'),
                                provenance.get('admission'))
    local_item = local.get('item')
    if (resource not in OUTPOST_RESOURCES or not isinstance(layout, str) or not layout
            or not isinstance(parent, dict) or not isinstance(child, dict)
            or not isinstance(admission, dict)
            or parent.get('item') != resource
            or parent.get('local_target_item') != local_item
            or not isinstance(local_item, str) or not local_item
            or type(parent.get('amount')) is not int or parent['amount'] <= 0
            or type(parent.get('inventory_now')) is not int
            or parent['inventory_now'] < 0
            or parent['inventory_now'] != snapshot.inventory.get(resource, 0)
            or type(child.get('quantity')) is not int or child['quantity'] < 1
            or child.get('kind') not in {'outpost_component', 'outpost_construction_fuel'}):
        return None
    kit_item = child.get('item')
    if (not isinstance(kit_item, str) or not kit_item
            or (child['kind'] == 'outpost_component' and kit_item not in OUTPOST_PARTS.values())
            or (child['kind'] == 'outpost_construction_fuel'
                and (kit_item != 'coal' or child['quantity'] != 5
                     or snapshot.inventory.get('coal', 0) >= 5))):
        return None

    try:
        row = outpost_sources(snapshot).get(resource)
        paid_sites = production_site_sources(snapshot)
        routes = input_route_sources(snapshot)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if (not isinstance(row, dict) or not outpost_current(row, snapshot)
            or row.get('layout') != layout or row.get('state') not in {'proposed', 'building'}
            or type(row.get('remaining')) is not int or row['remaining'] < 100
            or provenance.get('outpost_remaining') != row['remaining']
            or (child['kind'] == 'outpost_component'
                and outpost_remaining_kit(row).get(kit_item) != child['quantity'])):
        return None
    if (snapshot.factory.get('player_connected') is not True
            or snapshot.factory.get('player_bound') is not True):
        return None

    parent_path = parent.get('planner_item_path')
    if not _current_item_dependency_path(snapshot, catalog, parent_path,
                                         local_item, resource):
        return None

    # Recompute the source identity and direct-route condition from current
    # protocol/session/tick-bound witnesses; planner annotations alone do not
    # establish that the kit is attached to this producer.
    source_role = OUTPOST_RESOURCES[resource]
    producer = snapshot.factory.get('entities', {}).get(source_role)
    producer_site = paid_sites.get(source_role)
    if (not isinstance(producer, dict) or not isinstance(producer_site, dict)
            or producer_site.get('state') != 'owned'
            or type(producer.get('unit_number')) is not int or producer['unit_number'] <= 0
            or producer_site.get('source_unit') != producer['unit_number']
            or source_role in routes):
        return None

    outputs = []
    for entity in snapshot.factory.get('entities', {}).values():
        if not isinstance(entity, dict):
            return None
        output = entity.get('output', {})
        if not isinstance(output, dict):
            return None
        count = output.get(resource, 0)
        if type(count) is not int or count < 0:
            return None
        outputs.append(count)
    paid_output_absent = not any(outputs)

    policy = admission.get('classification')
    if row['state'] == 'proposed':
        shortage = parent['amount'] - parent['inventory_now']
        finished = producer.get('products_finished')
        if (policy != 'existing_proposed_outpost_policy_heuristic'
                or admission.get('state_at_admission') != 'proposed'
                or admission.get('source_role') != source_role
                or admission.get('source_unit') != producer['unit_number']
                or type(finished) is not int or finished < 20
                or admission.get('products_finished') != finished
                or admission.get('minimum_products_finished') != 20
                or admission.get('direct_input_route_absent') is not True
                or type(shortage) not in {int, float} or shortage < 10
                or admission.get('shortage_now') != shortage
                or admission.get('minimum_shortage') != 10
                or not paid_output_absent
                or admission.get('paid_output_absent') is not True
                or admission.get('native_outpost_payback_observed') is not False
                or admission.get('basis') !=
                    'current_planner_direct_route_and_minimum_runway_policy'):
            return None
        admission_basis = 'current_proposed_outpost_policy_heuristic'
    else:
        paid_parts = sorted(row['parts'])
        if (policy != 'current_paid_outpost_prefix_continuation'
                or not paid_parts
                or admission.get('state_at_admission') != 'building'
                or admission.get('paid_parts') != paid_parts
                or admission.get('current_paid_prefix') is not True
                or admission.get('native_outpost_payback_observed') is not False
                or admission.get('basis') != 'current_validated_outpost_component_receipts'):
            return None
        admission_basis = 'current_paid_outpost_prefix_continuation'

    step = plan.steps[0]
    try:
        if not step.allowed(snapshot) or step.satisfied(snapshot):
            return None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None

    child_path = None
    action_start = None
    if step.action == 'factory_gather' and step.effect == 'inventory':
        gather = (step.parameters or {}).get('resource')
        parameters = step.parameters or {}
        quantity = parameters.get('quantity')
        inventory_now = snapshot.inventory.get(gather, 0) if isinstance(gather, str) else None
        site = _current_native_fair_resource_target(snapshot, gather)
        if child['kind'] == 'outpost_component':
            raw = materials.get('raw_prerequisite')
            child_path = raw.get('planner_item_path') if isinstance(raw, dict) else None
            direct_product = (child_path[-2]
                              if isinstance(child_path, list) and len(child_path) >= 2 else None)
            try:
                recipe = catalog.recipe_for(direct_product) if direct_product else None
            except (KeyError, ValueError, TypeError):
                recipe = None
            qualified_dependency = (
                isinstance(raw, dict)
                and raw.get('observed_tick') == snapshot.tick
                and raw.get('ingredient') == gather
                and raw.get('direct_product') == direct_product
                and raw.get('recipe') == (recipe.get('name') if isinstance(recipe, dict) else None)
                and _current_item_dependency_path(snapshot, catalog, child_path, kit_item, gather))
            fuel_request_matches = True
        else:
            raw = None
            child_path = [kit_item]
            direct_product = None
            qualified_dependency = gather == 'coal' and gather == kit_item
            fuel_request_matches = (
                qualified_dependency and type(inventory_now) is int
                and inventory_now < child['quantity']
                and type(quantity) is int
                and quantity == min(50, child['quantity'] - inventory_now))
        quantity = (step.parameters or {}).get('quantity')
        inventory_now = snapshot.inventory.get(gather, 0) if isinstance(gather, str) else None
        if (child['kind'] == 'outpost_component' and not qualified_dependency
                or child['kind'] == 'outpost_construction_fuel' and not fuel_request_matches
                or gather != step.item
                or type(quantity) is not int or type(inventory_now) is not int
                or type(step.threshold) is not int
                or step.threshold != inventory_now + quantity
                or not isinstance(site, dict)):
            return None
        action_start = {
            'kind': 'observed_raw_gather_start', 'resource': gather,
            'quantity': quantity, 'inventory_now': inventory_now,
            'fair_target_identity_observed': True,
            'native_target_session_bound': True,
            'fair_target_surface_index': site['surface_index'],
            'travel_is_lower_bound_not_arrival_proof': True,
            'native_harvest_requires_fresh_verification': True,
        }
    elif step.action == 'factory_insert' and step.effect == 'transfer':
        if child['kind'] != 'outpost_component':
            return None
        transfer = _recipe_input_transfer_start_evidence(
            snapshot, catalog, plan, path_root=kit_item, require_receiver_capacity=True)
        if (transfer is None or not _current_item_dependency_path(
                snapshot, catalog, transfer['planner_item_path'], kit_item,
                transfer['ingredient'])):
            return None
        child_path = transfer['planner_item_path']
        action_start = {
            'kind': 'owned_native_recipe_input_transfer_start',
            'transfer': transfer,
            'receiver_capacity_observed': True,
            'fresh_native_dispatch_capacity_check_required': True,
            'native_dispatch_checks_receiver_insertable_count': True,
        }
    elif step.action == 'factory_extract' and step.effect == 'transfer':
        pickup = _output_pickup_start_evidence(snapshot, catalog, plan, path_root=kit_item)
        if (pickup is None or not _current_item_dependency_path(
                snapshot, catalog, pickup['planner_item_path'], kit_item,
                pickup['ready_output_item'])):
            return None
        if (child['kind'] == 'outpost_construction_fuel'
                and (pickup['ready_output_item'] != kit_item
                     or pickup['planned_pickup_quantity'] >
                        child['quantity'] - snapshot.inventory.get(kit_item, 0))):
            return None
        child_path = pickup['planner_item_path']
        action_start = {'kind': 'owned_native_output_pickup_start', 'pickup': pickup}
    elif step.action in {'factory_craft', 'factory_craft_job'}:
        if child['kind'] != 'outpost_component':
            return None
        craft = _craft_start_evidence(snapshot, catalog, step)
        dependency = materials.get('craft_dependency')
        child_path = dependency.get('planner_item_path') if isinstance(dependency, dict) else None
        parameters = step.parameters or {}
        if (step.effect not in {'inventory', 'craft_job_complete'}
                or not isinstance(craft, dict) or craft.get('observed_tick') != snapshot.tick
                or craft.get('native_recipe') != parameters.get('recipe')
                or not isinstance(dependency, dict)
                or dependency.get('observed_tick') != snapshot.tick
                or dependency.get('recipe') != parameters.get('recipe')
                or dependency.get('product') != step.item
                or not _current_item_dependency_path(snapshot, catalog, child_path,
                                                     kit_item, step.item)
                or not all(craft.get(key) is True for key in (
                    'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                    'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
                    'crafting_queue_empty'))):
            return None
        if step.action == 'factory_craft_job' and craft.get('craft_job_protocol_ready') is not True:
            return None
        action_start = {
            'kind': 'paid_native_handcraft_start', 'craft': craft,
            'native_output_and_child_completion_require_verification': True,
        }
    else:
        return None

    return {
        'schema': 1,
        'observed_tick': snapshot.tick,
        'outpost_resource': resource,
        'outpost_layout': layout,
        'outpost_state': row['state'],
        'parent_target_item': local_item,
        'parent_request_item': resource,
        'parent_request_amount': parent['amount'],
        'parent_inventory_now': parent['inventory_now'],
        'parent_shortage_now': parent['amount'] - parent['inventory_now'],
        'parent_planner_item_path': list(parent_path),
        'parent_and_child_paths_are_separate': True,
        'child_kit_item': kit_item,
        'child_kit_quantity': child['quantity'],
        'child_request_kind': child['kind'],
        'child_planner_item_path': list(child_path),
        'current_action': step.action,
        'current_action_item': step.item,
        'useful_partial_benefit_level': 1,
        'does_not_establish_level_two_blocker_removal': True,
        'admission_basis': admission_basis,
        'admission_is_not_native_payback_evidence': True,
        'outpost_placement_arrival_flow_output_and_parent_completion_unverified': True,
        'action_start_facts': action_start,
        'native_step_allowed_now': True,
        'native_action_outcome_requires_verification': True,
    }


def _native_research_trigger_start_evidence(snapshot, catalog, plan, gather_start):
    """Qualify immediate input to a native trigger without inventing a recipe edge."""
    from .research_trigger import current_machine_input_requirement, current_trigger

    materials = plan.materials or {}
    provenance = materials.get('native_research_trigger')
    intent = materials.get('work_intent')
    if (not isinstance(provenance, dict) or not isinstance(intent, dict)
            or intent.get('scope') != 'immediate'
            or intent.get('observed_tick') != snapshot.tick
            or provenance.get('observed_tick') != snapshot.tick):
        return None
    current = current_trigger(snapshot, catalog, provenance.get('technology'),
                              provenance.get('outer_recipe'))
    if current != provenance:
        return None
    step = plan.steps[0] if len(plan.steps) == 1 else None
    if step is None:
        return None
    if step.action == 'factory_gather' and step.effect == 'inventory':
        parameters = step.parameters or {}
        resource = parameters.get('resource')
        raw = materials.get('raw_prerequisite')
        raw_path = raw.get('planner_item_path') if isinstance(raw, dict) else None
        requirement = current_machine_input_requirement(
            snapshot, catalog, current, resource,
            [current['outer_recipe'], current['trigger_item'], resource]
            if isinstance(resource, str) else None)
        carried = snapshot.inventory.get(resource) if isinstance(resource, str) else None
        quantity = parameters.get('quantity')
        required_now = (requirement.get('machine_input_units_required_now')
                        if isinstance(requirement, dict) else None)
        expected_gather = (min(50, max(0, required_now - carried))
                           if isinstance(requirement, dict) and type(carried) is int else None)
        if (not isinstance(requirement, dict)
                or not isinstance(raw, dict) or raw.get('observed_tick') != snapshot.tick
                or raw.get('direct_product') != current['trigger_item']
                or raw.get('recipe') != current['trigger_recipe']
                or raw.get('ingredient') != resource
                or raw_path != [current['trigger_item'], resource]
                or not _current_item_dependency_path(
                    snapshot, catalog, raw_path, current['trigger_item'], resource)
                or gather_start is None
                or gather_start.get('resource_in_current_observation') is not True
                or gather_start.get('fair_target_identity_observed') is not True
                or type(quantity) is not int or quantity <= 0
                or quantity != expected_gather
                or step.threshold != carried + quantity):
            return None
        action = {
            'kind': 'direct_enabled_trigger_recipe_input_gather',
            'resource': resource, 'quantity': quantity,
            'trigger_recipe_input_units_required': requirement['planned_recipe_input_units'],
            'current_machine_input_units_required': required_now,
            'current_machine_input_now': requirement['machine_input_now'],
            'current_machine_input_in_flight': requirement['machine_input_in_flight'],
            'source_role': requirement['source_role'],
            'source_unit': requirement['source_unit'],
            'machine_recipe_observed': requirement['machine_recipe_observed'],
            'machine_recipe_identity_basis': requirement['machine_recipe_identity_basis'],
            'receiver_capacity': requirement['receiver_capacity'],
            'carried_resource_now': carried,
            'fair_target_identity_observed': True,
            'native_gather_outcome_requires_verification': True,
        }
    elif step.action == 'factory_insert' and step.effect == 'transfer':
        transfer = _recipe_input_transfer_start_evidence(
            snapshot, catalog, plan, path_root=current['trigger_item'],
            require_receiver_capacity=True)
        if not isinstance(transfer, dict):
            return None
        resource = transfer.get('ingredient')
        requirement = current_machine_input_requirement(
            snapshot, catalog, current, resource,
            [current['outer_recipe'], current['trigger_item'], resource])
        if (not isinstance(requirement, dict)
                or transfer.get('direct_native_recipe') != current['trigger_recipe']
                or transfer.get('planner_item_path') != [current['trigger_item'], resource]
                or transfer.get('owned_source_role') != requirement['source_role']
                or transfer.get('owned_source_unit') != requirement['source_unit']
                or transfer.get('paid_quantity_to_transfer') !=
                    requirement['machine_input_units_required_now']
                or transfer.get('receiver_capacity', {}).get('insertable_count_now', 0)
                    < transfer.get('paid_quantity_to_transfer', 1)
                or transfer.get('receiver_capacity', {}).get('observed_tick') != snapshot.tick
                or transfer.get('receiver_capacity', {}).get('session_id') != snapshot.session_id
                or transfer.get('receiver_capacity', {}).get('source_role') !=
                    requirement['source_role']
                or transfer.get('receiver_capacity', {}).get('source_unit') !=
                    requirement['source_unit']
                or transfer.get('receiver_capacity', {}).get('insertable_count_now') !=
                    requirement['receiver_capacity'].get('insertable_count_now')):
            return None
        action = {
            'kind': 'current_owned_trigger_recipe_input_transfer',
            'transfer': transfer,
            'trigger_recipe_input_units_required': requirement['planned_recipe_input_units'],
            'current_machine_input_units_required': requirement['machine_input_units_required_now'],
            'current_machine_input_now': requirement['machine_input_now'],
            'current_machine_input_in_flight': requirement['machine_input_in_flight'],
            'native_dispatch_rechecks_insertable_count_before_removal': True,
        }
    else:
        return None
    return {
        **current,
        'typed_dependency_path': [current['outer_recipe'], current['technology'],
                                  current['trigger_item'],
                                  action.get('resource') or (action.get('transfer') or {}).get(
                                      'ingredient')],
        'outer_recipe_is_not_a_direct_recipe_edge': True,
        'action_start_facts': action,
        'useful_partial_benefit_level': 1,
        'does_not_establish_trigger_item_output_or_unlock': True,
        'basis': 'typed_native_research_trigger_plus_direct_current_recipe_input',
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
        utility_lab_dependency = _utility_lab_research_dependency(snapshot, catalog, plan)
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
        local_target = (plan.materials or {}).get('local_objective')
        if local_target is not None:
            local_target = deepcopy(local_target)
        intent = (plan.materials or {}).get('work_intent', {})
        scope = (intent.get('scope') if isinstance(intent, dict)
                 and intent.get('observed_tick') == snapshot.tick else None)
        scope = scope if scope in {'immediate', 'lookahead'} else 'unclassified'
        if utility_lab_dependency is not None:
            unknown.append('placement_site:factory_place')
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
            craft_target_item = local.get('item') if isinstance(local, dict) else None
            step = plan.steps[0]
            if (provenance.get('observed_tick') == snapshot.tick
                    and provenance.get('recipe') == craft_start['native_recipe']
                    and provenance.get('product') == step.item
                    and isinstance(path, list) and 1 <= len(path) <= 32
                    and all(isinstance(item, str) and item for item in path)
                    and path[-1] == step.item
                    and isinstance(craft_target_item, str) and bool(craft_target_item)
                    and path[0] == craft_target_item):
                craft_dependency = {
                    'observed_tick': snapshot.tick,
                    'planner_item_path': list(path),
                    'current_craft_product': step.item,
                    'basis': 'current_recursive_planner_provenance_and_native_recipe',
                    'later_steps_require_fresh_native_preconditions': True,
                }
        local_target_completion = _local_target_completion_evidence(
            snapshot, catalog, plan, craft_start, craft_dependency)
        shared_bill_craft = None
        bill = (plan.materials or {}).get('shared_bill_craft')
        local = (plan.materials or {}).get('local_objective')
        if (scope == 'lookahead' and craft_start is not None
                and craft_start['recipe_unlocked_and_handcraftable'] is True
                and isinstance(bill, dict) and isinstance(local, dict)
                and len(plan.steps) == 1):
            step = plan.steps[0]
            parameters = step.parameters or {}
            bill_inventory_target = bill.get('bill_inventory_target')
            carried = bill.get('inventory_now')
            produced = craft_start['expected_products_after_native_verification'].get(step.item)
            bill_batches = (plan.materials or {}).get('batches')
            bill_batches = bill_batches if isinstance(bill_batches, dict) else {}
            if (step.action == 'factory_craft_job'
                    and isinstance(parameters.get('receipt'), str)
                    and bool(parameters['receipt'])
                    and craft_start.get('native_recipe') == parameters.get('recipe')
                    and all(craft_start.get(key) is True for key in (
                        'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                        'player_connected_and_bound', 'crafting_queue_empty',
                        'craft_job_protocol_ready', 'native_receipt_required_for_completion'))
                    and bill.get('basis') == 'current_catalog_shared_material_bill'
                    and bill.get('observed_tick') == snapshot.tick
                    and bill.get('local_target_item') == local.get('item')
                    and bill.get('local_target_amount') == local.get('inventory_target')
                    and bill.get('craft_item') == step.item
                    and type(bill.get('local_target_amount')) is int
                    and bill['local_target_amount'] > 0
                    and bill_batches.get(parameters.get('recipe')) == parameters.get('batches')
                    and type(bill_inventory_target) is int and bill_inventory_target > 0
                    and type(carried) is int and 0 <= carried < bill_inventory_target
                    and snapshot.inventory.get(step.item, 0) == carried
                    and type(produced) is int and produced > 0
                    and bill.get('planned_product_units') == produced
                    and produced >= bill_inventory_target - carried):
                shared_bill_craft = {
                    'observed_tick': snapshot.tick,
                    'local_target_item': local['item'],
                    'craft_item': step.item,
                    'bounded_bill_inventory_target': bill_inventory_target,
                    'inventory_now': carried,
                    'unfilled_bill_units': bill_inventory_target - carried,
                    'expected_products_after_native_verification': produced,
                    'basis': 'current_catalog_shared_material_bill_and_native_recipe',
                    'forecast_is_not_paid_stock_or_completed_output': True,
                    'background_overlap_requires_native_admission': True,
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
            sites = snapshot.factory.get('production_sites')
            sources = sites.get('sources') if isinstance(sites, dict) else None
            owned = sources.get(role) if isinstance(sources, dict) else None
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
                    and (startup or (isinstance(owned, dict)
                         and owned.get('state') == 'owned'
                         and owned.get('source_unit') == machine['unit_number']))
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
                    'coal_in_inventory_now': carried,
                    'planned_gather_units': gather_quantity,
                    'established_service_target': expected_target if not startup else None,
                    'startup_target': expected_target if startup else None,
                    'current_required_units': min(5, expected_target) - current,
                    'current_unfunded_units': max(0, min(5, expected_target) - current - carried),
                    'gather_units_beyond_current_need': max(
                        0, gather_quantity - max(0, min(5, expected_target) - current - carried)),
                    'basis': 'current_planner_fuel_need_and_owned_native_burner',
                    'later_fuel_transfer_and_output_require_fresh_native_preconditions': True,
                }
        if plan.goal == 'stockpile_fuel' and all(
                step.action in {'walk_to_coal', 'mine_coal'} for step in plan.steps):
            highest = max((step.threshold for step in plan.steps
                           if step.action == 'mine_coal'), default=0)
            scope = 'immediate' if highest <= 5 else 'lookahead'
        outpost_kit_start = _outpost_kit_prerequisite_start_evidence(
            snapshot, catalog, plan)
        native_research_trigger_start = _native_research_trigger_start_evidence(
            snapshot, catalog, plan, gather_start)
        passive = all(s.action in {'factory_wait', 'idle'} for s in plan.steps)
        result[plan.id] = {
            'work_scope': scope,
            'processed_units_basis': 'handling_volume_not_useful_production',
            'compiler_order': index, 'passive': passive, 'urgency': urgency,
            'reasons': sorted(set(reasons)), 'local_target': local_target,
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
            'local_target_completion_evidence': local_target_completion,
            'shared_bill_craft': shared_bill_craft,
            'placement_start_evidence': placement_start,
            'placement_dependency': placement_dependency,
            'utility_lab_research_dependency': utility_lab_dependency,
            'recipe_input_transfer_start_evidence': recipe_input_transfer_start,
            'native_research_trigger_start_evidence': native_research_trigger_start,
            'outpost_kit_prerequisite_start_evidence': outpost_kit_start,
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


def defer_gather_until_bill_craft(plans, support: dict, snapshot, memory):
    """Defer one independent raw gather until a complete paid craft can start.

    This changes only the JEV choice frontier. Native craft admission, its
    output lock, and a fresh observation still own any later gathering.
    """
    if (len(plans) != 2 or memory.status != 'running'
            or any(getattr(memory, name, None) is not None for name in (
                'pending', 'attempt', 'active_plan', 'transfer_recovery',
                'background_job', 'background_attempt', 'capital_investment',
                'solid_funding', 'coal_funding'))
            or any(getattr(memory, name, None) for name in (
                'reservations', 'solid_commitments', 'coal_commitments',
                'output_commitments', 'input_commitments', 'outpost_commitments',
                'successor_projects'))):
        return plans, None
    connector = getattr(memory, 'connector_ownership', None)
    if connector is not None and (not isinstance(connector, dict)
                                  or connector.get('routes') != {}):
        return plans, None
    from ..craft_jobs import permits_locked_outputs

    rows = support.get('candidate_evidence', {})
    crafts = [p for p in plans if len(p.steps) == 1
              and p.steps[0].action == 'factory_craft_job']
    gathers = [p for p in plans if len(p.steps) == 1
               and p.steps[0].action == 'factory_gather']
    if len(crafts) != 1 or len(gathers) != 1:
        return plans, None
    craft, gather = crafts[0], gathers[0]
    craft_row, gather_row = rows.get(craft.id), rows.get(gather.id)
    if not isinstance(craft_row, dict) or not isinstance(gather_row, dict):
        return plans, None
    bill, start = craft_row.get('shared_bill_craft'), craft_row.get('craft_start_evidence')
    raw, gather_start = gather_row.get('raw_prerequisite'), gather_row.get('gather_start_evidence')
    if not all(isinstance(value, dict) for value in (bill, start, raw, gather_start)):
        return plans, None
    craft_step, gather_step = craft.steps[0], gather.steps[0]
    path = raw.get('planner_item_path')
    needed, produced = bill.get('unfilled_bill_units'), bill.get('expected_products_after_native_verification')
    outputs = start.get('expected_products_after_native_verification')
    if (craft_row.get('work_scope') != 'lookahead'
            or gather_row.get('work_scope') != 'immediate'
            or any(row.get('unknowns') != [] or type(row.get('urgency')) is not int
                   or row['urgency'] != 0 or row.get('research_deadline_tick') is not None
                   for row in (craft_row, gather_row))
            or bill.get('observed_tick') != snapshot.tick
            or bill.get('forecast_is_not_paid_stock_or_completed_output') is not True
            or bill.get('background_overlap_requires_native_admission') is not True
            or not isinstance(bill.get('local_target_item'), str)
            or not bill['local_target_item']
            or bill.get('craft_item') != craft_step.item
            or type(needed) is not int or needed <= 0
            or type(produced) is not int or produced < needed
            or not isinstance(outputs, dict) or not 1 <= len(outputs) <= 32
            or outputs.get(craft_step.item) != produced
            or any(not isinstance(item, str) or not item or type(count) is not int
                   or count <= 0 for item, count in outputs.items())
            or start.get('observed_tick') != snapshot.tick
            or start.get('native_recipe') != (craft_step.parameters or {}).get('recipe')
            or not all(start.get(key) is True for key in (
                'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
                'crafting_queue_empty', 'craft_job_protocol_ready',
                'native_receipt_required_for_completion'))
            or not isinstance((craft_step.parameters or {}).get('receipt'), str)
            or not craft_step.parameters['receipt']
            or raw.get('observed_tick') != snapshot.tick
            or not isinstance(path, list) or not 2 <= len(path) <= 32
            or path[0] != bill['local_target_item']
            or path[-1] != (gather_step.parameters or {}).get('resource')
            or gather_start.get('resource_in_current_observation') is not True
            or gather_start.get('fair_target_identity_observed') is not True
            or gather_start.get('target_inventory_after_this_step') != gather_step.threshold
            or not craft_step.allowed(snapshot) or craft_step.satisfied(snapshot)
            or not gather_step.allowed(snapshot) or gather_step.satisfied(snapshot)
            or not permits_locked_outputs(gather_step, set(outputs))):
        return plans, None
    return [craft], 'complete_current_bill_craft_before_independent_raw_gather'


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
    first_evidence = evidence.get(plans[0].id, {}) if plans else {}
    lab_dependency = (first_evidence.get('utility_lab_research_dependency')
                      if isinstance(first_evidence, dict) else None)
    if isinstance(lab_dependency, dict):
        instruction = (
            f"Place the paid utility lab as the current immediate prerequisite for "
            f"starting {lab_dependency['technology']} research. Placement-site clearance, "
            "travel, lab power, and research completion remain unverified and require "
            "the existing native action and later observations.")
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
