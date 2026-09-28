"""The fixed read-only preflight can validate a composed checkpoint, but not its native owners."""
from dataclasses import asdict

from jev_factorio.acceptance_io import canonical
from jev_factorio.coal_controller import coal_loop_type
from jev_factorio.coal_supply import intents
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.dev_preflight import checkpoint_read, checkpoint_type, inspect_native, probe
from jev_factorio.solid_controller import solid_loop_type


def test_complete_checkpoint_is_readable_but_native_family_stays_unqualified(tmp_path):
    epoch = {'actor_index': 1, 'surface_index': 1, 'force_index': 1}
    Memory = coal_loop_type(solid_loop_type(HierarchicalLoop)).memory_type
    checkpoint = asdict(Memory('original-session', 'rocket_launch', last_tick=2,
        solid_intents=intents(['burner-a', 'burner-b']), solid_epoch=epoch,
        coal_targets=['burner-a', 'burner-b'], coal_epoch=epoch))
    path = tmp_path / 'composed.json'
    path.write_bytes(canonical(checkpoint))
    value, digest = checkpoint_read(path)
    assert value == checkpoint and len(digest) == 64
    assert checkpoint_type(value) is not HierarchicalLoop.memory_type
    native = {'schema': 1, 'tick': 2, 'speed': 1, 'tick_paused': False,
        'marked': True, 'runtime_present': True, 'campaign_present': True,
        'fair_present': True, 'connected': True, 'bound': True,
        'truncated': False, 'session_id': 'original-session',
        'actor_unit': 1, 'player_index': 1, 'surface_index': 1,
        'force_index': 1, 'entities': {}}
    issues = inspect_native(native, value, 'original-session')
    assert {'solid_preflight_not_supported', 'coal_preflight_not_supported'} <= set(issues)
    dev_uuid = '00000000-0000-4000-8000-000000000001'
    prod_uuid = '00000000-0000-4000-8000-000000000002'
    password = tmp_path / 'password'
    password.write_text('fixture-credential')
    password.chmod(0o600)
    config = tmp_path / 'config.json'
    config.write_bytes(canonical({'schema': 1, 'vm_uuid': dev_uuid,
        'production_vm_uuid': prod_uuid, 'session_id': 'original-session',
        'port': 27015, 'password_file': str(password)}))
    config.chmod(0o600)
    dmi = tmp_path / 'dmi'
    dmi.write_text(dev_uuid)
    class Client:
        def __init__(self, host, port, credential, timeout):
            assert host == '127.0.0.1' and credential == 'fixture-credential'
        def send_command(self, command):
            assert command.startswith('/sc ')
            return canonical(native).decode()
        def close(self): pass
    report = probe(config, path, client_factory=Client, dmi_path=dmi)
    assert report['checkpoint_sha256'] == digest
    assert not report['ready_for_coordinated_validation']
    assert {'solid_preflight_not_supported', 'coal_preflight_not_supported'} <= set(report['issues'])
