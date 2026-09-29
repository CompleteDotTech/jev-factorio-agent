"""Private one-use v5 to v6 journal migration candidate; no implicit install."""
from __future__ import annotations

import hashlib
import json
import os
from importlib.resources import files
from pathlib import Path

from ..connector_checkpoint import validate_binding
from ..iteration_timing import decode_native
from ..memory import load_checkpoint
from .native_attachment import (
    CALLBACKS_EXPR, CLOSED_WORLD_PROFILE, MANUAL_CYCLE_PROFILE, NATIVE_SCHEMA,
    CONNECTOR_OBSERVER_WITNESS_NAME, PINNED_ASSETS, WATER_ORIGIN_OBSERVATION_SHA256,
    connector_observer_bridge_sha256, connector_ownership_sha256, cycle_journal_sha256,
    manual_journal_sha256, readback,
)
from .native_manual_cycle_migration import (
    _append_intent, _intent_events, _new_intent, _no_open_investment,
    _sha256_text,
)
from .native_observation_migration import _digest, _private_bytes
from .native_observation_water_origin_migration import (
    _lock_identity, _original_receipt, _quiescent,
)

SENTINEL = 'JEV_NATIVE_CLOSED_WORLD_MIGRATED|6'
INTENT_NAME = 'native-closed-world-v6.intent.jsonl'
PREFLIGHT = '''local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair)
local q=assert(rt.coal_supply);local mj=assert(rt.coal_manual_journal_v1)
local a=assert(rt.agent_characters and rt.agent_characters[1])
local p=assert(f.actor());local ledger=assert(c.connector_ledger)
local route_count=0;for _ in pairs(ledger.routes) do route_count=route_count+1 end
local journal_count=0;for _ in pairs(mj.rows) do journal_count=journal_count+1 end
local coal_pending=false
for _,row in pairs(q.rows) do
    if row.pending or row.manual_pending or row.fault or next(row.parts) then
        coal_pending=true
    end
end
rcon.print(helpers.table_to_json({schema=1,session_id=rt.jev_session_id,
    actor_unit=a.unit_number,ledger_routes=route_count,
    ledger_active=ledger.active~=nil,journal_rows=journal_count,
    journal_pending=mj.pending~=nil,coal_committed=q.committed==true,
    coal_pending=coal_pending,actor_idle=(not f.job or f.job.status=="completed"
        or f.job.status=="failed") and not p.walking_state.walking
        and not p.mining_state.mining}))'''


def _preflight(client, session_id: str, actor_unit: int) -> None:
    row = decode_native(client.send_command('/sc ' + PREFLIGHT))
    if (not isinstance(row, dict) or set(row) != {
            'schema', 'session_id', 'actor_unit', 'ledger_routes', 'ledger_active',
            'journal_rows', 'journal_pending', 'coal_committed', 'coal_pending',
            'actor_idle'}
            or row['schema'] != 1 or row['session_id'] != session_id
            or row['actor_unit'] != actor_unit
            or type(row['ledger_routes']) is not int or row['ledger_routes'] != 0
            or type(row['journal_rows']) is not int or row['journal_rows'] != 0
            or row['ledger_active'] is not False
            or row['journal_pending'] is not False
            or row['coal_committed'] is not False
            or row['coal_pending'] is not False
            or row['actor_idle'] is not True):
        raise RuntimeError('Closed-world guest is not quiescent and empty')


def _manifest(attachment: dict) -> dict:
    native = attachment.get('native_installation')
    modules = attachment.get('modules')
    if (not isinstance(native, dict) or not isinstance(modules, dict)
            or native.get('schema') != NATIVE_SCHEMA
            or native.get('profile') != MANUAL_CYCLE_PROFILE
            or native.get('session_id') != attachment.get('session_id')
            or native.get('actor_unit') != attachment.get('actor_unit')
            or modules.get('connector_ownership') is not True
            or modules.get('coal_manual_journal_v1') is not True
            or modules.get('coal_manual_cycle_v2') is not False):
        raise RuntimeError('Closed-world journal requires exact v5 installation')
    assets = native.get('assets')
    if not isinstance(assets, dict) or 'coal_manual_cycle_v2' in assets:
        raise RuntimeError('Closed-world journal asset is already present')
    expected = {name: (WATER_ORIGIN_OBSERVATION_SHA256 if name == 'observation_v2'
                       else PINNED_ASSETS[name])
                for name, enabled in modules.items()
                if enabled and name in PINNED_ASSETS}
    expected.update(connector_ownership=connector_ownership_sha256(),
                    coal_manual_journal_v1=manual_journal_sha256(),
                    connector_observer_bridge_v1=connector_observer_bridge_sha256())
    if assets != expected:
        raise RuntimeError('Installed v5 assets require reconciliation')
    return {**native, 'profile': CLOSED_WORLD_PROFILE,
            'assets': {**assets, 'coal_manual_cycle_v2': cycle_journal_sha256()}}


