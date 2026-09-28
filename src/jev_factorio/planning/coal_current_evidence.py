"""Current, unpaid coal economics inputs; no admission or payment authority.

This deliberately prices acquisition at zero only when the *entire* kit is
already carried and unreserved. A forecast cannot turn collectible machine
output, queued crafting, or another project's hold into paid stock.
"""
from __future__ import annotations

from collections import Counter

from .. import coal_supply, solid_routes
from ..coal_economic_observation import NativeEconomics
from ..memory import CampaignMemory
from ..telemetry import validate_attempt
from . import coal_funding, solid_funding
from .coal_economic_proof import KIT_ITEMS, digest
from .demand import SupplyLedger


def prepaid_kit(snapshot, catalog, memory: CampaignMemory) -> dict:
    """Bind a whole unstarted kit to one current native snapshot and catalog."""
    if (not isinstance(memory, CampaignMemory) or memory.session_id != snapshot.session_id
            or memory.status != 'running' or memory.pending is not None
            or memory.active_plan is not None or memory.attempt is not None
            or memory.transfer_recovery is not None
            or memory.capital_investment is not None
            or getattr(memory, 'background_job', None) is not None
            or getattr(memory, 'background_attempt', None) is not None
            or getattr(memory, 'solid_funding', None) is not None
            or getattr(memory, 'coal_funding', None) is not None
            or not isinstance(memory.reservations, dict)
            or not isinstance(getattr(memory, 'solid_commitments', None), dict)
            or not isinstance(getattr(memory, 'coal_commitments', None), dict)
            or memory.coal_commitments):
        raise ValueError('Coal kit evidence requires a reconciled idle checkpoint')
    if (snapshot.factory.get('coal_supply', {}).get('protocol') != 2
            or snapshot.factory['coal_supply'].get('admission', {}).get('qualified') is not False):
        raise ValueError('Coal kit evidence requires the closed native treatment')
    rows = coal_funding.proposal(snapshot)
    connector = snapshot.factory.get('connector_ownership')
    if (not isinstance(connector, dict) or connector.get('protocol') != 1
            or connector.get('session_id') != snapshot.session_id
            or connector.get('tick') != snapshot.tick or connector.get('active') is not None
            or not isinstance(connector.get('routes'), (dict, list))):
        raise ValueError('Coal kit has unresolved connector work')
    connector_routes = connector['routes'] if connector['routes'] != [] else {}
    if (not isinstance(connector_routes, dict)
            or any(not isinstance(route, dict) or route.get('state') != 'complete'
                   or route.get('owned') is not True or route.get('pending') is not None
                   for route in connector_routes.values())
            or any(row['pending'] for row in solid_routes.routes(snapshot).values())):
        raise ValueError('Coal kit has unresolved route work')
    if set(rows) != set(snapshot.factory['coal_supply']['targets']):
        raise ValueError('Coal kit evidence lacks the whole unpaid bundle')
    kit = coal_supply.remaining_kit(rows, snapshot)
    if not isinstance(kit, dict) or set(kit) != KIT_ITEMS:
        raise ValueError('Coal kit evidence has an unsupported bill')
    reserved = Counter()
    for held in memory.reservations.values():
        if not isinstance(held, dict):
            raise ValueError('Coal kit checkpoint reservations are invalid')
        reserved.update(held)
    for saved in memory.solid_commitments.values():
        reserved.update(solid_routes.remaining(saved))
    ledger = SupplyLedger.capture(snapshot, catalog, reserved=dict(reserved))
    carried = {}
    for name, count in kit.items():
        if type(count) is not int or not 1 <= count <= 200:
            raise ValueError('Coal kit evidence has an invalid quantity')
        available = ledger.carried.get(name, 0)
        if type(available) not in (int, float) or available < count:
            raise ValueError('Coal kit is not fully carried and unreserved')
        carried[name] = count
    catalog_sha256 = solid_funding.bill_catalog_digest(kit, snapshot, catalog)
    basis = {'schema': 'jev.coal-prepaid-kit.v1', 'session_id': snapshot.session_id,
             'tick': snapshot.tick, 'bundle': coal_funding.bundle(rows),
             'kit': kit, 'carried_unreserved': carried,
             'checkpoint_reservations': dict(reserved),
             'catalog_sha256': catalog_sha256}
    return {'kit': kit, 'catalog_sha256': catalog_sha256,
            'acquisition_actor_ticks_forecast': 0,
            'acquisition_evidence_sha256': digest(basis),
            'basis': basis, 'mutation_authorized': False}


