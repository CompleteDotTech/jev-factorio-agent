"""One-use, source-bound migration from the installed 256-tile observer.

Only the existing service owner may invoke this under its single-writer lock.
An ambiguous RCON response requires read-only reconciliation, never a retry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from importlib.resources import files

from ..memory import load_checkpoint
from .native_attachment import (
    CALLBACKS_EXPR, EXPANDED_OBSERVATION_ASSET, EXPANDED_OBSERVATION_PROFILE,
    EXPANDED_OBSERVATION_SHA256, LEGACY_OBSERVATION_PROFILE,
    LEGACY_OBSERVATION_SHA256, NATIVE_SCHEMA, PINNED_ASSETS,
    PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE, readback,
)
from .native_observation_migration import _digest, _private_bytes


SENTINEL = 'JEV_NATIVE_OBSERVATION_MIGRATED|3'


def _manifest(attachment: dict) -> dict:
    native = attachment.get('native_installation')
    modules = attachment.get('modules')
    if (not isinstance(native, dict) or not isinstance(modules, dict)
            or native.get('schema') != NATIVE_SCHEMA
            or native.get('profile') != LEGACY_OBSERVATION_PROFILE
            or native.get('session_id') != attachment.get('session_id')
            or native.get('actor_unit') != attachment.get('actor_unit')
            or modules.get('connector_ownership') is not False
            or modules.get('successors') is not False):
        raise RuntimeError('Expanded observer requires exact v2 installation')
    expected = {name: (LEGACY_OBSERVATION_SHA256 if name == 'observation_v2'
                       else PINNED_ASSETS[name])
                for name, enabled in modules.items()
                if enabled and name in PINNED_ASSETS}
    if native.get('assets') != expected:
        raise RuntimeError('Installed v2 assets require reconciliation')
    return {**native, 'profile': EXPANDED_OBSERVATION_PROFILE,
            'assets': {**expected, 'observation_v2': EXPANDED_OBSERVATION_SHA256}}


def _original_receipt(raw: bytes, session_id: str, actor_unit: int) -> None:
    try:
        receipt = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError('Original attachment receipt requires reconciliation') from exc
    if receipt != {
            'schema': 'jev.native-attachment.v1', 'session_id': session_id,
            'actor_unit': actor_unit, 'installed_source_commit': PINNED_SOURCE_COMMIT,
            'installed_source_tree': PINNED_SOURCE_TREE,
            'installed_assets': PINNED_ASSETS}:
        raise RuntimeError('Original attachment receipt requires reconciliation')


def _command(attachment: dict) -> str:
    source = files('jev_factorio').joinpath('lua/' + EXPANDED_OBSERVATION_ASSET).read_bytes()
    if _digest(source) != EXPANDED_OBSERVATION_SHA256:
        raise RuntimeError('Expanded observer source differs from reviewed asset')
    proposed = _manifest(attachment)
    old = attachment['native_installation']
    assets = json.dumps(old['assets'], sort_keys=True, separators=(',', ':'))
    session = json.dumps(attachment['session_id'])
    actor = attachment['actor_unit']
    return '/sc ' + (
        'local rt=assert(jev_fle_runtime); local storage=rt; local c=assert(rt.campaign); '
        'local f=assert(rt.fair); local a=assert(rt.agent_characters[1]); '
        'local n=assert(rt.native_installation); '
        'assert(rt.jev_session_id==' + session + ' and a.valid and a.unit_number=='
        + str(actor) + ' and f.actor().character==a); '
        'assert(game.speed==1 and not game.tick_paused); '
        'assert(not f.job or f.job.status=="completed" or f.job.status=="failed"); '
        'assert(not f.actor().walking_state.walking and not f.actor().mining_state.mining); '
        'assert(n.schema==' + json.dumps(NATIVE_SCHEMA) + ' and n.session_id=='
        + session + ' and n.actor_unit==' + str(actor) + ' and n.profile=='
        + json.dumps(LEGACY_OBSERVATION_PROFILE) + '); '
        'local expected=helpers.json_to_table(' + json.dumps(assets) + '); '
        'for name,hash in pairs(expected) do assert(n.assets[name]==hash) end; '
        'for name in pairs(n.assets) do assert(expected[name]~=nil) end; '
        'local old=assert(c.observation_snapshot_v2); '
        'assert(n.callbacks and n.callbacks.snapshot_v2==old); '
        'local old_callbacks=n.callbacks; '
        'local ok,err=pcall(function() do\n' + source.decode('utf-8') + '\nend; '
        'assert(c.observation_snapshot_v2~=old); '
        'n.assets.observation_v2=' + json.dumps(EXPANDED_OBSERVATION_SHA256) + '; '
        'n.profile=' + json.dumps(EXPANDED_OBSERVATION_PROFILE) + '; '
        'n.callbacks=' + CALLBACKS_EXPR + ' end); '
        'if not ok then c.observation_snapshot_v2=old; '
        'n.assets.observation_v2=' + json.dumps(LEGACY_OBSERVATION_SHA256) + '; '
        'n.profile=' + json.dumps(LEGACY_OBSERVATION_PROFILE) + '; '
        'n.callbacks=old_callbacks; error(err) end; '
        'rcon.print(' + json.dumps(SENTINEL) + ')'
    )


def migrate_observation_anchor_v3(
    client, *, checkpoint_path: Path, receipt_path: Path, lock_path: Path,
    expected_session_id: str, expected_actor_unit: int, expected_target: str,
    expected_checkpoint_sha256: str, expected_receipt_sha256: str,
) -> dict:
    """Migrate once; leave checkpoint and original attachment receipt untouched."""
    if os.name != 'posix':
        raise RuntimeError('Native migration requires the POSIX owner-lock host')
    import fcntl

    lock_path = Path(lock_path)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError('Existing single-writer lock is required')
    lock_stat = lock_path.stat()
    if lock_stat.st_uid != os.geteuid() or lock_stat.st_mode & 0o077:
        raise RuntimeError('Single-writer lock must be owned by the controller and private')
    with lock_path.open('r+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        checkpoint_path, receipt_path = Path(checkpoint_path), Path(receipt_path)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence hash changed')
        _original_receipt(_private_bytes(receipt_path),
                          expected_session_id, expected_actor_unit)
        memory = load_checkpoint(checkpoint_path, expected_session_id, expected_target)
        if (memory.status not in {'running', 'blocked'} or memory.pending is not None
                or memory.attempt is not None or memory.transfer_recovery is not None):
            raise RuntimeError('Controller has unresolved work; migration refused')
        attachment = readback(client, receipt_path=receipt_path)
        if (attachment['session_id'] != expected_session_id
                or attachment['actor_unit'] != expected_actor_unit):
            raise RuntimeError('Native session or actor changed')
        proposed = _manifest(attachment)
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence changed during preflight')
        try:
            response = client.send_command(_command(attachment))
        except Exception as exc:
            raise RuntimeError(
                'Migration outcome unknown; inspect native manifest read-only, never retry'
            ) from exc
        if not isinstance(response, str) or not response.strip().endswith(SENTINEL):
            raise RuntimeError('Migration acknowledgement absent; inspect native manifest read-only')
        after = readback(client)
        if (after['session_id'] != expected_session_id
                or after['actor_unit'] != expected_actor_unit
                or after['modules'] != attachment['modules']
                or after['native_installation'] != proposed):
            raise RuntimeError('Migration postcondition failed; stop dispatch')
        if (_digest(_private_bytes(checkpoint_path)) != expected_checkpoint_sha256
                or _digest(_private_bytes(receipt_path)) != expected_receipt_sha256):
            raise RuntimeError('Migration evidence changed after native transaction')
        return after
