"""No live RCON: exercise the exact one-shot migration command in Lua 5.2."""
import json
import hashlib
import os
from dataclasses import asdict
from importlib.resources import files

import pytest

from jev_factorio.backends.native_attachment import (
    LEGACY_OBSERVATION_SHA256, PINNED_ASSETS, PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE,
    PROBE, readback, require_asset,
)
from jev_factorio.backends.native_observation_migration import (
    LEGACY_OBSERVATION_PROFILE, SENTINEL, _command, _manifest,
    migrate_legacy_observation_v2,
)
from jev_factorio.memory import CampaignMemory


def legacy():
    modules = dict.fromkeys(PINNED_ASSETS, True)
    modules['successors'] = False
    modules['connector_ownership'] = False
    modules['coal_manual_journal_v1'] = False
    modules['coal_manual_cycle_v2'] = False
    modules['connector_observer_bridge_v1'] = False
    return {'schema': 1, 'qualified': True, 'session_id': 'retained-session',
            'actor_unit': 2543, 'modules': modules, 'solid_intents': [],
            'coal_targets': [], 'coal_admission_evidence': True,
            'connector_observer_bridge_qualified': False,
            'connector_snapshot_qualified': False,
            'connector_snapshot_tick': 0,
            'connector_snapshot_ownership': False,
            'native_installation': False}


def test_migration_profile_refuses_any_existing_or_new_connector_installation():
    row = legacy()
    assert _manifest(row)['profile'] == LEGACY_OBSERVATION_PROFILE
    assert LEGACY_OBSERVATION_PROFILE == 'e759-observation-v2-bound-bootstrap-v2'
    assert LEGACY_OBSERVATION_SHA256 == hashlib.sha256(
        files('jev_factorio').joinpath('lua/observation_v2.lua').read_bytes()).hexdigest()
    assert PINNED_ASSETS['observation_v2'] == '983307da88317e690582511d2514046fd8819b2cd6ffa6d0832725b2a450710f'
    assert _manifest(row)['assets']['observation_v2'] == LEGACY_OBSERVATION_SHA256
    assert all(_manifest(row)['assets'][name] == value for name, value in PINNED_ASSETS.items()
               if name not in {'observation_v2', 'successors'})
    row['modules']['connector_ownership'] = True
    with pytest.raises(RuntimeError, match='unmodified e759'):
        _manifest(row)
    row['modules']['connector_ownership'] = False
    row['modules']['successors'] = True
    with pytest.raises(RuntimeError, match='unmodified e759'):
        _manifest(row)
    row['modules']['successors'] = False
    row['native_installation'] = {'profile': LEGACY_OBSERVATION_PROFILE}
    with pytest.raises(RuntimeError, match='unmodified e759'):
        _manifest(row)


def test_migrated_profile_accepts_only_original_modules_and_reviewed_observer():
    row = legacy()
    row['native_installation'] = _manifest(row)

    class Client:
        def send_command(self, command):
            return json.dumps(row)

    assert readback(Client())['native_installation']['profile'] == LEGACY_OBSERVATION_PROFILE
    assert require_asset(row, 'factory') is True  # Retained e759 closure.
    assert require_asset(row, 'observation_v2') is True  # Reviewed replacement.
    with pytest.raises(RuntimeError, match='not installed'):
        require_asset(row, 'connector_ownership')
    row['native_installation']['assets']['factory'] = '0' * 64
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        readback(Client())
    row['native_installation']['assets']['factory'] = PINNED_ASSETS['factory']
    row['modules']['successors'] = True
    row['native_installation']['assets']['successors'] = PINNED_ASSETS['successors']
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        readback(Client())


def test_old_output_tile_profile_is_not_reinterpreted_as_new_observer():
    row = legacy()
    row['native_installation'] = _manifest(row)
    row['native_installation']['profile'] = 'e759-observation-v2-output-tile-v1'
    row['native_installation']['assets']['observation_v2'] = (
        '30cce48ab896579473d625d38daea86dc7c61710092255436974d0111b41b416')

    class Client:
        def send_command(self, command):
            return json.dumps(row)

    with pytest.raises(RuntimeError, match='Unknown native installation profile'):
        readback(Client())
    with pytest.raises(RuntimeError, match='unmodified e759'):
        _manifest(row)  # An existing old profile is not a fresh v1 migration.


