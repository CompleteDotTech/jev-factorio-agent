"""One-use, source-bound opt-in v4 to v5 manual-journal migration.

This operation is deliberately separate from gameplay actions. An ambiguous
response requires read-only manifest reconciliation, never a blind retry.
"""
from __future__ import annotations

import json
import os
import hashlib
from importlib.resources import files
from pathlib import Path

from ..connector_checkpoint import validate_binding
from ..memory import load_checkpoint
from .native_attachment import (
    CALLBACKS_EXPR, MANUAL_CYCLE_PROFILE, NATIVE_SCHEMA, PINNED_ASSETS,
    WATER_ORIGIN_OBSERVATION_PROFILE, WATER_ORIGIN_OBSERVATION_SHA256,
    connector_observer_bridge_sha256, connector_ownership_sha256,
    manual_journal_sha256, readback,
)
from .native_observation_migration import _digest, _private_bytes
from .native_observation_water_origin_migration import (
    _lock_identity, _original_receipt, _quiescent,
)

SENTINEL = 'JEV_NATIVE_MANUAL_CYCLE_MIGRATED|5'


def _new_intent(path: Path, event: dict) -> int:
    """Durably reserve this migration attempt before the first mutating RPC."""
    path = Path(path)
    parent = path.parent
    stat = parent.stat()
    if (parent.is_symlink() or stat.st_uid != os.geteuid()
            or stat.st_mode & 0o077):
        raise RuntimeError('Migration intent directory must be private and owned')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        _append_intent(fd, event)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _append_intent(fd: int, event: dict) -> None:
    payload = (json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    while payload:
        written = os.write(fd, payload)
        if written < 1:
            raise RuntimeError('Migration intent write failed')
        payload = payload[written:]
    os.fsync(fd)


def _intent_events(path: Path) -> list[dict]:
    data = _private_bytes(Path(path))
    if not data.endswith(b'\n') or len(data) > 32768:
        raise RuntimeError('Migration intent requires reconciliation')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate migration intent key')
            result[key] = value
        return result
    def reject_constant(_):
        raise ValueError('Nonfinite migration intent value')
    try:
        rows = [json.loads(line, object_pairs_hook=unique_pairs,
                           parse_constant=reject_constant)
                for line in data.splitlines()]
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError('Migration intent requires reconciliation') from exc
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError('Migration intent requires reconciliation')
    return rows


def _sha256_text(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _valid_followup(row: dict) -> bool:
    if row.get('phase') == 'unknown':
        return (set(row) == {'phase', 'reason'} and isinstance(row['reason'], str)
                and 1 <= len(row['reason']) <= 128)
    if row.get('phase') == 'qualified':
        return set(row) == {'phase', 'readback_sha256'} and _sha256_text(row['readback_sha256'])
    return row == {'phase': 'reconciled_v5'}


def _no_open_investment(memory) -> None:
    if any(getattr(memory, name, None) is not None
           for name in ('capital_investment', 'solid_funding', 'coal_funding')):
        raise RuntimeError('Controller has unresolved investment; migration refused')


def _manifest(attachment: dict) -> dict:
    native = attachment.get('native_installation')
    modules = attachment.get('modules')
    if (not isinstance(native, dict) or not isinstance(modules, dict)
            or native.get('schema') != NATIVE_SCHEMA
            or native.get('profile') != WATER_ORIGIN_OBSERVATION_PROFILE
            or native.get('session_id') != attachment.get('session_id')
            or native.get('actor_unit') != attachment.get('actor_unit')
            or modules.get('connector_ownership') is not False
            or modules.get('successors') is not False
            or modules.get('coal_manual_journal_v1') is not False):
        raise RuntimeError('Manual-cycle journal requires exact v4 installation')
    expected = {name: (WATER_ORIGIN_OBSERVATION_SHA256 if name == 'observation_v2'
                       else PINNED_ASSETS[name])
                for name, enabled in modules.items()
                if enabled and name in PINNED_ASSETS}
    if native.get('assets') != expected:
        raise RuntimeError('Installed v4 assets require reconciliation')
    return {**native, 'profile': MANUAL_CYCLE_PROFILE,
            'assets': {**expected,
                       'connector_ownership': connector_ownership_sha256(),
                       'coal_manual_journal_v1': manual_journal_sha256(),
                       'connector_observer_bridge_v1': connector_observer_bridge_sha256()}}


def _command(attachment: dict) -> str:
    connector_source = files('jev_factorio').joinpath('lua/connector_ownership.lua').read_text()
    journal_source = files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_text()
    bridge_source = files('jev_factorio').joinpath(
        'lua/connector_observer_bridge_v1.lua').read_text()
    proposed = _manifest(attachment)
    old = attachment['native_installation']
    assets = json.dumps(old['assets'], sort_keys=True, separators=(',', ':'))
    session = json.dumps(attachment['session_id'])
    actor = str(attachment['actor_unit'])
    return '/sc ' + (
        'local rt=assert(jev_fle_runtime); local storage=rt; '
        'local c=assert(rt.campaign); local f=assert(rt.fair); '
        'local solid=assert(rt.solid_routes); '
        'local a=assert(rt.agent_characters[1]); '
        'local n=assert(rt.native_installation); local q=assert(rt.coal_supply); '
        'assert(rt.jev_session_id==' + session + ' and a.valid and a.unit_number=='
        + actor + ' and f.actor().character==a); '
        'assert(game.speed==1 and not game.tick_paused); '
        'assert(not f.job or f.job.status=="completed" or f.job.status=="failed"); '
        'assert(not f.actor().walking_state.walking and not f.actor().mining_state.mining); '
        'assert(c.connector_ledger==nil and c.connector_begin==nil and c.connector_finish==nil '
        'and c.connector_page==nil and c.observe_connector_ownership==nil '
        'and f.connector_place==nil and rt.coal_manual_journal_v1==nil '
        'and rt.connector_observer_bridge_v1==nil); '
        'assert(c.observe==solid.observer and n.callbacks and n.callbacks.observe==c.observe); '
        'assert(not q.committed and q.revision==4); '
        'for _,row in pairs(q.rows) do assert(not row.pending and not row.fault '
        'and not row.manual_pending and next(row.parts)==nil) end; '
        'assert(n.callbacks and n.callbacks.journal_tick==nil '
        'and n.callbacks.connector_begin==nil and n.callbacks.connector_finish==nil '
        'and n.callbacks.connector_page==nil and n.callbacks.connector_observe==nil); '
        'assert(n.schema==' + json.dumps(NATIVE_SCHEMA) + ' and n.session_id=='
        + session + ' and n.actor_unit==' + actor + ' and n.profile=='
        + json.dumps(WATER_ORIGIN_OBSERVATION_PROFILE) + '); '
        'local expected=helpers.json_to_table(' + json.dumps(assets) + '); '
        'for name,hash in pairs(expected) do assert(n.assets[name]==hash) end; '
        'for name in pairs(n.assets) do assert(expected[name]~=nil) end; '
        'local old_callbacks=n.callbacks; '
        'local old_ledger=c.connector_ledger; local old_begin=c.connector_begin; '
        'local old_finish=c.connector_finish; local old_page=c.connector_page; '
        'local old_observe=c.observe_connector_ownership; '
        'local old_place=f.connector_place; local old_campaign_observe=c.observe; '
        'local old_solid_observer=solid.observer; '
        'local old_bridge=rt.connector_observer_bridge_v1; '
        'local ok,err=pcall(function() do\n' + connector_source + '\nend; '
        'assert(c.connector_ledger and c.connector_ledger.protocol==1 '
        'and next(c.connector_ledger.routes)==nil and c.connector_ledger.active==nil); '
        'do\n' + journal_source + '\nend; '
        'assert(rt.coal_manual_journal_v1 and rt.coal_manual_journal_v1.protocol==1); '
        'do\n' + bridge_source + '\nend; '
        'assert(rt.connector_observer_bridge_v1 and rt.connector_observer_bridge_v1.protocol==1 '
        'and c.observe==solid.observer and c.observe==rt.connector_observer_bridge_v1.observer); '
        'n.assets.connector_ownership=' + json.dumps(connector_ownership_sha256()) + '; '
        'n.assets.coal_manual_journal_v1=' + json.dumps(manual_journal_sha256()) + '; '
        'n.assets.connector_observer_bridge_v1=' + json.dumps(connector_observer_bridge_sha256()) + '; '
        'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE) + '; '
        'n.callbacks=' + CALLBACKS_EXPR + ' end); '
        'if not ok then script.on_nth_tick(1,nil); rt.coal_manual_journal_v1=nil; '
        'c.connector_ledger=old_ledger; c.connector_begin=old_begin; '
        'c.connector_finish=old_finish; c.connector_page=old_page; '
        'c.observe_connector_ownership=old_observe; f.connector_place=old_place; '
        'c.observe=old_campaign_observe; solid.observer=old_solid_observer; '
        'rt.connector_observer_bridge_v1=old_bridge; '
        'n.assets.connector_ownership=nil; '
        'n.assets.coal_manual_journal_v1=nil; '
        'n.assets.connector_observer_bridge_v1=nil; '
        'n.profile=' + json.dumps(WATER_ORIGIN_OBSERVATION_PROFILE) + '; '
        'n.callbacks=old_callbacks; error(err) end; '
        'assert(n.assets.connector_ownership=='
        + json.dumps(proposed['assets']['connector_ownership']) + ' '
        'and n.assets.coal_manual_journal_v1=='
        + json.dumps(proposed['assets']['coal_manual_journal_v1'])
        + ' and n.assets.connector_observer_bridge_v1=='
        + json.dumps(proposed['assets']['connector_observer_bridge_v1']) + '); '
        'rcon.print(' + json.dumps(SENTINEL) + ')'
    )


def migrate_manual_cycle_v5(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    intent_path: Path,
    expected_session_id: str, expected_actor_unit: int, expected_target: str,
    expected_checkpoint_sha256: str, expected_receipt_sha256: str,
) -> dict:
    """Install both optional assets only under the quiescent single-writer lock."""
    if os.name != 'posix':
        raise RuntimeError('Native migration requires the POSIX owner-lock host')
    import fcntl

    intent_path = Path(intent_path)
    checkpoint_path = Path(checkpoint_path)
    if intent_path != checkpoint_path.with_name('native-manual-cycle-v5.intent.jsonl'):
        raise RuntimeError('Migration intent must use the checkpoint-bound fixed path')
    if intent_path.exists() or intent_path.is_symlink():
        raise RuntimeError('Migration intent already exists; reconcile read-only, never retry')
    lock_path = Path(lock_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    lock_stat = lock_path.stat()
    if lock_stat.st_uid != os.geteuid() or lock_stat.st_mode & 0o077:
        raise RuntimeError('Single-writer lock must be owned by the controller and private')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'r+b') as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence hash changed')
        _original_receipt(_private_bytes(receipt_path), expected_session_id, expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        _quiescent(memory)
        _no_open_investment(memory)
        binding = validate_binding(memory.connector_ownership, expected_session_id)
        if binding['routes']:
            raise RuntimeError('Connector checkpoint must be exactly empty')
        attachment = readback(client, receipt_path=receipt_path)
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit):
            raise RuntimeError('Native session or actor changed')
        proposed = _manifest(attachment)
        command = _command(attachment)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence changed during preflight')
        _lock_identity(lock_path, lock)
        intent_fd = _new_intent(intent_path, {
            'schema': 'jev.native-manual-cycle-intent.v1',
            'phase': 'dispatching',
            'session_id': expected_session_id,
            'actor_unit': expected_actor_unit,
            'target': expected_target,
            'checkpoint_sha256': expected_checkpoint_sha256,
            'receipt_sha256': expected_receipt_sha256,
            'before': attachment['native_installation'],
            'after': proposed,
            'before_modules': attachment['modules'],
            'after_modules': {**attachment['modules'], 'connector_ownership': True,
                              'coal_manual_journal_v1': True,
                              'connector_observer_bridge_v1': True},
            'command_sha256': hashlib.sha256(command.encode('utf-8')).hexdigest(),
        })
        try:
            try:
                response = client.send_command(command)
                if not isinstance(response, str) or not response.strip().endswith(SENTINEL):
                    raise RuntimeError('Migration acknowledgement absent')
                after = readback(client, allow_unqualified_connector_bridge=True)
                expected_modules = {**attachment['modules'], 'connector_ownership': True,
                                    'coal_manual_journal_v1': True,
                                    'connector_observer_bridge_v1': True}
                if (after['session_id'] != expected_session_id
                        or after['actor_unit'] != expected_actor_unit
                        or after['modules'] != expected_modules
                        or after['native_installation'] != proposed):
                    raise RuntimeError('Migration postcondition failed')
                if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                        or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
                    raise RuntimeError('Migration evidence changed after native transaction')
                _append_intent(intent_fd, {'phase': 'qualified',
                    'readback_sha256': hashlib.sha256(json.dumps(
                        after, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()})
                return after
            except Exception as exc:
                _append_intent(intent_fd, {'phase': 'unknown', 'reason': type(exc).__name__})
                raise RuntimeError('Migration outcome unknown; inspect native manifest read-only, never retry') from exc
        finally:
            os.close(intent_fd)


def reconcile_manual_cycle_v5(
    client, *, intent_path: Path, lock_path: Path,
    checkpoint_path: Path, receipt_path: Path,
) -> str:
    """Classify an existing one-use attempt by readback; never redispatch it."""
    if os.name != 'posix':
        raise RuntimeError('Native reconciliation requires the POSIX owner-lock host')
    import fcntl

    intent_path, checkpoint_path = Path(intent_path), Path(checkpoint_path)
    if intent_path != checkpoint_path.with_name('native-manual-cycle-v5.intent.jsonl'):
        raise RuntimeError('Migration intent must use the checkpoint-bound fixed path')
    lock_path = Path(lock_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'r+b') as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        rows = _intent_events(intent_path)
        first = rows[0]
        if (set(first) != {'schema', 'phase', 'session_id', 'actor_unit', 'target',
                           'checkpoint_sha256', 'receipt_sha256', 'before', 'after',
                           'before_modules', 'after_modules', 'command_sha256'}
                or first['schema'] != 'jev.native-manual-cycle-intent.v1'
                or first['phase'] != 'dispatching'
                or not isinstance(first['session_id'], str) or not first['session_id']
                or type(first['actor_unit']) is not int or first['actor_unit'] < 1
                or not isinstance(first['target'], str) or not first['target']
                or any(not _sha256_text(first[name])
                       for name in ('checkpoint_sha256', 'receipt_sha256', 'command_sha256'))
                or not isinstance(first['before'], dict)
                or not isinstance(first['after'], dict)
                or not isinstance(first['before_modules'], dict)
                or not isinstance(first['after_modules'], dict)
                or set(first['before_modules']) != set(first['after_modules'])
                or any(type(value) is not bool for value in first['before_modules'].values())
                or any(type(value) is not bool for value in first['after_modules'].values())
                or first['after_modules'] != {**first['before_modules'],
                    'connector_ownership': True, 'coal_manual_journal_v1': True,
                    'connector_observer_bridge_v1': True}
                or [row.get('phase') for row in rows[1:]] not in (
                    [], ['unknown'], ['qualified'], ['unknown', 'reconciled_v5'])
                or any(not _valid_followup(row) for row in rows[1:])):
            raise RuntimeError('Migration intent requires reconciliation')
        if (first['before'].get('session_id') != first['session_id']
                or first['before'].get('actor_unit') != first['actor_unit']
                or first['after'].get('session_id') != first['session_id']
                or first['after'].get('actor_unit') != first['actor_unit']):
            raise RuntimeError('Migration intent identity requires reconciliation')
        try:
            expected_after = _manifest({
                'native_installation': first['before'],
                'modules': first['before_modules'],
                'session_id': first['session_id'],
                'actor_unit': first['actor_unit'],
            })
        except RuntimeError as exc:
            raise RuntimeError('Migration intent profile requires reconciliation') from exc
        if first['after'] != expected_after:
            raise RuntimeError('Migration intent profile requires reconciliation')
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']):
            raise RuntimeError('Migration evidence changed; stop dispatch')
        observed = readback(client, allow_unqualified_connector_bridge=True)
        if (_digest(_private_bytes(checkpoint_path)) != first['checkpoint_sha256']
                or _digest(_private_bytes(Path(receipt_path))) != first['receipt_sha256']):
            raise RuntimeError('Migration evidence changed during readback; stop dispatch')
        if (observed['session_id'] != first['session_id']
                or observed['actor_unit'] != first['actor_unit']):
            raise RuntimeError('Migration session/actor changed; stop dispatch')
        profile = observed['native_installation']
        if profile == first['after']:
            if observed['modules'] != first['after_modules']:
                raise RuntimeError('Migration v5 modules mismatch; stop dispatch')
            if rows[-1]['phase'] not in {'qualified', 'reconciled_v5'}:
                append_fd = os.open(intent_path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    _append_intent(append_fd, {'phase': 'reconciled_v5'})
                finally:
                    os.close(append_fd)
            return 'v5_installed'
        if profile == first['before']:
            if rows[-1]['phase'] in {'qualified', 'reconciled_v5'}:
                raise RuntimeError('Migration journal success contradicts v4 readback; stop dispatch')
            if observed['modules'] != first['before_modules']:
                raise RuntimeError('Migration v4 modules mismatch; stop dispatch')
            return 'v4_observed_attempt_consumed'
        raise RuntimeError('Migration profile ambiguous; stop dispatch')
