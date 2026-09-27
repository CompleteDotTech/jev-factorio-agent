"""Strict decoder for the negotiated, single-command native observation v2.

No inventory, receipt or actor control survives across freshness boundaries.
Discovery is advisory: returned coordinates never grant native action permission.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

from ..observation import parse_snapshot
from ..state import GameSnapshot

RAW_ITEMS = frozenset({'wood', 'coal', 'iron-ore', 'copper-ore', 'stone'})
BOUNDS = {'anchor_radius': 256, 'anchor_limit': 129,
          'bootstrap_radius': 1000, 'bootstrap_limit': 129,
          'bootstrap_output_radius': .15, 'bootstrap_output_limit': 2}


def _map(value: Any, label: str, limit: int = 4096) -> dict:
    if value == []:
        value = {}
    if not isinstance(value, dict) or len(value) > limit:
        raise ValueError(f'Invalid atomic {label}')
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise ValueError(f'Invalid atomic {label}')
    return value


def _position(value: Any) -> tuple[float, float]:
    if (not isinstance(value, dict) or set(value) != {'x', 'y'}
            or any(type(v) not in {int, float} or not math.isfinite(v)
                   or abs(v) > 1_000_000 for v in value.values())):
        raise ValueError('Invalid atomic position')
    return value['x'], value['y']


def _inventory(value: Any) -> dict[str, int]:
    result = _map(value, 'inventory')
    for item, quantity in result.items():
        if not isinstance(item, str) or not item or len(item) > 128:
            raise ValueError('Invalid atomic inventory')
        _integer(quantity, 'inventory')
    return dict(result)


def _capacity(value: Any) -> dict:
    result = _map(value, 'capacity')
    for item, quantity in result.items():
        if not isinstance(item, str) or not item or len(item) > 128:
            raise ValueError('Invalid atomic capacity item')
        _integer(quantity, 'capacity')
    return dict(result)


def _actor_capacity(value: Any, tick: int) -> dict | None:
    """Accept only a fresh, explicitly identified main-inventory coal reading."""
    if value is False:
        return None
    if (not isinstance(value, dict)
            or set(value) != {'schema', 'tick', 'inventory', 'quality', 'method', 'items'}
            or type(value['schema']) is not int or value['schema'] != 1
            or type(value['tick']) is not int or value['tick'] != tick
            or value['inventory'] != 'character_main' or value['quality'] != 'normal'
            or value['method'] != 'get_insertable_count'
            or not isinstance(value['items'], dict) or set(value['items']) != {'coal'}):
        raise ValueError('Invalid atomic inventory capacity')
    count = value['items']['coal']
    if type(count) is not int or not 0 <= count <= 2**32 - 1:
        raise ValueError('Invalid atomic inventory capacity')
    return {**value, 'items': {'coal': count}}


def observe_atomic(native: Any, snapshot: GameSnapshot) -> GameSnapshot:
    """Read and validate one v2 snapshot before exposing any of its native facts."""
    from fle.env import Position

    backend = native.backend
    prior = getattr(native, '_coherent_identity', None)
    prior_drill = getattr(native, '_coherent_drill', None)
    # A just-built fair bootstrap drill is also identity-bound, when available.
    if prior_drill is None and getattr(backend._drill, 'unit_number', None) is not None:
        prior_drill = _integer(backend._drill.unit_number, 'bootstrap identity', 1)
    result = parse_snapshot(native.call('observation_snapshot_v2', native._discovery_epoch,
                                        prior_drill), backend._observation_profile, schemas=(2,))
    session = result.get('session_id')
    if not isinstance(session, str) or not session or len(session) > 128:
        raise ValueError('Invalid atomic session identity')
    identity = (session, *(_integer(result.get(k), 'identity', 1)
                           for k in ('actor_unit', 'surface_index', 'force_index')))
    if prior is not None and prior != identity:
        raise ValueError('Atomic observation identity changed')
    if snapshot.session_id and snapshot.session_id != session:
        raise ValueError('Atomic snapshot session changed')
    tick = _integer(result.get('tick'), 'tick')
    if tick < max(snapshot.tick, getattr(native, '_coherent_tick', 0)):
        raise ValueError('Atomic observation tick regressed')
    position = _position(result.get('position'))
    inventory = _inventory(result.get('inventory'))
    capacity = _actor_capacity(result.get('inventory_capacity', False), tick)
    controls = result.get('controls')
    if (not isinstance(controls, dict) or type(controls.get('tick')) is not int
            or controls['tick'] != tick or _position(controls.get('position')) != position
            or not isinstance(controls.get('status'), str) or len(controls['status']) > 64
            or any(type(controls.get(k)) is not bool
                   for k in ('walking', 'mining', 'movement_started'))):
        raise ValueError('Invalid atomic control boundary')
    for field in ('path_requests', 'gained'):
        # Mining gains may be negative while a machine/player spends material;
        # no positive gain or action completion is inferred by this decoder.
        if field == 'gained':
            if type(controls.get(field)) is not int or abs(controls[field]) > 2**53 - 1:
                raise ValueError('Invalid atomic controls')
        else:
            _integer(controls.get(field), 'controls')
    factory = _map(result.get('factory'), 'factory')
    runtime = factory.get('acceptance_runtime')
    if (not isinstance(runtime, dict) or runtime.get('schema') != 1
            or type(runtime.get('schema')) is not int
            or factory.get('player_bound') is not True
            or factory.get('player_connected') is not True
            or type(factory.get('tick')) is not int or factory['tick'] != tick
            or runtime.get('session_id') != session
            or type(runtime.get('speed')) not in {int, float} or runtime['speed'] != 1
            or runtime.get('tick_paused') is not False):
        raise ValueError('Invalid atomic runtime binding')
    for key in ('actor_unit', 'surface_index', 'force_index'):
        if type(runtime.get(key)) is not int or runtime[key] != result[key]:
            raise ValueError('Atomic runtime identity changed')
    for key in ('entities', 'receipts'):
        factory[key] = _map(factory.get(key), key)
    # A capability wrapper cannot supply or preserve actor headroom. Only the
    # current top-level reading above can publish it after full validation.
    factory.pop('inventory_insertable', None)
    factory.pop('inventory_insertable_evidence', None)
    for entity in factory['entities'].values():
        if isinstance(entity, dict) and 'fuel_insertable' in entity:
            entity['fuel_insertable'] = _capacity(entity['fuel_insertable'])
    researched = factory.get('researched')
    if (not isinstance(researched, list) or len(researched) > 4096
            or any(not isinstance(v, str) or not v or len(v) > 128 for v in researched)):
        raise ValueError('Invalid atomic research state')
    launched = _integer(factory.get('rockets_launched'), 'launch counter')
    baseline = _integer(factory.get('rocket_baseline'), 'launch baseline')
    if launched < baseline:
        raise ValueError('Atomic launch counter regressed')
    bounds = result.get('bounds')
    if (not isinstance(bounds, dict) or bounds != BOUNDS
            or any(type(value) is not type(BOUNDS[key]) for key, value in bounds.items())):
        raise ValueError('Invalid atomic query bounds')
    bootstrap = result.get('bootstrap')
    if (not isinstance(bootstrap, dict) or type(bootstrap.get('query_limit')) is not int
            or bootstrap['query_limit'] != 129
            or type(bootstrap.get('output_connected')) is not bool):
        raise ValueError('Invalid atomic bootstrap')
    placed = bootstrap.get('placed_entities')
    if (not isinstance(placed, list) or len(placed) > 128
            or any(name not in {'burner-mining-drill', 'wooden-chest'} for name in placed)):
        raise ValueError('Invalid atomic bootstrap entities')
    collected = _integer(bootstrap.get('iron_ore_collected'), 'bootstrap stock')
    drill_data = bootstrap.get('drill')
    drill = None
    if drill_data is not False:
        if (not isinstance(drill_data, dict) or drill_data.get('name') != 'burner-mining-drill'
                or not isinstance(drill_data.get('status'), str)
                or not drill_data['status'] or len(drill_data['status']) > 64):
            raise ValueError('Invalid atomic bootstrap drill')
        unit = _integer(drill_data.get('unit_number'), 'bootstrap unit', 1)
        if prior_drill is not None and unit != prior_drill:
            raise ValueError('Atomic bootstrap identity changed')
        drill = SimpleNamespace(name='burner-mining-drill', unit_number=unit,
                position=Position(x=_position(drill_data.get('position'))[0],
                                  y=_position(drill_data.get('position'))[1]),
                drop_position=Position(x=_position(drill_data.get('drop_position'))[0],
                                       y=_position(drill_data.get('drop_position'))[1]),
                status=SimpleNamespace(value=drill_data['status']),
                fuel=_inventory(drill_data.get('fuel')))
        if 'burner-mining-drill' not in placed:
            raise ValueError('Invalid atomic bootstrap membership')
    elif prior_drill is not None:
        raise ValueError('Atomic bootstrap drill missing')
    elif bootstrap['output_connected'] or collected:
        raise ValueError('Atomic bootstrap output without producer')
    if bootstrap['output_connected'] and 'wooden-chest' not in placed:
        raise ValueError('Atomic bootstrap chest missing')
    resources, nearby, targets = {}, {}, {}
    for group, allowed in (('targets', RAW_ITEMS), ('anchors', {'water', 'crude-oil'})):
        observed = _map(result.get(group), 'discovery', len(allowed))
        if set(observed) - allowed:
            raise ValueError('Invalid atomic discovery target')
        for item, value in observed.items():
            if (not isinstance(value, dict) or not isinstance(value.get('name'), str)
                    or not value['name'] or len(value['name']) > 128
                    or (item not in {'wood', 'water'} and value['name'] != item)
                    or (item == 'water' and value['name'] not in {'water', 'deepwater'})
                    or type(value.get('surface_index')) is not int
                    or value['surface_index'] != result['surface_index']):
                raise ValueError('Invalid atomic discovery identity')
            x, y = _position(value.get('position'))
            resources[item] = Position(x=x, y=y)
            nearby[item] = math.hypot(x - position[0], y - position[1])
            if group == 'anchors' and nearby[item] > BOUNDS['anchor_radius'] + 2:
                raise ValueError('Atomic anchor outside query bounds')
            if group == 'targets':
                targets[item] = {k: value[k] for k in ('name', 'position', 'surface_index')}
    counts = result.get('cache')
    if (not isinstance(counts, dict) or set(counts) != {'hits', 'misses'}
            or any(type(v) is not int or not 0 <= v <= 5 for v in counts.values())
            or sum(counts.values()) != 5):
        raise ValueError('Invalid atomic discovery diagnostics')
    # Validation has completed. Only now publish this observation and advisory
    # caches; malformed payloads cannot overwrite a previously coherent view.
    if capacity is not None:
        factory['inventory_insertable'] = dict(capacity['items'])
        factory['inventory_insertable_evidence'] = {
            **capacity, 'session_id': session,
            **{key: result[key] for key in ('actor_unit', 'surface_index', 'force_index')},
            'basis': 'native_insertable_count_estimate',
        }
    factory['fair_resource_targets'] = targets
    factory['observation_snapshot_schema'] = 2
    factory['observation_query_bounds'] = dict(bounds)
    if drill:
        for role, entity in factory['entities'].items():
            if (isinstance(entity, dict) and entity.get('name') == 'wooden-chest'
                    and isinstance(entity.get('position'), dict)):
                point = _position(entity['position'])
                if math.hypot(point[0] - drill.drop_position.x, point[1] - drill.drop_position.y) <= .1:
                    factory['drill_output_role'] = role
                    break
    snapshot.tick, snapshot.session_id, snapshot.world_kind = tick, session, 'fle'
    snapshot.player_position, snapshot.inventory = position, inventory
    snapshot.placed_entities, snapshot.nearby_resources = list(placed), nearby
    snapshot.drill_status = drill.status.value if drill else ''
    snapshot.drill_fuel = drill.fuel.get('coal', 0) if drill else 0
    snapshot.drill_output_connected = bootstrap['output_connected']
    snapshot.iron_ore_collected = collected
    snapshot.factory, snapshot.game_version = factory, native.catalog.version
    snapshot.researched, snapshot.victory = researched, launched > baseline
    snapshot.victory_source = 'native:base-game-rocket-launch' if snapshot.victory else None
    snapshot._native_controls = controls
    snapshot._coherent_observation_verified = (session, tick)
    backend._resources, backend._drill = resources, drill
    native._coherent_identity, native._coherent_tick = identity, tick
    native._coherent_drill = drill.unit_number if drill else None
    for name, value in counts.items():
        backend._observation_profile.cache[name] += value
    return snapshot