def _lua_fixture(lua):
    player = lua.table_from({
        'walking_state': lua.table_from({'walking': False}),
        'mining_state': lua.table_from({'mining': False}),
    })
    actor = lua.table_from({'valid': True, 'unit_number': 2543})
    player.character = actor
    old = lua.eval('function() return "old" end')
    campaign = lua.table_from({'observation_snapshot_v2': old})
    fair = lua.table_from({'actor': lambda: player, 'observe': lambda: None})
    runtime = lua.table_from({
        'campaign': campaign, 'fair': fair,
        'agent_characters': lua.table_from({1: actor}),
        'jev_session_id': 'retained-session',
    })
    lua.globals().jev_fle_runtime = runtime
    lua.globals().game = lua.table_from({'speed': 1, 'tick_paused': False})
    lua.globals().helpers = lua.table_from({
        'json_to_table': lambda raw: lua.table_from(json.loads(raw), recursive=True)})
    output = []
    lua.globals().rcon = lua.table_from({'print': output.append})
    return runtime, old, output


def test_exact_lua_transaction_commits_only_observer_and_manifest_or_rolls_back():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    runtime, old, output = _lua_fixture(lua)
    command = _command(legacy())
    assert callable(lua.eval('load')(command[4:]))
    injected = command.replace('n.callbacks=', 'error("injected"); n.callbacks=', 1)
    with pytest.raises(lua52.LuaError, match='injected'):
        lua.execute(injected[4:])
    same = lua.eval('function(a,b) return a==b end')
    assert same(runtime.campaign.observation_snapshot_v2, old)
    assert runtime.native_installation is None
    assert SENTINEL not in output
    lua.execute(command[4:])
    assert not same(runtime.campaign.observation_snapshot_v2, old)
    assert runtime.native_installation.profile == LEGACY_OBSERVATION_PROFILE
    assert same(runtime.native_installation.callbacks.snapshot_v2,
                runtime.campaign.observation_snapshot_v2)
    assert output[-1] == SENTINEL
    with pytest.raises(lua52.LuaError):
        lua.execute(command[4:])  # A second invocation cannot overwrite it.


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
def test_owner_locked_migration_requires_clear_checkpoint_and_never_replays(tmp_path):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    lock.write_bytes(b'')
    lock.chmod(0o600)
    held = CampaignMemory('retained-session', 'rocket_launch')
    held.status = 'blocked'  # A quiesced owner can migrate without unblocking play.
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
        row = legacy()
        mutations = 0

        def send_command(self, command):
            if command == '/sc ' + PROBE:
                return json.dumps(self.row)
            self.mutations += 1
            self.row['native_installation'] = _manifest(self.row)
            return 'JEV_ATOMIC_READY|2\n' + SENTINEL

    client = Client()
    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  expected_session_id='retained-session', expected_actor_unit=2543,
                  expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(raw).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest())
    result = migrate_legacy_observation_v2(client, **kwargs)
    assert result['native_installation']['profile'] == LEGACY_OBSERVATION_PROFILE
    assert client.mutations == 1 and checkpoint.read_bytes() == raw
    with pytest.raises(RuntimeError, match='unmodified e759'):
        migrate_legacy_observation_v2(client, **kwargs)
    assert client.mutations == 1
    unresolved = asdict(CampaignMemory('retained-session', 'rocket_launch'))
    unresolved['status'] = 'uncertain'
    changed = json.dumps(unresolved).encode()
    checkpoint.write_bytes(changed)
    kwargs['expected_checkpoint_sha256'] = hashlib.sha256(changed).hexdigest()
    client.row = legacy()
    with pytest.raises(RuntimeError, match='unresolved work'):
        migrate_legacy_observation_v2(client, **kwargs)
    assert client.mutations == 1
    checkpoint.write_bytes(raw)
    kwargs['expected_checkpoint_sha256'] = '0' * 64
    with pytest.raises(RuntimeError, match='hash changed'):
        migrate_legacy_observation_v2(client, **kwargs)
    assert client.mutations == 1
    kwargs['expected_checkpoint_sha256'] = hashlib.sha256(raw).hexdigest()
    import fcntl
    with lock.open('r+b') as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            migrate_legacy_observation_v2(client, **kwargs)
    assert client.mutations == 1
