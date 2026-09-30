"""Bounded paid kit acquisition for an explicitly configured coal bundle.

This is not autonomous coal-network adoption or a profitability model. Geometry,
source availability and power must already satisfy the observed coal contract.
Only current owned stock and enabled deterministic hand recipes can fund it.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..state import GameSnapshot
    from ..skills import Step
    from .catalog import Catalog

from .. import coal_supply as coal, solid_routes as solid
from ..skills import Plan
from . import solid_funding as funding
from .demand import SupplyLedger
from .coal_supply import source_project
from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE
from .service_policy import position

MARKER = 'coal_kit'
MAX_ACTIONS = funding.MAX_ACTIONS
MAX_TICKS = funding.MAX_TICKS
STATE_FIELDS = {'schema', 'key', 'bundle', 'kit', 'held', 'catalog_sha256',
                'started_tick', 'deadline_tick', 'actions'}
MARKER_FIELDS = {'schema', 'key', 'bundle_sha256', 'catalog_sha256', 'observed_tick'}


def project_key(targets: list) -> str:
    """Same targets retain their budget across order, layout and unit changes."""
    coal.validate_targets(targets)
    return 'coal-kit:' + funding.digest(sorted(targets))


def failure_count(plan: Plan, failures: dict) -> int:
    step = plan.steps[0]
    p = step.parameters or {}
    ordinary = f"factory:{step.action}:{p.get('role', p.get('recipe', ''))}"
    return failures.get(plan.id, 0) + failures.get(ordinary, 0)


def build_budget_exhausted(rows: dict, failures: dict) -> bool:
    """A new kit identity cannot buy fresh source/corridor failure budgets."""
    for target, row in rows.items():
        if any(failures.get(source_project(target, part), 0) >= 2 for part in coal.PARTS):
            return True
        stub = {'source': {'role': coal.role(target, 'chest')}, 'target': row['target'], 'item': 'coal'}
        key = funding.project_key(stub)
        parts = ['receive', 'send', *[f'belt:{i}' for i in range(1, solid.MAX_BELTS + 1)]]
        if any(failures.get(f'{key}:{part}', 0) >= 2 for part in parts):
            return True
    return False


def _hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _quantities(value: object, upper: dict | None = None) -> bool:
    return (isinstance(value, dict) and len(value) <= 32
            and all(isinstance(k, str) and 0 < len(k) <= 128
                    and solid.integer(v, 1, (upper or {}).get(k, 200))
                    and (upper is None or k in upper) for k, v in value.items()))


def validate_state(state: dict, last_tick: int, targets: list) -> None:
    if not isinstance(state, dict) or set(state) != STATE_FIELDS:
        raise ValueError('Invalid coal funding checkpoint fields')
    if (not solid.integer(state['schema'], 1, 1) or state['key'] != project_key(targets)
            or not isinstance(state['bundle'], dict) or set(state['bundle']) != set(targets)
            or not state['kit'] or not _quantities(state['kit'])
            or not _quantities(state['held'], state['kit']) or not _hash(state['catalog_sha256'])
            or not solid.integer(state['started_tick'], 0, last_tick)
            or not solid.integer(state['deadline_tick'], state['started_tick'] + 1,
                                  state['started_tick'] + MAX_TICKS)
            or not solid.integer(state['actions'], 1, MAX_ACTIONS)):
        raise ValueError('Invalid coal funding checkpoint binding or bounds')
    kit, units, layouts, areas = Counter(), set(), set(), []
    for target, saved in state['bundle'].items():
        coal.validate_commitment(saved, target)
        if saved['parts'] or saved['target']['unit_number'] in units:
            raise ValueError('Coal funding may not adopt paid or aliased sources')
        area = coal._bounds(saved['mining_area'])
        if any(coal._overlap(area, old) for old in areas):
            raise ValueError('Coal funding bundle shares a mining area')
        areas.append(area)
        units.add(saved['target']['unit_number'])
        layouts.add(saved['layout'])
        kit.update(spec['name'] for spec in saved['steps'])
        kit.update(spec['name'] for spec in saved['corridor'])
    if len(layouts) != 1 or dict(kit) != state['kit']:
        raise ValueError('Coal funding bill differs from its complete bundle')


def proposal(snapshot: GameSnapshot) -> dict:
    rows = coal.sources(snapshot)
    if (snapshot.factory['coal_supply']['committed'] or not solid.integer(snapshot.factory.get('crafting_queue'), 0, 0)
            or snapshot.factory.get('player_connected') is not True
            or snapshot.factory.get('player_bound') is not True
            or any(not coal.current(row, snapshot) or row['state'] != 'proposed'
                   or row['parts'] or row['pending'] or row['manual_pending']
                   or row['remaining'] < coal.MIN_ORE or not row['power']['energized']
                   for row in rows.values())):
        raise ValueError('Coal funding requires a fresh unstarted powered bundle')
    boiler = snapshot.factory.get('entities', {}).get('utility:boiler', {})
    if boiler and boiler.get('fuel', {}).get('coal', 0) < 5:
        raise ValueError('Urgent boiler fuel takes precedence over optional funding')
    if any(row['pending'] for row in solid.routes(snapshot).values()):
        raise ValueError('Pending transport work must reconcile before kit funding')
    return rows


def project_setup_cost_estimate(snapshot: GameSnapshot, acquisition: dict) -> dict:
    """Estimate full initial kit and placement work for economic forecasting.

    Acquisition is the existing deterministic funding planner's fresh estimate.
    Construction service/travel use the documented serial Manhattan scheduling
    policy. These are forecasts, not measured upper bounds or native payback.
    """
    if not isinstance(acquisition, dict):
        raise ValueError('Coal project needs a fresh acquisition forecast')
    if acquisition.get('reason') == 'complete_carried_kit':
        acquisition_ticks = 0
        acquisition_actions = 0
    else:
        acquisition_ticks = acquisition.get('acquisition_game_ticks_estimate')
        acquisition_actions = acquisition.get('acquisition_actions_estimate')
        if (not solid.integer(acquisition_ticks, 0, MAX_TICKS)
                or not solid.integer(acquisition_actions, 0, MAX_ACTIONS)):
            raise ValueError('Coal project acquisition forecast is incomplete')

    actor = position(snapshot.player_position)
    if actor is None:
        raise ValueError('Coal project travel forecast lacks current actor position')
    rows = proposal(snapshot)
    locations = []
    coal_source_roles = {coal.role(target, 'chest') for target in rows}
    for target, row in sorted(rows.items()):
        for spec in row['steps']:
            if spec['part'] not in row['parts']:
                locations.append(solid.point(spec['position']))
        route = coal.route_for(row, snapshot)
        if route is None:
            corridor_steps, corridor_parts = row['corridor'], {}
        else:
            corridor_steps, corridor_parts = route['steps'], route['parts']
        for spec in corridor_steps:
            if spec['part'] not in corridor_parts:
                locations.append(solid.point(spec['position']))
    # Match the native whole-kit bill: it also includes any unfinished
    # non-coal transport cells already registered in the same campaign.
    for _, route in sorted(solid.routes(snapshot).items(),
                           key=lambda item: (item[1]['source']['role'], item[0])):
        if route['source']['role'] in coal_source_roles:
            continue
        for spec in route['steps']:
            if spec['part'] not in route['parts']:
                locations.append(solid.point(spec['position']))
    if not locations or len(locations) > 512:
        raise ValueError('Coal project placement scope is empty or unbounded')

    distance = 0
    previous = actor
    for location in locations:
        distance += abs(previous[0] - location[0]) + abs(previous[1] - location[1])
        previous = location
        if distance > 2**31:
            raise ValueError('Coal project travel forecast overflow')
    service_ticks = len(locations) * SERVICE_TICKS
    travel_ticks = math.ceil(distance * TRAVEL_TICKS_PER_TILE)
    total_ticks = acquisition_ticks + service_ticks + travel_ticks
    if not 0 < total_ticks <= MAX_TICKS:
        raise ValueError('Coal project setup forecast exceeds the supported horizon')
    return {
        'schema': 'jev.coal-project-setup-cost-estimate.v1',
        'acquisition_ticks_estimate': acquisition_ticks,
        'acquisition_actions_estimate': acquisition_actions,
        'placement_count': len(locations),
        'placement_service_ticks_estimate': service_ticks,
        'placement_manhattan_distance_tiles_estimate': distance,
        'placement_travel_ticks_estimate': travel_ticks,
        'total_setup_ticks_estimate': total_ticks,
        'basis': 'deterministic_funding_plus_serial_manhattan_service_policy',
        'measured': False,
        'native_payback_proven': False,
        'mutation_authorized': False,
    }


def bundle(rows: dict) -> dict:
    return {target: coal.commitment(row) for target, row in rows.items()}


def bound(state: dict, rows: dict) -> bool:
    return state['bundle'] == bundle(rows)


def spendable_reservations(reserved: dict | None, state: dict | None) -> dict:
    """Remove this project's own actual holds, never another project's stock."""
    result = Counter(reserved or {})
    if state:
        result.subtract(state['held'])
    if any(value < 0 for value in result.values()):
        raise ValueError('Coal funding holds missing from reservation accounting')
    return {key: value for key, value in result.items() if value}


def candidate(snapshot: GameSnapshot, catalog: Catalog, *, reserved=None, job=None,
              failures=None, state=None, capital=None, other_funding=None,
              goal="rocket_launch", successor_projects=None) -> tuple[Plan | None, dict]:
    if any(project.get('status') != 'qualified' for project in (successor_projects or {}).values()):
        raise ValueError('An unfinished successor project owns the investment lane')
    rows = proposal(snapshot)
    targets = snapshot.factory['coal_supply']['targets']
    key = project_key(targets)
    failures = failures or {}
    if (job is not None or capital is not None or other_funding is not None
            or failures.get(key, 0) >= 2 or build_budget_exhausted(rows, failures)):
        raise ValueError('Coal funding is locked or its stable budget is exhausted')
    kit = coal.remaining_kit(rows, snapshot)
    if state:
        validate_state(state, snapshot.tick, targets)
        if (not bound(state, rows) or snapshot.tick >= state['deadline_tick']
                or state['catalog_sha256'] != funding.bill_catalog_digest(kit, snapshot, catalog)):
            raise ValueError('Coal funding binding, catalog or deadline changed')
    external = spendable_reservations(reserved, state)
    plan, estimates, _ = funding._acquire_bill(
        kit, key, snapshot, catalog, reserved=external, job=job, failures=failures,
        budget_check=failure_count, protect_final_stock=True)
    if plan is None:
        return None, estimates
    if (plan.steps[0].action == 'factory_extract'
            and plan.steps[0].parameters['receipt'] in snapshot.factory.get('receipts', {})):
        raise ValueError('An existing transfer receipt cannot fund a new kit action')
    if state and state['actions'] >= MAX_ACTIONS:
        raise ValueError('Coal funding action budget exhausted')
    marker = {'schema': 1, 'key': key, 'bundle_sha256': funding.digest(bundle(rows)),
              'catalog_sha256': estimates['catalog_sha256'], 'observed_tick': snapshot.tick}
    costs = {**estimates, 'kit_components': dict(kit),
             'admission_basis': 'explicit_immutable_coal_bundle_not_autonomous_payback',
             'native_flow_proven': False, 'avoided_haul_ticks': None, 'net_coal_return': None}
    return replace(plan, goal=goal, description='Acquire the next paid coal-network kit prerequisite',
                   materials={MARKER: marker, 'coal_kit_cost': costs}), costs


def held_stock(snapshot: GameSnapshot, catalog: Catalog, kit: dict, reserved=None, job=None) -> dict:
    ledger = SupplyLedger.capture(snapshot, catalog, reserved=reserved, job=job)
    return {item: int(min(amount, ledger.carried.get(item, 0))) for item, amount in kit.items()
            if min(amount, ledger.carried.get(item, 0)) > 0}


def start(plan: Plan, snapshot: GameSnapshot, catalog: Catalog, reserved=None, job=None) -> dict:
    rows = proposal(snapshot)
    kit = coal.remaining_kit(rows, snapshot)
    return {'schema': 1, 'key': project_key(snapshot.factory['coal_supply']['targets']),
            'bundle': bundle(rows), 'kit': kit,
            'held': held_stock(snapshot, catalog, kit, reserved, job),
            'catalog_sha256': plan.materials[MARKER]['catalog_sha256'],
            'started_tick': snapshot.tick, 'deadline_tick': snapshot.tick + MAX_TICKS, 'actions': 1}


def validate_active(state: dict, plan: Plan, last_tick: int, goal: str) -> None:
    marker = (plan.materials or {}).get(MARKER)
    if (not state or not isinstance(marker, dict) or set(marker) != MARKER_FIELDS
            or not solid.integer(marker['schema'], 1, 1) or marker['key'] != state['key']
            or marker['bundle_sha256'] != funding.digest(state['bundle'])
            or marker['catalog_sha256'] != state['catalog_sha256']
            or not solid.integer(marker['observed_tick'], state['started_tick'], last_tick)
            or plan.id != state['key'] or plan.goal != goal
            or len(plan.steps) != 1 or plan.steps[0].action not in {'factory_craft', 'factory_extract'}):
        raise ValueError('Coal funding and its active plan disagree')


def fresh_permission(plan: Plan, step: Step, snapshot: GameSnapshot, catalog: Catalog, **options) -> bool:
    try:
        state = options.get('state')
        marker = (plan.materials or {}).get(MARKER)
        if (not isinstance(marker, dict) or set(marker) != MARKER_FIELDS
                or not solid.integer(marker['observed_tick'], 0, snapshot.tick)
                or len(plan.steps) != 1 or step != plan.steps[0]):
            return False
        if state:
            validate_active(state, plan, snapshot.tick, options.get("goal", "rocket_launch"))
        # The selected next action is already charged to the action budget.
        check_state = deepcopy(state)
        if check_state and check_state['actions'] == MAX_ACTIONS:
            check_state['actions'] -= 1
        current, _ = candidate(snapshot, catalog, **{**options, 'state': check_state})
        if (current is None or current.id != plan.id or current.goal != plan.goal
                or (plan.materials or {}).get("coal_kit_cost") != current.materials["coal_kit_cost"]):
            return False
        expected = {**current.materials[MARKER], 'observed_tick': marker['observed_tick']}
        if marker != expected:
            return False
        a, b = asdict(step), asdict(current.steps[0])
        if step.action == 'factory_extract':
            p = step.parameters
            receipt = f"{marker['observed_tick']}:factory_extract:{p['role']}:{p['item']}"
            if p['receipt'] != receipt or p['receipt'] in snapshot.factory.get('receipts', {}):
                return False
            a['parameters'].pop('receipt')
            b['parameters'].pop('receipt')
        return a == b
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False


def ranking_marker(plan: Plan, snapshot: GameSnapshot) -> bool:
    """Recognize only this decision's canonical, opt-in coal-kit offer."""
    marker = (plan.materials or {}).get(MARKER)
    approved = getattr(snapshot, '_coal_kit_annotations', {})
    return (isinstance(marker, dict) and isinstance(approved, dict)
            and plan.to_dict() == approved.get(plan.id)
            and marker.get('schema') == 1 and marker.get('observed_tick') == snapshot.tick
            and plan.id == marker.get('key') and len(plan.steps) == 1
            and plan.steps[0].action in {'factory_extract', 'factory_craft'})
