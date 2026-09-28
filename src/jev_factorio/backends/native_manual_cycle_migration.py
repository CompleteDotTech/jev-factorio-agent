"""One-use, source-bound opt-in v4 to v5 manual-journal migration.

This operation is deliberately separate from gameplay actions. An ambiguous
response requires read-only manifest reconciliation, never a blind retry.
"""
from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path

from ..connector_checkpoint import validate_binding
from ..memory import load_checkpoint
from .native_attachment import (
    CALLBACKS_EXPR, MANUAL_CYCLE_PROFILE, NATIVE_SCHEMA, PINNED_ASSETS,
    WATER_ORIGIN_OBSERVATION_PROFILE, WATER_ORIGIN_OBSERVATION_SHA256,
    connector_ownership_sha256, manual_journal_sha256, readback,
)
from .native_observation_migration import _digest, _private_bytes
from .native_observation_water_origin_migration import (
    _lock_identity, _original_receipt, _quiescent,
)

SENTINEL = 'JEV_NATIVE_MANUAL_CYCLE_MIGRATED|5'


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
                       'coal_manual_journal_v1': manual_journal_sha256()}}


def _command(attachment: dict) -> str:
    connector_source = files('jev_factorio').joinpath('lua/connector_ownership.lua').read_text()
    journal_source = files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_text()
    proposed = _manifest(attachment)
    old = attachment['native_installation']
    assets = json.dumps(old['assets'], sort_keys=True, separators=(',', ':'))
    session = json.dumps(attachment['session_id'])
    actor = str(attachment['actor_unit'])
    return '/sc ' + (
        'local rt=assert(jev_fle_runtime); local storage=rt; '
        'local c=assert(rt.campaign); local f=assert(rt.fair); '
        'local a=assert(rt.agent_characters[1]); '
        'local n=assert(rt.native_installation); local q=assert(rt.coal_supply); '
        'assert(rt.jev_session_id==' + session + ' and a.valid and a.unit_number=='
        + actor + ' and f.actor().character==a); '
        'assert(game.speed==1 and not game.tick_paused); '
        'assert(not f.job or f.job.status=="completed" or f.job.status=="failed"); '
        'assert(not f.actor().walking_state.walking and not f.actor().mining_state.mining); '
        'assert(c.connector_ledger==nil and c.connector_begin==nil and c.connector_finish==nil '
        'and c.connector_page==nil and c.observe_connector_ownership==nil '
        'and f.connector_place==nil and rt.coal_manual_journal_v1==nil); '
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
        'local old_place=f.connector_place; '
        'local ok,err=pcall(function() do\n' + connector_source + '\nend; '
        'assert(c.connector_ledger and c.connector_ledger.protocol==1 '
        'and next(c.connector_ledger.routes)==nil and c.connector_ledger.active==nil); '
        'do\n' + journal_source + '\nend; '
        'assert(rt.coal_manual_journal_v1 and rt.coal_manual_journal_v1.protocol==1); '
        'n.assets.connector_ownership=' + json.dumps(connector_ownership_sha256()) + '; '
        'n.assets.coal_manual_journal_v1=' + json.dumps(manual_journal_sha256()) + '; '
        'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE) + '; '
        'n.callbacks=' + CALLBACKS_EXPR + ' end); '
        'if not ok then script.on_nth_tick(1,nil); rt.coal_manual_journal_v1=nil; '
        'c.connector_ledger=old_ledger; c.connector_begin=old_begin; '
        'c.connector_finish=old_finish; c.connector_page=old_page; '
        'c.observe_connector_ownership=old_observe; f.connector_place=old_place; '
        'n.assets.connector_ownership=nil; '
        'n.assets.coal_manual_journal_v1=nil; '
        'n.profile=' + json.dumps(WATER_ORIGIN_OBSERVATION_PROFILE) + '; '
        'n.callbacks=old_callbacks; error(err) end; '
        'assert(n.assets.connector_ownership=='
        + json.dumps(proposed['assets']['connector_ownership']) + ' '
        'and n.assets.coal_manual_journal_v1=='
        + json.dumps(proposed['assets']['coal_manual_journal_v1']) + '); '
        'rcon.print(' + json.dumps(SENTINEL) + ')'
    )


def migrate_manual_cycle_v5(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    expected_session_id: str, expected_actor_unit: int, expected_target: str,
    expected_checkpoint_sha256: str, expected_receipt_sha256: str,
) -> dict:
    """Install both optional assets only under the quiescent single-writer lock."""
    if os.name != 'posix':
        raise RuntimeError('Native migration requires the POSIX owner-lock host')
    import fcntl

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
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence changed during preflight')
        _lock_identity(lock_path, lock)
        try:
            response = client.send_command(_command(attachment))
        except Exception as exc:
            raise RuntimeError('Migration outcome unknown; inspect native manifest read-only, never retry') from exc
        if not isinstance(response, str) or not response.strip().endswith(SENTINEL):
            raise RuntimeError('Migration acknowledgement absent; inspect native manifest read-only')
        after = readback(client)
        expected_modules = {**attachment['modules'], 'connector_ownership': True,
                            'coal_manual_journal_v1': True}
        if (after['session_id'] != expected_session_id
                or after['actor_unit'] != expected_actor_unit
                or after['modules'] != expected_modules
                or after['native_installation'] != proposed):
            raise RuntimeError('Migration postcondition failed; stop dispatch')
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence changed after native transaction')
        return after
