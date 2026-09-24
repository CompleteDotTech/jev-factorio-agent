"""Read-only, guest-local development preflight. Never starts or resumes gameplay."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import UUID

from .acceptance_io import canonical, load_json, sha256, stable_read, write_new


DMI_UUID = Path('/sys/class/dmi/id/product_uuid')
CONFIG_KEYS = {'schema', 'vm_uuid', 'production_vm_uuid', 'session_id', 'port', 'password_file'}


def checkpoint_type(data):
    from .memory import CampaignMemory
    from .controller import HierarchicalLoop
    from .background import BackgroundWorkLoop
    from .buffer_controller import buffered_loop_type
    from .input_controller import input_loop_type
    from .outpost_controller import outpost_loop_type
    from .successor_controller import successor_loop_type
    base = BackgroundWorkLoop if 'background_schema' in data else HierarchicalLoop
    if 'input_routes_schema' in data:
        base = input_loop_type(buffered_loop_type(base))
    if 'outposts_schema' in data:
        base = outpost_loop_type(base)
    if 'successor_schema' in data:
        base = successor_loop_type(base)
    return base.memory_type if base else CampaignMemory


def checkpoint_read(path: Path, *, idle: bool = True) -> tuple[dict, str]:
    raw = stable_read(path)
    data = load_json(raw)
    if not isinstance(data, dict): raise ValueError('Checkpoint must be an object')
    # Existing loaders validate schemas, owned receipts, failure history and session.
    # They do not contact a backend or write the input file.
    checkpoint_type(data).load(path, data['session_id'], data['target'])
    if stable_read(path) != raw: raise ValueError('Checkpoint changed during validation')
    if idle and (data.get('status') != 'running' or data.get('target') != 'rocket_launch'
            or any(data.get(k) for k in ('active_plan', 'pending', 'attempt', 'reservations',
                                       'background_job', 'background_attempt', 'capital_investment'))):
        raise ValueError('An idle, reconciled running rocket checkpoint is required')
    return data, sha256(raw)


def inspect_native(native: dict, checkpoint: dict, expected_session: str) -> list[str]:
    """Endpoint responsiveness is not session, actor or ownership continuity."""
    issues = []
    if not isinstance(native, dict) or type(native.get('schema')) is not int or native['schema'] != 1:
        return ['invalid_probe_schema']
    for key in ('marked', 'runtime_present', 'campaign_present', 'fair_present', 'connected', 'bound'):
        if native.get(key) is not True: issues.append('native_' + key + '_missing')
    if native.get('truncated') is not False: issues.append('ownership_probe_incomplete')
    if native.get('session_id') != expected_session or checkpoint.get('session_id') != expected_session:
        issues.append('session_mismatch')
    if type(native.get('speed')) not in {int, float} or native['speed'] != 1:
        issues.append('nonstandard_game_speed')
    if native.get('tick_paused') is not False: issues.append('simulation_paused_or_unknown')
    if type(native.get('tick')) is not int or native['tick'] < checkpoint.get('last_tick', 0):
        issues.append('native_tick_precedes_checkpoint')
    for key in ('actor_unit', 'player_index', 'surface_index', 'force_index'):
        if type(native.get(key)) is not int or native[key] <= 0: issues.append('invalid_' + key)
    entities = native.get('entities')
    if not isinstance(entities, dict):
        issues.append('missing_owned_entities')
        entities = {}
    def parts_match(source, expected, family):
        row = native.get(family, {}).get(source, {})
        if (row.get('source_unit') != expected.get('source_unit')
                or row.get('layout') != expected.get('layout') or row.get('fault') is not False
                or canonical(row.get('parts')) != canonical(expected.get('parts'))):
            issues.append('owned_' + family + '_mismatch')
        for part in expected.get('parts', {}).values():
            if entities.get(part['role'], {}).get('unit_number') != part['unit_number']:
                issues.append('owned_component_missing')
    for source, expected in checkpoint.get('input_commitments', {}).items():
        parts_match(source, expected, 'input_routes')
        if entities.get(source, {}).get('unit_number') != expected['source_unit']:
            issues.append('owned_input_source_missing')
    for source, project in checkpoint.get('successor_projects', {}).items():
        old = entities.get('recipe:' + source[7:], {})
        if old.get('unit_number') != project['predecessor_unit']: issues.append('predecessor_missing')
        if project['source_unit'] and entities.get(source, {}).get('unit_number') != project['source_unit']:
            issues.append('successor_missing')
        retained = checkpoint.get('successor_receipts', {}).get(source, {})
        for label, family in (('input', 'input_routes'), ('output', 'output_buffers')):
            if retained.get(label):
                parts_match(source, {'source_unit': project['source_unit'],
                    'layout': retained[label + '_layout'], 'parts': retained[label]}, family)
    # This first probe does not project outpost ownership. It must not pretend to.
    if checkpoint.get('outpost_commitments'): issues.append('outpost_preflight_not_supported')
    return sorted(set(issues))


def probe(config_path: Path, checkpoint_path: Path, *, client_factory=None,
          dmi_path: Path = DMI_UUID) -> dict:
    config = load_json(stable_read(config_path, private=True))
    if (not isinstance(config, dict) or set(config) != CONFIG_KEYS
            or type(config['schema']) is not int or config['schema'] != 1
            or type(config['port']) is not int or not 1 <= config['port'] <= 65535
            or not isinstance(config['session_id'], str) or not 0 < len(config['session_id']) <= 128
            or not isinstance(config['password_file'], str) or not Path(config['password_file']).is_absolute()):
        raise ValueError('Invalid private dev configuration')
    expected, production = str(UUID(config['vm_uuid'])), str(UUID(config['production_vm_uuid']))
    actual = str(UUID(dmi_path.read_text().strip()))
    if expected == production or actual != expected or actual == production:
        raise ValueError('Development VM identity mismatch; no network connection attempted')
    checkpoint, checkpoint_hash = checkpoint_read(checkpoint_path)
    if checkpoint['session_id'] != config['session_id']:
        raise ValueError('Checkpoint differs from pinned session; no network connection attempted')
    password_raw = stable_read(Path(config['password_file']), maximum=4096, private=True)
    password = password_raw.decode('utf-8').rstrip('\r\n')
    if not password or '\n' in password or '\r' in password or '\x00' in password:
        raise ValueError('Invalid private RCON credential file')
    code = files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text()
    if client_factory is None:
        from factorio_rcon import RCONClient
        client_factory = RCONClient
    # Deliberately no host override, tunnel setup, arbitrary command or FLE import.
    client = client_factory('127.0.0.1', config['port'], password, timeout=10)
    try:
        raw = client.send_command('/sc ' + code)
        if not isinstance(raw, str) or len(raw.encode()) > 1024 * 1024:
            raise ValueError('Native probe exceeded response budget')
        native = load_json(raw)
    finally:
        client.close()
    if sha256(stable_read(checkpoint_path)) != checkpoint_hash:
        raise ValueError('Checkpoint changed during native probe')
    issues = inspect_native(native, checkpoint, config['session_id'])
    return {'schema': 'jev-factorio.dev-preflight.v1',
        'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'vm_uuid': actual, 'production_vm_uuid': production,
        'checkpoint_sha256': checkpoint_hash, 'query_sha256': sha256(code.encode()),
        'ready_for_coordinated_validation': not issues, 'issues': issues, 'native': native,
        'gameplay_started': False, 'deployment_authorized': False,
        'limitations': ['Read-only point-in-time evidence, not a single-writer lease or throughput test.',
                       'VM isolation, matching save/checkpoint handoff and native trials remain operator gates.']}


def cli(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink(): raise ValueError('Output already exists')
        report = probe(args.config, args.checkpoint)
        write_new(args.output, canonical(report))
    except Exception as error:
        # Never echo provider, RCON, config, path or password-bearing exception text.
        parser.exit(2, f'Development preflight failed ({type(error).__name__}); gameplay not started.\n')
    parser.exit(0 if report['ready_for_coordinated_validation'] else 2,
                'Development preflight recorded; gameplay not started.\n')


if __name__ == '__main__':
    cli()
