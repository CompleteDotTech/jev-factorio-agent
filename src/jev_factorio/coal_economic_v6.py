"""Fixed v6 native query prototype; no admission or native installation authority.

The v6 source is composed deterministically from the reviewed v5 graph query
and an additional bounded material census at its success boundary. A returned
dictionary remains diagnostic until an exact source/engine readback qualifies
the whole command and a separate economic/payment proof is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import hashlib

from .coal_economic_observation import (
    NativeEconomics, NativeEconomicsUnavailable, ROOT_KEYS, decode, fields,
    identity, integer, require, rows,
)
from .planning.coal_economic_proof import digest

SCHEMA = 'jev.coal-native-economics.v6'
MARKER = '    out.query_status="observed";out.reason="bounded_native_projection"'
EXTRA_KEY = 'material_census'
BASE_SOURCE_SHA256 = '6ca65021645de249ec203fe2891a7318657e97de00033a88d8665d044cb89a06'
CENSUS_SOURCE_SHA256 = '9e299fd494108d1ddfc3696b203d22dd965278196ce33145ca8b15fe1fe7c00e'
CYCLE_SOURCE_SHA256 = '956fe0e80b04c91f2d0c0eaf4e897999d5c4d5d53d55b20abdc6c0dcc593cbc7'
V5_PROFILE = 'e759-observation-v2-water-origin-v4-manual-cycle-v5'
V6_PROFILE = 'e759-observation-v2-water-origin-v4-manual-cycle-v6'


def fixed_query() -> str:
    root = files('jev_factorio').joinpath('lua')
    base = root.joinpath('coal_economics.lua').read_text()
    census = root.joinpath('coal_material_census_v6.lua').read_text()
    cycle = root.joinpath('coal_manual_cycle_v2.lua').read_bytes()
    if (hashlib.sha256(base.encode('utf-8')).hexdigest() != BASE_SOURCE_SHA256
            or hashlib.sha256(census.encode('utf-8')).hexdigest() != CENSUS_SOURCE_SHA256
            or hashlib.sha256(cycle).hexdigest() != CYCLE_SOURCE_SHA256
            or base.count(MARKER) != 1
            or base.count('jev.coal-native-economics.v5') != 1
            or base.count(V5_PROFILE) != 1
            or census.count('__CYCLE_ASSET_SHA256__') != 1):
        raise RuntimeError('Fixed native graph query changed before v6 composition')
    census = census.replace('__CYCLE_ASSET_SHA256__', CYCLE_SOURCE_SHA256)
    return (base.replace('jev.coal-native-economics.v5', SCHEMA, 1)
            .replace(V5_PROFILE, V6_PROFILE, 1)
            .replace(MARKER, census + '\n' + MARKER, 1))


def query_sha256() -> str:
    return hashlib.sha256(fixed_query().encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class StockRow:
    role: str
    unit: int
    name: str
    inventories: tuple[tuple[int, tuple[tuple[str, int], ...]], ...]
    belts: tuple[tuple[int, tuple[tuple[str, int], ...]], ...]
    held: tuple[tuple[str, int], ...]
    fuel: tuple[tuple[str, int], ...]
    output: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Census:
    actor_items: tuple[tuple[str, int], ...]
    actor_inventories: tuple[tuple[int, tuple[tuple[str, int], ...]], ...]
    entities: tuple[StockRow, ...]
    owned_surface_coverage_complete: bool


@dataclass(frozen=True)
class CycleDelivery:
    receipt: str
    role: str
    unit: int
    coal: int
    tick: int
    started_tick: int
    finished_tick: int
    walking_ticks: int


@dataclass(frozen=True)
class CycleRow:
    receipt: str
    gather_receipt: str
    started_tick: int
    finished_tick: int
    gathered_coal: int
    delivered_coal: int
    gather_mining_ticks: int
    deliveries: tuple[CycleDelivery, ...]


@dataclass(frozen=True)
class V6Observation:
    graph: NativeEconomics
    census: Census
    cycles: tuple[CycleRow, ...]
    raw_sha256: str
    native_payback_proven: bool = False
    mutation_authorized: bool = False


def _items(value):
    result = []
    for item in rows(value, 0, 128):
        fields(item, 'name count')
        result.append((identity(item['name']), integer(item['count'], 1, 200_000)))
    require([name for name, _ in result] == sorted({name for name, _ in result}),
            'material_census_item_alias')
    return tuple(result)


def decode_v6(raw: dict, *, expected_epoch: dict, expected_bundle: dict,
              unit_qualification: dict, expected_connectors=None,
              expected_routes=None, expected_journal_asset_sha256=None) -> V6Observation:
    """Reject incomplete/foreign census rows; never infer economic authority."""
    require(type(raw) is dict and set(raw) == ROOT_KEYS | {EXTRA_KEY, 'cycle_v2'},
            'invalid_v6_native_fields')
    require(raw['schema'] == SCHEMA and raw['query_status'] == 'observed',
            'v6_native_projection_unsupported')
    core = {key: raw[key] for key in ROOT_KEYS}
    core['schema'] = 'jev.coal-native-economics.v5'
    graph = decode(core, expected_epoch=expected_epoch,
                   expected_bundle=expected_bundle,
                   unit_qualification=unit_qualification,
                   expected_connectors=expected_connectors,
                   expected_routes=expected_routes,
                   expected_journal_asset_sha256=expected_journal_asset_sha256)
    value = raw['material_census']
    fields(value, 'status reason owned_surface_coverage_complete actor_unit cycle_asset_sha256 '
                  'actor_items actor_inventories entities ground_items trees crafting_queue')
    require(value['status'] == 'observed'
            and value['reason'] == 'bounded_owned_surface'
            and value['owned_surface_coverage_complete'] is True
            and value['actor_unit'] == graph.actor_unit
            and value['cycle_asset_sha256'] == CYCLE_SOURCE_SHA256
            and value['ground_items'] == 0 and value['trees'] == 0
            and value['crafting_queue'] == 0,
            'material_census_incomplete')
    actor_items = _items(value['actor_items'])
    require(actor_items == graph.material_scope.actor_items,
            'material_census_actor_mismatch')
    actor_inventories = []
    for entry in rows(value['actor_inventories'], 1, 128):
        fields(entry, 'index items')
        actor_inventories.append((integer(entry['index'], 1, 128), _items(entry['items'])))
    require([index for index, _ in actor_inventories]
            == sorted({index for index, _ in actor_inventories}),
            'material_census_actor_inventory_alias')
    expected = {row['role']: (row['unit'], row['name'])
                for row in raw['registry']}
    target_stock = {row.role: row for row in graph.material_scope.owned_stock}
    parsed = []
    for row in rows(value['entities'], len(expected), len(expected)):
        fields(row, 'role unit name inventories belts held fuel output')
        role = identity(row['role'])
        unit = integer(row['unit'], 1)
        name = identity(row['name'])
        require(expected.get(role) == (unit, name),
                'material_census_owner_mismatch')
        inventories = []
        for entry in rows(row['inventories'], 0, 128):
            fields(entry, 'index items')
            inventories.append((integer(entry['index'], 1, 128), _items(entry['items'])))
        require([index for index, _ in inventories] == sorted({index for index, _ in inventories}),
                'material_census_inventory_alias')
        belts = []
        for entry in rows(row['belts'], 0, 2):
            fields(entry, 'lane items')
            belts.append((integer(entry['lane'], 1, 2), _items(entry['items'])))
        require(([lane for lane, _ in belts] == [1, 2]) == (name == 'transport-belt')
                and (name == 'transport-belt' or not belts),
                'material_census_belt_mismatch')
        held = _items(row['held'])
        require(name == 'inserter' or not held,
                'material_census_hand_mismatch')
        fuel = _items(row['fuel']); output = _items(row['output'])
        if role in target_stock:
            require((fuel, output) == (target_stock[role].fuel, target_stock[role].output),
                    'material_census_target_mismatch')
        parsed.append(StockRow(role, unit, name, tuple(inventories),
                               tuple(belts), held, fuel, output))
    require([row.role for row in parsed] == sorted(expected),
            'material_census_set_mismatch')
    cycle = raw['cycle_v2']
    fields(cycle, 'status journal_asset_sha256 cycle_complete rows')
    require(cycle['status'] == 'observed'
            and cycle['journal_asset_sha256'] == CYCLE_SOURCE_SHA256
            and cycle['cycle_complete'] is False
            and graph.manual_cycle is not None,
            'cycle_v2_unqualified')
    gathers = {row.receipt: row for row in graph.manual_cycle.gathers}
    transfers = {row.receipt: row for row in graph.manual_cycle.deliveries}
    targets = tuple((row.target_role, row.target_unit) for row in graph.sources)
    cycle_rows = []
    seen = set()
    for row in rows(cycle['rows'], 0, 32):
        fields(row, 'receipt gather_receipt started_tick finished_tick '
                    'gathered_coal delivered_coal gather_mining_ticks deliveries')
        receipt = identity(row['receipt'])
        gather_receipt = identity(row['gather_receipt'])
        require(receipt not in seen and gather_receipt not in seen,
                'cycle_v2_receipt_alias')
        seen.update((receipt, gather_receipt))
        gather = gathers.get(gather_receipt)
        require(gather is not None, 'cycle_v2_gather_mismatch')
        start = integer(row['started_tick'], 0, graph.epoch.tick)
        finish = integer(row['finished_tick'], start + 1, graph.epoch.tick)
        require(start <= gather.started_tick < gather.finished_tick <= finish
                and gather.coal_before == 0
                and row['gathered_coal'] == gather.coal_after
                and row['gather_mining_ticks'] == gather.mining_ticks,
                'cycle_v2_gather_mismatch')
        deliveries = []
        previous = gather.finished_tick
        for item in rows(row['deliveries'], len(targets), len(targets)):
            fields(item, 'receipt role unit coal tick started_tick finished_tick walking_ticks')
            transfer_receipt = identity(item['receipt'])
            require(transfer_receipt not in seen, 'cycle_v2_receipt_alias')
            seen.add(transfer_receipt)
            role = identity(item['role'])
            unit = integer(item['unit'], 1)
            coal = integer(item['coal'], 1, 200)
            tick = integer(item['tick'], gather.finished_tick, finish)
            begin = integer(item['started_tick'], previous, finish)
            end = integer(item['finished_tick'], begin, finish)
            walking = integer(item['walking_ticks'], 0, end - begin)
            require(begin <= tick <= end, 'cycle_v2_delivery_interval')
            native = transfers.get(transfer_receipt)
            require(native is not None and (native.role, native.unit, native.coal, native.tick)
                    == (role, unit, coal, tick), 'cycle_v2_delivery_mismatch')
            deliveries.append(CycleDelivery(transfer_receipt, role, unit,
                                            coal, tick, begin, end, walking))
            previous = end
        require(tuple((d.role, d.unit) for d in deliveries) == targets
                and sum(d.coal for d in deliveries) == row['delivered_coal']
                and 0 < row['delivered_coal'] <= gather.coal_after,
                'cycle_v2_conservation')
        cycle_rows.append(CycleRow(receipt, gather_receipt, start, finish,
                                   gather.coal_after, row['delivered_coal'],
                                   gather.mining_ticks, tuple(deliveries)))
    require(all(left.finished_tick <= right.started_tick
                for left, right in zip(cycle_rows, cycle_rows[1:])),
            'cycle_v2_overlap')
    # This is a positive *inventory coverage* claim only. Open-world resource
    # substitutes, future goal commitment, complete manual work and same-RPC
    # first payment remain unsupported.
    return V6Observation(graph, Census(actor_items, tuple(actor_inventories),
                                       tuple(parsed), True), tuple(cycle_rows),
                         digest(raw))
