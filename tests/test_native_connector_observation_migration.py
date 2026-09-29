"""Versioned v5 connector-observer repair; no live Factorio dependency."""
from dataclasses import asdict
import hashlib
import json
import os
from importlib.resources import files
from types import SimpleNamespace

import pytest

from jev_factorio.backends.native_attachment import (
    LEGACY_MANUAL_CYCLE_PROFILE, MANUAL_CYCLE_PROFILE, PINNED_ASSETS,
    PINNED_SOURCE_COMMIT, PINNED_SOURCE_TREE, PROBE,
    connector_observer_bridge_sha256, connector_ownership_sha256,
    manual_journal_sha256, readback,
)
from jev_factorio.backends.native_connector_observation_migration import (
    INTENT_NAME, SENTINEL, _command, _manifest, _preflight,
    _transaction_lock,
    migrate_connector_observer_v51, reconcile_connector_observer_v51,
    _snapshot_command, qualify_connector_snapshot_v1, reconcile_connector_snapshot_v1,
)
from jev_factorio.backends.native_manual_cycle_migration import _intent_events
from jev_factorio.memory import CampaignMemory
from test_native_manual_cycle_migration import installed_v4
from test_native_observation_migration import _lua_fixture
from native_connector_witness_helpers import write_snapshot_witness


def installed_legacy_v5():
    row = installed_v4()
    row['modules']['connector_ownership'] = True
    row['modules']['coal_manual_journal_v1'] = True
    row['modules']['connector_observer_bridge_v1'] = False
    row['native_installation'] = {
        **row['native_installation'],
        'profile': LEGACY_MANUAL_CYCLE_PROFILE,
        'assets': {**row['native_installation']['assets'],
                   'connector_ownership': connector_ownership_sha256(),
                   'coal_manual_journal_v1': manual_journal_sha256()},
    }
    row['connector_observer_bridge_qualified'] = False
    row['connector_snapshot_qualified'] = False
    row['connector_snapshot_tick'] = 0
    row['connector_snapshot_ownership'] = False
    return row


def installed_repaired_v5(row=None):
    row = row or installed_legacy_v5()
    result = _manifest(row)
    row['modules']['connector_observer_bridge_v1'] = True
    row['connector_observer_bridge_qualified'] = True
    row['connector_snapshot_qualified'] = False
    row['connector_snapshot_tick'] = 0
    row['connector_snapshot_ownership'] = False
    row['native_installation'] = result
    return row


def test_legacy_v5_manifest_is_repair_only_and_bridge_is_hash_pinned():
    old = installed_legacy_v5()

    class Client:
        def send_command(self, command):
            assert command == '/sc ' + PROBE
            return json.dumps(old)

    with pytest.raises(RuntimeError, match='one-use repair'):
        readback(Client())
    assert readback(Client(), allow_legacy_manual_cycle_repair=True)[
        'native_installation']['profile'] == LEGACY_MANUAL_CYCLE_PROFILE

    proposed = _manifest(old)
    assert proposed['profile'] == MANUAL_CYCLE_PROFILE
    assert proposed['assets']['connector_observer_bridge_v1'] == connector_observer_bridge_sha256()
    repaired = installed_repaired_v5(old)
    assert readback(SimpleNamespace(send_command=lambda _: json.dumps(repaired)),
                    allow_unqualified_connector_bridge=True)[
        'connector_observer_bridge_qualified'] is True
    repaired['native_installation']['assets']['connector_observer_bridge_v1'] = '0' * 64
    with pytest.raises(RuntimeError, match='Manual-cycle migration profile'):
        readback(SimpleNamespace(send_command=lambda _: json.dumps(repaired)))


