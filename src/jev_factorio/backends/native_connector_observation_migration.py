"""One-use repair of the retained v5 observer's connector ownership projection."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from ..connector_checkpoint import validate_binding
from ..iteration_timing import decode_native
from ..memory import load_checkpoint
from .native_attachment import (
    CALLBACKS_EXPR, LEGACY_MANUAL_CYCLE_PROFILE, MANUAL_CYCLE_PROFILE,
    NATIVE_SCHEMA, PINNED_ASSETS, WATER_ORIGIN_OBSERVATION_SHA256,
    CONNECTOR_OBSERVER_WITNESS_NAME, CLOSED_WORLD_PROFILE,
    connector_observer_bridge_sha256, connector_ownership_sha256,
    connector_snapshot_sha256, manual_journal_sha256, readback,
    connector_snapshot_observation_command as _snapshot_command,
    _connector_witness, _normalize_empty_connector_routes,
)
from .native_manual_cycle_migration import (
    _append_intent, _intent_events, _new_intent, _no_open_investment,
    _sha256_text,
)
from .native_observation_migration import _digest, _private_bytes
from .native_observation_water_origin_migration import (
    _lock_identity, _original_receipt, _quiescent,
)

SENTINEL = 'JEV_NATIVE_CONNECTOR_OBSERVER_MIGRATED|1'
INTENT_NAME = 'native-connector-observer-v1.intent.jsonl'


def _manifest(attachment: dict) -> dict:
    native = attachment.get('native_installation')
    modules = attachment.get('modules')
    if (not isinstance(native, dict) or not isinstance(modules, dict)
            or native.get('schema') != NATIVE_SCHEMA
            or native.get('profile') != LEGACY_MANUAL_CYCLE_PROFILE
            or native.get('session_id') != attachment.get('session_id')
            or native.get('actor_unit') != attachment.get('actor_unit')
            or modules.get('connector_ownership') is not True
            or modules.get('coal_manual_journal_v1') is not True
            or modules.get('coal_manual_cycle_v2') is not False
            or modules.get('connector_observer_bridge_v1') is not False):
        raise RuntimeError('Observer repair requires the exact legacy v5 installation')
    expected = {name: (WATER_ORIGIN_OBSERVATION_SHA256 if name == 'observation_v2'
                       else PINNED_ASSETS[name])
                for name, enabled in modules.items()
                if enabled and name in PINNED_ASSETS}
    expected.update(connector_ownership=connector_ownership_sha256(),
                    coal_manual_journal_v1=manual_journal_sha256())
    if native.get('assets') != expected:
        raise RuntimeError('Legacy v5 assets require reconciliation')
    return {**native, 'profile': MANUAL_CYCLE_PROFILE,
            'assets': {**expected,
                       'connector_observer_bridge_v1': connector_observer_bridge_sha256()}}


def _preflight(client, session_id: str, actor_unit: int) -> None:
    source = '''local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair)
local s=assert(rt.solid_routes);local q=assert(rt.coal_supply)
local j=assert(rt.coal_manual_journal_v1)
local a=assert(rt.agent_characters and rt.agent_characters[1]);local p=assert(f.actor())
local ledger=assert(c.connector_ledger);local routes=0;for _ in pairs(ledger.routes) do routes=routes+1 end
local rows=0;for _ in pairs(j.rows) do rows=rows+1 end
local coal_pending=false
for _,row in pairs(q.rows) do
    if row.pending or row.manual_pending or row.fault or next(row.parts) then coal_pending=true end
end
rcon.print(helpers.table_to_json({schema=1,session_id=rt.jev_session_id,
    actor_unit=a.unit_number,legacy_profile=rt.native_installation.profile,
    bridge_absent=rt.connector_observer_bridge_v1==nil,
    observer_chain=(c.observe==s.observer and rt.native_installation.callbacks.observe==c.observe),
    ledger_protocol=ledger.protocol,ledger_active=ledger.active~=nil,ledger_routes=routes,
    journal_protocol=j.protocol,journal_pending=j.pending~=nil,journal_rows=rows,
    coal_committed=q.committed==true,coal_pending=coal_pending,
    actor_idle=(not f.job or f.job.status=="completed" or f.job.status=="failed")
        and not p.walking_state.walking and not p.mining_state.mining}))'''
    row = decode_native(client.send_command('/sc ' + source))
    if (not isinstance(row, dict) or set(row) != {
            'schema', 'session_id', 'actor_unit', 'legacy_profile', 'bridge_absent',
            'observer_chain', 'ledger_protocol', 'ledger_active', 'ledger_routes',
            'journal_protocol', 'journal_pending', 'journal_rows', 'coal_committed',
            'coal_pending', 'actor_idle'}
            or row['schema'] != 1 or row['session_id'] != session_id
            or row['actor_unit'] != actor_unit
            or row['legacy_profile'] != LEGACY_MANUAL_CYCLE_PROFILE
            or row['bridge_absent'] is not True or row['observer_chain'] is not True
            or type(row['ledger_protocol']) is not int or row['ledger_protocol'] != 1
            or row['ledger_active'] is not False or type(row['ledger_routes']) is not int
            or row['ledger_routes'] != 0
            or type(row['journal_protocol']) is not int or row['journal_protocol'] != 1
            or row['journal_pending'] is not False or type(row['journal_rows']) is not int
            or row['journal_rows'] != 0 or row['coal_committed'] is not False
            or row['coal_pending'] is not False or row['actor_idle'] is not True):
        raise RuntimeError('Legacy v5 observer repair requires an idle empty runtime')


def _lock_binding(lock, expected: dict | None = None) -> dict:
    opened = os.fstat(lock.fileno())
    binding = {'device': opened.st_dev, 'inode': opened.st_ino}
    if expected is not None and binding != expected:
        raise RuntimeError('Single-writer lock identity changed')
    return binding


@contextmanager
def _transaction_lock(lock_path: Path, owner_lock_fd: int | None):
    """Use the existing owner flock or acquire the lock for a standalone call.

    Duplicating the caller's descriptor preserves its open-file description and
    therefore its flock. Closing our duplicate cannot release the caller's lock.
    """
    import fcntl

    if owner_lock_fd is None:
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    else:
        if type(owner_lock_fd) is not int or owner_lock_fd < 0:
            raise RuntimeError('Already-acquired owner lock descriptor is invalid')
        fd = os.dup(owner_lock_fd)
    with os.fdopen(fd, 'r+b') as lock:
        _lock_identity(lock_path, lock)
        if owner_lock_fd is not None:
            opened = os.fstat(lock.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o600
                    or fcntl.fcntl(lock.fileno(), fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
                raise RuntimeError('Already-acquired owner lock identity changed')
            # An independent shared lock must contend. An exclusive probe
            # would also contend with a caller's shared lock and could cause
            # us to upgrade that shared lock before establishing ownership.
            probe = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                try:
                    fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    fcntl.flock(probe, fcntl.LOCK_UN)
                    raise RuntimeError('Owner lock descriptor is not already locked')
            finally:
                os.close(probe)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        yield lock


def _command(attachment: dict) -> str:
    proposed = _manifest(attachment)
    source = files('jev_factorio').joinpath(
        'lua/connector_observer_bridge_v1.lua').read_text()
    before = attachment['native_installation']
    assets = json.dumps(before['assets'], sort_keys=True, separators=(',', ':'))
    session = json.dumps(attachment['session_id'])
    actor = str(attachment['actor_unit'])
    return '/sc ' + (
        'local rt=assert(jev_fle_runtime);local c=assert(rt.campaign);'
        'local solid=assert(rt.solid_routes);local f=assert(rt.fair);'
        'local n=assert(rt.native_installation);local q=assert(rt.coal_supply);'
        'local j=assert(rt.coal_manual_journal_v1);local a=assert(rt.agent_characters[1]);'
        'local player=assert(f.actor());'
        'assert(rt.jev_session_id==' + session + ' and a.valid and a.unit_number==' + actor
        + ' and player.character==a);'
        'assert(game.speed==1 and not game.tick_paused);'
        'assert(not f.job or f.job.status=="completed" or f.job.status=="failed");'
        'assert(not player.walking_state.walking and not player.mining_state.mining);'
        'assert(n.schema==' + json.dumps(NATIVE_SCHEMA) + ' and n.session_id==' + session
        + ' and n.actor_unit==' + actor + ' and n.profile=='
        + json.dumps(LEGACY_MANUAL_CYCLE_PROFILE) + ');'
        'assert(rt.connector_observer_bridge_v1==nil and c.observe==solid.observer '
        'and n.callbacks and n.callbacks.observe==c.observe);'
        'assert(c.connector_ledger and c.connector_ledger.protocol==1 '
        'and c.connector_ledger.active==nil and next(c.connector_ledger.routes)==nil);'
        'assert(j.protocol==1 and j.pending==nil and next(j.rows)==nil and #j.order==0);'
        'assert(not q.committed and q.revision==4);'
        'for _,row in pairs(q.rows) do assert(not row.pending and not row.fault '
        'and not row.manual_pending and next(row.parts)==nil) end;'
        'local expected=helpers.json_to_table(' + json.dumps(assets) + ');'
        'for name,hash in pairs(expected) do assert(n.assets[name]==hash) end;'
        'for name in pairs(n.assets) do assert(expected[name]~=nil) end;'
        'local old_observe=c.observe;local old_solid_observer=solid.observer;'
        'local old_bridge=rt.connector_observer_bridge_v1;local old_callbacks=n.callbacks;'
        'local ok,err=pcall(function() do\n' + source + '\nend;'
        'assert(rt.connector_observer_bridge_v1 and c.observe==solid.observer);'
        'n.assets.connector_observer_bridge_v1='
        + json.dumps(connector_observer_bridge_sha256()) + ';'
        'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE) + ';'
        'n.callbacks=' + CALLBACKS_EXPR + ';end);'
        'if not ok then c.observe=old_observe;solid.observer=old_solid_observer;'
        'rt.connector_observer_bridge_v1=old_bridge;'
        'n.assets.connector_observer_bridge_v1=nil;'
        'n.profile=' + json.dumps(LEGACY_MANUAL_CYCLE_PROFILE) + ';'
        'n.callbacks=old_callbacks;error(err) end;'
        'assert(n.assets.connector_observer_bridge_v1=='
        + json.dumps(proposed['assets']['connector_observer_bridge_v1'])
        + ' and n.profile==' + json.dumps(MANUAL_CYCLE_PROFILE) + ');'
        'rcon.print(' + json.dumps(SENTINEL) + ')'
    )


def migrate_connector_observer_v51(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    intent_path: Path, expected_session_id: str, expected_actor_unit: int,
    expected_target: str, expected_checkpoint_sha256: str,
    expected_receipt_sha256: str, owner_lock_fd: int | None = None,
) -> dict:
    """Add only the missing observation bridge; never run a controller step."""
    if os.name != 'posix':
        raise RuntimeError('Native migration requires the POSIX owner-lock host')
    checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
    intent_path = Path(intent_path)
    if intent_path != checkpoint_path.with_name(INTENT_NAME):
        raise RuntimeError('Observer repair intent must use its checkpoint-bound fixed path')
    if intent_path.exists() or intent_path.is_symlink():
        raise RuntimeError('Observer repair intent exists; reconcile read-only, never retry')
    lock_path = Path(lock_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    lock_stat = lock_path.stat()
    if lock_stat.st_uid != os.geteuid() or lock_stat.st_mode & 0o077:
        raise RuntimeError('Single-writer lock must be owned by controller and private')
    with _transaction_lock(lock_path, owner_lock_fd) as lock:
        lock_binding = _lock_binding(lock)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Observer repair evidence hash changed')
        _original_receipt(_private_bytes(receipt_path), expected_session_id,
                          expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        _quiescent(memory)
        _no_open_investment(memory)
        binding = validate_binding(memory.connector_ownership, expected_session_id)
        if binding['routes']:
            raise RuntimeError('Observer repair requires an empty connector checkpoint')
        attachment = readback(client, receipt_path=receipt_path,
                              allow_legacy_manual_cycle_repair=True,
                              allow_unqualified_connector_bridge=True)
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit
                or attachment['native_installation']['profile'] != LEGACY_MANUAL_CYCLE_PROFILE
                or attachment['connector_observer_bridge_qualified'] is not False):
            raise RuntimeError('Observer repair requires the exact incomplete v5 profile')
        proposed = _manifest(attachment)
        _preflight(client, expected_session_id, expected_actor_unit)
        command = _command(attachment)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Observer repair evidence changed before dispatch')
        _lock_identity(lock_path, lock)
        intent_fd = _new_intent(intent_path, {
            'schema': 'jev.native-connector-observer-intent.v1',
            'phase': 'dispatching', 'session_id': expected_session_id,
            'actor_unit': expected_actor_unit, 'target': expected_target,
            'checkpoint_sha256': expected_checkpoint_sha256,
            'receipt_sha256': expected_receipt_sha256,
            'lock_identity': lock_binding,
            'before': attachment['native_installation'], 'after': proposed,
            'before_modules': attachment['modules'],
            'after_modules': {**attachment['modules'],
                              'connector_observer_bridge_v1': True},
            'command_sha256': hashlib.sha256(command.encode('utf-8')).hexdigest(),
        })
        try:
            try:
                response = client.send_command(command)
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                if not isinstance(response, str) or not response.strip().endswith(SENTINEL):
                    raise RuntimeError('Observer repair acknowledgement absent')
                after = readback(client, receipt_path=receipt_path,
                                 allow_unqualified_connector_bridge=True)
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                expected_modules = {**attachment['modules'],
                                    'connector_observer_bridge_v1': True}
                if (after['session_id'] != expected_session_id
                        or after['actor_unit'] != expected_actor_unit
                        or after['modules'] != expected_modules
                        or after['connector_observer_bridge_qualified'] is not True
                        or after['native_installation'] != proposed):
                    raise RuntimeError('Observer repair postcondition failed')
                if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                        or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
                    raise RuntimeError('Observer repair evidence changed after native transaction')
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                _append_intent(intent_fd, {'phase': 'qualified',
                    'readback_sha256': hashlib.sha256(json.dumps(
                        after, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()})
                return after
            except Exception as exc:
                _append_intent(intent_fd, {'phase': 'unknown', 'reason': type(exc).__name__})
                raise RuntimeError('Observer repair outcome unknown; reconcile read-only, never retry') from exc
        finally:
            os.close(intent_fd)


def reconcile_connector_observer_v51(
    client, *, intent_path: Path, lock_path: Path,
    checkpoint_path: Path, receipt_path: Path,
) -> str:
    """Classify the existing one-use repair attempt; never redispatch it."""
    if os.name != 'posix':
        raise RuntimeError('Native reconciliation requires the POSIX owner-lock host')
    import fcntl

    intent_path, checkpoint_path = Path(intent_path), Path(checkpoint_path)
    if intent_path != checkpoint_path.with_name(INTENT_NAME):
        raise RuntimeError('Observer repair intent must use its checkpoint-bound fixed path')
    lock_path = Path(lock_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'r+b') as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        lock_binding = _lock_binding(lock)
        rows = _intent_events(intent_path)
        first = rows[0]
        if (set(first) != {'schema', 'phase', 'session_id', 'actor_unit', 'target',
                           'checkpoint_sha256', 'receipt_sha256', 'lock_identity', 'before', 'after',
                           'before_modules', 'after_modules', 'command_sha256'}
                or first['schema'] != 'jev.native-connector-observer-intent.v1'
                or first['phase'] != 'dispatching'
                or not isinstance(first['session_id'], str) or not first['session_id']
                or type(first['actor_unit']) is not int or first['actor_unit'] < 1
                or not isinstance(first['target'], str) or not first['target']
                or any(not _sha256_text(first[name]) for name in (
                    'checkpoint_sha256', 'receipt_sha256', 'command_sha256'))
                or not isinstance(first['lock_identity'], dict)
                or set(first['lock_identity']) != {'device', 'inode'}
                or any(type(value) is not int or value < 0
                       for value in first['lock_identity'].values())
                or not isinstance(first['before'], dict) or not isinstance(first['after'], dict)
                or not isinstance(first['before_modules'], dict)
                or not isinstance(first['after_modules'], dict)
                or set(first['before_modules']) != set(first['after_modules'])
                or any(type(value) is not bool for value in first['before_modules'].values())
                or any(type(value) is not bool for value in first['after_modules'].values())
                or first['after_modules'] != {**first['before_modules'],
                                               'connector_observer_bridge_v1': True}
                or [row.get('phase') for row in rows[1:]] not in (
                    [], ['unknown'], ['qualified'], ['unknown', 'reconciled_v51'])):
            raise RuntimeError('Observer repair intent requires reconciliation')
        _lock_binding(lock, first['lock_identity'])
        for row in rows[1:]:
            if row.get('phase') == 'unknown':
                valid = (set(row) == {'phase', 'reason'}
                         and isinstance(row['reason'], str)
                         and 1 <= len(row['reason']) <= 128)
            elif row.get('phase') == 'qualified':
                valid = (set(row) == {'phase', 'readback_sha256'}
                         and _sha256_text(row['readback_sha256']))
            else:
                valid = row == {'phase': 'reconciled_v51'}
            if not valid:
                raise RuntimeError('Observer repair intent requires reconciliation')
        expected_after = _manifest({
            'native_installation': first['before'], 'modules': first['before_modules'],
            'session_id': first['session_id'], 'actor_unit': first['actor_unit']})
        if first['after'] != expected_after:
            raise RuntimeError('Observer repair intent profile requires reconciliation')
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']):
            raise RuntimeError('Observer repair evidence changed; hold controller')
        observed = readback(client, receipt_path=receipt_path,
                            allow_legacy_manual_cycle_repair=True,
                            allow_unqualified_connector_bridge=True)
        _lock_identity(lock_path, lock)
        _lock_binding(lock, first['lock_identity'])
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']):
            raise RuntimeError('Observer repair evidence changed during readback; hold controller')
        if (observed['session_id'] != first['session_id']
                or observed['actor_unit'] != first['actor_unit']):
            raise RuntimeError('Observer repair session/actor changed; hold controller')
        if observed['native_installation'] == first['after']:
            if (observed['modules'] != first['after_modules']
                    or observed['connector_observer_bridge_qualified'] is not True):
                raise RuntimeError('Observer bridge behavior is not qualified; hold controller')
            if rows[-1]['phase'] not in {'qualified', 'reconciled_v51'}:
                fd = os.open(intent_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    _append_intent(fd, {'phase': 'reconciled_v51'})
                finally:
                    os.close(fd)
            return 'v5_observer_repaired'
        if observed['native_installation'] == first['before']:
            if rows[-1]['phase'] in {'qualified', 'reconciled_v51'}:
                raise RuntimeError('Success journal contradicts legacy v5 readback')
            if (observed['modules'] != first['before_modules']
                    or observed['connector_observer_bridge_qualified'] is not False):
                raise RuntimeError('Legacy v5 readback is inconsistent; hold controller')
            return 'legacy_v5_observed_attempt_consumed'
        raise RuntimeError('Observer repair profile is ambiguous; hold controller')


SNAPSHOT_WITNESS_SCHEMA = 'jev.native-connector-observer-witness.v1'


def _snapshot_preflight(client, session_id: str, actor_unit: int) -> None:
    source = '''local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair);local s=assert(rt.solid_routes)
local q=assert(rt.coal_supply);local j=assert(rt.coal_manual_journal_v1)
local b=assert(rt.connector_observer_bridge_v1)
local a=assert(rt.agent_characters and rt.agent_characters[1]);local p=assert(f.actor())
local ledger=assert(c.connector_ledger);local routes=0;for _ in pairs(ledger.routes) do routes=routes+1 end
local rows=0;for _ in pairs(j.rows) do rows=rows+1 end
local coal_pending=false
for _,row in pairs(q.rows) do
    if row.pending or row.manual_pending or row.fault or next(row.parts) then coal_pending=true end
end
rcon.print(helpers.table_to_json({schema=1,session_id=rt.jev_session_id,
    actor_unit=a.unit_number,profile=rt.native_installation.profile,
    bridge_qualified=(b.protocol==1 and b.observer==c.observe and c.observe==s.observer),
    snapshot_qualified=b.snapshot_qualified==true,
    ledger_protocol=ledger.protocol,ledger_active=ledger.active~=nil,ledger_routes=routes,
    journal_protocol=j.protocol,journal_pending=j.pending~=nil,journal_rows=rows,
    coal_committed=q.committed==true,coal_pending=coal_pending,
    actor_idle=(not f.job or f.job.status=="completed" or f.job.status=="failed")
        and not p.walking_state.walking and not p.mining_state.mining}))'''
    row = decode_native(client.send_command('/sc ' + source))
    if (not isinstance(row, dict) or set(row) != {
            'schema', 'session_id', 'actor_unit', 'profile', 'bridge_qualified',
            'snapshot_qualified', 'ledger_protocol', 'ledger_active', 'ledger_routes',
            'journal_protocol', 'journal_pending', 'journal_rows', 'coal_committed',
            'coal_pending', 'actor_idle'}
            or row['schema'] != 1 or row['session_id'] != session_id
            or row['actor_unit'] != actor_unit
            or row['profile'] not in {MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE}
            or row['bridge_qualified'] is not True or row['snapshot_qualified'] is not False
            or type(row['ledger_protocol']) is not int or row['ledger_protocol'] != 1
            or row['ledger_active'] is not False or row['ledger_routes'] != 0
            or type(row['journal_protocol']) is not int or row['journal_protocol'] != 1
            or row['journal_pending'] is not False or row['journal_rows'] != 0
            or row['coal_committed'] is not False or row['coal_pending'] is not False
            or row['actor_idle'] is not True):
        raise RuntimeError('Connector snapshot qualification requires an idle empty runtime')


def qualify_connector_snapshot_v1(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    witness_path: Path, expected_session_id: str, expected_actor_unit: int,
    expected_target: str, expected_checkpoint_sha256: str,
    expected_receipt_sha256: str, owner_lock_fd: int | None = None,
) -> dict:
    """Call the retained observer once under a durable, owner-locked intent."""
    if os.name != 'posix':
        raise RuntimeError('Native snapshot qualification requires the POSIX owner-lock host')
    checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
    witness_path, lock_path = Path(witness_path), Path(lock_path)
    if witness_path != checkpoint_path.with_name(CONNECTOR_OBSERVER_WITNESS_NAME):
        raise RuntimeError('Connector snapshot witness must use its checkpoint-bound fixed path')
    if witness_path.exists() or witness_path.is_symlink():
        raise RuntimeError('Connector snapshot intent exists; reconcile read-only, never retry')
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    lock_stat = lock_path.stat()
    if lock_stat.st_uid != os.geteuid() or lock_stat.st_mode & 0o077:
        raise RuntimeError('Single-writer lock must be owned by controller and private')
    with _transaction_lock(lock_path, owner_lock_fd) as lock:
        lock_binding = _lock_binding(lock)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Connector snapshot evidence hash changed')
        _original_receipt(_private_bytes(receipt_path), expected_session_id,
                          expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        _quiescent(memory)
        _no_open_investment(memory)
        binding = validate_binding(memory.connector_ownership, expected_session_id)
        if binding['routes']:
            raise RuntimeError('Connector snapshot qualification requires an empty checkpoint binding')
        attachment = readback(client, receipt_path=receipt_path,
                              allow_unqualified_connector_bridge=True)
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit
                or attachment['modules']['connector_observer_bridge_v1'] is not True
                or attachment['connector_observer_bridge_qualified'] is not True
                or attachment['connector_snapshot_qualified'] is not False
                or attachment['native_installation']['profile'] not in {
                    MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE}):
            raise RuntimeError('Connector snapshot bridge is not in its one-time qualification state')
        _snapshot_preflight(client, expected_session_id, expected_actor_unit)
        command = _snapshot_command(expected_session_id, expected_actor_unit)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Connector snapshot evidence changed before dispatch')
        _lock_identity(lock_path, lock)
        _lock_binding(lock, lock_binding)
        intent_fd = _new_intent(witness_path, {
            'schema': SNAPSHOT_WITNESS_SCHEMA, 'phase': 'dispatching',
            'session_id': expected_session_id, 'actor_unit': expected_actor_unit,
            'checkpoint_sha256': expected_checkpoint_sha256,
            'receipt_sha256': expected_receipt_sha256,
            'lock_identity': lock_binding,
            'bridge_asset_sha256': connector_observer_bridge_sha256(),
            'command_sha256': hashlib.sha256(command.encode('utf-8')).hexdigest(),
        })
        try:
            try:
                response = client.send_command(command)
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                observed = decode_native(response)
                if isinstance(observed, dict):
                    observed['connector_ownership'] = _normalize_empty_connector_routes(
                        observed.get('connector_ownership'))
                if (not isinstance(observed, dict)
                        or set(observed) != {'schema', 'session_id', 'actor_unit', 'tick',
                                             'connector_ownership'}
                        or observed['schema'] != 1
                        or observed['session_id'] != expected_session_id
                        or observed['actor_unit'] != expected_actor_unit
                        or type(observed['tick']) is not int
                        or not isinstance(observed['connector_ownership'], dict)
                        or observed['connector_ownership'].get('protocol') != 1
                        or observed['connector_ownership'].get('session_id') != expected_session_id
                        or observed['connector_ownership'].get('tick') != observed['tick']
                        or not isinstance(observed['connector_ownership'].get('routes'), dict)):
                    raise RuntimeError('Native observer did not emit the session-bound connector snapshot')
                after = readback(client, receipt_path=receipt_path,
                                 allow_unqualified_connector_bridge=True)
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                if (after['session_id'] != expected_session_id
                        or after['actor_unit'] != expected_actor_unit
                        or after['native_installation'] != attachment['native_installation']
                        or after['connector_snapshot_qualified'] is not True
                        or after['connector_snapshot_tick'] != observed['tick']
                        or after['connector_snapshot_ownership'] != observed['connector_ownership']):
                    raise RuntimeError('Native connector snapshot witness readback differs')
                if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                        or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
                    raise RuntimeError('Connector snapshot evidence changed after native observation')
                _lock_identity(lock_path, lock)
                _lock_binding(lock, lock_binding)
                _append_intent(intent_fd, {
                    'phase': 'qualified', 'snapshot_tick': observed['tick'],
                    'snapshot_sha256': connector_snapshot_sha256(observed['connector_ownership']),
                })
                _connector_witness(witness_path, after, receipt_path)
                return after
            except Exception as exc:
                _append_intent(intent_fd, {'phase': 'unknown', 'reason': type(exc).__name__})
                raise RuntimeError('Connector snapshot outcome unknown; reconcile read-only, never retry') from exc
        finally:
            os.close(intent_fd)


def reconcile_connector_snapshot_v1(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    witness_path: Path,
) -> str:
    """Verify a prior snapshot call without invoking any native observer."""
    if os.name != 'posix':
        raise RuntimeError('Native snapshot reconciliation requires the POSIX owner-lock host')
    import fcntl

    checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
    witness_path, lock_path = Path(witness_path), Path(lock_path)
    if witness_path != checkpoint_path.with_name(CONNECTOR_OBSERVER_WITNESS_NAME):
        raise RuntimeError('Connector snapshot witness must use its checkpoint-bound fixed path')
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'r+b') as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        rows = _intent_events(witness_path)
        first = rows[0]
        if (set(first) != {'schema', 'phase', 'session_id', 'actor_unit',
                           'checkpoint_sha256', 'receipt_sha256', 'lock_identity',
                           'bridge_asset_sha256', 'command_sha256'}
                or first['schema'] != SNAPSHOT_WITNESS_SCHEMA
                or first['phase'] != 'dispatching'
                or not isinstance(first['session_id'], str) or not first['session_id']
                or type(first['actor_unit']) is not int or first['actor_unit'] < 1
                or any(not _sha256_text(first[name]) for name in (
                    'checkpoint_sha256', 'receipt_sha256', 'bridge_asset_sha256',
                    'command_sha256'))
                or first['bridge_asset_sha256'] != connector_observer_bridge_sha256()
                or not isinstance(first['lock_identity'], dict)
                or set(first['lock_identity']) != {'device', 'inode'}
                or any(type(value) is not int or value < 0
                       for value in first['lock_identity'].values())
                or len(rows) not in {1, 2, 3}):
            raise RuntimeError('Connector snapshot intent requires reconciliation')
        _lock_binding(lock, first['lock_identity'])
        phases = [row.get('phase') for row in rows[1:]]
        if phases not in ([], ['unknown'], ['qualified'], ['unknown', 'qualified']):
            raise RuntimeError('Connector snapshot intent event order is invalid')
        for event in rows[1:]:
            if event.get('phase') == 'unknown':
                if (set(event) != {'phase', 'reason'} or not isinstance(event['reason'], str)
                        or not 1 <= len(event['reason']) <= 128):
                    raise RuntimeError('Connector snapshot ambiguity event is malformed')
            elif (set(event) != {'phase', 'snapshot_tick', 'snapshot_sha256'}
                  or type(event['snapshot_tick']) is not int
                  or not _sha256_text(event['snapshot_sha256'])):
                raise RuntimeError('Connector snapshot qualification event is malformed')
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(receipt_path)) != first['receipt_sha256']):
            raise RuntimeError('Connector snapshot evidence changed; hold controller')
        observed = readback(client, receipt_path=receipt_path,
                            allow_unqualified_connector_bridge=True)
        _lock_identity(lock_path, lock)
        _lock_binding(lock, first['lock_identity'])
        if (observed['session_id'] != first['session_id']
                or observed['actor_unit'] != first['actor_unit']
                or observed['native_installation']['assets'].get(
                    'connector_observer_bridge_v1') != first['bridge_asset_sha256']):
            raise RuntimeError('Connector snapshot reconciliation identity changed')
        if observed['connector_snapshot_qualified'] is not True:
            if rows[-1].get('phase') == 'qualified':
                raise RuntimeError('Durable connector snapshot claim contradicts native readback')
            return 'connector_snapshot_attempt_consumed_unqualified'
        proof = {'phase': 'qualified',
                 'snapshot_tick': observed['connector_snapshot_tick'],
                 'snapshot_sha256': connector_snapshot_sha256(
                     observed['connector_snapshot_ownership'])}
        if rows[-1].get('phase') != 'qualified':
            append_fd = os.open(witness_path, os.O_WRONLY | os.O_APPEND
                                | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                _append_intent(append_fd, proof)
            finally:
                os.close(append_fd)
        _connector_witness(witness_path, observed, receipt_path)
        return 'connector_snapshot_qualified'
