"""One-use, owner-locked metadata preparation before the separate v5 install.

This never installs a native asset or dispatches an actor action. Its fixed
intent and backup are retained even when the checkpoint write is ambiguous.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..connector_checkpoint import validate_binding
from ..checkpoint_io import checkpoint_data
from ..memory import load_checkpoint
from .native_attachment import decode_native, readback
from .native_manual_cycle_migration import (
    _append_intent, _intent_events, _manifest, _new_intent, _no_open_investment,
)
from .native_observation_migration import _digest, _private_bytes
from .native_observation_water_origin_migration import (
    _lock_identity, _original_receipt, _quiescent,
)

INTENT_NAME = 'native-manual-cycle-v5-checkpoint-prep.intent.jsonl'
BACKUP_NAME = 'native-manual-cycle-v5-checkpoint-prep.backup.json'
ABSENCE_COMMAND = (
    '/sc local rt=assert(jev_fle_runtime); local c=assert(rt.campaign); '
    'local f=assert(rt.fair); local n=assert(rt.native_installation); '
    'local q=assert(rt.coal_supply); local coal_clear=not q.committed and '
    'q.revision==4 and type(q.rows)=="table"; '
    'for _,row in pairs(q.rows) do coal_clear=coal_clear and '
    'type(row)=="table" and type(row.parts)=="table" and '
    'not row.pending and not row.fault and not row.manual_pending and '
    'next(row.parts)==nil end; '
    'local a=assert(rt.agent_characters[1]); local cb=assert(n.callbacks); '
    'rcon.print(helpers.table_to_json({schema=1,session_id=rt.jev_session_id,'
    'actor_unit=a.unit_number,coal_clear=coal_clear,'
    'absent=c.connector_ledger==nil and '
    'c.connector_begin==nil and c.connector_finish==nil and '
    'c.connector_page==nil and c.observe_connector_ownership==nil and '
    'f.connector_place==nil and rt.coal_manual_journal_v1==nil and '
    'cb.journal_tick==nil and cb.connector_begin==nil and '
    'cb.connector_finish==nil and cb.connector_page==nil and '
    'cb.connector_observe==nil, idle=a.valid and '
    'not f.actor().walking_state.walking and '
    'not f.actor().mining_state.mining and '
    '(not f.job or f.job.status=="completed" or f.job.status=="failed")}))'
)


def _paths(checkpoint_path: Path, intent_path: Path, backup_path: Path) -> None:
    if (intent_path != checkpoint_path.with_name(INTENT_NAME)
            or backup_path != checkpoint_path.with_name(BACKUP_NAME)):
        raise RuntimeError('Checkpoint preparation requires fixed one-use paths')


def _native_absent(client, session_id: str, actor_unit: int) -> dict:
    observed = decode_native(client.send_command(ABSENCE_COMMAND))
    if observed != {'schema': 1, 'session_id': session_id,
                    'actor_unit': actor_unit, 'absent': True, 'idle': True,
                    'coal_clear': True}:
        raise RuntimeError('Native connector absence or actor idleness unqualified')
    return observed


def _prepared_bytes(memory) -> bytes:
    data = checkpoint_data(memory)
    data['connector_ownership'] = {
        'protocol': 1, 'session_id': memory.session_id, 'routes': {}}
    return json.dumps(data, sort_keys=True, allow_nan=False).encode('utf-8')


def _write_backup(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        remaining = raw
        while remaining:
            count = os.write(fd, remaining)
            if count < 1:
                raise RuntimeError('Checkpoint backup write failed')
            remaining = remaining[count:]
        os.fsync(fd)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(fd)


def _owner_lock(lock_path: Path):
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    return os.fdopen(fd, 'r+b')


def prepare_legacy_empty_connector_checkpoint(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    intent_path: Path, backup_path: Path, expected_session_id: str,
    expected_actor_unit: int, expected_target: str,
    expected_checkpoint_sha256: str, expected_receipt_sha256: str,
) -> dict:
    """Convert only legacy ``None`` to an empty binding; never self-grant v5."""
    if os.name != 'posix':
        raise RuntimeError('Checkpoint preparation requires the POSIX owner host')
    import fcntl

    checkpoint_path, receipt_path, lock_path = map(
        Path, (checkpoint_path, receipt_path, lock_path))
    intent_path, backup_path = Path(intent_path), Path(backup_path)
    _paths(checkpoint_path, intent_path, backup_path)
    if any(p.exists() or p.is_symlink() for p in (intent_path, backup_path)):
        raise RuntimeError('Checkpoint preparation already reserved; reconcile, never retry')
    migration_intent = checkpoint_path.with_name('native-manual-cycle-v5.intent.jsonl')
    if migration_intent.exists() or migration_intent.is_symlink():
        raise RuntimeError('Existing v5 installation attempt requires reconciliation')
    with _owner_lock(lock_path) as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        if migration_intent.exists() or migration_intent.is_symlink():
            raise RuntimeError('Existing v5 installation attempt requires reconciliation')
        before_raw = _private_bytes(checkpoint_path)
        receipt_raw = _private_bytes(receipt_path)
        if (_digest(before_raw) != expected_checkpoint_sha256
                or _digest(receipt_raw) != expected_receipt_sha256):
            raise RuntimeError('Checkpoint preparation evidence changed')
        _original_receipt(receipt_raw, expected_session_id, expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        _quiescent(memory)
        _no_open_investment(memory)
        if getattr(memory, 'successor_projects', None):
            raise RuntimeError('Successor ownership unresolved')
        if memory.connector_ownership is not None:
            validate_binding(memory.connector_ownership, expected_session_id)
            raise RuntimeError('Only a legacy null connector checkpoint can be prepared')
        attachment = readback(client, receipt_path=receipt_path)
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit):
            raise RuntimeError('Native session or actor changed')
        _manifest(attachment)
        native_absence = _native_absent(client, expected_session_id, expected_actor_unit)
        after_raw = _prepared_bytes(memory)
        after_sha = _digest(after_raw)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Checkpoint preparation evidence changed during readback')
        _lock_identity(lock_path, lock)
        intent_fd = _new_intent(intent_path, {
            'schema': 'jev.native-manual-cycle-checkpoint-prep.v1',
            'phase': 'prepared', 'session_id': expected_session_id,
            'actor_unit': expected_actor_unit, 'target': expected_target,
            'before_sha256': expected_checkpoint_sha256,
            'after_sha256': after_sha, 'receipt_sha256': expected_receipt_sha256,
            'v4_manifest_sha256': _digest(json.dumps(
                attachment['native_installation'], sort_keys=True,
                separators=(',', ':')).encode('utf-8')),
            'native_absence_sha256': _digest(json.dumps(
                native_absence, sort_keys=True, separators=(',', ':')).encode('utf-8')),
        })
        try:
            try:
                _write_backup(backup_path, before_raw)
                memory.connector_ownership = {
                    'protocol': 1, 'session_id': expected_session_id, 'routes': {}}
                memory.save(checkpoint_path)
                if (_private_bytes(checkpoint_path) != after_raw
                        or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
                    raise RuntimeError('Prepared checkpoint postcondition failed')
                _lock_identity(lock_path, lock)
                _append_intent(intent_fd, {'phase': 'qualified', 'after_sha256': after_sha})
                return {'status': 'checkpoint_prepared',
                        'before_sha256': expected_checkpoint_sha256,
                        'after_sha256': after_sha,
                        'receipt_sha256': expected_receipt_sha256,
                        'native_v4_manifest': attachment['native_installation']}
            except BaseException as exc:
                _append_intent(intent_fd, {'phase': 'unknown', 'reason': type(exc).__name__})
                raise RuntimeError('Checkpoint preparation outcome unknown; reconcile read-only, never retry') from exc
        finally:
            os.close(intent_fd)


def reconcile_legacy_empty_connector_checkpoint(
    *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    intent_path: Path, backup_path: Path,
) -> str:
    """Classify a consumed preparation by immutable files; never rewrite it."""
    if os.name != 'posix':
        raise RuntimeError('Checkpoint reconciliation requires the POSIX owner host')
    import fcntl

    checkpoint_path, receipt_path, lock_path = map(
        Path, (checkpoint_path, receipt_path, lock_path))
    intent_path, backup_path = Path(intent_path), Path(backup_path)
    _paths(checkpoint_path, intent_path, backup_path)
    with _owner_lock(lock_path) as lock:
        _lock_identity(lock_path, lock)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_identity(lock_path, lock)
        events = _intent_events(intent_path)
        first = events[0]
        expected_keys = {'schema', 'phase', 'session_id', 'actor_unit', 'target',
                         'before_sha256', 'after_sha256', 'receipt_sha256',
                         'v4_manifest_sha256', 'native_absence_sha256'}
        if (set(first) != expected_keys
                or first['schema'] != 'jev.native-manual-cycle-checkpoint-prep.v1'
                or first['phase'] != 'prepared'
                or not isinstance(first['session_id'], str) or not first['session_id']
                or type(first['actor_unit']) is not int or first['actor_unit'] < 1
                or not isinstance(first['target'], str) or not first['target']
                or any(not isinstance(first[key], str) or len(first[key]) != 64
                       or any(c not in '0123456789abcdef' for c in first[key])
                       for key in expected_keys if key.endswith('_sha256'))
                or len(events) > 2
                or (len(events) == 2 and events[1] not in (
                    {'phase': 'qualified', 'after_sha256': first['after_sha256']},
                    {'phase': 'unknown', 'reason': events[1].get('reason')}))):
            raise RuntimeError('Checkpoint preparation intent requires reconciliation')
        if len(events) == 2 and events[1]['phase'] == 'unknown':
            reason = events[1].get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason) <= 128:
                raise RuntimeError('Checkpoint preparation intent requires reconciliation')
        if (_digest(_private_bytes(receipt_path)) != first['receipt_sha256']):
            raise RuntimeError('Original receipt changed; stop dispatch')
        _original_receipt(_private_bytes(receipt_path), first['session_id'], first['actor_unit'])
        if (_digest(_private_bytes(backup_path)) != first['before_sha256']):
            raise RuntimeError('Checkpoint backup missing or changed; stop dispatch')
        before = load_checkpoint(backup_path, first['session_id'], first['target'])
        if before.connector_ownership is not None:
            raise RuntimeError('Checkpoint backup is not legacy empty')
        expected_after = _digest(_prepared_bytes(before))
        if expected_after != first['after_sha256']:
            raise RuntimeError('Prepared checkpoint digest changed; stop dispatch')
        current = _digest(_private_bytes(checkpoint_path))
        if current == first['before_sha256']:
            if len(events) == 2 and events[1]['phase'] == 'qualified':
                raise RuntimeError('Qualified preparation contradicts old checkpoint')
            return 'old_checkpoint_attempt_consumed'
        if current == first['after_sha256']:
            memory = load_checkpoint(checkpoint_path, first['session_id'], first['target'])
            binding = validate_binding(memory.connector_ownership, first['session_id'])
            if binding['routes']:
                raise RuntimeError('Prepared connector binding is not empty')
            return 'prepared_checkpoint_present'
        raise RuntimeError('Checkpoint changed beyond one-use preparation; stop dispatch')
