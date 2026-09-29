"""Bounded native coal/power facts, never a positive admission or spending token.

The fixed raw query and its retained response provenance are operator evidence.
Parsing a synthetic or forged dictionary cannot attest engine authenticity.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from importlib.resources import files
import math

from .planning.coal_economic_proof import Epoch, Power, digest

SCHEMA = 'jev.coal-native-economics.v5'
QUERY = 'lua/coal_economics.lua'
MAX_SAFE = 2**53 - 1
# This is a content-qualified conversion record, not an arbitrary caller hash.
# Exact base assets plus the isolated runtime prototype readback agree on units.
UNIT_QUALIFICATION = {
    'schema': 'jev.coal-units.base-2.0.77.v1', 'base_version': '2.0.77',
    'mods': {'base': '2.0.77'}, 'ticks_per_second': 60,
    'energy_unit': 'joule', 'runtime_power_unit': 'joule_per_tick',
    'entities_lua_sha256': '26d738a076608130081d8cfa4a0d36dc39d2f98d40abb45735930b41841d06e4',
    'mining_drill_lua_sha256': '13efc72d3ca60026b3d44de2ea2e534715e9ce7c4271df16a246d93c6a21af8f',
    'prototype_query_sha256': 'b5671464250a2020981ad64a2502e329d93183deb398e59521c80f12cb16583e',
    'prototype_readback_sha256': 'd983622380209987e0a1d978194ec0a2056708b7595188e3c1a4510a981529da',
    'static_power_watts': {'electric-mining-drill': 90_000, 'boiler': 1_800_000,
                           'stone-furnace': 90_000, 'assembling-machine-1': 75_000, 'lab': 60_000},
    'inserter': {'movement_joules': 5000, 'rotation_joules': 5000,
                 'extension_per_tick': .035, 'rotation_per_tick': .014, 'drain_watts': 400},
    'steam': {'usage_per_tick': .5, 'heat_capacity': 200,
              'input_temperature': 15, 'output_temperature': 165, 'efficiency': 1},
}
RATES = {
    'electric-mining-drill': ('electric', 1500, 0, 0),
    'inserter': ('electric', 245, 0, 400 / 60),
    'assembling-machine-1': ('electric', 1250, 0, 2500 / 60),
    'lab': ('electric', 1000, 0, 0),
    'boiler': ('burner', 30_000, 0, 0),
    'steam-engine': ('electric', 0, 15_000, 0),
    'stone-furnace': ('burner', 1500, 0, 0),
    'offshore-pump': ('void', 1000, 0, 0),
}
POLE_RADII = {'small-electric-pole': 2.5, 'medium-electric-pole': 3.5,
              'big-electric-pole': 2, 'substation': 9}
ROOT_KEYS = set(('schema base_version mods query_status reason epoch registry connector_routes prototypes poles '
    'supply_surveys electric_members fluid_members fuel_targets sources coal_fuel_joules '
    'fluid_prototypes pole_prototypes buffer_witnesses material_scope research_work manual_cycle').split())


class NativeEconomicsUnavailable(ValueError):
    """The observed graph/input is unsupported, incomplete or inconsistent."""


def require(value, reason):
    if not value:
        raise NativeEconomicsUnavailable(reason)


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'invalid_native_fields')


def integer(value, low=0, high=MAX_SAFE):
    require(type(value) is int and low <= value <= high, 'invalid_native_integer')
    return value


def number(value, low=0, high=MAX_SAFE):
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            'invalid_native_number')
    return value


def close(value, expected):
    number(value)
    require(math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12), 'prototype_unit_mismatch')


def rows(value, low, high):
    # Arrays remain arrays. Factorio can encode an empty table as an empty map.
    if value == {} and low == 0:
        value = []
    require(type(value) is list and low <= len(value) <= high, 'invalid_native_array')
    return value


def identity(value):
    require(isinstance(value, str) and 0 < len(value) <= 128
            and all(32 <= ord(c) <= 126 for c in value), 'invalid_native_identity')
    return value


def point(value):
    fields(value, 'x y')
    return tuple(number(value[k], -1_000_000, 1_000_000) for k in ('x', 'y'))


def bounds(value):
    fields(value, 'left_top right_bottom')
    a, b = point(value['left_top']), point(value['right_bottom'])
    require(a[0] < b[0] and a[1] < b[1], 'invalid_native_bounds')
    return a, b


def overlaps(left, right):
    a, b = bounds(left); c, d = bounds(right)
    return a[0] < d[0] and c[0] < b[0] and a[1] < d[1] and c[1] < b[1]


def unique_rows(value, key, low, high):
    value = rows(value, low, high)
    keys = [row[key] for row in value]
    if key == 'unit':
        for unit in keys:
            integer(unit, 1)
    require(len(set(keys)) == len(keys) and keys == sorted(keys), 'aliased_or_unsorted_native_rows')
    return {row[key]: row for row in value}


def unit_list(value, high):
    value = rows(value, 0, high)
    for item in value:
        integer(item, 1)
    require(value == sorted(set(value)), 'aliased_native_edges')
    return set(value)


def connected(nodes, edges, start):
    seen, pending = set(), deque([start])
    while pending:
        unit = pending.popleft()
        if unit not in seen:
            require(unit in nodes, 'foreign_graph_member')
            seen.add(unit); pending.extend(edges[unit] - seen)
    require(seen == set(nodes), 'disconnected_native_graph')


def operating(value, *, supply=False):
    fields(value, 'active status control_behavior_present')
    require(value['active'] is True and value['control_behavior_present'] is False,
            'inactive_or_controlled_member')
    # Supported idle workloads may still incur maximum power cost. Producers
    # additionally need a currently usable status, not only a rated prototype.
    statuses = {'normal', 'working', 'full_output', 'low_power', 'no_power', 'no_recipe',
                'no_ingredients', 'item_ingredient_shortage', 'fluid_ingredient_shortage',
                'missing_science_packs', 'no_research_in_progress', 'no_minable_resources',
                'waiting_for_source_items', 'waiting_for_space_in_destination'}
    require(value['status'] in ({'normal', 'working', 'full_output'} if supply else statuses),
            'unsupported_operating_status')


@dataclass(frozen=True)
class SourceFacts:
    target_role: str
    target_unit: int
    layout: str
    remaining_ore: int
    ore_mining_ticks: int
    consumer_max_joules_per_tick: int
    consumer_stored_fuel_joules_lower: int
    consumer_stored_fuel_joules_upper: int


@dataclass(frozen=True)
class ResearchTargetFacts:
    role: str
    unit: int
    recipe: str
    crafting: bool
    crafting_progress: float
    burning: str
    input: tuple[tuple[str, int], ...]
    recipe_ingredients: tuple[tuple[str, int], ...]
    recipe_products: tuple[tuple[str, int], ...]
    crafting_speed: float = 0
    recipe_energy: float = 0


@dataclass(frozen=True)
class ResearchWorkFacts:
    technology: str
    progress: float
    unit_count: int
    cost_multiplier: float
    ignore_cost_multiplier: bool
    ingredients: tuple[tuple[str, int], ...]
    lab_unit: int
    lab_input: tuple[tuple[str, int], ...]
    targets: tuple[ResearchTargetFacts, ...]
    unit_energy: float = 0


@dataclass(frozen=True)
class MaterialStockFacts:
    role: str
    unit: int
    fuel: tuple[tuple[str, int], ...]
    output: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class MaterialScopeFacts:
    actor_items: tuple[tuple[str, int], ...]
    crafting_queue: int
    owned_stock: tuple[MaterialStockFacts, ...]
    closure_complete: bool = False


@dataclass(frozen=True)
class ManualGatherFacts:
    receipt: str
    started_tick: int
    finished_tick: int
    coal_before: int
    coal_after: int
    walking_ticks: int
    mining_ticks: int


@dataclass(frozen=True)
class ManualDeliveryFacts:
    receipt: str
    role: str
    unit: int
    coal: int
    tick: int


@dataclass(frozen=True)
class ManualCycleFacts:
    journal_asset_sha256: str
    gathers: tuple[ManualGatherFacts, ...]
    deliveries: tuple[ManualDeliveryFacts, ...]
    attempts_bound: bool = False
    cycle_complete: bool = False


@dataclass(frozen=True)
class NativeEconomics:
    epoch: Epoch
    actor_unit: int
    bundle_sha256: str
    raw_sha256: str
    power: Power
    sources: tuple[SourceFacts, ...]
    mutation_authorized: bool = False
    native_payback_proven: bool = False
    research_work: ResearchWorkFacts | None = None
    manual_cycle: ManualCycleFacts | None = None
    material_scope: MaterialScopeFacts | None = None


def query_sha256():
    import hashlib
    return hashlib.sha256(files('jev_factorio').joinpath(QUERY).read_bytes()).hexdigest()


def decode(raw: dict, *, expected_epoch: dict, expected_bundle: dict,
           unit_qualification: dict, expected_connectors: dict | None = None,
           expected_routes: dict | None = None,
           expected_journal_asset_sha256: str | None = None) -> NativeEconomics:
    """Derive bounded facts; no caller flag/hash can establish eligibility."""
    try:
        return _decode(raw, expected_epoch, expected_bundle, unit_qualification,
                       expected_connectors, expected_routes, expected_journal_asset_sha256)
    except NativeEconomicsUnavailable:
        raise
    except (KeyError, TypeError, AttributeError, IndexError, ValueError, OverflowError) as error:
        raise NativeEconomicsUnavailable('invalid_native_projection') from error


def _decode(raw, expected_epoch, expected_bundle, unit_qualification, expected_connectors,
            expected_routes, expected_journal_asset_sha256):
    require(isinstance(raw, dict) and raw.get('query_status') == 'observed', 'native_projection_unsupported')
    fields(raw, ' '.join(ROOT_KEYS))
    require(raw['schema'] == SCHEMA and raw['reason'] == 'bounded_native_projection'
            and raw['base_version'] == '2.0.77' and raw['mods'] == {'base': '2.0.77'}, 'native_treatment_mismatch')
    require(digest(unit_qualification) == digest(UNIT_QUALIFICATION), 'unqualified_native_units')
    fields(raw['epoch'], 'session_id tick actor_index actor_unit surface_index force_index')
    require(digest(raw['epoch']) == digest(expected_epoch), 'native_epoch_mismatch')
    actor_unit = integer(raw['epoch']['actor_unit'], 1)
    epoch = Epoch.parse({k: v for k, v in raw['epoch'].items() if k != 'actor_unit'})
    close(raw['coal_fuel_joules'], 4_000_000)
    prototypes = unique_rows(raw['prototypes'], 'name', len(RATES), len(RATES))
    require(set(prototypes) == set(RATES), 'unsupported_prototype_set')
    for name, (source, usage, production, drain) in RATES.items():
        row = prototypes[name]
        keys = 'name source max_usage max_production drain buffer_capacity'
        if source == 'burner': keys += ' burner_efficiency'
        if name == 'boiler': keys += ' target_temperature boiler_mode'
        if name == 'steam-engine': keys += ' generator_efficiency fluid_usage_per_tick maximum_temperature'
        if name == 'electric-mining-drill': keys += ' mining_speed'
        fields(row, keys)
        require(row['source'] == source, 'prototype_energy_source_mismatch')
        close(row['max_usage'], usage); close(row['max_production'], production); close(row['drain'], drain)
        number(row['buffer_capacity'], 0, 1_000_000)
        if source != 'electric': close(row['buffer_capacity'], 0)
        if source == 'burner': close(row['burner_efficiency'], 1)
    close(prototypes['electric-mining-drill']['mining_speed'], .5)
    boiler_proto, engine_proto = prototypes['boiler'], prototypes['steam-engine']
    require(boiler_proto['boiler_mode'] == 'output-to-separate-pipe', 'unsupported_boiler_mode')
    close(boiler_proto['target_temperature'], 165)
    close(engine_proto['generator_efficiency'], 1); close(engine_proto['fluid_usage_per_tick'], .5)
    close(engine_proto['maximum_temperature'], 165)
    fluids = unique_rows(raw['fluid_prototypes'], 'name', 2, 2)
    for name, heat, maximum in (('steam', 200, 5000), ('water', 2000, 100)):
        fields(fluids[name], 'name heat_capacity default_temperature max_temperature')
        close(fluids[name]['heat_capacity'], heat); close(fluids[name]['default_temperature'], 15)
        close(fluids[name]['max_temperature'], maximum)
    poles_prototypes = unique_rows(raw['pole_prototypes'], 'name', 4, 4)
    require(set(poles_prototypes) == set(POLE_RADII), 'unsupported_pole_set')
    for name, radius in POLE_RADII.items():
        fields(poles_prototypes[name], 'name supply_radius'); close(poles_prototypes[name]['supply_radius'], radius)

    registry = unique_rows(raw['registry'], 'role', 1, 2048)
    owned = {}
    for role, row in registry.items():
        fields(row, 'role unit name quality surface_index force_index position bounds')
        identity(role); identity(row['name']); unit = integer(row['unit'], 1)
        require(unit not in owned and unit != actor_unit and row['quality'] == 'normal', 'aliased_native_owner')
        require(type(row['surface_index']) is int and row['surface_index'] == epoch.surface_index
                and type(row['force_index']) is int and row['force_index'] == epoch.force_index, 'foreign_native_owner')
        point(row['position']); bounds(row['bounds']); owned[unit] = row
    if expected_connectors is None:
        expected_connectors = {}
    if expected_routes is None:
        expected_routes = {}
    require(isinstance(expected_routes, dict) and len(expected_routes) <= 128,
            'invalid_connector_binding')
    native_routes = unique_rows(raw['connector_routes'], 'id', 0, 128)
    require(set(native_routes) == set(expected_routes), 'connector_route_binding_mismatch')
    route_fields = ('id source target source_unit target_unit kind fluid actor_unit session_id '
                    'surface_index force_index state owned paid cell_count')
    for receipt, expected in expected_routes.items():
        row = native_routes[receipt]
        fields(row, route_fields)
        require(isinstance(expected, dict) and set(expected) == set(route_fields.split())
                and row == expected and row['state'] == 'complete' and row['owned'] is True,
                'connector_route_binding_mismatch')
        require(row['session_id'] == epoch.session_id and row['actor_unit'] == actor_unit
                and row['surface_index'] == epoch.surface_index
                and row['force_index'] == epoch.force_index,
                'connector_route_epoch_mismatch')
        integer(row['source_unit'], 1); integer(row['target_unit'], 1)
        integer(row['paid'], 1, 128); integer(row['cell_count'], 1, 128)
        require(row['paid'] == row['cell_count'] and row['kind'] in {'pipe', 'small-electric-pole'},
                'connector_route_unqualified')
        require(row['source'] in registry and row['target'] in registry
                and registry[row['source']]['unit'] == row['source_unit']
                and registry[row['target']]['unit'] == row['target_unit'],
                'connector_endpoint_changed')
        require(sum(role.startswith(f'connector:{receipt}:') for role in expected_connectors)
                == row['cell_count'], 'connector_route_cell_mismatch')
    require(isinstance(expected_connectors, dict) and len(expected_connectors) <= 128,
            'invalid_connector_binding')
    connector_roles = {role for role in registry if role.startswith('connector:')}
    require(connector_roles == set(expected_connectors), 'connector_binding_mismatch')
    for role, expected in expected_connectors.items():
        require(isinstance(expected, dict) and set(expected) == {'unit', 'name', 'position'},
                'invalid_connector_binding')
        receipt, separator, index = role.removeprefix('connector:').rpartition(':')
        require(role.startswith('connector:') and separator and len(receipt) == 64
                and all(char in '0123456789abcdef' for char in receipt)
                and index.isascii() and index.isdigit() and str(integer(int(index), 1, 128)) == index,
                'invalid_connector_binding')
        row = registry[role]
        require(row['unit'] == integer(expected['unit'], 1)
                and row['name'] == expected['name']
                and row['position'] == expected['position']
                and row['name'] in {'pipe', 'small-electric-pole'},
                'connector_payment_changed')
        point(expected['position'])
    witnesses = unique_rows(raw['buffer_witnesses'], 'name', 2, 2)
    require(set(witnesses) == {'electric-mining-drill', 'inserter'}, 'missing_buffer_witness')
    for name, witness in witnesses.items():
        fields(witness, 'name unit capacity')
        unit = integer(witness['unit'], 1)
        require(unit in owned and owned[unit]['name'] == name, 'foreign_buffer_witness')
        number(witness['capacity'], 0, 1_000_000)
    boiler = registry.get('utility:boiler')
    require(boiler is not None and boiler['name'] == 'boiler', 'owned_boiler_missing')
    pole_rows = unique_rows(raw['poles'], 'unit', 1, 32)
    edges = {}; network = None; edge_count = 0
    for unit, row in pole_rows.items():
        fields(row, 'unit network_id supply_radius neighbors')
        require(unit in owned and owned[unit]['name'] in POLE_RADII, 'foreign_power_pole')
        close(row['supply_radius'], POLE_RADII[owned[unit]['name']])
        current = integer(row['network_id'], 1)
        network = current if network is None else network
        require(current == network, 'overlapping_or_split_power')
        edges[unit] = unit_list(row['neighbors'], 32); edge_count += len(edges[unit])
        require(unit not in edges[unit], 'self_connected_pole')
    require(edge_count <= 128, 'survey_bound')
    for unit, peers in edges.items():
        require(all(peer in edges and unit in edges[peer] for peer in peers), 'incomplete_copper_graph')
    connected(pole_rows, edges, next(iter(pole_rows)))

    electric = unique_rows(raw['electric_members'], 'unit', 2, 64)
    loads = defaultdict(list); generators = []; electric_buffer = 0
    for unit, row in electric.items():
        fields(row, 'unit network_id buffer_capacity energy drain operation')
        require(unit in owned and owned[unit]['name'] in RATES, 'foreign_electric_member')
        name = owned[unit]['name']; proto = prototypes[name]
        operating(row['operation'], supply=name == 'steam-engine')
        require(proto['source'] == 'electric' and type(row['network_id']) is int
                and row['network_id'] == network, 'unsupported_electric_member')
        capacity = number(row['buffer_capacity'], 0, 1_000_000)
        number(row['energy'], 0, capacity); close(row['drain'], proto['drain'])
        if name in witnesses:
            close(capacity, witnesses[name]['capacity'])
        electric_buffer += math.ceil(capacity)
        if name == 'steam-engine': generators.append(unit)
        else: loads[name].append(unit)
    require(1 <= len(generators) <= 2 and bool(loads), 'unsupported_generator_or_load_count')
    require(len(electric) <= 64 and sum(len(v) for v in loads.values()) <= 32, 'survey_bound')

    require(isinstance(expected_bundle, dict) and 2 <= len(expected_bundle) <= 4, 'invalid_expected_bundle')
    from .coal_supply import validate_commitment
    for target, saved in expected_bundle.items():
        validate_commitment(saved, target)
        require(saved['parts'] == {}, 'new_investment_requires_unpaid_bundle')
    sources = unique_rows(raw['sources'], 'target', 2, 4)
    require(set(sources) == set(expected_bundle), 'source_bundle_mismatch')
    areas = [row['mining_area'] for row in sources.values()]
    require(not any(overlaps(area, other) for i, area in enumerate(areas) for other in areas[i+1:]),
            'overlapping_source_claims')
    expected_coverage = {'unit:' + str(unit): owned[unit]['bounds'] for unit in electric}
    source_ore = {}
    for target, row in sources.items():
        fields(row, 'target target_unit layout mining_area steps corridor resources neighbor_drills drill_bounds chest_bounds')
        integer(row['target_unit'], 1)
        saved = expected_bundle[target]
        require(target in registry and row['target_unit'] == registry[target]['unit']
                and row['target_unit'] == saved['target']['unit_number'], 'source_target_identity_mismatch')
        for key in ('layout', 'mining_area', 'steps', 'corridor', 'drill_bounds', 'chest_bounds'):
            require(digest(row[key]) == digest(saved[key]), 'source_geometry_mismatch')
        for key in ('position', 'bounds', 'name'):
            require(digest(registry[target][key]) == digest(saved['target'][key]), 'source_target_geometry_mismatch')
        expected_coverage[target + ':drill'] = row['drill_bounds']
        for step in row['corridor']:
            if step['name'] == 'inserter':
                x, y = point(step['position'])
                expected_coverage[target + ':' + step['part']] = {
                    'left_top': {'x': x-.15, 'y': y-.15}, 'right_bottom': {'x': x+.15, 'y': y+.15}}
        resources = rows(row['resources'], 1, 64); positions = []; ore = 0; mining_ticks = set()
        area_a, area_b = bounds(row['mining_area'])
        for resource in resources:
            fields(resource, 'name position amount mining_time')
            require(resource['name'] == 'coal', 'unsupported_resource')
            p = point(resource['position']); positions.append(p)
            require(area_a[0] <= p[0] < area_b[0] and area_a[1] <= p[1] < area_b[1], 'resource_outside_source')
            ore += integer(resource['amount'], 1, 10_000_000)
            close(resource['mining_time'], 1); mining_ticks.add(60)
        require(positions == sorted(set(positions)), 'aliased_resource')
        for drill in rows(row['neighbor_drills'], 0, 64):
            fields(drill, 'unit mining_area'); integer(drill['unit'], 1)
            require(not overlaps(row['mining_area'], drill['mining_area']), 'foreign_source_drill')
        source_ore[target] = (ore, mining_ticks.pop())
    surveys = rows(raw['supply_surveys'], 1, 160); supplied = {}; covering = {}
    for survey in surveys:
        if survey.get('kind') == 'supply':
            fields(survey, 'kind key bounds members')
            require(survey['key'].isdigit() and str(int(survey['key'])) == survey['key'], 'invalid_supply_key')
            unit = int(survey['key']); require(unit in pole_rows and unit not in supplied, 'unknown_supply_pole')
            x, y = point(owned[unit]['position']); r = pole_rows[unit]['supply_radius']
            require(bounds(survey['bounds']) == ((x-r, y-r), (x+r, y+r)), 'supply_bounds_mismatch')
            supplied[unit] = unit_list(survey['members'], 64)
            require(supplied[unit] <= electric.keys(), 'foreign_supplied_member')
            expected = {u for u in electric if overlaps(survey['bounds'], owned[u]['bounds'])}
            require(supplied[unit] == expected, 'incomplete_supply_membership')
        else:
            fields(survey, 'kind key bounds poles')
            require(survey['kind'] == 'coverage' and survey['key'] in expected_coverage
                    and survey['key'] not in covering, 'unknown_coverage_target')
            require(digest(survey['bounds']) == digest(expected_coverage[survey['key']]), 'coverage_geometry_mismatch')
            covering[survey['key']] = unit_list(survey['poles'], 32)
            expected = set()
            for unit in pole_rows:
                x, y = point(owned[unit]['position']); r = pole_rows[unit]['supply_radius']
                square = {'left_top': {'x': x-r, 'y': y-r}, 'right_bottom': {'x': x+r, 'y': y+r}}
                if overlaps(square, survey['bounds']): expected.add(unit)
            require(bool(expected) and covering[survey['key']] == expected, 'foreign_or_incomplete_power_coverage')
    require(set(supplied) == set(pole_rows) and set(covering) == set(expected_coverage)
            and set().union(*supplied.values()) == set(electric), 'incomplete_power_surveys')

    fluid_members = unique_rows(raw['fluid_members'], 'unit', 3, 64)
    fluid_edges = defaultdict(set); boxes = {}; segments = defaultdict(list)
    pump_units = []; fluid_engines = []; boiler_units = []
    for unit, member in fluid_members.items():
        fields(member, 'unit boxes operation')
        require(unit in owned and owned[unit]['name'] in {'boiler', 'steam-engine', 'pipe', 'offshore-pump'},
                'unsupported_fluid_member')
        name = owned[unit]['name']
        if name == 'pipe':
            require(member['operation'] == {'kind': 'passive'}, 'invalid_passive_pipe_status')
        else:
            operating(member['operation'], supply=True)
            if unit in electric:
                require(digest(member['operation']) == digest(electric[unit]['operation']), 'operating_status_mismatch')
        if name == 'offshore-pump': pump_units.append(unit)
        elif name == 'steam-engine': fluid_engines.append(unit)
        elif name == 'boiler': boiler_units.append(unit)
        for box_row in rows(member['boxes'], 1, 8):
            fields(box_row, 'index segment_id capacity filter fluid amount temperature connections')
            index = integer(box_row['index'], 1, 8); key = (unit, index)
            require(key not in boxes, 'aliased_fluidbox'); boxes[key] = box_row
            segment = integer(box_row['segment_id'], 1)
            number(box_row['capacity'], 1, 100_000); number(box_row['amount'], 0, box_row['capacity'])
            require(box_row['filter'] in ('', 'water', 'steam') and box_row['fluid'] in ('', 'water', 'steam'), 'unsupported_fluid')
            number(box_row['temperature'], 15, 165)
            require((box_row['fluid'] == '') == (box_row['amount'] == 0), 'fluid_amount_mismatch')
            segments[segment].append((key, box_row))
    require(boiler_units == [boiler['unit']] and len(pump_units) == 1
            and set(fluid_engines) == set(generators), 'unsupported_steam_topology')
    links = 0
    for key, row in boxes.items():
        peers = set()
        for edge in rows(row['connections'], 0, 8):
            fields(edge, 'unit index'); peer = (integer(edge['unit'], 1), integer(edge['index'], 1, 8))
            require(peer in boxes and peer != key and peer not in peers, 'foreign_or_aliased_fluid_link')
            require(any(p['unit'] == key[0] and p['index'] == key[1] for p in boxes[peer]['connections']), 'asymmetric_fluid_link')
            require(row['segment_id'] == boxes[peer]['segment_id'], 'connected_fluid_segments_disagree')
            peers.add(peer); fluid_edges[key[0]].add(peer[0]); links += 1
        require(links <= 128, 'survey_bound')
    connected(fluid_members, fluid_edges, boiler['unit'])
    thermal_buffer = 0
    for segment, members in segments.items():
        kinds = {row['filter'] for _, row in members if row['filter']} | {row['fluid'] for _, row in members if row['fluid']}
        require(len(kinds) == 1, 'unclassified_or_mixed_fluid_segment')
        kind = kinds.pop(); capacities = [row['capacity'] for _, row in members]
        require(max(capacities) == min(capacities), 'segment_capacity_mismatch')
        if kind == 'water':
            require(all(row['temperature'] == 15 for _, row in members), 'unsupported_hot_water')
        else:
            # get_capacity is the whole segment capacity: charge it once.
            thermal_buffer += math.ceil(max(capacities) * 200 * (165 - 15))
        for (unit, index), row in members:
            name = owned[unit]['name']
            if name == 'steam-engine': require(kind == 'steam', 'generator_fluid_mismatch')
            if name == 'offshore-pump': require(kind == 'water', 'pump_fluid_mismatch')
    boiler_boxes = [row for (unit, _), row in boxes.items() if unit == boiler['unit']]
    require(len(boiler_boxes) == 2 and {row['filter'] for row in boiler_boxes} == {'water', 'steam'}, 'boiler_fluid_mismatch')

    fuel = unique_rows(raw['fuel_targets'], 'role', 2, 4)
    require(set(fuel) == set(sources) and 'utility:boiler' in fuel, 'fuel_target_set_mismatch')
    facts = []; boiler_fuel = None
    for role, row in fuel.items():
        fields(row, 'role unit name coal burning remaining_burning_fuel heat operation')
        integer(row['unit'], 1)
        operating(row['operation'], supply=row['name'] == 'boiler')
        if row['name'] == 'boiler':
            require(digest(row['operation']) == digest(fluid_members[row['unit']]['operation']), 'operating_status_mismatch')
        require(row['unit'] == registry[role]['unit'] and row['name'] == registry[role]['name']
                and row['name'] in {'boiler', 'stone-furnace'} and row['unit'] not in electric, 'fuel_target_identity_mismatch')
        coal = integer(row['coal'], 0, 200)
        remaining = number(row['remaining_burning_fuel'], 0, 4_000_000)
        require(row['burning'] in ('', 'coal') and (row['burning'] or remaining == 0), 'unsupported_burning_fuel')
        # Both native burner attributes are energy. Preserve rounding direction:
        # bootstrap uses the lower stock; a demand ceiling needs the upper stock.
        heat = number(row['heat'], 0, 4_000_000)
        stock_lower = coal * 4_000_000 + math.floor(remaining + heat)
        stock_upper = coal * 4_000_000 + math.ceil(remaining + heat)
        if role == 'utility:boiler': boiler_fuel = stock_lower
        ore, mining_ticks = source_ore[role]
        facts.append(SourceFacts(role, row['unit'], sources[role]['layout'], ore, mining_ticks,
                                 math.ceil(prototypes[row['name']]['max_usage']), stock_lower, stock_upper))
    planned_buffer = len(sources) * (math.ceil(witnesses['electric-mining-drill']['capacity'])
                                    + 2 * math.ceil(witnesses['inserter']['capacity']))
    total_buffer = electric_buffer + thermal_buffer + planned_buffer
    require(total_buffer <= 10_000_000_000, 'supported_buffer_bound')
    topology = {k: raw[k] for k in ('registry', 'buffer_witnesses', 'poles', 'supply_surveys', 'electric_members', 'fluid_members')}
    power = Power.parse({'network_id': network, 'boiler_role': 'utility:boiler', 'boiler_unit': boiler['unit'],
        'generator_units': sorted(generators), 'topology_sha256': digest(topology),
        'unit_qualification_sha256': digest(UNIT_QUALIFICATION), 'prototype_sha256': digest(raw['prototypes']),
        'coal_fuel_joules': 4_000_000, 'boiler_efficiency_ppm': 1_000_000, 'generator_efficiency_ppm': 1_000_000,
        'boiler_max_joules_per_tick': 30_000, 'generator_max_joules_per_tick': 15_000,
        'buffer_capacity_joules_upper': total_buffer, 'boiler_stored_fuel_joules': boiler_fuel,
        'existing_loads': [{'name': name, 'max_joules_per_tick': math.ceil(prototypes[name]['max_usage']),
                            'drain_joules_per_tick': math.ceil(prototypes[name]['drain']), 'units': sorted(units)}
                           for name, units in sorted(loads.items())],
        'drill_max_joules_per_tick': math.ceil(prototypes['electric-mining-drill']['max_usage']),
        'drill_drain_joules_per_tick': math.ceil(prototypes['electric-mining-drill']['drain']),
        'inserter_max_joules_per_tick': math.ceil(prototypes['inserter']['max_usage']),
        'inserter_drain_joules_per_tick': math.ceil(prototypes['inserter']['drain'])})
    def inventory(value):
        items = rows(value, 0, 128)
        names = []
        result = []
        for item in items:
            fields(item, 'name count')
            names.append(identity(item['name']))
            result.append((item['name'], integer(item['count'], 1, 200_000)))
        require(names == sorted(set(names)), 'material_inventory_alias')
        return tuple(result)

    scope = raw['material_scope']
    fields(scope, 'status reason closure_complete actor_unit actor_items crafting_queue owned_stock')
    require(scope['status'] == 'observed' and scope['reason'] == 'partial_actor_and_fuel_targets'
            and scope['closure_complete'] is False and scope['actor_unit'] == actor_unit,
            'invalid_material_scope')
    actor_items = inventory(scope['actor_items'])
    crafting_queue = integer(scope['crafting_queue'], 0, 1000)
    stock_rows = rows(scope['owned_stock'], 2, 4)
    stock = []
    for row in stock_rows:
        fields(row, 'role unit fuel output')
        role = identity(row['role']); unit = integer(row['unit'], 1)
        require(role in fuel and unit == fuel[role]['unit'], 'material_stock_owner_mismatch')
        fuel_items = inventory(row['fuel']); output_items = inventory(row['output'])
        require(dict(fuel_items).get('coal', 0) == fuel[role]['coal'], 'material_fuel_stock_mismatch')
        if fuel[role]['name'] == 'boiler':
            require(output_items == (), 'material_boiler_output_mismatch')
        stock.append(MaterialStockFacts(role, unit, fuel_items, output_items))
    require([row.role for row in stock] == sorted(fuel), 'material_stock_set_mismatch')
    material_scope = MaterialScopeFacts(actor_items, crafting_queue, tuple(stock))

    work = raw['research_work']
    fields(work, 'status reason technology progress unit_count cost_multiplier '
                 'ignore_cost_multiplier unit_energy ingredients lab targets')
    research_work = None
    if work['status'] == 'unavailable':
        require(work['lab'] == {} and work['targets'] in ([], {})
                and work['unit_count'] == 0 and work['cost_multiplier'] == 0
                and work['unit_energy'] == 0
                and work['ignore_cost_multiplier'] is False
                and work['ingredients'] in ([], {}),
                'invalid_research_absence')
        if work['reason'] == 'no_current_research':
            require(work['technology'] == '' and work['progress'] == 0,
                    'invalid_research_absence')
        else:
            require(work['reason'] == 'research_lab_unowned', 'invalid_research_absence')
            identity(work['technology']); number(work['progress'], 0, 1)
            require('utility:lab' not in registry
                    or registry['utility:lab']['name'] != 'lab',
                    'research_lab_identity_mismatch')
    else:
        require(work['status'] == 'observed' and work['reason'] == 'current_research_activity',
                'invalid_research_status')
        technology = identity(work['technology'])
        progress = number(work['progress'], 0, 1)
        unit_count = integer(work['unit_count'], 1, 1_000_000)
        cost_multiplier = number(work['cost_multiplier'], .001, 100_000)
        unit_energy = number(work['unit_energy'], .001, 100_000)
        require(type(work['ignore_cost_multiplier']) is bool, 'invalid_research_cost_setting')
        def bill(value):
            items = rows(value, 1, 8)
            names = []
            result = []
            for item in items:
                fields(item, 'name amount')
                names.append(identity(item['name']))
                result.append((item['name'], integer(item['amount'], 1, 1000)))
            require(names == sorted(set(names)), 'research_bill_alias')
            return tuple(result)
        ingredients = bill(work['ingredients'])
        fields(work['lab'], 'role unit input')
        lab = work['lab']
        lab_unit = integer(lab['unit'], 1)
        lab_members = {unit for load in power.existing_loads if load.name == 'lab'
                       for unit in load.units}
        require(lab['role'] == 'utility:lab' and 'utility:lab' in registry
                and registry['utility:lab']['unit'] == lab_unit
                and lab_unit in lab_members, 'research_lab_identity_mismatch')
        lab_input = inventory(lab['input'])
        target_rows = rows(work['targets'], 0, 3)
        roles = []
        parsed = []
        expected_furnaces = {role for role, row in fuel.items() if row['name'] == 'stone-furnace'}
        for row in target_rows:
            fields(row, 'role unit recipe crafting crafting_progress burning input '
                        'crafting_speed recipe_energy recipe_ingredients recipe_products')
            role = identity(row['role'])
            roles.append(role)
            unit = integer(row['unit'], 1)
            require(role in expected_furnaces and unit == registry[role]['unit']
                    and unit == fuel[role]['unit'], 'research_target_identity_mismatch')
            require(type(row['crafting']) is bool, 'invalid_research_crafting_state')
            crafting_progress = number(row['crafting_progress'], 0, 1)
            crafting_speed = number(row['crafting_speed'], .001, 1000)
            require(row['recipe'] == '' or isinstance(row['recipe'], str)
                    and len(row['recipe']) <= 128 and all(32 <= ord(c) <= 126 for c in row['recipe']),
                    'invalid_research_recipe')
            require(row['burning'] == fuel[role]['burning'] and row['burning'] in ('', 'coal'),
                    'research_burner_mismatch')
            if row['crafting']:
                # A started craft may be stalled; the API explicitly does not
                # attest that progress is advancing at this instant.
                require(row['recipe'] != '', 'research_activity_without_recipe')
            if row['recipe']:
                recipe_energy = number(row['recipe_energy'], .001, 100_000)
                recipe_ingredients = bill(row['recipe_ingredients'])
                recipe_products = bill(row['recipe_products'])
            else:
                require(row['recipe_energy'] == 0, 'research_recipe_energy_without_recipe')
                recipe_energy = 0
                require(row['recipe_ingredients'] in ([], {})
                        and row['recipe_products'] in ([], {}),
                        'research_recipe_bill_without_recipe')
                recipe_ingredients = recipe_products = ()
            parsed.append(ResearchTargetFacts(role, unit, row['recipe'], row['crafting'],
                                               crafting_progress, row['burning'], inventory(row['input']),
                                               recipe_ingredients, recipe_products,
                                               crafting_speed, recipe_energy))
        require(roles == sorted(expected_furnaces), 'research_target_set_mismatch')
        research_work = ResearchWorkFacts(technology, progress, unit_count,
                                          cost_multiplier, work['ignore_cost_multiplier'],
                                          ingredients, lab_unit, lab_input, tuple(parsed), unit_energy)
    manual = raw['manual_cycle']
    fields(manual, 'status reason journal_asset_sha256 gathers deliveries')
    manual_cycle = None
    if manual['status'] == 'unavailable':
        require(manual['reason'] == 'journal_not_installed'
                and manual['journal_asset_sha256'] == ''
                and manual['gathers'] in ([], {}) and manual['deliveries'] in ([], {})
                and expected_journal_asset_sha256 is None,
                'invalid_manual_absence')
    else:
        require(manual['status'] == 'observed' and manual['reason'] == 'qualified_journal_rows'
                and isinstance(expected_journal_asset_sha256, str)
                and len(expected_journal_asset_sha256) == 64
                and manual['journal_asset_sha256'] == expected_journal_asset_sha256,
                'manual_journal_source_mismatch')
        gather_rows = rows(manual['gathers'], 0, 64)
        delivery_rows = rows(manual['deliveries'], 0, 128)
        gathers = []
        seen = set()
        for row in gather_rows:
            fields(row, 'receipt started_tick finished_tick coal_before coal_after walking_ticks mining_ticks')
            receipt = identity(row['receipt'])
            require(receipt not in seen, 'manual_receipt_alias'); seen.add(receipt)
            start = integer(row['started_tick'], 0, epoch.tick)
            finish = integer(row['finished_tick'], start + 1, epoch.tick)
            before = integer(row['coal_before'], 0, 1_000_000)
            after = integer(row['coal_after'], before + 1, 1_000_000)
            walking = integer(row['walking_ticks'], 0, finish - start)
            mining = integer(row['mining_ticks'], 1, finish - start)
            require(after - before <= 200 and walking + mining <= finish - start,
                    'manual_gather_bound')
            gathers.append(ManualGatherFacts(receipt, start, finish, before, after,
                                             walking, mining))
        require(all(left.finished_tick <= right.started_tick
                    for left, right in zip(gathers, gathers[1:])),
                'manual_gather_order')
        deliveries = []
        seen = set()
        source_by_role = {source.target_role: source.target_unit for source in facts}
        for row in delivery_rows:
            fields(row, 'receipt role unit coal tick')
            receipt = identity(row['receipt'])
            require(receipt not in seen, 'manual_receipt_alias'); seen.add(receipt)
            role = identity(row['role'])
            unit = integer(row['unit'], 1)
            require(source_by_role.get(role) == unit, 'manual_delivery_owner_mismatch')
            deliveries.append(ManualDeliveryFacts(receipt, role, unit,
                                                   integer(row['coal'], 1, 200),
                                                   integer(row['tick'], 0, epoch.tick)))
        require(all(left.tick <= right.tick for left, right in zip(deliveries, deliveries[1:])),
                'manual_delivery_order')
        manual_cycle = ManualCycleFacts(expected_journal_asset_sha256,
                                        tuple(gathers), tuple(deliveries))
    return NativeEconomics(epoch, actor_unit, digest(expected_bundle), digest(raw), power,
                           tuple(facts), research_work=research_work, manual_cycle=manual_cycle,
                           material_scope=material_scope)
