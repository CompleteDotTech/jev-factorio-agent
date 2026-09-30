"""Fail-closed admission for the first paid coal network component.

Candidate eligibility is bound to a fresh, fixed native v7 query and the
current rocket-research bill. The query is evidence only; the backend repeats
the decisive native check in the same RCON command as the first placement.
"""
from __future__ import annotations

import hashlib
import json
import math

from ..state import GameSnapshot
from .catalog import Catalog
from .coal_goal_alignment import evaluate as evaluate_goal_alignment
from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE


def _hash(value):
    return (type(value) is str and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def _deny(reason, snapshot):
    return {'schema': 'jev.coal-current-goal-admission.v1',
            'eligible': False, 'reason': reason,
            'observed_tick': getattr(snapshot, 'tick', -1),
            'session_id': getattr(snapshot, 'session_id', ''),
            'native_payback_proven': False,
            'mutation_authorized': False}


def evaluate(snapshot, memory=None, catalog=None, projection=None) -> dict:
    """Check typed native proof, exact current research and source hashes.

    This function grants no mutation authority. A positive result only permits
    the controller to consider a candidate; first payment is requalified by
    the fixed Lua proof and placement in one RCON request.
    """
    from ..coal_economic_v7 import PROFILE, V7Observation, digest, query_sha256
    from ..memory import CampaignMemory

    # Retain the old read-only diagnostics call shape for legacy callers. It
    # can only defer; the v7 eligibility path always supplies every argument.
    if memory is None and catalog is None and projection is None:
        from .. import coal_supply

        rows = coal_supply.sources(snapshot)
        data = snapshot.factory['coal_supply']
        reason = ('coal_admission_v2_unavailable' if data['protocol'] == 1
                  else data['admission']['reason'])
        return {'eligible': False, 'reason': reason,
                'observed_tick': snapshot.tick,
                'session_id': snapshot.session_id,
                'source_count': len(rows),
                'native_payback_proven': False,
                'mutation_authorized': False}
    if memory is None or catalog is None or projection is None:
        return _deny('unqualified_inputs', snapshot)

    result = _deny('unqualified_inputs', snapshot)
    try:
        if (not isinstance(snapshot, GameSnapshot)
                or not isinstance(memory, CampaignMemory)
                or not isinstance(catalog, Catalog)
                or memory.session_id != snapshot.session_id
                or memory.last_tick != snapshot.tick
                or memory.active_goal != 'rocket_launch'
                or memory.target != 'rocket_launch' or memory.status != 'running'
                or memory.coal_economic_admission is not True
                or memory.coal_kit_policy is not True
                or memory.pending is not None or memory.attempt is not None
                or not isinstance(projection, dict)
                or set(projection) != {'native', 'raw_response', 'query_sha256',
                                       'response_sha256', 'coal_admission',
                                       'project_setup_cost_estimate'}):
            return result

        native = projection['native']
        proof = projection['coal_admission']
        response = projection['raw_response']
        query_hash = projection['query_sha256']
        response_hash = projection['response_sha256']
        setup = projection['project_setup_cost_estimate']
        setup_fields = {'schema', 'acquisition_ticks_estimate',
                        'acquisition_actions_estimate', 'placement_count',
                        'placement_service_ticks_estimate',
                        'placement_manhattan_distance_tiles_estimate',
                        'placement_travel_ticks_estimate',
                        'total_setup_ticks_estimate', 'basis', 'measured',
                        'native_payback_proven', 'mutation_authorized'}
        if (type(native) is not V7Observation or native.profile != PROFILE
                or native.mutation_authorized is not False
                or native.native_payback_proven is not False
                or type(response) is not str or len(response) > 262144
                or not _hash(query_hash) or query_hash != query_sha256()
                or not _hash(response_hash)
                or hashlib.sha256(response.encode('utf-8')).hexdigest() != response_hash
                or not isinstance(proof, dict)
                or proof != native.native_admission
                or proof.get('qualified') is not True
                or proof.get('mutation_authorized') is not False
                or proof.get('time_to_completion_claimed') is not False
                or proof.get('current_surface_only') is not True
                or proof.get('local_scope_only') is not True
                or proof.get('local_alternative_scope') !=
                   'actor_and_copper_consumer_radius_64'
                or proof.get('observed_generated_entities_only') is not True
                or proof.get('unobserved_chunks_inside_scope_unknown') is not True
                or proof.get('unobserved_current_surface_outside_scope') is not True
                or proof.get('future_generated_chunks_unknown') is not True
                or not isinstance(setup, dict) or set(setup) != setup_fields
                or setup.get('schema') != 'jev.coal-project-setup-cost-estimate.v1'
                or setup.get('basis') != 'deterministic_funding_plus_serial_manhattan_service_policy'
                or setup.get('measured') is not False
                or setup.get('native_payback_proven') is not False
                or setup.get('mutation_authorized') is not False
                or any(type(setup[key]) is not int or setup[key] < 0 for key in
                       ('acquisition_ticks_estimate', 'acquisition_actions_estimate',
                        'placement_count', 'placement_service_ticks_estimate',
                        'placement_travel_ticks_estimate', 'total_setup_ticks_estimate'))
                or type(setup['placement_manhattan_distance_tiles_estimate']) not in {int, float}
                or not math.isfinite(setup['placement_manhattan_distance_tiles_estimate'])
                or setup['placement_manhattan_distance_tiles_estimate'] < 0
                or setup['placement_count'] < 1
                or setup['placement_service_ticks_estimate'] != setup['placement_count'] * SERVICE_TICKS
                or setup['placement_travel_ticks_estimate'] !=
                   math.ceil(setup['placement_manhattan_distance_tiles_estimate'] * TRAVEL_TICKS_PER_TILE)
                or setup['total_setup_ticks_estimate'] !=
                   (setup['acquisition_ticks_estimate']
                    + setup['placement_service_ticks_estimate']
                    + setup['placement_travel_ticks_estimate'])
                or setup['placement_count'] != proof.get('current_placement_count')
                or setup['placement_manhattan_distance_tiles_estimate']
                   != proof.get('current_placement_manhattan_distance_tiles_estimate')
                or setup['placement_service_ticks_estimate']
                   != proof.get('placement_service_ticks_estimate')
                or setup['placement_travel_ticks_estimate']
                   != proof.get('placement_travel_ticks_estimate')
                or setup['total_setup_ticks_estimate'] !=
                   (setup['acquisition_ticks_estimate']
                    + proof.get('remaining_project_setup_ticks_estimate', 0))
                or setup['total_setup_ticks_estimate'] <= 0):
            return _deny('native_current_goal_proof_unqualified', snapshot)

        payload = json.loads(response)
        epoch = payload.get('epoch') if isinstance(payload, dict) else None
        graph = native.graph
        runtime = snapshot.factory.get('acceptance_runtime')
        source = snapshot.factory.get('coal_supply')
        if (not isinstance(payload, dict)
                or payload.get('schema') != 'jev.coal-native-economics.v7'
                or payload.get('query_status') != 'observed'
                or payload.get('reason') != 'bounded_native_projection'
                or payload.get('coal_admission') != proof
                or digest(payload) != native.raw_sha256
                or not isinstance(epoch, dict)
                or set(epoch) != {'session_id', 'tick', 'actor_index', 'actor_unit',
                                  'surface_index', 'force_index'}
                or not isinstance(runtime, dict) or not isinstance(source, dict)
                or type(epoch['session_id']) is not str
                or any(type(epoch[key]) is not int for key in
                       ('actor_index', 'actor_unit', 'surface_index', 'force_index'))
                or epoch['session_id'] != snapshot.session_id
                or type(epoch['tick']) is not int or epoch['tick'] < snapshot.tick
                or epoch['actor_index'] != source.get('actor_index')
                or epoch['actor_unit'] != runtime.get('actor_unit')
                or epoch['surface_index'] != source.get('surface_index')
                or epoch['force_index'] != source.get('force_index')
                or graph.epoch.session_id != epoch['session_id']
                or graph.epoch.tick != epoch['tick']
                or graph.epoch.actor_index != epoch['actor_index']
                or graph.epoch.surface_index != epoch['surface_index']
                or graph.epoch.force_index != epoch['force_index']
                or graph.actor_unit != epoch['actor_unit']
                or proof.get('session_id') != epoch['session_id']
                or proof.get('tick') != epoch['tick']
                or proof.get('actor_unit') != epoch['actor_unit']):
            return _deny('native_current_goal_epoch_changed', snapshot)

        work = graph.research_work
        if (work is None or work.technology != proof.get('technology')
                or snapshot.factory.get('research') != work.technology):
            return _deny('native_current_research_changed', snapshot)
        units = math.ceil(work.unit_count
                          * (1 if work.ignore_cost_multiplier else work.cost_multiplier)
                          * (1 - work.progress))
        expected_demand = {name: amount * units
                           for name, amount in work.ingredients}
        if (units < 1 or units != proof.get('remaining_research_units')
                or expected_demand != proof.get('research_demand')):
            return _deny('native_current_research_bill_changed', snapshot)

        copper = [row for row in work.targets if row.role == 'recipe:copper-plate']
        if (len(copper) != 1 or copper[0].recipe != 'copper-plate'
                or copper[0].crafting is not False
                or len(copper[0].recipe_ingredients) != 1
                or copper[0].recipe_ingredients[0][0] != 'copper-ore'
                or copper[0].recipe_products != (('copper-plate', 1),)):
            return _deny('native_copper_target_changed', snapshot)
        input_stock = dict(copper[0].input)
        ore_amount = copper[0].recipe_ingredients[0][1]
        if (proof.get('direct_target') != 'recipe:copper-plate'
                or proof.get('copper_plate_ore_input_required')
                   != proof.get('copper_plate_recipe_batches') * ore_amount
                or proof.get('copper_plate_ore_input_available')
                   != input_stock.get('copper-ore', 0)
                or proof.get('copper_plate_ore_input_available', 0)
                   < proof.get('copper_plate_ore_input_required', 1)):
            return _deny('native_copper_goal_demand_changed', snapshot)

        alignment = evaluate_goal_alignment(snapshot, memory, catalog, graph)
        if (alignment.get('relevant') is not True
                or alignment.get('mutation_authorized') is not False
                or alignment.get('path') != proof.get('rocket_goal_path')):
            return _deny('current_goal_alignment_unqualified', snapshot)

        manual_ticks = proof.get('manual_fuel_service_ticks_estimate')
        manual_coal = proof.get('manual_copper_coal_units_estimate')
        if (type(manual_ticks) is not int or manual_ticks <= 0
                or type(manual_coal) is not int or manual_coal <= 0
                or manual_ticks <= setup['total_setup_ticks_estimate']):
            return _deny('manual_fuel_service_forecast_not_positive', snapshot)

        return {'schema': 'jev.coal-current-goal-admission.v1',
                'eligible': True,
                'reason': 'current_research_direct_copper_fuel_and_setup_estimate_margin',
                'observed_tick': epoch['tick'],
                'session_id': epoch['session_id'],
                'technology': work.technology,
                'rocket_goal_path': alignment['path'],
                'remaining_research_units': units,
                'research_demand': expected_demand,
                'copper_plate_recipe_batches': proof['copper_plate_recipe_batches'],
                'copper_plate_ore_input_required': proof['copper_plate_ore_input_required'],
                'copper_plate_ore_input_available': proof['copper_plate_ore_input_available'],
                'boiler_residual_demand_joules_upper': proof['boiler_residual_demand_joules_upper'],
                'boiler_source_energy_joules_lower': proof['boiler_source_energy_joules_lower'],
                'copper_residual_demand_joules_upper': proof['copper_residual_demand_joules_upper'],
                'copper_source_energy_joules_lower': proof['copper_source_energy_joules_lower'],
                'manual_copper_coal_units_estimate': manual_coal,
                'manual_copper_mining_ticks_estimate_no_walk':
                    proof['manual_copper_mining_ticks_estimate_no_walk'],
                'manual_fuel_service_ticks_estimate': manual_ticks,
                'manual_fuel_service_basis': proof['manual_fuel_service_basis'],
                'manual_local_collection_ticks_estimate':
                    proof['manual_local_collection_ticks_estimate'],
                'manual_local_collection_fuel_joules_estimate':
                    proof['manual_local_collection_fuel_joules_estimate'],
                'manual_local_collection_source_count_estimate':
                    proof['manual_local_collection_source_count_estimate'],
                'whole_project_setup_ticks_estimate': setup['total_setup_ticks_estimate'],
                'project_setup_cost_estimate': setup,
                'native_query_sha256': query_hash,
                'native_response_sha256': response_hash,
                'current_surface_only': True,
                'local_scope_only': True,
                'local_alternative_scope': 'actor_and_copper_consumer_radius_64',
                'observed_generated_entities_only': True,
                'unobserved_chunks_inside_scope_unknown': True,
                'unobserved_current_surface_outside_scope': True,
                'future_generated_chunks_unknown': True,
                'time_to_completion_claimed': False,
                'native_payback_proven': False,
                'mutation_authorized': False}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError,
            OverflowError, RecursionError):
        return _deny('native_current_goal_projection_unavailable', snapshot)
