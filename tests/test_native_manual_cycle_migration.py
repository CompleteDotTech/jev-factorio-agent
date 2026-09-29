"""Additive v5 journal qualification; no game connection or live migration."""
import hashlib
import json
import os
from dataclasses import asdict

import pytest

from jev_factorio.backends.native_attachment import (
    MANUAL_CYCLE_PROFILE, PROBE, WATER_ORIGIN_OBSERVATION_PROFILE,
    PINNED_ASSETS, PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE,
    connector_ownership_sha256, manual_journal_sha256, readback, require_asset,
)
from jev_factorio.backends.native_manual_cycle_migration import (
    SENTINEL, _command, _manifest, _no_open_investment, _intent_events,
    migrate_manual_cycle_v5, reconcile_manual_cycle_v5,
)
from jev_factorio.memory import CampaignMemory
from test_native_observation_migration import _lua_fixture
from test_native_observation_water_origin_migration import installed_v3, _manifest as v4_manifest
from test_coal_native_projection_adapter import connector_binding


def installed_v4():
    row = installed_v3()
    row['native_installation'] = v4_manifest(row)
    return row


def test_v5_adds_only_exact_source_qualified_journal_and_v4_remains_closed():
    row = installed_v4()
    proposed = _manifest(row)
    assert proposed['profile'] == MANUAL_CYCLE_PROFILE
    assert proposed['assets'] == {
        **row['native_installation']['assets'],
        'connector_ownership': connector_ownership_sha256(),
        'coal_manual_journal_v1': manual_journal_sha256()}
    row['modules']['connector_ownership'] = True
    row['modules']['coal_manual_journal_v1'] = True
    row['native_installation'] = proposed

    class Client:
        def send_command(self, command):
            assert command == '/sc ' + PROBE
            return json.dumps(row)

    assert readback(Client())['native_installation']['profile'] == MANUAL_CYCLE_PROFILE
    assert require_asset(row, 'connector_ownership') is True
    assert require_asset(row, 'coal_manual_journal_v1') is True
    row['native_installation']['assets']['coal_manual_journal_v1'] = '0' * 64
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        readback(Client())
    with pytest.raises(RuntimeError, match='verified installed revision'):
        require_asset(row, 'coal_manual_journal_v1')
    row['native_installation']['assets']['coal_manual_journal_v1'] = manual_journal_sha256()
    row['native_installation']['profile'] = WATER_ORIGIN_OBSERVATION_PROFILE
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        readback(Client())


def test_v5_manifest_rejects_unreconciled_or_already_installed_work():
    row = installed_v4()
    row['modules']['coal_manual_journal_v1'] = True
    with pytest.raises(RuntimeError, match='exact v4'):
        _manifest(row)
    row = installed_v4()
    row['native_installation']['assets']['factory'] = '0' * 64
    with pytest.raises(RuntimeError, match='assets require reconciliation'):
        _manifest(row)


@pytest.mark.parametrize('field', ['capital_investment', 'solid_funding', 'coal_funding'])
def test_v5_preflight_rejects_unresolved_investment(field):
    memory = CampaignMemory('retained-session', 'rocket_launch')
    setattr(memory, field, {'pending': True})
    with pytest.raises(RuntimeError, match='unresolved investment'):
        _no_open_investment(memory)


