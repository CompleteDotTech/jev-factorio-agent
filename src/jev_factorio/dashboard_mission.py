"""Bounded display summaries of recorded facts. Never authorize or evaluate actions.

No gameplay imports, network calls, predicates, checkpoint writes or forecasts.
Missing, stale, contradictory and truncated evidence must not become readiness.
"""
from __future__ import annotations

import math
from itertools import islice

PAYLOADS = ('raw-fish', 'satellite')
SILO = 'recipe:rocket-part'


def mapping(value):
    return value if isinstance(value, dict) else {}


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53 - 1


def label(value):
    return value[:160] if isinstance(value, str) else None


def flag(value):
    return value if type(value) is bool else None


def gate(key, title, state='unknown', detail='Not captured'):
    return {'key': key, 'title': title, 'state': state, 'detail': detail}


def launch_summary(state: dict) -> dict:
    factory = mapping(state.get('factory'))
    raw = factory.get('launch_readiness')
    row = mapping(raw)
    gates = [gate(k, title) for k, title in (
        ('contract', 'Version / session'), ('pad', 'Landing pad'),
        ('payload', 'Owned payload'), ('rocket', 'Rocket ready'),
        ('cargo', 'Cargo / destination'), ('request', 'Launch request'), ('victory', 'Native victory'))]
    by_id = {g['key']: g for g in gates}
    def put(key, status, detail): by_id[key].update(state=status, detail=detail)
    world = state.get('world_kind')
    victory = (world == 'fle' and state.get('victory') is True
               and state.get('victory_source') == 'native:base-game-rocket-launch')
    if victory:
        put('victory', 'observed', 'Native victory flag and source recorded; not an acceptance certificate')
    elif state.get('victory') is True:
        put('victory', 'unknown', 'Reported victory is not native launch evidence')
    elif state.get('victory') is False:
        put('victory', 'pending', 'Not observed in this snapshot')
    valid = (type(row.get('schema')) is int and row['schema'] == 1
             and row.get('supported') is True and row.get('version') == '2.0.77'
             and isinstance(state.get('session_id'), str) and bool(state['session_id'])
             and row.get('session_id') == state['session_id']
             and integer(state.get('tick')) and type(row.get('tick')) is int and row['tick'] == state['tick']
             and all(integer(row.get(k), 1) for k in ('actor_unit', 'surface_index', 'force_index'))
             and type(row.get('fault')) is bool
             and all(isinstance(row.get(k), dict) for k in ('pad', 'silo', 'attempts', 'receipts')))
    basis = 'Recorded snapshot only; never a launch command or deployment approval.'
    result = {'schema': 1, 'tick': state.get('tick') if integer(state.get('tick')) else None,
              'session_id': label(state.get('session_id')), 'gates': gates,
              'headline': 'Native victory observed' if victory else 'Launch evidence unavailable',
              'evidence_valid': valid, 'basis': basis, 'fault': None}
    if not valid:
        if row.get('supported') is False:
            put('contract', 'blocked', 'Unsupported version or mod profile')
        elif raw is not None:
            put('contract', 'unknown', 'Incomplete, malformed or mismatched tick/session evidence')
        return result
    put('contract', 'observed', 'Base 2.0.77; matching observation identity')
    result['fault'] = row['fault']
    if row['fault']:
        result['headline'] = 'Native victory observed' if victory else 'Launch evidence fault'
        put('contract', 'blocked', 'Native observer reported a fault; reconcile before action')
        return result
    pad, silo = row['pad'], row['silo']
    accepts = mapping(pad.get('accepts'))
    pad_valid = (pad.get('name') == 'cargo-landing-pad' and integer(pad.get('unit_number'), 1)
                 and all(type(accepts.get(k)) is bool for k in PAYLOADS))
    put('pad', 'observed' if pad_valid else 'pending' if not pad else 'unknown',
        f"Unit {pad['unit_number']} observed" if pad_valid else 'No pad observed' if not pad else 'Incomplete pad identity/capacity')
    entity = mapping(mapping(factory.get('entities')).get(SILO))
    silo_valid = (entity.get('name') == 'rocket-silo' and integer(entity.get('unit_number'), 1)
                  and type(silo.get('unit_number')) is int and silo['unit_number'] == entity['unit_number']
                  and integer(silo.get('rocket_unit')) and isinstance(silo.get('cargo'), dict)
                  and all(type(silo.get(k)) is bool for k in ('ready', 'automatic', 'cargo_available')))
    cargo = mapping(silo.get('cargo')) if silo_valid else {}
    loaded = next((k for k in PAYLOADS if len(cargo) == 1 and type(cargo.get(k)) is int and cargo[k] == 1), None)
    inventory = state.get('inventory')
    carried = next((k for k in PAYLOADS if integer(mapping(inventory).get(k), 1)), None)
    if loaded:
        put('payload', 'observed', f'{loaded}: one in cargo')
    elif carried:
        put('payload', 'observed', f'{carried}: carried; reservation not independently captured')
    elif isinstance(inventory, dict):
        put('payload', 'pending', 'No carried or loaded payload observed')
    if silo_valid:
        put('rocket', 'blocked' if silo['automatic'] else 'observed' if silo['ready'] and silo['rocket_unit'] > 0 else 'pending',
            'Automatic launch enabled; viewer does not change it' if silo['automatic'] else
            f"Rocket {silo['rocket_unit']} ready" if silo['ready'] and silo['rocket_unit'] > 0 else 'Rocket not ready')
        if cargo and not loaded:
            put('cargo', 'blocked', 'Unexpected or multiple cargo; do not silently clear')
        elif loaded and pad_valid:
            put('cargo', 'observed' if accepts[loaded] and silo['cargo_available'] else 'blocked',
                'One payload and destination capacity observed' if accepts[loaded] and silo['cargo_available'] else
                'Cargo unavailable or destination lacks full capacity')
        else:
            put('cargo', 'pending', 'Payload not loaded or destination not verified')
    elif not silo:
        put('rocket', 'pending', 'No silo observed')
    receipt = mapping(row['receipts'].get('launch'))
    submitted = (receipt.get('kind') == 'launch' and receipt.get('session_id') == state['session_id']
                 and type(receipt.get('actor_unit')) is int and receipt['actor_unit'] == row['actor_unit']
                 and integer(receipt.get('tick')) and receipt['tick'] <= state['tick']
                 and integer(receipt.get('silo_unit'), 1) and integer(receipt.get('rocket_unit'), 1)
                 and (not silo_valid or receipt['silo_unit'] == silo['unit_number']))
    if submitted:
        put('request', 'submitted', f"Receipt at tick {receipt['tick']}; submitted is not victory")
    elif row['attempts'].get('launch'):
        put('request', 'blocked', 'Unresolved intent; reconcile, never replay automatically')
    elif receipt:
        put('request', 'unknown', 'Receipt identity does not match this observation')
    else:
        put('request', 'pending', 'Not submitted in captured evidence')
    if not victory:
        result['headline'] = ('Launch submitted; victory unverified' if submitted else
                              'Launch prerequisites blocked' if any(g['state'] == 'blocked' for g in gates) else
                              'Launch prerequisites observed' if all(by_id[k]['state'] == 'observed'
                                  for k in ('pad', 'payload', 'rocket', 'cargo')) else 'Launch preparation')
    return result