def _command(attachment: dict) -> str:
    proposed = _manifest(attachment)
    source = files('jev_factorio').joinpath('lua/coal_manual_cycle_v2.lua').read_text()
    before = attachment['native_installation']
    assets = json.dumps(before['assets'], sort_keys=True, separators=(',', ':'))
    session = json.dumps(attachment['session_id'])
    actor = str(attachment['actor_unit'])
    return '/sc ' + (
        'local rt=assert(jev_fle_runtime); local storage=rt; '
        'local c=assert(rt.campaign); local f=assert(rt.fair); '
        'local a=assert(rt.agent_characters[1]); '
        'local n=assert(rt.native_installation); local q=assert(rt.coal_supply); '
        'local mj=assert(rt.coal_manual_journal_v1); '
        'assert(rt.jev_session_id==' + session + ' and a.valid and a.unit_number=='
        + actor + ' and f.actor().character==a); '
        'assert(game.speed==1 and not game.tick_paused); '
        'assert(not f.job or f.job.status=="completed" or f.job.status=="failed"); '
        'assert(not f.actor().walking_state.walking and not f.actor().mining_state.mining); '
        'assert(rt.coal_manual_cycle_v2==nil and mj.protocol==1 and mj.pending==nil '
        'and next(mj.rows)==nil and #mj.order==0); '
        'assert(c.connector_ledger and c.connector_ledger.protocol==1 '
        'and c.connector_ledger.active==nil and next(c.connector_ledger.routes)==nil); '
        'assert(not q.committed and q.revision==4); '
        'for _,row in pairs(q.rows) do assert(not row.pending and not row.fault '
        'and not row.manual_pending and next(row.parts)==nil) end; '
        'assert(n.callbacks and n.callbacks.journal_tick==mj.tick_handler '
        'and n.callbacks.cycle_tick==nil); '
        'assert(n.schema==' + json.dumps(NATIVE_SCHEMA) + ' and n.session_id=='
        + session + ' and n.actor_unit==' + actor + ' and n.profile=='
        + json.dumps(MANUAL_CYCLE_PROFILE) + '); '
        'local expected=helpers.json_to_table(' + json.dumps(assets) + '); '
        'for name,hash in pairs(expected) do assert(n.assets[name]==hash) end; '
        'for name in pairs(n.assets) do assert(expected[name]~=nil) end; '
        'local old_callbacks=n.callbacks; '
        'local ok,err=pcall(function() do\n' + source + '\nend; '
        'assert(rt.coal_manual_cycle_v2 and rt.coal_manual_cycle_v2.protocol==2); '
        'n.assets.coal_manual_cycle_v2=' + json.dumps(cycle_journal_sha256()) + '; '
        'n.profile=' + json.dumps(CLOSED_WORLD_PROFILE) + '; '
        'n.callbacks=' + CALLBACKS_EXPR + ' end); '
        'if not ok then script.on_nth_tick(1,mj.tick_handler); '
        'rt.coal_manual_cycle_v2=nil; '
        'n.assets.coal_manual_cycle_v2=nil; '
        'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE) + '; '
        'n.callbacks=old_callbacks; error(err) end; '
        'assert(n.assets.coal_manual_cycle_v2=='
        + json.dumps(proposed['assets']['coal_manual_cycle_v2']) + '); '
        'rcon.print(' + json.dumps(SENTINEL) + ')'
    )


