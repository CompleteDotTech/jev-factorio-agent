"""Fresh attachment and one-use direct qualification compatibility."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from jev_factorio.backends.native_attachment import connector_snapshot_command, readback
from jev_factorio.backends.native_connector_observation_migration import qualify_connector_snapshot_v1
from native_connector_witness_helpers import write_snapshot_witness
from test_native_connector_observation_migration import installed_repaired_v5


def test_direct_v1_command_keeps_preexisting_witness_bytes():
    command = connector_snapshot_command('retained-session', 2543, mode='ownership-only-v1')
    # Independently computed from the already reviewed direct v1 qualification
    # builder. Changing its bytes would invalidate retained immutable witnesses.
    assert hashlib.sha256(command.encode()).hexdigest() == (
        '3b90a3c3cc0c4fccc328d45dd050b3ec393c103347dae592c5ff2396d7b40b38')


def test_unknown_command_mode_fails_before_any_file_or_native_access(tmp_path):
    class Client:
        def send_command(self, command):
            raise AssertionError('Unknown mode must not reach native code')

    with pytest.raises(ValueError, match='Unknown connector snapshot'):
        qualify_connector_snapshot_v1(
            Client(), checkpoint_path=tmp_path/'checkpoint', receipt_path=tmp_path/'receipt',
            lock_path=tmp_path/'lock', witness_path=tmp_path/'witness',
            expected_session_id='retained-session', expected_actor_unit=2543,
            expected_target='rocket_launch', expected_checkpoint_sha256='1'*64,
            expected_receipt_sha256='2'*64, snapshot_mode='arbitrary-lua')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('bad_hash', [[], {}, None, True, 1, 'unapproved', '0'*64])
def test_direct_witness_rejects_malformed_or_unknown_command_hash(tmp_path, bad_hash):
    row = installed_repaired_v5()
    receipt, witness = write_snapshot_witness(tmp_path, row, snapshot_mode='ownership-only-v1')
    events = [json.loads(line) for line in witness.read_text().splitlines()]
    events[0]['command_sha256'] = bad_hash
    witness.write_text(''.join(json.dumps(event)+'\n' for event in events))

    class Client:
        def send_command(self, command):
            return json.dumps(row)

    with pytest.raises(RuntimeError, match='witness identity changed'):
        readback(Client(), receipt_path=receipt, connector_witness_path=witness)


@pytest.mark.parametrize('snapshot_mode', ['coherent', 'ownership-only-v1'])
def test_fresh_interpreter_attaches_without_builder_overrides(tmp_path, snapshot_mode):
    row = installed_repaired_v5()
    receipt, witness = write_snapshot_witness(tmp_path, row, snapshot_mode=snapshot_mode)
    payload = tmp_path/'snapshot.json'
    payload.write_text(json.dumps(row))
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(root/'src')
    env['JEV_NATIVE_ATTACHMENT_RECEIPT'] = str(receipt)
    env['JEV_NATIVE_CONNECTOR_OBSERVER_WITNESS'] = str(witness)
    script = '''import json,sys
from jev_factorio.backends.native_attachment import readback,PROBE
row=json.loads(open(sys.argv[1]).read())
class Client:
    def send_command(self,command):
        assert command == '/sc '+PROBE
        return json.dumps(row)
assert readback(Client())['connector_snapshot_qualified'] is True
print('qualified')
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(payload)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'qualified'


@pytest.mark.parametrize('failure', ['success', 'wrong_session', 'nonempty_routes', 'print_failure'])
def test_direct_query_avoids_general_observer_and_preserves_ambiguous_snapshot(failure):
    lua = pytest.importorskip('lupa.lua52').LuaRuntime(unpack_returned_tuples=True)
    lua.globals().failure = failure
    lua.execute('''
game={tick=900}
local force={};local surface={}
local actor={valid=true,unit_number=2543,force=force,surface=surface}
local player={connected=true,character=actor,force=force,surface=surface}
local observe=function() error('general factory observer must not be called') end
local ownership=function()
  return {protocol=1,session_id=failure=='wrong_session' and 'foreign' or 'retained-session',
    tick=900,routes=failure=='nonempty_routes' and {foreign=true} or {}}
end
jev_fle_runtime={jev_session_id='retained-session',agent_characters={[1]=actor},
  fair={actor=function() return player end},solid_routes={observer=observe},
  campaign={observe=observe,observe_connector_ownership=ownership,
    connector_ledger={protocol=1,routes={}}},
  connector_observer_bridge_v1={protocol=1,observer=observe},
  native_installation={schema='jev.native-installation.v2',session_id='retained-session',
    actor_unit=2543,callbacks={observe=observe,connector_observe=ownership}}}
helpers={table_to_json=function(_) return 'snapshot' end}
rcon={print=function(payload)
  if failure=='print_failure' then error('lost response') end
  emitted=payload
end}
''')
    command = connector_snapshot_command('retained-session', 2543, mode='ownership-only-v1')
    if failure == 'success':
        lua.execute(command.removeprefix('/sc '))
        assert lua.globals().emitted == 'snapshot'
    else:
        with pytest.raises(Exception):
            lua.execute(command.removeprefix('/sc '))
        assert lua.globals().emitted is None
    bridge = lua.globals().jev_fle_runtime.connector_observer_bridge_v1
    # Direct v1 stores qualification before printing: response loss is ambiguous,
    # with native metadata retained for read-only reconciliation, never a retry.
    assert (bridge.snapshot_qualified is True) == (failure in ('success', 'print_failure'))
    assert lua.globals().game.tick == 900
