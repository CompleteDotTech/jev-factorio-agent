"""Strict immutable arithmetic inputs for bounded coal investment forecasts.

The eventual native decoder must establish the cited topology, unit calibration
and receipt evidence. Parsing a digest does not authenticate that evidence or
authorize an actor mutation. This module is not wired into a controller.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

INPUT_SCHEMA = 'jev.coal-economic-input.v1'
PROOF_SCHEMA = 'jev.coal-economic-proof.v1'
POLICY = 'owned-steam-coal-v1'
MAX_TICKS = 216_000
MAX_INTEGER = 2**53 - 1
MILLION = 1_000_000
KIT_ITEMS = {'electric-mining-drill', 'wooden-chest', 'inserter', 'transport-belt'}
LOAD_NAMES = {'electric-mining-drill', 'inserter', 'assembling-machine-1',
              'assembling-machine-2', 'lab', 'offshore-pump'}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('Unexpected coal economic evidence fields')
    return value


def _integer(value, lower=0, upper=MAX_INTEGER):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('Invalid coal economic integer or bound')
    return value


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or any(not 32 <= ord(char) <= 126 for char in value)):
        raise ValueError('Invalid coal economic identity')
    return value


def _hash(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        raise ValueError('Invalid coal economic evidence digest')
    return value


def _rows(value, minimum, maximum):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError('Coal economic collection exceeds bounds')
    return value


@dataclass(frozen=True)
class Epoch:
    session_id: str
    tick: int
    actor_index: int
    surface_index: int
    force_index: int

    @classmethod
    def parse(cls, value):
        _fields(value, 'session_id tick actor_index surface_index force_index')
        return cls(_text(value['session_id']), _integer(value['tick']),
                   *(_integer(value[key], 1) for key in
                     ('actor_index', 'surface_index', 'force_index')))


@dataclass(frozen=True)
class Load:
    name: str
    max_joules_per_tick: int
    drain_joules_per_tick: int
    units: tuple[int, ...]

    @classmethod
    def parse(cls, value):
        _fields(value, 'name max_joules_per_tick drain_joules_per_tick units')
        if not isinstance(value['name'], str) or value['name'] not in LOAD_NAMES:
            raise ValueError('Unsupported electric load prototype')
        units = tuple(_integer(unit, 1) for unit in _rows(value['units'], 1, 64))
        if tuple(sorted(set(units))) != units:
            raise ValueError('Electric load units must be unique and sorted')
        return cls(value['name'], _integer(value['max_joules_per_tick'], 1, 10**9),
                   _integer(value['drain_joules_per_tick'], 0, 10**9), units)


@dataclass(frozen=True)
class Power:
    network_id: int
    boiler_role: str
    boiler_unit: int
    generator_units: tuple[int, ...]
    topology_sha256: str
    unit_qualification_sha256: str
    prototype_sha256: str
    coal_fuel_joules: int
    boiler_efficiency_ppm: int
    generator_efficiency_ppm: int
    boiler_max_joules_per_tick: int
    generator_max_joules_per_tick: int
    buffer_capacity_joules_upper: int
    boiler_stored_fuel_joules: int
    existing_loads: tuple[Load, ...]
    drill_max_joules_per_tick: int
    drill_drain_joules_per_tick: int
    inserter_max_joules_per_tick: int
    inserter_drain_joules_per_tick: int

    @classmethod
    def parse(cls, value):
        _fields(value, 'network_id boiler_role boiler_unit generator_units topology_sha256 '
                'unit_qualification_sha256 prototype_sha256 coal_fuel_joules '
                'boiler_efficiency_ppm generator_efficiency_ppm boiler_max_joules_per_tick '
                'generator_max_joules_per_tick buffer_capacity_joules_upper '
                'boiler_stored_fuel_joules existing_loads drill_max_joules_per_tick '
                'drill_drain_joules_per_tick inserter_max_joules_per_tick inserter_drain_joules_per_tick')
        boiler = _integer(value['boiler_unit'], 1)
        generators = tuple(_integer(unit, 1) for unit in _rows(value['generator_units'], 1, 2))
        loads = tuple(Load.parse(row) for row in _rows(value['existing_loads'], 1, 16))
        identities = [boiler, *generators, *[unit for row in loads for unit in row.units]]
        if len(set(identities)) != len(identities) or generators != tuple(sorted(generators)):
            raise ValueError('Power entities are aliased or unordered')
        if len({row.name for row in loads}) != len(loads):
            raise ValueError('Duplicate load prototype group')
        return cls(_integer(value['network_id'], 1), _text(value['boiler_role']), boiler, generators,
            *(_hash(value[key]) for key in ('topology_sha256', 'unit_qualification_sha256', 'prototype_sha256')),
            _integer(value['coal_fuel_joules'], 1, 10**12),
            _integer(value['boiler_efficiency_ppm'], 1, MILLION),
            _integer(value['generator_efficiency_ppm'], 1, MILLION),
            _integer(value['boiler_max_joules_per_tick'], 1, 10**9),
            _integer(value['generator_max_joules_per_tick'], 1, 10**9),
            _integer(value['buffer_capacity_joules_upper'], 1, 10**12),
            _integer(value['boiler_stored_fuel_joules'], 0, 10**12), loads,
            _integer(value['drill_max_joules_per_tick'], 1, 10**9),
            _integer(value['drill_drain_joules_per_tick'], 0, 10**9),
            _integer(value['inserter_max_joules_per_tick'], 1, 10**9),
            _integer(value['inserter_drain_joules_per_tick'], 0, 10**9))


@dataclass(frozen=True)
class Source:
    target_role: str
    target_unit: int
    layout: str
    remaining_ore: int
    mining_speed_ppm: int
    ore_mining_ticks: int
    consumer_max_joules_per_tick: int
    consumer_stored_fuel_joules: int
    baseline_coal_demand_forecast: int
    demand_basis: str
    demand_evidence_sha256: str

    @classmethod
    def parse(cls, value):
        _fields(value, 'target_role target_unit layout remaining_ore mining_speed_ppm '
                'ore_mining_ticks consumer_max_joules_per_tick consumer_stored_fuel_joules '
                'baseline_coal_demand_forecast demand_basis demand_evidence_sha256')
        if (not isinstance(value['demand_basis'], str) or value['demand_basis'] not in
                {'current_required_recipe_forecast', 'observed_burn_projection'}):
            raise ValueError('Unsupported coal demand basis')
        return cls(_text(value['target_role']), _integer(value['target_unit'], 1),
            _text(value['layout']), _integer(value['remaining_ore'], 0, 10**9),
            _integer(value['mining_speed_ppm'], 1, 10 * MILLION),
            _integer(value['ore_mining_ticks'], 1, 3600),
            _integer(value['consumer_max_joules_per_tick'], 1, 10**9),
            _integer(value['consumer_stored_fuel_joules'], 0, 10**12),
            _integer(value['baseline_coal_demand_forecast'], 0, 10_000),
            value['demand_basis'], _hash(value['demand_evidence_sha256']))


@dataclass(frozen=True)
class Delivery:
    target_role: str
    target_unit: int
    coal: int

    @classmethod
    def parse(cls, value):
        _fields(value, 'target_role target_unit coal')
        return cls(_text(value['target_role']), _integer(value['target_unit'], 1),
                   _integer(value['coal'], 1, 200))


@dataclass(frozen=True)
class ManualCycle:
    first_tick: int
    last_tick: int
    coal_delivered: int
    actor_ticks: int
    deliveries: tuple[Delivery, ...]
    receipts_sha256: str
    bundle_sha256: str

    @classmethod
    def parse(cls, value, tick):
        _fields(value, 'first_tick last_tick coal_delivered actor_ticks deliveries receipts_sha256 bundle_sha256')
        first = _integer(value['first_tick'], max(0, tick - MAX_TICKS), tick)
        last = _integer(value['last_tick'], first + 1, tick)
        deliveries = tuple(Delivery.parse(row) for row in _rows(value['deliveries'], 2, 4))
        total = _integer(value['coal_delivered'], 1, 200)
        if sum(row.coal for row in deliveries) != total:
            raise ValueError('Manual cycle total differs from per-consumer receipts')
        return cls(first, last, total,
                   _integer(value['actor_ticks'], 1, last - first),
                   deliveries,
                   _hash(value['receipts_sha256']), _hash(value['bundle_sha256']))


@dataclass(frozen=True)
class EconomicInput:
    epoch: Epoch
    bundle_sha256: str
    catalog_sha256: str
    horizon_ticks: int
    sources: tuple[Source, ...]
    kit: tuple[tuple[str, int], ...]
    acquisition_actor_ticks_forecast: int
    acquisition_evidence_sha256: str
    construction_walk_tiles_forecast: int
    recurring_service_actor_ticks_forecast: int
    power: Power
    manual_cycle: ManualCycle

    @classmethod
    def parse(cls, value):
        _fields(value, 'schema policy base_version mods epoch bundle_sha256 catalog_sha256 '
                'horizon_ticks sources kit acquisition_actor_ticks_forecast acquisition_evidence_sha256 '
                'construction_walk_tiles_forecast recurring_service_actor_ticks_forecast power manual_cycle')
        if (value['schema'] != INPUT_SCHEMA or value['policy'] != POLICY
                or value['base_version'] != '2.0.77' or value['mods'] != {'base': '2.0.77'}):
            raise ValueError('Unsupported coal economics protocol or native treatment')
        epoch = Epoch.parse(value['epoch'])
        sources = tuple(Source.parse(row) for row in _rows(value['sources'], 2, 4))
        if (len({row.target_unit for row in sources}) != len(sources)
                or len({row.target_role for row in sources}) != len(sources)
                or tuple(row.target_role for row in sources) != tuple(sorted(row.target_role for row in sources))):
            raise ValueError('Coal consumer identities are aliased or unordered')
        kit = value['kit']
        if not isinstance(kit, dict) or set(kit) != KIT_ITEMS:
            raise ValueError('Incomplete supported paid kit')
        kit = tuple((key, _integer(count, 1, 200)) for key, count in sorted(kit.items()))
        counts = dict(kit)
        if (counts['electric-mining-drill'] != len(sources) or counts['wooden-chest'] != len(sources)
                or counts['inserter'] != 2 * len(sources)
                or not len(sources) <= counts['transport-belt'] <= 24 * len(sources)):
            raise ValueError('Coal kit differs from the bounded whole bundle')
        power = Power.parse(value['power'])
        boiler = [row for row in sources if row.target_role == power.boiler_role]
        if len(boiler) != 1 or boiler[0].target_unit != power.boiler_unit:
            raise ValueError('Power fuel must have exactly one internal owned target')
        if (boiler[0].consumer_stored_fuel_joules != power.boiler_stored_fuel_joules
                or boiler[0].consumer_max_joules_per_tick != power.boiler_max_joules_per_tick):
            raise ValueError('Boiler source and power facts disagree')
        electric_units = set(power.generator_units) | {unit for row in power.existing_loads for unit in row.units}
        if any(row.target_unit in electric_units for row in sources):
            raise ValueError('Coal fuel consumer aliases an electric load or generator')
        manual = ManualCycle.parse(value['manual_cycle'], epoch.tick)
        if manual.bundle_sha256 != value['bundle_sha256']:
            raise ValueError('Manual comparison belongs to a different bundle')
        if (tuple((row.target_role, row.target_unit) for row in manual.deliveries)
                != tuple((row.target_role, row.target_unit) for row in sources)):
            raise ValueError('Manual cycle must account for each exact source target')
        return cls(epoch, _hash(value['bundle_sha256']), _hash(value['catalog_sha256']),
            _integer(value['horizon_ticks'], 600, MAX_TICKS), sources, kit,
            _integer(value['acquisition_actor_ticks_forecast'], 0, MAX_TICKS),
            _hash(value['acquisition_evidence_sha256']),
            _integer(value['construction_walk_tiles_forecast'], 0, 10_000),
            _integer(value['recurring_service_actor_ticks_forecast'], 0, MAX_TICKS), power, manual)

    def to_dict(self):
        value = asdict(self)
        value.update(schema=INPUT_SCHEMA, policy=POLICY, base_version='2.0.77', mods={'base': '2.0.77'})
        value['kit'] = dict(self.kit)
        return json.loads(json.dumps(value, allow_nan=False))


def validate_proof(value: dict) -> dict:
    """Recompute arithmetic/binding; this never authenticates native evidence."""
    from .coal_economics import evaluate
    _fields(value, 'schema policy input input_sha256 eligible reason terms '
            'native_payback_proven mutation_authorized proof_sha256')
    expected = evaluate(value['input'])
    if value != expected or digest(value) != digest(expected):
        raise ValueError('Coal economics proof differs from canonical input and calculation')
    return expected
