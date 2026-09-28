"""Versioned source-only migration tests; no native game or live RCON."""
import hashlib
import json
import os
from dataclasses import asdict
from importlib.resources import files

import pytest

from jev_factorio.backends.native_attachment import (
    EXPANDED_OBSERVATION_ASSET, EXPANDED_OBSERVATION_PROFILE,
    EXPANDED_OBSERVATION_SHA256, LEGACY_OBSERVATION_PROFILE,
    LEGACY_OBSERVATION_SHA256, PROBE, prepare_install_command, readback,
    require_asset, PINNED_ASSETS, PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE,
    WATER_ORIGIN_OBSERVATION_ASSET, WATER_ORIGIN_OBSERVATION_PROFILE,
    WATER_ORIGIN_OBSERVATION_SHA256,
)
from jev_factorio.backends.native_observation_anchor_migration import (
    SENTINEL, _command, _manifest, migrate_observation_anchor_v3,
)
from jev_factorio.backends.native_observation_migration import _manifest as old_manifest
from jev_factorio.memory import CampaignMemory
from test_native_observation_migration import _lua_fixture, legacy


def installed_v2():
    row = legacy()
    row['native_installation'] = old_manifest(row)
    return row


def test_new_asset_digest_and_fresh_installer_logical_identity():
    source = files('jev_factorio').joinpath('lua/' + EXPANDED_OBSERVATION_ASSET).read_bytes()
    assert hashlib.sha256(source).hexdigest() == EXPANDED_OBSERVATION_SHA256
    assert EXPANDED_OBSERVATION_SHA256 != LEGACY_OBSERVATION_SHA256
    assert prepare_install_command(source.decode('utf-8')) == source.decode('utf-8')
    source = files('jev_factorio').joinpath('lua/' + WATER_ORIGIN_OBSERVATION_ASSET).read_bytes()
    assert hashlib.sha256(source).hexdigest() == WATER_ORIGIN_OBSERVATION_SHA256
    command = prepare_install_command(source.decode('utf-8'))
    assert command != source.decode('utf-8')
    assert 'observation_v2' in command
    assert WATER_ORIGIN_OBSERVATION_SHA256 in command
    assert prepare_install_command(
        files('jev_factorio').joinpath('lua/observation_v2.lua').read_text()
    ) == files('jev_factorio').joinpath('lua/observation_v2.lua').read_text()


def test_old_and_new_attachment_profiles_are_exact_and_distinct():
    row = installed_v2()
    class Client:
        def send_command(self, command):
            return json.dumps(row)
    assert readback(Client())['native_installation']['profile'] == LEGACY_OBSERVATION_PROFILE
    assert require_asset(row, 'observation_v2')
    proposed = _manifest(row)
    assert proposed['profile'] == EXPANDED_OBSERVATION_PROFILE
    assert proposed['assets']['observation_v2'] == EXPANDED_OBSERVATION_SHA256
    row['native_installation'] = proposed
    assert readback(Client())['native_installation']['profile'] == EXPANDED_OBSERVATION_PROFILE
    assert require_asset(row, 'observation_v2')
    row['native_installation'] = {**proposed, 'profile': WATER_ORIGIN_OBSERVATION_PROFILE,
                                  'assets': {**proposed['assets'],
                                             'observation_v2': WATER_ORIGIN_OBSERVATION_SHA256}}
    assert readback(Client())['native_installation']['profile'] == WATER_ORIGIN_OBSERVATION_PROFILE
    assert require_asset(row, 'observation_v2')
    row['native_installation']['assets']['observation_v2'] = LEGACY_OBSERVATION_SHA256
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        readback(Client())
    with pytest.raises(RuntimeError, match='exact v2'):
        _manifest(row)


