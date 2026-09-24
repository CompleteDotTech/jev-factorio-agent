"""Create/verify a private immutable acceptance capture from stopped local files."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import gzip
import io
import os
from pathlib import Path
import re

from .acceptance_io import MAX_LOG, canonical, hash_file, load_json, records, sha256, stable_read, write_new
from .dev_preflight import checkpoint_read
from .research_log import Redactor

SCHEMA = 'jev-factorio.acceptance-capture.v1'
FILES = {'capture-manifest.json', 'trial.json', 'preflight.json', 'initial-checkpoint.json',
         'final-checkpoint.json', 'gameplay.jsonl.gz'}
RECORD_FIELDS = set('schema_version controller session_id world_kind target policy requested_model resolved_model '
    'run_id segment_id execution_id code_revision recorded_at_utc tick action outcome verified status reason '
    'model_call usage completed_goals pending attempt attempt_outcomes performance phases fair_action_metrics '
    'capacity_evidence planning_diagnostics failure_budgets background_work background_schema background_job '
    'background_attempt capital_investment furnace_output_buffers furnace_input_belts mining_outposts '
    'ore_side_successors buffer_evidence input_route_evidence mining_outpost_evidence successor_evidence '
    'successor_projects acceptance_configuration'.split())
STATE_FIELDS = set('tick session_id world_kind game_version inventory player_position nearby_resources '
                   'researched victory victory_source world_seed health'.split())
FACTORY_FIELDS = set('tick entities produced consumed researched research research_progress receipts '
    'connections force_entity_counts crafting_queue player_bound player_connected rockets_launched '
    'rocket_baseline exploration_radius fair_resource_targets fair_action_metrics output_buffers '
    'input_routes production_sites mining_outposts successors craft_jobs_protocol craft_job_actor '
    'craft_job_inventory craft_job acceptance_runtime'.split())
DENIED = {'password', 'api_key', 'authorization', 'headers', 'environment', 'endpoint', 'endpoints',
          'prompt', 'prompts', 'questions', 'answers', 'request', 'response', 'raw_request', 'raw_response',
          'provider_body', 'provider_response', 'provider_request', 'access_token', 'refresh_token', 'secret'}
TRIAL_KEYS = {'schema', 'experiment_id', 'trial_id', 'pair_id', 'arm', 'expected_commit', 'expected_policy',
              'expected_model', 'initial_save_sha256', 'initial_checkpoint_sha256', 'vm_uuid',
              'production_vm_uuid', 'goal', 'configuration'}


def validate_trial(trial: dict) -> None:
    if not isinstance(trial, dict) or set(trial) != TRIAL_KEYS or type(trial['schema']) is not int or trial['schema'] != 1:
        raise ValueError('Invalid trial descriptor')
    for key in TRIAL_KEYS - {'schema', 'configuration'}:
        if not isinstance(trial[key], str) or not 0 < len(trial[key]) <= 128:
            raise ValueError('Trial identifiers must be bounded strings')
    for key, size in (('expected_commit', 40), ('initial_save_sha256', 64), ('initial_checkpoint_sha256', 64)):
        if not re.fullmatch('[0-9a-f]{' + str(size) + '}', trial[key]):
            raise ValueError('Invalid pinned trial digest')
    if trial['arm'] not in {'baseline', 'treatment', 'soak'} or trial['expected_policy'] not in {'jev', 'hybrid', 'deterministic'}:
        raise ValueError('Unknown trial arm or policy')
    flags = {'background_work', 'furnace_output_buffers', 'furnace_input_belts', 'mining_outposts', 'ore_side_successors'}
    config = trial['configuration']
    if (not isinstance(config, dict) or set(config) != flags | {'factory_scheduling'}
            or config['factory_scheduling'] not in {'serial', 'ready-work'}
            or any(type(config[k]) is not bool for k in flags)):
        raise ValueError('Invalid trial configuration')


def project_record(row: dict, redactor: Redactor, counts: Counter) -> dict:
    result = {key: deepcopy(row[key]) for key in RECORD_FIELDS if key in row}
    counts.update('top:' + k for k in row.keys() - RECORD_FIELDS - {'state', 'after_state', 'decision'})
    for label in ('state', 'after_state'):
        state = row.get(label)
        if not isinstance(state, dict): raise ValueError('Missing before/after observation')
        value = {key: deepcopy(state[key]) for key in STATE_FIELDS if key in state}
        factory = state.get('factory', {})
        if not isinstance(factory, dict): raise ValueError('Invalid factory observation')
        value['factory'] = {key: deepcopy(factory[key]) for key in FACTORY_FIELDS if key in factory}
        counts.update('factory:' + k for k in factory.keys() - FACTORY_FIELDS)
        result[label] = value
    decision = row.get('decision') or {}
    result['decision'] = {k: deepcopy(decision[k]) for k in ('plan_id', 'source') if k in decision}
    # Candidate annotations remain in planning_diagnostics. Never copy model state,
    # questions, answers, request/response bodies, or provider exceptions wholesale.
    def clean(value):
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                if key.lower().replace('-', '_') in DENIED:
                    counts['sensitive_key'] += 1
                    continue
                safe = redactor.text(key)
                if safe in cleaned: raise ValueError('Redaction key collision')
                cleaned[safe] = clean(item)
            return cleaned
        if isinstance(value, list): return [clean(item) for item in value]
        if isinstance(value, str):
            safe = redactor.text(value)
            counts['redacted_strings'] += int(safe != value)
            return safe
        return value
    return clean(result)


def capture(*, gameplay: Path, initial_checkpoint: Path, final_checkpoint: Path, save: Path,
            trial_path: Path, preflight_path: Path, output: Path, environ=None) -> dict:
    if output.exists() or output.is_symlink(): raise ValueError('Capture destination already exists')
    trial = load_json(stable_read(trial_path)); validate_trial(trial)
    initial, initial_hash = checkpoint_read(initial_checkpoint)
    final, final_hash = checkpoint_read(final_checkpoint, idle=False)
    if final['session_id'] != initial['session_id']: raise ValueError('Checkpoint sessions differ')
    saved = hash_file(save)
    if saved['sha256'] != trial['initial_save_sha256'] or initial_hash != trial['initial_checkpoint_sha256']:
        raise ValueError('Initial save/checkpoint differs from the predeclared trial')
    probe_raw = stable_read(preflight_path)
    preflight = load_json(probe_raw)
    if (preflight.get('schema') != 'jev-factorio.dev-preflight.v1'
            or preflight.get('checkpoint_sha256') != initial_hash
            or preflight.get('vm_uuid') != trial['vm_uuid']
            or preflight.get('production_vm_uuid') != trial['production_vm_uuid']):
        raise ValueError('Preflight is not bound to this trial boundary')
    raw = stable_read(gameplay, MAX_LOG)
    rows = records(raw)
    redactor = Redactor(dict(os.environ if environ is None else environ))
    dropped = Counter()
    projected = [project_record(row, redactor, dropped) for row in rows]
    # Checkpoints remain private evidence. Preserve their entire validated schema,
    # including history/ownership, while redacting recognizable secrets defensively.
    initial_safe, final_safe = redactor.clean(initial), redactor.clean(final)
    payload = b''.join(canonical(row) for row in projected)
    if len(payload) > MAX_LOG: raise ValueError('Projected gameplay exceeds capture budget')
    content = {'trial.json': canonical(redactor.clean(trial)), 'preflight.json': canonical(redactor.clean(preflight)),
        'initial-checkpoint.json': canonical(initial_safe), 'final-checkpoint.json': canonical(final_safe),
        'gameplay.jsonl.gz': gzip.compress(payload, mtime=0)}
    manifest = {'schema': SCHEMA, 'records': len(projected),
        'decompressed_bytes': len(payload), 'decompressed_sha256': sha256(payload),
        'source_gameplay_sha256': sha256(raw), 'source_initial_checkpoint_sha256': initial_hash,
        'source_final_checkpoint_sha256': final_hash, 'source_preflight_sha256': sha256(probe_raw),
        'source_save': saved, 'projection_schema': 1, 'projection_omissions': dict(dropped),
        'capture_complete': True, 'cross_file_atomicity_proven': False,
        'external_authenticity_proven': False, 'deployment_authorized': False}
    content['capture-manifest.json'] = canonical(manifest)
    output.mkdir(mode=0o700)  # Never merge with or replace a previous capture.
    for name, value in content.items(): write_new(output / name, value)
    sums = ''.join(sha256(content[name]) + '  ' + name + '\n' for name in sorted(content))
    write_new(output / 'SHA256SUMS', sums.encode())  # Completion marker written last.
    return manifest


def verify(directory: Path) -> dict:
    if directory.is_symlink(): raise ValueError('Capture directory cannot be a symlink')
    if {p.name for p in directory.iterdir()} != FILES | {'SHA256SUMS'}:
        raise ValueError('Unexpected or missing capture files')
    entries = {}
    for line in stable_read(directory / 'SHA256SUMS').decode().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([a-zA-Z0-9._-]+)', line)
        if not match or match[2] in entries: raise ValueError('Invalid checksum manifest')
        entries[match[2]] = match[1]
    if set(entries) != FILES: raise ValueError('Checksum coverage is incomplete')
    content = {}
    for name, digest in entries.items():
        value = stable_read(directory / name, MAX_LOG)
        if sha256(value) != digest: raise ValueError('Capture checksum mismatch')
        content[name] = value
    manifest = load_json(content['capture-manifest.json'])
    size = manifest.get('decompressed_bytes')
    if (manifest.get('schema') != SCHEMA or manifest.get('capture_complete') is not True
            or type(size) is not int or not 0 < size <= MAX_LOG):
        raise ValueError('Invalid capture schema or decompression budget')
    with gzip.GzipFile(fileobj=io.BytesIO(content['gameplay.jsonl.gz'])) as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or sha256(raw) != manifest['decompressed_sha256']:
        raise ValueError('Decompressed capture mismatch')
    rows = records(raw)
    if type(manifest.get('records')) is not int or len(rows) != manifest['records']:
        raise ValueError('Capture record count mismatch')
    result = {name[:-5]: load_json(value) for name, value in content.items() if name.endswith('.json')}
    validate_trial(result['trial'])
    result.update(rows=rows, bundle_sha256=sha256(stable_read(directory / 'SHA256SUMS')))
    return result


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('gameplay', 'initial-checkpoint', 'final-checkpoint', 'save', 'trial', 'preflight', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        capture(gameplay=args.gameplay, initial_checkpoint=args.initial_checkpoint,
            final_checkpoint=args.final_checkpoint, save=args.save, trial_path=args.trial,
            preflight_path=args.preflight, output=args.output)
    except Exception as error:
        parser.exit(2, f'Acceptance capture failed ({type(error).__name__}); no acceptance claim produced.\n')
    print('Private capture created; native acceptance and deployment remain separate gates.')


if __name__ == '__main__': cli()