def test_bridge_preserves_the_existing_outer_route_observer_and_emits_same_tick_ledger():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    old_observe = lua.eval('function() calls=(calls or 0)+1; return {tick=900} end')
    ownership = lua.table_from({'protocol': 1, 'session_id': 'retained-session',
                                'tick': 900, 'active': None, 'routes': lua.table_from({})})
    campaign = lua.table_from({'observe': old_observe})
    solid = lua.table_from({'observer': old_observe})
    runtime = lua.table_from({'campaign': campaign, 'solid_routes': solid,
                              'native_installation': lua.table_from({}),
                              'jev_session_id': 'retained-session'})
    lua.globals().jev_fle_runtime = runtime
    lua.globals().old = old_observe
    lua.globals().ownership = ownership
    lua.execute('jev_fle_runtime.campaign.observe_connector_ownership=function() return ownership end')
    source = files('jev_factorio').joinpath(
        'lua/connector_observer_bridge_v1.lua').read_text()
    lua.execute(source)
    assert lua.eval('jev_fle_runtime.campaign.observe==jev_fle_runtime.solid_routes.observer')
    assert lua.eval('jev_fle_runtime.connector_observer_bridge_v1.previous_observe==old')
    assert lua.eval('jev_fle_runtime.connector_observer_bridge_v1.previous_solid_observer==old')
    result = campaign.observe()
    assert result.tick == 900
    assert result.connector_ownership.protocol == 1
    assert result.connector_ownership.session_id == 'retained-session'
    assert result.connector_ownership.tick == result.tick
    assert lua.globals().calls == 1
    assert lua.globals().jev_fle_runtime.connector_observer_bridge_v1.snapshot_qualified is None
    assert lua.globals().jev_fle_runtime.connector_observer_bridge_v1.snapshot_tick is None


@pytest.mark.parametrize('failure', ['ledger_mismatch', 'print_failure', 'success'])
def test_snapshot_qualification_marker_is_written_only_after_checks_and_print(failure):
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    previous = lua.eval('function() return {tick=900} end')
    campaign = lua.table_from({'observe': previous})
    solid = lua.table_from({'observer': previous})
    actor = lua.table_from({'valid': True, 'unit_number': 2543})
    actors = lua.table(); actors[1] = actor
    runtime = lua.table_from({'campaign': campaign, 'solid_routes': solid,
                              'jev_session_id': 'retained-session',
                              'agent_characters': actors})
    lua.globals().jev_fle_runtime = runtime
    lua.globals().qualification_failure = failure
    lua.execute('''
        ownership_calls=0
        jev_fle_runtime.campaign.observe_connector_ownership=function()
            ownership_calls=ownership_calls+1
            if qualification_failure=="ledger_mismatch" and ownership_calls==2 then
                return {protocol=1,session_id="retained-session",tick=900,
                    active=nil,routes={foreign=true}}
            end
            return {protocol=1,session_id="retained-session",tick=900,
                active=nil,routes={}}
        end
        helpers={table_to_json=function(_) return "qualified-payload" end}
        rcon={print=function(payload)
            if qualification_failure=="print_failure" then error("print failed") end
            emitted=payload
        end}
    ''')
    source = files('jev_factorio').joinpath(
        'lua/connector_observer_bridge_v1.lua').read_text()
    lua.execute(source)
    command = _snapshot_command('retained-session', 2543).removeprefix('/sc ')
    bridge = runtime.connector_observer_bridge_v1
    if failure == 'success':
        lua.execute(command)
        assert bridge.snapshot_qualified is True
        assert bridge.snapshot_tick == 900
        assert len(bridge.snapshot_ownership.routes) == 0
        assert lua.globals().emitted == 'qualified-payload'
    else:
        with pytest.raises(Exception):
            lua.execute(command)
        assert bridge.snapshot_qualified is None
        assert bridge.snapshot_tick is None
        assert lua.globals().emitted is None


def test_repair_command_is_one_shot_and_keeps_original_factory_and_route_assets():
    row = installed_legacy_v5()
    command = _command(row)
    assert command.startswith('/sc ')
    assert json.dumps(LEGACY_MANUAL_CYCLE_PROFILE) in command
    assert json.dumps(MANUAL_CYCLE_PROFILE) in command
    assert json.dumps(connector_observer_bridge_sha256()) in command
    assert 'c.observe==solid.observer' in command
    assert 'local factory=c.observe()' not in command
    assert 'c.observe_connector_ownership' in command
    assert 'c.transfer=' not in command and 'c.configure=' not in command
    assert row['native_installation']['assets']['factory'] == PINNED_ASSETS['factory']