def project_state(state: dict) -> dict:
    """Summarize before sanitization so a large factory cannot hide key gates."""
    factory = mapping(state.get('factory'))
    progress = factory.get('research_progress')
    if type(progress) not in (int, float) or not 0 <= progress <= 1 or not math.isfinite(progress):
        progress = None
    automation = []
    for family in ('input_routes', 'production_sites', 'successors'):
        table = mapping(factory.get(family))
        sources = mapping(table.get('sources'))
        diagnostics = mapping(table.get('diagnostics'))
        # Explicit small window, not claimed to be a complete factory inventory.
        for role in islice(dict.fromkeys((*islice(sources, 6), *islice(diagnostics, 6))), 6):
            source, diagnostic = mapping(sources.get(role)), mapping(diagnostics.get(role))
            automation.append({'family': family, 'role': label(role),
                'state': label(source.get('phase') or source.get('state')),
                'reason': label(diagnostic.get('reason') or source.get('reason')),
                'survey_tick': diagnostic.get('survey_tick') if integer(diagnostic.get('survey_tick')) else None,
                'cached': flag(diagnostic.get('cached'))})
    return {'schema': 1, 'launch': launch_summary(state),
            'research': {'name': label(factory.get('research')), 'progress': progress},
            'automation': automation, 'automation_scope': 'At most six roles per capability; absent rows are unknown.'}


def project_record(record: dict) -> dict:
    revision = mapping(record.get('code_revision'))
    config = mapping(record.get('acceptance_configuration'))
    budgets = record.get('failure_budgets')
    valid_budgets = isinstance(budgets, dict) and len(budgets) <= 256 and all(integer(v) for v in budgets.values())
    planning = mapping(record.get('planning_diagnostics'))
    result = {'schema': 1, 'tick': record.get('tick') if integer(record.get('tick')) else None,
        'recorded_at_utc': label(record.get('recorded_at_utc')),
        'commit': label(revision.get('commit')), 'source_sha256': label(revision.get('source_sha256')),
        'features': {k: flag(config.get(k, record.get(k))) for k in
            ('background_work', 'furnace_output_buffers', 'furnace_input_belts', 'mining_outposts', 'ore_side_successors')},
        'failure_count': sum(budgets.values()) if valid_budgets else None,
        'failure_scope': 'Retained plan-budget counts, not unique errors or retry authority.',
        'planning_boundary': label(planning.get('boundary')),
        'generated_count': len(planning['generated_plan_ids']) if isinstance(planning.get('generated_plan_ids'), list) else None,
        'ranked_count': len(planning['ranked_plan_ids']) if isinstance(planning.get('ranked_plan_ids'), list) else None,
        'merge_status': 'Not supplied by gameplay', 'deployment_status': 'Not established by source merge',
        'native_acceptance': 'No acceptance report connected'}
    return result