def test_v5_lua_transaction_rolls_back_handler_and_manifest_on_failure():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    runtime, _, output = _lua_fixture(lua)
    row = installed_v4()
    actor = runtime.agent_characters[1]
    actor.surface = lua.table_from({'index': 1})
    actor.force = lua.table_from({'index': 1})
    player = runtime.fair.actor()
    player.index = 1
    player.get_item_count = lambda _: 0
    runtime.coal_supply = lua.table_from({'revision': 4, 'committed': False,
                                          'rows': lua.table_from({})})
    lua.globals().script = lua.table_from({})
    lua.execute('script.on_nth_tick=function(n,handler) assert(n==1); script.registered=handler end')
    runtime.native_installation = lua.table_from(row['native_installation'], recursive=True)
    runtime.native_installation.callbacks = lua.table_from({})
    lua.globals().prior_callbacks = runtime.native_installation.callbacks
    command = _command(row)
    assert callable(lua.eval('load')(command[4:]))
    before = command.replace('local ok,err=pcall(function() do\n',
                             'local ok,err=pcall(function() error("before"); do\n', 1)
    with pytest.raises(lua52.LuaError, match='before'):
        lua.execute(before[4:])
    assert runtime.coal_manual_journal_v1 is None
    assert runtime.campaign.connector_ledger is None
    assert runtime.campaign.connector_begin is None
    assert runtime.fair.connector_place is None
    assert lua.eval('script.registered==nil')
    assert runtime.native_installation.profile == WATER_ORIGIN_OBSERVATION_PROFILE
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')
    needle = 'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE)
    with pytest.raises(lua52.LuaError, match='injected'):
        lua.execute(command.replace(needle, 'error("injected"); ' + needle, 1)[4:])
    assert runtime.coal_manual_journal_v1 is None
    assert runtime.campaign.connector_ledger is None
    assert runtime.campaign.connector_begin is None
    assert runtime.campaign.connector_finish is None
    assert runtime.campaign.connector_page is None
    assert runtime.campaign.observe_connector_ownership is None
    assert runtime.fair.connector_place is None
    assert lua.eval('script.registered==nil')
    assert runtime.native_installation.profile == WATER_ORIGIN_OBSERVATION_PROFILE
    assert runtime.native_installation.assets.coal_manual_journal_v1 is None
    assert runtime.native_installation.assets.connector_ownership is None
    assert {name: runtime.native_installation.assets[name]
            for name in row['native_installation']['assets']} == row['native_installation']['assets']
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')
    asset_write = 'n.assets.connector_ownership=' + json.dumps(connector_ownership_sha256()) + '; '
    with pytest.raises(lua52.LuaError, match='partial_asset'):
        lua.execute(command.replace(asset_write, asset_write + 'error("partial_asset"); ', 1)[4:])
    assert runtime.native_installation.assets.connector_ownership is None
    assert runtime.native_installation.assets.coal_manual_journal_v1 is None
    assert {name: runtime.native_installation.assets[name]
            for name in row['native_installation']['assets']} == row['native_installation']['assets']
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')
    assert runtime.campaign.connector_ledger is None
    assert runtime.coal_manual_journal_v1 is None
    assert lua.eval('script.registered==nil')
    assert SENTINEL not in output
    lua.execute(command[4:])
    assert runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert runtime.native_installation.assets.coal_manual_journal_v1 == manual_journal_sha256()
    assert runtime.native_installation.assets.connector_ownership == connector_ownership_sha256()
    assert runtime.campaign.connector_ledger.protocol == 1
    assert runtime.campaign.connector_begin is not None
    assert runtime.fair.connector_place is not None
    assert lua.eval('script.registered==jev_fle_runtime.coal_manual_journal_v1.tick_handler')
    assert output[-1] == SENTINEL
    with pytest.raises(lua52.LuaError):
        lua.execute(command[4:])


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
@pytest.mark.parametrize('outcome', ['ack_v5', 'missing_ack_v5', 'throw_v4'])
def test_v5_owner_locked_migration_reconciles_ambiguous_response(tmp_path, outcome):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    intent = tmp_path / 'native-manual-cycle-v5.intent.jsonl'
    lock.write_bytes(b''); lock.chmod(0o600)
    memory = CampaignMemory('retained-session', 'rocket_launch')
    memory.status = 'blocked'
    memory.connector_ownership = {'protocol': 1, 'session_id': 'retained-session', 'routes': {}}
    checkpoint_bytes = json.dumps(asdict(memory)).encode()
    checkpoint.write_bytes(checkpoint_bytes); checkpoint.chmod(0o600)
    receipt_bytes = json.dumps({
        'schema': 'jev.native-attachment.v1', 'session_id': 'retained-session',
        'actor_unit': 2543, 'installed_source_commit': PINNED_SOURCE_COMMIT,
        'installed_source_tree': PINNED_SOURCE_TREE, 'installed_assets': PINNED_ASSETS,
    }).encode()
    receipt.write_bytes(receipt_bytes); receipt.chmod(0o600)

    class Client:
        def __init__(self):
            self.row = installed_v4()
            self.calls = 0
            self.probes = 0

        def send_command(self, command):
            if command == '/sc ' + PROBE:
                self.probes += 1
                return json.dumps(self.row)
            self.calls += 1
            if outcome == 'throw_v4':
                raise ConnectionError('unknown dispatch')
            proposed = _manifest(self.row)
            self.row['modules']['connector_ownership'] = True
            self.row['modules']['coal_manual_journal_v1'] = True
            self.row['native_installation'] = proposed
            return '' if outcome == 'missing_ack_v5' else SENTINEL

    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  intent_path=intent,
                  expected_session_id='retained-session', expected_actor_unit=2543,
                  expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest())
    client = Client()
    if outcome != 'ack_v5':
        with pytest.raises(RuntimeError, match='outcome unknown'):
            migrate_manual_cycle_v5(client, **kwargs)
    else:
        assert migrate_manual_cycle_v5(client, **kwargs)['native_installation']['profile'] == MANUAL_CYCLE_PROFILE
    assert client.calls == 1 and checkpoint.read_bytes() == checkpoint_bytes
    assert [row['phase'] for row in _intent_events(intent)] == [
        'dispatching', 'qualified' if outcome == 'ack_v5' else 'unknown']
    assert reconcile_manual_cycle_v5(client, intent_path=intent, lock_path=lock,
                                     checkpoint_path=checkpoint, receipt_path=receipt) == (
        'v4_observed_attempt_consumed' if outcome == 'throw_v4' else 'v5_installed')
    assert _intent_events(intent)[-1]['phase'] == (
        'reconciled_v5' if outcome == 'missing_ack_v5' else
        'qualified' if outcome == 'ack_v5' else 'unknown')
    with pytest.raises(RuntimeError, match='intent already exists'):
        migrate_manual_cycle_v5(client, **kwargs)
    assert client.calls == 1
    probes = client.probes
    with pytest.raises(RuntimeError, match='checkpoint-bound fixed path'):
        reconcile_manual_cycle_v5(client, intent_path=tmp_path / 'arbitrary.jsonl',
                                  lock_path=lock, checkpoint_path=checkpoint,
                                  receipt_path=receipt)
    assert client.probes == probes
    original_intent = intent.read_bytes()
    events = _intent_events(intent)
    events[0]['after'] = events[0]['before']
    intent.write_text(''.join(json.dumps(row) + '\n' for row in events))
    with pytest.raises(RuntimeError, match='profile requires reconciliation'):
        reconcile_manual_cycle_v5(client, intent_path=intent, lock_path=lock,
                                  checkpoint_path=checkpoint, receipt_path=receipt)
    assert client.probes == probes
    intent.write_bytes(original_intent)
    events = _intent_events(intent)
    events[0]['actor_unit'] = str(events[0]['actor_unit'])
    intent.write_text(''.join(json.dumps(row) + '\n' for row in events))
    with pytest.raises(RuntimeError, match='requires reconciliation'):
        reconcile_manual_cycle_v5(client, intent_path=intent, lock_path=lock,
                                  checkpoint_path=checkpoint, receipt_path=receipt)
    assert client.probes == probes
    intent.write_bytes(original_intent)
    if outcome == 'ack_v5':
        current = client.row
        client.row = installed_v4()
        with pytest.raises(RuntimeError, match='success contradicts v4'):
            reconcile_manual_cycle_v5(client, intent_path=intent, lock_path=lock,
                                      checkpoint_path=checkpoint, receipt_path=receipt)
        client.row = current
        probes = client.probes
    checkpoint.write_bytes(checkpoint_bytes + b' ')
    with pytest.raises(RuntimeError, match='evidence changed'):
        reconcile_manual_cycle_v5(client, intent_path=intent, lock_path=lock,
                                  checkpoint_path=checkpoint, receipt_path=receipt)
    assert client.probes == probes


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
def test_v5_migration_rejects_nonempty_connector_checkpoint_before_rpc(tmp_path):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    intent = tmp_path / 'native-manual-cycle-v5.intent.jsonl'
    lock.write_bytes(b''); lock.chmod(0o600)
    memory = CampaignMemory('retained-session', 'rocket_launch')
    memory.status = 'blocked'
    memory.connector_ownership = connector_binding('retained-session')
    checkpoint_bytes = json.dumps(asdict(memory)).encode()
    checkpoint.write_bytes(checkpoint_bytes); checkpoint.chmod(0o600)
    receipt_bytes = json.dumps({
        'schema': 'jev.native-attachment.v1', 'session_id': 'retained-session',
        'actor_unit': 2543, 'installed_source_commit': PINNED_SOURCE_COMMIT,
        'installed_source_tree': PINNED_SOURCE_TREE, 'installed_assets': PINNED_ASSETS,
    }).encode()
    receipt.write_bytes(receipt_bytes); receipt.chmod(0o600)

    class Client:
        def send_command(self, _):
            raise AssertionError('Migration contacted native game before checkpoint validation')

    with pytest.raises(RuntimeError, match='exactly empty'):
        migrate_manual_cycle_v5(Client(), checkpoint_path=checkpoint,
            receipt_path=receipt, lock_path=lock, intent_path=intent,
            expected_session_id='retained-session',
            expected_actor_unit=2543, expected_target='rocket_launch',
            expected_checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
            expected_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest())
    assert not intent.exists()


@pytest.mark.skipif(os.name != 'posix', reason='private intent requires POSIX')
def test_v5_partial_or_replaced_intent_fails_closed(tmp_path):
    intent = tmp_path / 'native-manual-cycle-v5.intent.jsonl'
    intent.write_bytes(b'{"phase":"dispatching"}')
    intent.chmod(0o600)
    with pytest.raises(RuntimeError, match='requires reconciliation'):
        _intent_events(intent)
    intent.write_bytes(b'{"phase":"dispatching","phase":"qualified"}\n')
    with pytest.raises(RuntimeError, match='requires reconciliation'):
        _intent_events(intent)
    intent.unlink()
    intent.symlink_to(tmp_path / 'absent')
    with pytest.raises(RuntimeError, match='requires reconciliation|missing or is a symlink'):
        _intent_events(intent)