def test_exact_lua_repair_command_rolls_back_and_keeps_all_other_observer_owners():
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime(unpack_returned_tuples=True)
    runtime, _, output = _lua_fixture(lua)
    row = installed_legacy_v5()
    actor = runtime.agent_characters[1]
    actor.surface = lua.table_from({'index': 1})
    actor.force = lua.table_from({'index': 1})
    player = runtime.fair.actor()
    player.character = actor
    player.walking_state = lua.table_from({'walking': False})
    player.mining_state = lua.table_from({'mining': False})
    runtime.campaign.observe = lua.eval('function() return {tick=900} end')
    runtime.solid_routes = lua.table_from({'observer': runtime.campaign.observe})
    lua.globals().ownership = lua.table_from({
        'protocol': 1, 'session_id': 'retained-session', 'tick': 900,
        'active': None, 'routes': lua.table_from({})})
    lua.execute('jev_fle_runtime.campaign.connector_ledger={protocol=1,routes={}}; '
                'jev_fle_runtime.campaign.observe_connector_ownership='
                'function() return ownership end')
    runtime.coal_supply = lua.table_from({'revision': 4, 'committed': False,
                                          'rows': lua.table_from({})})
    runtime.coal_manual_journal_v1 = lua.table_from({
        'protocol': 1, 'pending': None, 'rows': lua.table_from({}),
        'order': lua.table_from({})})
    runtime.native_installation = lua.table_from(row['native_installation'], recursive=True)
    runtime.native_installation.callbacks = lua.table_from({'observe': runtime.campaign.observe})
    lua.globals().game.speed = 1
    lua.globals().game.tick_paused = False
    lua.globals().game.tick = 900
    lua.globals().script = lua.table_from({})
    prior_callbacks = runtime.native_installation.callbacks
    old_observe = runtime.campaign.observe
    lua.globals().old = old_observe
    command = _command(row)
    needle = 'n.profile=' + json.dumps(MANUAL_CYCLE_PROFILE) + ';'
    injected = command.replace(needle, 'error("after_bridge");' + needle, 1)
    with pytest.raises(lua52.LuaError, match='after_bridge'):
        lua.execute(injected[4:])
    assert lua.eval('jev_fle_runtime.campaign.observe==jev_fle_runtime.solid_routes.observer')
    assert lua.eval('jev_fle_runtime.campaign.observe==old')
    assert runtime.connector_observer_bridge_v1 is None
    assert runtime.native_installation.profile == LEGACY_MANUAL_CYCLE_PROFILE
    assert runtime.native_installation.assets.connector_observer_bridge_v1 is None
    lua.globals().prior_callbacks = prior_callbacks
    assert lua.eval('jev_fle_runtime.native_installation.callbacks==prior_callbacks')

    lua.execute(command[4:])
    assert runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert runtime.native_installation.assets.connector_observer_bridge_v1 == connector_observer_bridge_sha256()
    assert runtime.connector_observer_bridge_v1.protocol == 1
    assert lua.eval('jev_fle_runtime.campaign.observe==jev_fle_runtime.solid_routes.observer')
    assert lua.eval('jev_fle_runtime.native_installation.callbacks.observe==jev_fle_runtime.campaign.observe')
    result = runtime.campaign.observe()
    assert result.connector_ownership.session_id == 'retained-session'
    assert result.connector_ownership.tick == result.tick == 900
    assert output[-1] == SENTINEL
    with pytest.raises(lua52.LuaError):
        lua.execute(command[4:])