def test_lua_transaction_rolls_back_observer_hash_profile_and_callback():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    runtime, old, output = _lua_fixture(lua)
    row = installed_v2()
    runtime.native_installation = lua.table_from(row['native_installation'], recursive=True)
    runtime.native_installation.callbacks = lua.table_from({'snapshot_v2': old})
    same = lua.eval('function(a,b) return a==b end')
    command = _command(row)
    assert callable(lua.eval('load')(command[4:]))
    needle = 'n.profile=' + json.dumps(EXPANDED_OBSERVATION_PROFILE)
    injected = command.replace(needle, 'error("injected"); ' + needle, 1)
    with pytest.raises(lua52.LuaError, match='injected'):
        lua.execute(injected[4:])
    assert same(runtime.campaign.observation_snapshot_v2, old)
    assert runtime.native_installation.profile == LEGACY_OBSERVATION_PROFILE
    assert runtime.native_installation.assets.observation_v2 == LEGACY_OBSERVATION_SHA256
    assert same(runtime.native_installation.callbacks.snapshot_v2, old)
    assert SENTINEL not in output
    lua.execute(command[4:])
    assert not same(runtime.campaign.observation_snapshot_v2, old)
    assert runtime.native_installation.profile == EXPANDED_OBSERVATION_PROFILE
    assert runtime.native_installation.assets.observation_v2 == EXPANDED_OBSERVATION_SHA256
    assert same(runtime.native_installation.callbacks.snapshot_v2,
                runtime.campaign.observation_snapshot_v2)
    assert output[-1] == SENTINEL
    with pytest.raises(lua52.LuaError):
        lua.execute(command[4:])  # A second invocation cannot overwrite v3.


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
def test_owner_locked_v2_to_v3_refuses_changed_evidence_and_duplicate(tmp_path):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    lock.write_bytes(b'')
    lock.chmod(0o600)
    held = CampaignMemory('retained-session', 'rocket_launch')
    held.status = 'blocked'
    raw = json.dumps(asdict(held)).encode()
    checkpoint.write_bytes(raw)
    checkpoint.chmod(0o600)
    receipt_raw = json.dumps({
        'schema': 'jev.native-attachment.v1', 'session_id': 'retained-session',
        'actor_unit': 2543, 'installed_source_commit': PINNED_SOURCE_COMMIT,
        'installed_source_tree': PINNED_SOURCE_TREE,
        'installed_assets': PINNED_ASSETS,
    }).encode()
    receipt.write_bytes(receipt_raw)
    receipt.chmod(0o600)

    class Client:
        row = installed_v2()
        mutations = 0
        def send_command(self, command):
            if command == '/sc ' + PROBE:
                return json.dumps(self.row)
            self.mutations += 1
            self.row['native_installation'] = _manifest(self.row)
            return SENTINEL

    client = Client()
    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  expected_session_id='retained-session', expected_actor_unit=2543,
                  expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest())
    result = migrate_observation_anchor_v3(client, **kwargs)
    assert result['native_installation']['profile'] == EXPANDED_OBSERVATION_PROFILE
    assert client.mutations == 1 and checkpoint.read_bytes() == raw
    assert receipt.read_bytes() == receipt_raw
    with pytest.raises(RuntimeError, match='exact v2'):
        migrate_observation_anchor_v3(client, **kwargs)
    assert client.mutations == 1
    client.row = installed_v2()
    checkpoint.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='hash changed'):
        migrate_observation_anchor_v3(client, **kwargs)
    assert client.mutations == 1
    checkpoint.write_bytes(raw)
    client.row = installed_v2()
    class AmbiguousClient(Client):
        def send_command(self, command):
            if command == '/sc ' + PROBE:
                return json.dumps(self.row)
            self.mutations += 1
            self.row['native_installation'] = _manifest(self.row)
            return ''  # Native mutation occurred; acknowledgement was lost.
    ambiguous = AmbiguousClient()
    ambiguous.row = installed_v2()
    with pytest.raises(RuntimeError, match='acknowledgement absent'):
        migrate_observation_anchor_v3(ambiguous, **kwargs)
    with pytest.raises(RuntimeError, match='exact v2'):
        migrate_observation_anchor_v3(ambiguous, **kwargs)
    assert ambiguous.mutations == 1
    import fcntl
    with lock.open('r+b') as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            migrate_observation_anchor_v3(Client(), **kwargs)
    wrong_receipt = json.loads(receipt_raw)
    wrong_receipt['actor_unit'] = 17
    receipt.write_bytes(json.dumps(wrong_receipt).encode())
    kwargs['expected_receipt_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='Original attachment receipt'):
        migrate_observation_anchor_v3(Client(), **kwargs)
