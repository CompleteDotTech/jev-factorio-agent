"""Private v5-to-v6 source migration contract; no game connection."""
from dataclasses import asdict
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from jev_factorio.backends.native_attachment import (
    CLOSED_WORLD_PROFILE, CONNECTOR_OBSERVER_WITNESS_NAME, MANUAL_CYCLE_PROFILE, PINNED_ASSETS,
    PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE, PROBE,
    cycle_journal_sha256, readback, require_asset,
)
from jev_factorio.backends.native_closed_world_migration import (
    SENTINEL, PREFLIGHT, _command, _manifest, _no_successor_projects,
    _preflight,
    migrate_closed_world_v6,
    reconcile_closed_world_v6,
)
from jev_factorio.backends.native_manual_cycle_migration import (
    _intent_events, _manifest as v5_manifest,
)
from jev_factorio.memory import CampaignMemory
from test_native_manual_cycle_migration import installed_v4
from test_native_observation_migration import _lua_fixture


def test_successor_project_refuses_migration_before_intent():
    _no_successor_projects(SimpleNamespace(successor_projects={}))
    for projects in ({'growth:iron-plate': {'status': 'active'}},
                     {'growth:iron-plate': {'status': 'qualified'}}, []):
        with pytest.raises(RuntimeError, match='no successor project'):
            _no_successor_projects(SimpleNamespace(successor_projects=projects))


def installed_v5():
    row = installed_v4()
    row['native_installation'] = v5_manifest(row)
    row['modules']['connector_ownership'] = True
    row['modules']['coal_manual_journal_v1'] = True
    row['modules']['connector_observer_bridge_v1'] = True
    row['connector_observer_bridge_qualified'] = True
    return row


def test_exact_v6_profile_adds_only_cycle_asset_and_v5_stays_closed(tmp_path):
    row = installed_v5()
    proposed = _manifest(row)
    assert proposed['profile'] == CLOSED_WORLD_PROFILE
    assert proposed['assets'] == {
        **row['native_installation']['assets'],
        'coal_manual_cycle_v2': cycle_journal_sha256()}
    command = _command(row)
    assert command.startswith('/sc ')
    assert 'script.on_nth_tick(1,journal.combined_tick_handler)' in command
    assert 'script.on_nth_tick(1,mj.tick_handler)' in command
    row['modules']['coal_manual_cycle_v2'] = True
    row['native_installation'] = proposed

    class Client:
        def send_command(self, command):
            assert command == '/sc ' + PROBE
            return json.dumps(row)

    from native_connector_witness_helpers import write_snapshot_witness
    receipt, witness = write_snapshot_witness(tmp_path, row)
    observed = readback(Client(), receipt_path=receipt, connector_witness_path=witness)
    assert observed['native_installation']['profile'] == CLOSED_WORLD_PROFILE
    assert require_asset(observed, 'coal_manual_cycle_v2') is True
    row['native_installation']['assets']['coal_manual_cycle_v2'] = '0' * 64
    with pytest.raises(RuntimeError):
        readback(Client(), receipt_path=receipt, connector_witness_path=witness)
    with pytest.raises(RuntimeError):
        require_asset(row, 'coal_manual_cycle_v2')


def test_v6_rejects_changed_or_premature_v5_manifest():
    row = installed_v5()
    row['native_installation']['assets']['coal_manual_journal_v1'] = '0' * 64
    with pytest.raises(RuntimeError, match='Installed v5 assets'):
        _manifest(row)
    row = installed_v5()
    row['modules']['coal_manual_cycle_v2'] = True
    with pytest.raises(RuntimeError, match='exact v5'):
        _manifest(row)
    row = installed_v4()
    with pytest.raises(RuntimeError, match='exact v5'):
        _manifest(row)


@pytest.mark.parametrize('changed', [
    {'ledger_routes': 1}, {'journal_rows': 1}, {'coal_pending': True},
    {'actor_idle': False}, {'session_id': 'other'},
])
def test_read_only_guest_preflight_rejects_unreconciled_state(changed):
    row = {'schema': 1, 'session_id': 'retained-session', 'actor_unit': 2543,
           'ledger_routes': 0, 'ledger_active': False, 'journal_rows': 0,
           'journal_pending': False, 'coal_committed': False,
           'coal_pending': False, 'actor_idle': True}
    row.update(changed)

    class Client:
        def send_command(self, command):
            assert command == '/sc ' + PREFLIGHT
            return json.dumps(row)

    with pytest.raises(RuntimeError, match='not quiescent'):
        _preflight(Client(), 'retained-session', 2543)