def verified_coal_deliveries(native: NativeEconomics, snapshot, memory: CampaignMemory,
                             receipt_ids: list[str]) -> dict:
    """Join each target's exact native transfer receipt to a verified attempt.

    This proves deliveries only. A complete manual cycle additionally needs a
    native gather receipt and a conservative actor-work lower bound.
    """
    if (type(native) is not NativeEconomics or not isinstance(memory, CampaignMemory)
            or memory.session_id != snapshot.session_id != native.epoch.session_id
            or snapshot.tick != native.epoch.tick or native.mutation_authorized is not False
            or not isinstance(receipt_ids, list) or len(receipt_ids) != len(native.sources)
            or any(not isinstance(value, str) or not 1 <= len(value) <= 128
                   for value in receipt_ids)
            or len(set(receipt_ids)) != len(receipt_ids)
            or not isinstance(memory.attempt_outcomes, list)
            or len(memory.attempt_outcomes) > 64):
        raise ValueError('Coal manual delivery owner or receipt set is unbound')
    receipts = snapshot.factory.get('receipts')
    if receipts == []:
        receipts = {}
    if not isinstance(receipts, dict):
        raise ValueError('Native transfer receipts are unavailable')
    selected = []
    used_attempts = set()
    for source, receipt_id in zip(native.sources, receipt_ids):
        row = receipts.get(receipt_id)
        if (not isinstance(row, dict) or set(row) != {'role', 'item', 'quantity',
                'unit_number', 'extracting', 'tick'}
                or row['role'] != source.target_role or row['item'] != 'coal'
                or row['unit_number'] != source.target_unit or row['extracting'] is not False
                or type(row['quantity']) is not int or not 1 <= row['quantity'] <= 200
                or type(row['tick']) is not int or not 0 <= row['tick'] <= snapshot.tick):
            raise ValueError('Coal delivery native receipt differs from target')
        matches = [attempt for attempt in memory.attempt_outcomes
                   if attempt.get('receipt') == receipt_id]
        if len(matches) != 1:
            raise ValueError('Coal delivery lacks one verified controller attempt')
        attempt = matches[0]
        validate_attempt(attempt, finished=True)
        if (attempt['id'] in used_attempts or attempt['action'] != 'factory_insert'
                or attempt['outcome'] != 'verified'
                or attempt['expected_unit_number'] != source.target_unit
                or not attempt['started_tick'] <= row['tick'] <= attempt['finished_tick'] <= snapshot.tick):
            raise ValueError('Coal delivery attempt does not bind its native receipt')
        used_attempts.add(attempt['id'])
        selected.append({'receipt': receipt_id, 'target_role': source.target_role,
                         'target_unit': source.target_unit, 'coal': row['quantity'],
                         'native_tick': row['tick'], 'attempt_id': attempt['id'],
                         'started_tick': attempt['started_tick'],
                         'finished_tick': attempt['finished_tick']})
    if [row['native_tick'] for row in selected] != sorted(row['native_tick'] for row in selected):
        raise ValueError('Coal delivery receipts are not ordered')
    basis = {'schema': 'jev.coal-manual-deliveries.v1', 'session_id': snapshot.session_id,
             'tick': snapshot.tick, 'bundle_sha256': native.bundle_sha256,
             'deliveries': selected}
    return {'basis': basis, 'receipts_sha256': digest(basis),
            'cycle_complete': False, 'mutation_authorized': False}
