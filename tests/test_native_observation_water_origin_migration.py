"""Source-only v3-to-v4 transaction tests; never connect to a game."""
import hashlib
import json
import os
from dataclasses import asdict
from importlib.resources import files

import pytest

from jev_factorio.backends.native_attachment import (
    EXPANDED_OBSERVATION_PROFILE, EXPANDED_OBSERVATION_SHA256,
    WATER_ORIGIN_OBSERVATION_PROFILE, WATER_ORIGIN_OBSERVATION_SHA256,
    PINNED_ASSETS, PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE, PROBE, readback,
)
from jev_factorio.backends.native_observation_anchor_migration import _manifest as v3_manifest
from jev_factorio.backends.native_observation_water_origin_migration import (
    SENTINEL, _command, _manifest, _lock_identity, _quiescent,
    migrate_observation_water_origin_v4,
)
from jev_factorio.memory import CampaignMemory
from test_native_observation_anchor_migration import installed_v2
from test_native_observation_migration import _lua_fixture


def installed_v3():
    row = installed_v2()
    row['native_installation'] = v3_manifest(row)
    return row


def test_exact_v3_manifest_and_v4_hash_required():
    row = installed_v3()
    proposed = _manifest(row)
    assert proposed['profile'] == WATER_ORIGIN_OBSERVATION_PROFILE
    assert proposed['assets']['observation_v2'] == WATER_ORIGIN_OBSERVATION_SHA256
    row['native_installation']['assets']['observation_v2'] = '0' * 64
    with pytest.raises(RuntimeError, match='Installed v3 assets'):
        _manifest(row)


def test_retained_v4_profile_cannot_silently_adopt_optional_coal_journal():
    row = installed_v3()
    row['native_installation'] = _manifest(row)
    row['modules']['coal_manual_journal_v1'] = True
    row['native_installation']['assets']['coal_manual_journal_v1'] = hashlib.sha256(
        files('jev_factorio').joinpath('lua/coal_manual_journal_v1.lua').read_bytes()).hexdigest()
    class Client:
        def send_command(self, command):
            return json.dumps(row)
    with pytest.raises(RuntimeError, match='Observation migration profile'):
        readback(Client())


@pytest.mark.parametrize('field,value', [
    ('active_plan', {'id': 'old-water-placement'}),
    ('background_job', {'receipt': 'in-flight'}),
    ('background_attempt', {'receipt': 'in-flight'}),
    ('input_commitments', {'iron': {'state': 'building'}}),
    ('solid_commitments', {'coal': {'state': 'building'}}),
    ('reservations', {'pump': {'iron-plate': 2}}),
])
def test_coordinate_migration_rejects_unreconciled_checkpoint_work(field, value):
    memory = CampaignMemory('retained-session', 'rocket_launch')
    setattr(memory, field, value)
    with pytest.raises(RuntimeError, match='unresolved work'):
        _quiescent(memory)


@pytest.mark.skipif(os.name != 'posix', reason='inode identity requires POSIX')
def test_open_lock_must_still_name_private_path(tmp_path):
    path = tmp_path / 'owner.lock'
    path.write_bytes(b'')
    path.chmod(0o600)
    with path.open('r+b') as handle:
        _lock_identity(path, handle)
        path.rename(tmp_path / 'old.lock')
        path.write_bytes(b'')
        path.chmod(0o600)
        with pytest.raises(RuntimeError, match='identity changed'):
            _lock_identity(path, handle)


def test_lua_transaction_rollback_and_one_use():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    runtime, old, output = _lua_fixture(lua)
    row = installed_v3()
    runtime.native_installation = lua.table_from(row['native_installation'], recursive=True)
    runtime.native_installation.callbacks = lua.table_from({'snapshot_v2': old})
    command = _command(row)
    assert callable(lua.eval('load')(command[4:]))
    same = lua.eval('function(a,b) return a==b end')
    needle = 'n.profile=' + json.dumps(WATER_ORIGIN_OBSERVATION_PROFILE)
    with pytest.raises(lua52.LuaError, match='injected'):
        lua.execute(command.replace(needle, 'error("injected"); ' + needle, 1)[4:])
    assert same(runtime.campaign.observation_snapshot_v2, old)
    assert runtime.native_installation.profile == EXPANDED_OBSERVATION_PROFILE
    assert runtime.native_installation.assets.observation_v2 == EXPANDED_OBSERVATION_SHA256
    assert same(runtime.native_installation.callbacks.snapshot_v2, old)
    assert SENTINEL not in output
    lua.execute(command[4:])
    assert runtime.native_installation.profile == WATER_ORIGIN_OBSERVATION_PROFILE
    assert runtime.native_installation.assets.observation_v2 == WATER_ORIGIN_OBSERVATION_SHA256
    assert same(runtime.native_installation.callbacks.snapshot_v2,
                runtime.campaign.observation_snapshot_v2)
    assert output[-1] == SENTINEL
    with pytest.raises(lua52.LuaError):
        lua.execute(command[4:])


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
def test_owner_locked_migration_reconciles_ambiguous_response(tmp_path):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    lock.write_bytes(b'')
    lock.chmod(0o600)
    memory = CampaignMemory('retained-session', 'rocket_launch')
    memory.status = 'blocked'
    raw = json.dumps(asdict(memory)).encode()
    checkpoint.write_bytes(raw)
    checkpoint.chmod(0o600)
    original = json.dumps({
        'schema': 'jev.native-attachment.v1', 'session_id': 'retained-session',
        'actor_unit': 2543, 'installed_source_commit': PINNED_SOURCE_COMMIT,
        'installed_source_tree': PINNED_SOURCE_TREE, 'installed_assets': PINNED_ASSETS,
    }).encode()
    receipt.write_bytes(original)
    receipt.chmod(0o600)

    class Client:
        def __init__(self, ambiguous=False):
            self.row = installed_v3()
            self.calls = 0
            self.ambiguous = ambiguous

        def send_command(self, command):
            if command == '/sc ' + PROBE:
                return json.dumps(self.row)
            self.calls += 1
            self.row['native_installation'] = _manifest(self.row)
            return '' if self.ambiguous else SENTINEL

    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  expected_session_id='retained-session', expected_actor_unit=2543,
                  expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(original).hexdigest())
    client = Client()
    assert migrate_observation_water_origin_v4(client, **kwargs)['native_installation']['profile'] == WATER_ORIGIN_OBSERVATION_PROFILE
    assert client.calls == 1 and checkpoint.read_bytes() == raw and receipt.read_bytes() == original
    with pytest.raises(RuntimeError, match='exact v3'):
        migrate_observation_water_origin_v4(client, **kwargs)
    assert client.calls == 1
    ambiguous = Client(ambiguous=True)
    with pytest.raises(RuntimeError, match='acknowledgement absent'):
        migrate_observation_water_origin_v4(ambiguous, **kwargs)
    assert readback(ambiguous)['native_installation']['profile'] == WATER_ORIGIN_OBSERVATION_PROFILE
    with pytest.raises(RuntimeError, match='exact v3'):
        migrate_observation_water_origin_v4(ambiguous, **kwargs)
    assert ambiguous.calls == 1
    checkpoint.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='hash changed'):
        migrate_observation_water_origin_v4(Client(), **kwargs)