def test_v6_lua_install_rolls_back_new_slot_without_touching_v5_slot():
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
    runtime.campaign.observe = lua.eval('function() return {tick=0} end')
    runtime.solid_routes = lua.table_from({'observer': runtime.campaign.observe})
    runtime.coal_supply = lua.table_from({'revision': 4, 'committed': False,
                                          'rows': lua.table_from({})})
    lua.globals().script = lua.table_from({})
    lua.execute('script.slots={};script.on_nth_tick=function(n,handler) '
                'assert(n==1 or n==2);script.slots[n]=handler end')
    runtime.native_installation = lua.table_from(row['native_installation'], recursive=True)
    runtime.native_installation.callbacks = lua.table_from(
        {'observe': runtime.campaign.observe})
    from jev_factorio.backends.native_manual_cycle_migration import _command as v5_command
    lua.execute(v5_command(row)[4:])
    assert lua.eval('script.slots[1]==jev_fle_runtime.coal_manual_journal_v1.tick_handler')
    row = installed_v5()
    command = _command(row)
    assert callable(lua.eval('load')(command[4:]))
    old_callbacks = runtime.native_installation.callbacks
    lua.globals().prior_callbacks = old_callbacks
    before = command.replace('local ok,err=pcall(function() do\n',
                             'local ok,err=pcall(function() error("before"); do\n', 1)
    with pytest.raises(lua52.LuaError, match='before'):
        lua.execute(before[4:])
    assert runtime.coal_manual_cycle_v2 is None
    assert runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')
    assert lua.eval('script.slots[2]==nil')
    assert lua.eval('script.slots[1]==jev_fle_runtime.coal_manual_journal_v1.tick_handler')
    needle = 'n.profile=' + json.dumps(CLOSED_WORLD_PROFILE)
    with pytest.raises(lua52.LuaError, match='after_register'):
        lua.execute(command.replace(needle, 'error("after_register"); ' + needle, 1)[4:])
    assert runtime.coal_manual_cycle_v2 is None
    assert runtime.native_installation.assets.coal_manual_cycle_v2 is None
    assert runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')
    assert lua.eval('script.slots[2]==nil')
    assert lua.eval('script.slots[1]==jev_fle_runtime.coal_manual_journal_v1.tick_handler')
    lua.execute(command[4:])
    assert runtime.native_installation.profile == CLOSED_WORLD_PROFILE
    assert runtime.native_installation.assets.coal_manual_cycle_v2 == cycle_journal_sha256()
    assert lua.eval('script.slots[1]==jev_fle_runtime.coal_manual_cycle_v2.combined_tick_handler')
    assert lua.eval('script.slots[2]==nil')
    assert output[-1] == SENTINEL
@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
@pytest.mark.parametrize('outcome', ['ack_v6', 'rcon_ack_v6', 'missing_ack_v6',
                                     'oversized_ack_v6', 'throw_v5'])
def test_owner_locked_v6_install_is_one_use_and_reconciles(tmp_path, outcome):
    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    intent = tmp_path / 'native-closed-world-v6.intent.jsonl'
    lock.write_bytes(b''); lock.chmod(0o600)
    memory = CampaignMemory('retained-session', 'rocket_launch')
    memory.status = 'blocked'
    memory.connector_ownership = {
        'protocol': 1, 'session_id': 'retained-session', 'routes': {}}
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
            self.row = installed_v5()
            self.commands = 0
            self.probes = 0

        def send_command(self, command):
            if command == '/sc ' + PROBE:
                self.probes += 1
                return json.dumps(self.row)
            if command == '/sc ' + PREFLIGHT:
                return json.dumps({'schema': 1, 'session_id': 'retained-session',
                    'actor_unit': 2543, 'ledger_routes': 0, 'ledger_active': False,
                    'journal_rows': 0, 'journal_pending': False,
                    'coal_committed': False, 'coal_pending': False,
                    'actor_idle': True})
            self.commands += 1
            if outcome == 'throw_v5':
                raise ConnectionError('ambiguous dispatch')
            proposed = _manifest(self.row)
            self.row['modules']['coal_manual_cycle_v2'] = True
            self.row['native_installation'] = proposed
            return ('RCON command executed\n' + SENTINEL + '\n' if outcome == 'rcon_ack_v6'
                    else 'x' * 1025 + SENTINEL if outcome == 'oversized_ack_v6'
                    else SENTINEL if outcome == 'ack_v6' else '')

    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt,
                  lock_path=lock, intent_path=intent,
                  expected_session_id='retained-session',
                  expected_actor_unit=2543, expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest())
    client = Client()
    from native_connector_witness_helpers import write_snapshot_witness
    write_snapshot_witness(tmp_path, client.row, receipt_path=receipt)
    if outcome in {'ack_v6', 'rcon_ack_v6'}:
        assert migrate_closed_world_v6(client, **kwargs)['native_installation']['profile'] == CLOSED_WORLD_PROFILE
    else:
        with pytest.raises((RuntimeError, ConnectionError)):
            migrate_closed_world_v6(client, **kwargs)
    assert client.commands == 1 and checkpoint.read_bytes() == checkpoint_bytes
    assert [row['phase'] for row in _intent_events(intent)] == [
        'dispatching', 'qualified' if outcome in {'ack_v6', 'rcon_ack_v6'} else 'unknown']
    assert reconcile_closed_world_v6(client, intent_path=intent, lock_path=lock,
                                     checkpoint_path=checkpoint, receipt_path=receipt) == (
        'v5_observed_attempt_consumed' if outcome == 'throw_v5' else 'v6_installed')
    with pytest.raises(RuntimeError, match='intent exists'):
        migrate_closed_world_v6(client, **kwargs)
    assert client.commands == 1

    events = _intent_events(intent)
    events[0]['after']['profile'] = MANUAL_CYCLE_PROFILE
    intent.write_text(''.join(json.dumps(row) + '\n' for row in events))
    probes = client.probes
    with pytest.raises(RuntimeError, match='profile invalid'):
        reconcile_closed_world_v6(client, intent_path=intent, lock_path=lock,
                                  checkpoint_path=checkpoint, receipt_path=receipt)
    assert client.probes == probes
