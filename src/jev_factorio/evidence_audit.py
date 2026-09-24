"""Verify a projected capture offline; missing measurements remain unavailable.

No backend, model, network, world, checkpoint or failure-history mutation occurs.
Hashes establish consistency with the supplied manifest, not external authenticity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re

MAX_FILE_BYTES = 80_000_000
MAX_DECOMPRESSED_BYTES = 128_000_000
MAX_BUNDLE_BYTES = 128_000_000
REQUIRED = {'capture-manifest.json', 'controller-snapshot.json',
            'gameplay-recent.jsonl.gz', 'gameplay-timeline.jsonl',
            'supervisor-events-recent.jsonl', 'README.md', 'NOTE.md'}
TIMELINE_KEYS = ('recorded_at_utc', 'tick', 'action', 'outcome', 'verified')
COVERAGE_FIELDS = ('performance', 'phases', 'attempt', 'attempt_outcomes',
                   'fair_action_metrics', 'capacity_evidence', 'planning_diagnostics',
                   'failure_budgets', 'background_job', 'capital_investment', 'mining_outposts')


def _json(raw):
    def invalid(value):
        raise ValueError('Non-finite JSON number')
    return json.loads(raw, parse_constant=invalid)


def _read(root: Path, name: str) -> bytes:
    path = root / name
    if path.is_symlink() or path.resolve().parent != root or not path.is_file():
        raise ValueError('Capture files must be regular files inside the bundle')
    with path.open('rb') as stream:
        value = stream.read(MAX_FILE_BYTES + 1)
    if len(value) > MAX_FILE_BYTES:
        raise ValueError('Capture file exceeds the read budget')
    return value


def _records(raw: bytes, label: str) -> list[dict]:
    if not raw or not raw.endswith(b'\n'):
        raise ValueError(label + ': missing complete final newline')
    result = []
    for number, line in enumerate(raw.splitlines(), 1):
        try:
            row = _json(line)
        except (ValueError, UnicodeError) as error:
            raise ValueError(f'{label}: invalid JSON at line {number}') from error
        if not isinstance(row, dict):
            raise ValueError(f'{label}: non-object at line {number}')
        result.append(row)
    return result


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Missing UTC timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp lacks timezone')
    return result


def _number(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _position(value):
    if isinstance(value, dict):
        value = [value.get('x'), value.get('y')]
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(v) in {int, float} and math.isfinite(v) for v in value)):
        return tuple(value)
    return None


def measurements(rows: list[dict]) -> dict:
    """Conservative metrics for one ordered treatment, not a victory benchmark."""
    if not rows:
        raise ValueError('Empty gameplay stream')
    identities = {json.dumps([r.get(key) for key in
        ('session_id', 'code_revision', 'policy', 'run_id', 'segment_id')],
        sort_keys=True, allow_nan=False) for r in rows}
    if len(identities) != 1:
        raise ValueError('Partition different sessions, revisions or policies before aggregation')
    if not rows[0].get('session_id'):
        raise ValueError('Missing session identity')
    times = [_time(r.get('recorded_at_utc')) for r in rows]
    ticks = [r.get('tick') for r in rows]
    if (any(type(t) is not int or t < 0 for t in ticks)
            or any(b < a for a, b in zip(ticks, ticks[1:]))
            or any(b < a for a, b in zip(times, times[1:]))):
        raise ValueError('Gameplay timestamps or ticks regressed')
    record_ids = [json.dumps([r.get(k) for k in ('execution_id', 'tick', 'recorded_at_utc')],
                             sort_keys=True) for r in rows]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError('Duplicate gameplay records must be removed before aggregation')
    calls = [r for r in rows if r.get('model_call') is True]
    usage = {}
    for field in ('input_tokens', 'output_tokens'):
        values = [(r.get('usage') or {}).get(field) for r in calls]
        known = [v for v in values if type(v) is int and v >= 0]
        usage[field] = {'known_sum': sum(known), 'reported_calls': len(known),
                        'missing_calls': len(values) - len(known),
                        'total': sum(known) if len(known) == len(values)
                        and all(type(r.get('model_call')) is bool for r in rows) else None}
    distance, known_legs, unknown_legs = 0.0, 0, 0
    previous = None
    previous_tick = None
    states = []
    for row in rows:
        for key in ('state', 'after_state'):
            state = row.get(key) or {}
            current = _position(state.get('player_position'))
            tick = state.get('tick')
            if type(tick) is not int or tick < 0:
                current = None
            if previous_tick is not None and type(tick) is int and tick < previous_tick:
                raise ValueError('Observation tick regressed')
            if states:
                if current is None or previous is None:
                    unknown_legs += 1
                else:
                    distance += math.dist(previous, current)
                    known_legs += 1
            states.append(state)
            previous, previous_tick = current, tick if type(tick) is int else None
    # Baseline receipts are historical, not work done in this capture.
    seen = {}
    receipt_coverage = sum(isinstance(s.get('factory', {}).get('receipts'), dict) for s in states)
    baseline_known = isinstance(states[0].get('factory', {}).get('receipts'), dict)
    transfers, transfer_units, small = Counter(), Counter(), 0
    for index, state in enumerate(states):
        receipts = state.get('factory', {}).get('receipts', {})
        if not isinstance(receipts, dict):
            raise ValueError('Invalid receipt map')
        for identity, receipt in receipts.items():
            if identity in seen:
                if seen[identity] != receipt:
                    raise ValueError('Conflicting receipt identity')
                continue
            seen[identity] = receipt
            if index == 0 or not baseline_known:
                continue
            if (not isinstance(receipt, dict) or type(receipt.get('quantity')) is not int
                    or not 1 <= receipt['quantity'] <= 200
                    or type(receipt.get('extracting')) is not bool
                    or type(receipt.get('unit_number')) is not int
                    or receipt['unit_number'] <= 0
                    or not isinstance(receipt.get('item'), str)):
                continue
            kind = 'extract' if receipt['extracting'] else 'insert'
            transfers[kind] += 1
            transfer_units[kind + ':' + receipt['item']] += receipt['quantity']
            small += int(receipt['quantity'] <= 5)
    first, last = states[0], states[-1]
    before = first.get('factory', {}).get('produced')
    after = last.get('factory', {}).get('produced')
    production = None
    if isinstance(before, dict) and isinstance(after, dict):
        if not all(_number(v) for v in (*before.values(), *after.values())):
            raise ValueError('Invalid native production counters')
        production = {k: after.get(k, 0) - before.get(k, 0) for k in before.keys() | after.keys()}
        if any(v < 0 for v in production.values()):
            production = None
    researched = None
    if isinstance(first.get('researched'), list) and isinstance(last.get('researched'), list):
        researched = sorted(set(last['researched']) - set(first['researched']))
    rejects = Counter()
    for row in rows:
        factory = (row.get('after_state') or row.get('state') or {}).get('factory', {})
        for capability in ('input_routes', 'production_sites', 'mining_outposts'):
            native = factory.get(capability, {})
            diagnostics = dict(native.get('diagnostics', {}))
            if capability == 'production_sites':
                diagnostics.update({role: value for role, value in native.get('sources', {}).items()
                                    if value.get('state') in {'rejected', 'fault'}})
            for role, reason in diagnostics.items():
                if isinstance(reason, dict) and isinstance(reason.get('reason'), str):
                    rejects[capability + ':' + role + ':' + reason['reason']] += 1
    return {
        'records': len(rows), 'first_record_utc': rows[0]['recorded_at_utc'],
        'last_record_utc': rows[-1]['recorded_at_utc'], 'first_tick': ticks[0], 'last_tick': ticks[-1],
        'wall_span_seconds': (times[-1] - times[0]).total_seconds(),
        'action_label_counts': dict(Counter(r.get('action', 'missing') for r in rows)),
        'verified_label_count': sum(r.get('verified') is True for r in rows),
        'labels_are_not_unique_successful_actions': True,
        'model_calls_reported': len(calls),
        'model_call_flag_missing_records': sum(type(r.get('model_call')) is not bool for r in rows),
        'model_usage': usage, 'billed_model_cost': None,
        'sampled_travel_lower_bound': {'tiles': round(distance, 3), 'known_legs': known_legs,
            'unknown_legs': unknown_legs, 'actual_path_distance': None},
        'newly_observed_transfer_receipts': dict(transfers) if baseline_known else None,
        'newly_observed_transfer_units': dict(transfer_units) if baseline_known else None,
        'small_transfer_receipts_le_5': small if baseline_known else None,
        'transfer_receipt_coverage': {'observations_with_receipts': receipt_coverage,
            'missing_observations': len(states) - receipt_coverage,
            'baseline_known': baseline_known, 'rolling_native_window_may_omit_transfers': True},
        'transfer_receipt_scope': 'New IDs after first observation; historical baseline excluded; not full campaign totals',
        'native_production_counter_delta': production,
        'useful_downstream_production_verified': None,
        'new_researched_names': researched,
        'research_start': {k: first.get('factory', {}).get(k) for k in ('research', 'research_progress')},
        'research_end': {k: last.get('factory', {}).get(k) for k in ('research', 'research_progress')},
        'automation_rejection_observations': dict(rejects),
        'field_coverage': {key: {'present': sum(key in r for r in rows),
            'missing': sum(key not in r for r in rows)} for key in COVERAGE_FIELDS},
        'actor_idle_seconds': None, 'native_recovery_reliability': None,
        'acceptance_status': 'not_a_native_acceptance_run',
    }


def audit_bundle(directory: Path) -> dict:
    root = directory.resolve(strict=True)
    checksums = _read(root, 'SHA256SUMS').decode('utf-8')
    expected = {}
    for line in checksums.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)', line)
        if not match or match[2] in expected or match[2] == 'SHA256SUMS':
            raise ValueError('Invalid or duplicate SHA256SUMS entry')
        expected[match[2]] = match[1]
    if not REQUIRED <= expected.keys() or len(expected) > 64:
        raise ValueError('Missing required bundle files or excessive manifest entries')
    files, verified, total_bytes = {}, {}, 0
    for name, digest in expected.items():
        raw = _read(root, name)
        total_bytes += len(raw)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError('Capture bundle exceeds the aggregate read budget')
        actual = hashlib.sha256(raw).hexdigest()
        if actual != digest:
            raise ValueError(name + ': SHA256 mismatch')
        files[name] = raw
        verified[name] = {'sha256': actual, 'bytes': len(raw)}
    manifest = _json(files['capture-manifest.json'])
    stored = manifest['stored_gameplay']
    size = stored['uncompressed_bytes']
    if type(size) is not int or not 0 < size <= MAX_DECOMPRESSED_BYTES:
        raise ValueError('Invalid decompression budget')
    with gzip.GzipFile(fileobj=io.BytesIO(files['gameplay-recent.jsonl.gz'])) as stream:
        data = stream.read(size + 1)
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != size or digest != stored['uncompressed_sha256']:
        raise ValueError('Decompressed size or SHA256 mismatch')
    rows = _records(data, 'gameplay')
    timeline = _records(files['gameplay-timeline.jsonl'], 'timeline')
    events = _records(files['supervisor-events-recent.jsonl'], 'supervisor')
    if (type(manifest['gameplay_records']) is not int or type(manifest['supervisor_events']) is not int
            or len(rows) != manifest['gameplay_records'] or len(timeline) != len(rows)
            or len(events) != manifest['supervisor_events']):
        raise ValueError('Manifest record counts do not match capture')
    for number, (row, slim) in enumerate(zip(rows, timeline), 1):
        if any(row.get(key) != slim.get(key) for key in TIMELINE_KEYS):
            raise ValueError(f'Timeline disagrees with gameplay at line {number}')
    if (rows[0].get('recorded_at_utc') != manifest['first_record_utc']
            or rows[-1].get('recorded_at_utc') != manifest['last_record_utc']):
        raise ValueError('Manifest time window mismatch')
    summary = measurements(rows)
    checkpoint = _json(files['controller-snapshot.json'])
    from .performance import summarize
    return {'schema': 1, 'bundle_files_verified': verified,
        'decompressed_bytes': size, 'decompressed_sha256': digest, 'gzip_integrity_checked': True,
        'timeline_matches_gameplay': True, 'supervisor_records': len(events),
        'supervisor_event_counts': dict(Counter(r.get('event', 'missing') for r in events)),
        'source_commit_reported': checkpoint.get('source_commit'),
        'source_range_hashes_independently_verified': False,
        'snapshot_is_atomic': False, 'hashes_prove_external_authenticity': False,
        'metrics': summary, 'controller_overhead': summarize(root / 'gameplay-recent.jsonl.gz'),
        'limitations': ['Original growing source byte ranges are not in this bundle.',
            'The checkpoint, supervisor and gameplay streams were sampled sequentially.',
            'A matching hash verifies supplied bytes, not fair gameplay or external authenticity.',
            'Missing idle, billed cost, useful-output or recovery evidence cannot pass cutover gates.']}


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--output', type=Path, help='Create a new report; never overwrite a file')
    args = parser.parse_args()
    try:
        result = json.dumps(audit_bundle(args.bundle), sort_keys=True, indent=2, allow_nan=False) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(result)
        else:
            print(result, end='')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, EOFError) as error:
        parser.exit(2, f'Capture audit failed ({type(error).__name__}); no acceptance claim produced.\n')


if __name__ == '__main__':
    cli()