def _fixed_path(checkpoint_path: Path, intent_path: Path) -> None:
    if intent_path != checkpoint_path.with_name(INTENT_NAME):
        raise RuntimeError('Closed-world intent must use checkpoint-bound fixed path')


def _no_successor_projects(memory) -> None:
    projects = getattr(memory, 'successor_projects', {})
    if not isinstance(projects, dict) or projects:
        raise RuntimeError('Closed-world migration requires no successor project')


def _lock(lock_path: Path):
    if os.name != 'posix':
        raise RuntimeError('Native migration requires the POSIX owner-lock host')
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    stat = lock_path.stat()
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
        raise RuntimeError('Single-writer lock must be owned by controller and private')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    return os.fdopen(fd, 'r+b')


def migrate_closed_world_v6(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    intent_path: Path, expected_session_id: str, expected_actor_unit: int,
    expected_target: str, expected_checkpoint_sha256: str,
    expected_receipt_sha256: str,
) -> dict:
    """Dispatch exactly once after a private durable intent; never replay."""
    import fcntl
    checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
    lock_path, intent_path = Path(lock_path), Path(intent_path)
    _fixed_path(checkpoint_path, intent_path)
    if intent_path.exists() or intent_path.is_symlink():
        raise RuntimeError('Closed-world intent exists; reconcile read-only')
    with _lock(lock_path) as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Closed-world evidence hash changed')
        _original_receipt(_private_bytes(receipt_path), expected_session_id,
                          expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        _no_successor_projects(memory)
        _quiescent(memory); _no_open_investment(memory)
        binding = validate_binding(memory.connector_ownership, expected_session_id)
        if binding['routes']:
            raise RuntimeError('Closed-world migration requires empty connector checkpoint')
        attachment = readback(client, receipt_path=receipt_path,
                              connector_witness_path=checkpoint_path.with_name(
                                  CONNECTOR_OBSERVER_WITNESS_NAME))
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit):
            raise RuntimeError('Closed-world migration session or actor changed')
        proposed = _manifest(attachment)
        _preflight(client, expected_session_id, expected_actor_unit)
        command = _command(attachment)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Closed-world evidence changed before dispatch')
        intent_fd = _new_intent(intent_path, {
            'schema': 'jev.native-closed-world-intent.v1', 'phase': 'dispatching',
            'session_id': expected_session_id, 'actor_unit': expected_actor_unit,
            'target': expected_target,
            'checkpoint_sha256': expected_checkpoint_sha256,
            'receipt_sha256': expected_receipt_sha256,
            'before': attachment['native_installation'], 'after': proposed,
            'before_modules': attachment['modules'],
            'after_modules': {**attachment['modules'], 'coal_manual_cycle_v2': True},
            'command_sha256': hashlib.sha256(command.encode('utf-8')).hexdigest(),
        })
        try:
            try:
                acknowledgement = client.send_command(command)
                if (not isinstance(acknowledgement, str)
                        or len(acknowledgement) > 1024
                        or not acknowledgement.strip().endswith(SENTINEL)):
                    raise RuntimeError('Closed-world migration acknowledgement ambiguous')
                after = readback(client, receipt_path=receipt_path,
                                 connector_witness_path=checkpoint_path.with_name(
                                     CONNECTOR_OBSERVER_WITNESS_NAME))
                if (after['native_installation'] != proposed
                        or after['modules'] != {**attachment['modules'],
                                               'coal_manual_cycle_v2': True}
                        or after['session_id'] != expected_session_id
                        or after['actor_unit'] != expected_actor_unit
                        or _digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                        or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
                    raise RuntimeError('Closed-world migration readback changed')
                _append_intent(intent_fd, {'phase': 'qualified',
                    'readback_sha256': _digest(json.dumps(after, sort_keys=True).encode())})
                return after
            except Exception as exc:
                _append_intent(intent_fd, {'phase': 'unknown',
                                           'reason': type(exc).__name__})
                raise
        finally:
            os.close(intent_fd)


def reconcile_closed_world_v6(
    client, *, intent_path: Path, lock_path: Path, checkpoint_path: Path,
    receipt_path: Path,
) -> str:
    """Classify one prior attempt by readback; never dispatch a new install."""
    import fcntl
    checkpoint_path, intent_path = Path(checkpoint_path), Path(intent_path)
    _fixed_path(checkpoint_path, intent_path)
    lock_path = Path(lock_path)
    with _lock(lock_path) as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        rows = _intent_events(intent_path)
        first = rows[0]
        keys = {'schema', 'phase', 'session_id', 'actor_unit', 'target',
                'checkpoint_sha256', 'receipt_sha256', 'before', 'after',
                'before_modules', 'after_modules', 'command_sha256'}
        if (set(first) != keys
                or first['schema'] != 'jev.native-closed-world-intent.v1'
                or first['phase'] != 'dispatching'
                or not isinstance(first['session_id'], str)
                or not first['session_id']
                or type(first['actor_unit']) is not int or first['actor_unit'] < 1
                or not isinstance(first['target'], str) or not first['target']
                or any(not _sha256_text(first[k]) for k in
                       ('checkpoint_sha256', 'receipt_sha256', 'command_sha256'))
                or not isinstance(first['before_modules'], dict)
                or first['after_modules'] != {**first['before_modules'],
                                              'coal_manual_cycle_v2': True}
                or first['before_modules'].get('coal_manual_cycle_v2') is not False
                or len(rows) > 3):
            raise RuntimeError('Closed-world intent requires reconciliation')
        if (len(rows) == 2 and rows[1].get('phase') not in {'unknown', 'qualified'}
                or len(rows) == 3 and not (rows[1].get('phase') == 'unknown'
                                           and rows[2] == {'phase': 'reconciled_v6'})):
            raise RuntimeError('Closed-world intent event order invalid')
        for event in rows[1:]:
            if event.get('phase') == 'unknown':
                if set(event) != {'phase', 'reason'} or not isinstance(event['reason'], str):
                    raise RuntimeError('Closed-world intent event malformed')
            elif event.get('phase') == 'qualified':
                if set(event) != {'phase', 'readback_sha256'} or not _sha256_text(event['readback_sha256']):
                    raise RuntimeError('Closed-world intent event malformed')
        before = first['before']; after = first['after']
        if (not isinstance(before, dict) or not isinstance(after, dict)
                or before.get('profile') != MANUAL_CYCLE_PROFILE
                or after.get('profile') != CLOSED_WORLD_PROFILE
                or before.get('schema') != NATIVE_SCHEMA
                or after.get('schema') != NATIVE_SCHEMA
                or before.get('session_id') != first['session_id']
                or after.get('session_id') != first['session_id']
                or before.get('actor_unit') != first['actor_unit']
                or after.get('actor_unit') != first['actor_unit']
                or not isinstance(before.get('assets'), dict)
                or after.get('assets') != {**before['assets'],
                                            'coal_manual_cycle_v2': cycle_journal_sha256()}):
            raise RuntimeError('Closed-world intent profile invalid')
        if _manifest({'native_installation': before,
                      'modules': first['before_modules'],
                      'session_id': first['session_id'],
                      'actor_unit': first['actor_unit']}) != after:
            raise RuntimeError('Closed-world intent source transition invalid')
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']):
            raise RuntimeError('Closed-world evidence hash changed')
        observed = readback(client, receipt_path=receipt_path,
                            connector_witness_path=checkpoint_path.with_name(
                                CONNECTOR_OBSERVER_WITNESS_NAME))
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']
                or observed['session_id'] != first['session_id']
                or observed['actor_unit'] != first['actor_unit']):
            raise RuntimeError('Closed-world reconciliation identity changed')
        if (observed['native_installation'] == after
                and observed['modules'] == first['after_modules']):
            if rows[-1]['phase'] not in {'qualified', 'reconciled_v6'}:
                fd = os.open(intent_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    _append_intent(fd, {'phase': 'reconciled_v6'})
                finally:
                    os.close(fd)
            return 'v6_installed'
        if (observed['native_installation'] == before
                and observed['modules'] == first['before_modules']):
            if any(row.get('phase') in {'qualified', 'reconciled_v6'} for row in rows[1:]):
                raise RuntimeError('Contradictory closed-world success/readback')
            return 'v5_observed_attempt_consumed'
        raise RuntimeError('Closed-world installation requires owner reconciliation')