def test_preflight_rejects_any_nonempty_or_ambiguous_native_state():
    good = {'schema': 1, 'session_id': 'retained-session', 'actor_unit': 2543,
            'legacy_profile': LEGACY_MANUAL_CYCLE_PROFILE, 'bridge_absent': True,
            'observer_chain': True, 'ledger_protocol': 1, 'ledger_active': False,
            'ledger_routes': 0, 'journal_protocol': 1, 'journal_pending': False,
            'journal_rows': 0, 'coal_committed': False, 'coal_pending': False,
            'actor_idle': True}

    class Client:
        def __init__(self, changes=()):
            self.row = {**good, **dict(changes)}

        def send_command(self, command):
            assert command.startswith('/sc local rt=assert(jev_fle_runtime)')
            return json.dumps(self.row)

    _preflight(Client(), 'retained-session', 2543)
    for changed in ({'ledger_routes': 1}, {'ledger_active': True},
                    {'journal_pending': True}, {'coal_pending': True},
                    {'observer_chain': False}, {'actor_idle': False}):
        with pytest.raises(RuntimeError, match='idle empty runtime'):
            _preflight(Client(changed.items()), 'retained-session', 2543)


def test_bridge_attachment_requires_emitted_snapshot_and_matching_durable_witness(tmp_path):
    row = installed_repaired_v5()

    class Client:
        def send_command(self, command):
            assert command == '/sc ' + PROBE
            return json.dumps(row)

    with pytest.raises(RuntimeError, match='one-use native qualification'):
        readback(Client())
    receipt, witness = write_snapshot_witness(tmp_path, row)
    witness.unlink()
    with pytest.raises(RuntimeError, match='fixed durable witness'):
        readback(Client(), receipt_path=receipt, connector_witness_path=witness)
    receipt, witness = write_snapshot_witness(tmp_path, row, receipt_path=receipt)
    row['connector_snapshot_ownership']['routes'] = []
    qualified = readback(Client(), receipt_path=receipt, connector_witness_path=witness)
    assert qualified['connector_snapshot_qualified'] is True
    assert qualified['connector_snapshot_ownership']['routes'] == {}
    events = _intent_events(witness)
    events[0]['command_sha256'] = '0' * 64
    witness.write_text(''.join(json.dumps(event) + '\n' for event in events))
    witness.chmod(0o600)
    with pytest.raises(RuntimeError, match='witness identity changed'):
        readback(Client(), receipt_path=receipt, connector_witness_path=witness)
    events[0]['command_sha256'] = hashlib.sha256(
        _snapshot_command(row['session_id'], row['actor_unit']).encode('utf-8')).hexdigest()
    events[-1]['snapshot_sha256'] = '0' * 64
    witness.write_text(''.join(json.dumps(event) + '\n' for event in events))
    witness.chmod(0o600)
    with pytest.raises(RuntimeError, match='differs from the emitted native snapshot'):
        readback(Client(), receipt_path=receipt, connector_witness_path=witness)


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
@pytest.mark.parametrize('outcome', ['ack', 'lost_ack', 'unknown_before_apply', 'lock_replaced'])
@pytest.mark.parametrize('external_lock', [False, True])
def test_owner_locked_repair_is_one_use_and_readback_only_after_ambiguity(
        tmp_path, outcome, external_lock):
    import fcntl

    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    intent = tmp_path / INTENT_NAME
    lock.write_bytes(b''); lock.chmod(0o600)

    def independent_available():
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True
        finally:
            os.close(fd)
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
            self.row = installed_legacy_v5()
            self.mutations = 0
            self.probes = 0

        def send_command(self, command):
            assert not independent_available()
            if command == '/sc ' + PROBE:
                self.probes += 1
                return json.dumps(self.row)
            if ('legacy_profile=rt.native_installation.profile' in command):
                self.probes += 1
                return json.dumps({'schema': 1, 'session_id': 'retained-session',
                    'actor_unit': 2543, 'legacy_profile': LEGACY_MANUAL_CYCLE_PROFILE,
                    'bridge_absent': True, 'observer_chain': True, 'ledger_protocol': 1,
                    'ledger_active': False, 'ledger_routes': 0, 'journal_protocol': 1,
                    'journal_pending': False, 'journal_rows': 0, 'coal_committed': False,
                    'coal_pending': False, 'actor_idle': True})
            self.mutations += 1
            if outcome == 'unknown_before_apply':
                raise ConnectionError('unknown one-use dispatch')
            self.row = installed_repaired_v5(self.row)
            if outcome == 'lock_replaced':
                replacement = lock.with_name('replacement.lock')
                replacement.write_bytes(b'new-owner-lock')
                replacement.chmod(0o600)
                os.replace(replacement, lock)
            return '' if outcome == 'lost_ack' else SENTINEL

    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  intent_path=intent, expected_session_id='retained-session',
                  expected_actor_unit=2543, expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest())
    client = Client()

    def invoke():
        if not external_lock:
            return migrate_connector_observer_v51(client, **kwargs)
        with lock.open('r+b') as owner:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert not independent_available()
            try:
                return migrate_connector_observer_v51(
                    client, **kwargs, owner_lock_fd=owner.fileno())
            finally:
                # Closing the API's dup did not release the original flock.
                if outcome != 'lock_replaced':
                    assert not independent_available()

    if outcome == 'ack':
        assert invoke()[
            'native_installation']['profile'] == MANUAL_CYCLE_PROFILE
    else:
        with pytest.raises(RuntimeError, match='outcome unknown'):
            invoke()
    if outcome != 'lock_replaced':
        assert independent_available()
    assert client.mutations == 1
    phases = [row['phase'] for row in _intent_events(intent)]
    assert phases == ['dispatching', 'qualified' if outcome == 'ack' else 'unknown']
    if outcome == 'lock_replaced':
        with pytest.raises(RuntimeError, match='lock identity changed'):
            reconcile_connector_observer_v51(
                client, intent_path=intent, lock_path=lock,
                checkpoint_path=checkpoint, receipt_path=receipt)
        with pytest.raises(RuntimeError, match='intent exists'):
            migrate_connector_observer_v51(client, **kwargs)
        assert client.mutations == 1
        return
    status = reconcile_connector_observer_v51(
        client, intent_path=intent, lock_path=lock,
        checkpoint_path=checkpoint, receipt_path=receipt)
    assert status == ('legacy_v5_observed_attempt_consumed'
                      if outcome == 'unknown_before_apply' else 'v5_observer_repaired')
    with pytest.raises(RuntimeError, match='intent exists'):
        migrate_connector_observer_v51(client, **kwargs)
    assert client.mutations == 1


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
@pytest.mark.parametrize('outcome', ['ack', 'lost_ack', 'unknown_before_apply'])
@pytest.mark.parametrize('external_lock', [False, True])
def test_native_snapshot_qualification_is_journaled_one_shot_and_reconciles_without_observing(
        tmp_path, outcome, external_lock):
    import fcntl

    checkpoint = tmp_path / 'controller.json'
    receipt = tmp_path / 'attachment.json'
    lock = tmp_path / 'single-writer.lock'
    witness = tmp_path / 'native-connector-observer-v1.witness.jsonl'
    lock.write_bytes(b''); lock.chmod(0o600)

    def independent_available():
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True
        finally:
            os.close(fd)
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
            self.row = installed_repaired_v5()
            self.dispatches = 0
            self.observations = 0
            self.expect_held = True

        def send_command(self, command):
            if self.expect_held:
                assert not independent_available()
            if command == '/sc ' + PROBE:
                return json.dumps(self.row)
            if 'profile=rt.native_installation.profile' in command:
                return json.dumps({'schema': 1, 'session_id': 'retained-session',
                    'actor_unit': 2543, 'profile': MANUAL_CYCLE_PROFILE,
                    'bridge_qualified': True, 'snapshot_qualified': False,
                    'ledger_protocol': 1, 'ledger_active': False, 'ledger_routes': 0,
                    'journal_protocol': 1, 'journal_pending': False, 'journal_rows': 0,
                    'coal_committed': False, 'coal_pending': False, 'actor_idle': True})
            if 'local factory=c.observe();' in command:
                self.dispatches += 1
                if outcome == 'unknown_before_apply':
                    raise ConnectionError('ambiguous snapshot probe')
                self.observations += 1
                snapshot = {'protocol': 1, 'session_id': 'retained-session',
                            'tick': 900, 'active': None, 'routes': []}
                self.row['connector_snapshot_qualified'] = True
                self.row['connector_snapshot_tick'] = 900
                self.row['connector_snapshot_ownership'] = snapshot
                response = {'schema': 1, 'session_id': 'retained-session',
                            'actor_unit': 2543, 'tick': 900,
                            'connector_ownership': snapshot}
                return '' if outcome == 'lost_ack' else json.dumps(response)
            raise AssertionError('Unexpected native command')

    client = Client()
    kwargs = dict(checkpoint_path=checkpoint, receipt_path=receipt, lock_path=lock,
                  witness_path=witness, expected_session_id='retained-session',
                  expected_actor_unit=2543, expected_target='rocket_launch',
                  expected_checkpoint_sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
                  expected_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest())

    def invoke():
        if not external_lock:
            return qualify_connector_snapshot_v1(client, **kwargs)
        with lock.open('r+b') as owner:
            fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert not independent_available()
            try:
                return qualify_connector_snapshot_v1(
                    client, **kwargs, owner_lock_fd=owner.fileno())
            finally:
                assert not independent_available()

    if outcome == 'ack':
        assert invoke()[
            'connector_snapshot_qualified'] is True
    else:
        with pytest.raises(RuntimeError, match='outcome unknown'):
            invoke()
    assert independent_available()
    assert client.dispatches == 1
    status = reconcile_connector_snapshot_v1(
        client, checkpoint_path=checkpoint, receipt_path=receipt,
        lock_path=lock, witness_path=witness)
    client.expect_held = False  # Later direct readback is outside the API lock.
    if outcome == 'unknown_before_apply':
        assert status == 'connector_snapshot_attempt_consumed_unqualified'
        with pytest.raises(RuntimeError, match='one-use native qualification'):
            readback(client, receipt_path=receipt, connector_witness_path=witness)
    else:
        assert status == 'connector_snapshot_qualified'
        assert readback(client, receipt_path=receipt,
                        connector_witness_path=witness)['connector_snapshot_qualified'] is True
    assert client.observations == (0 if outcome == 'unknown_before_apply' else 1)
    with pytest.raises(RuntimeError, match='intent exists'):
        qualify_connector_snapshot_v1(client, **kwargs)
    assert client.dispatches == 1


@pytest.mark.skipif(os.name != 'posix', reason='owner lock requires POSIX')
def test_external_lock_fd_must_be_held_and_match_the_private_lock_path(tmp_path):
    import fcntl

    lock = tmp_path / 'single-writer.lock'
    other = tmp_path / 'other.lock'
    for path in (lock, other):
        path.write_bytes(b'')
        path.chmod(0o600)
    with lock.open('r+b') as owner:
        with pytest.raises(RuntimeError, match='not already locked'):
            with _transaction_lock(lock, owner.fileno()):
                pass
        fcntl.flock(owner.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='not already locked'):
            with _transaction_lock(lock, owner.fileno()):
                pass
        # Rejection must not upgrade or release the caller's shared lock.
        with lock.open('r+b') as independent:
            fcntl.flock(independent.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):
                fcntl.flock(independent.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with other.open('r+b') as wrong:
            fcntl.flock(wrong.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(RuntimeError, match='lock identity changed'):
                with _transaction_lock(lock, wrong.fileno()):
                    pass
        lock.chmod(0o400)
        with pytest.raises(RuntimeError, match='owner lock identity changed'):
            with _transaction_lock(lock, owner.fileno()):
                pass
