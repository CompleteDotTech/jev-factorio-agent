"""Bounded offline latency distributions with explicit nested/unknown scopes.

Reads a single nonduplicated gameplay stream, never opens a game connection.
Only fixed metric names, numeric counters and validated source digests leave
this report. Session IDs, process IDs, native payloads and paths do not.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re
import statistics

from .observation import LABELS
from .performance import CALLS, CHECKPOINT_STATUSES
from .iteration_timing import validate_timing, CLOCKS
from .telemetry import STAGES, validate_phase

MAX_LINE = 8 * 1024 * 1024
MAX_RECORDS = 100_000
PARTS = {'rpc', 'helpers', 'decode', 'unattributed'}


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {'count': len(ordered), 'median_ns': statistics.median(ordered) if ordered else None,
            'p95_ns': ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
            'total_ns': sum(ordered)}


def nonnegative(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('Invalid nonnegative integer')
    return value


def timestamp(value) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Invalid timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Timestamp requires timezone')
    return result


def analyze(path: Path, *, max_records: int = MAX_RECORDS) -> dict:
    if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
        raise ValueError('Invalid record budget')
    samples = defaultdict(list)
    counts = defaultdict(int)
    records = legacy = incomplete = 0
    identity = source = source_digest = source_dirty = previous_record_time = None
    profiles_seen = partitions_seen = 0
    timed_iterations = incomplete_iterations = timed_gaps = missing_prior = 0
    previous_iteration_index = None
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        while raw := stream.readline(MAX_LINE + 1):
            records += 1
            try:
                if (records > max_records or not raw.endswith('\n')
                        or len(raw.encode('utf-8')) > MAX_LINE):
                    raise ValueError('Capture exceeds budget or is incomplete')
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError('Gameplay row must be an object')
                # Enforce one immutable treatment/epoch without publishing its identifiers.
                signature = json.dumps({key: row.get(key) for key in (
                    'session_id', 'world_kind', 'process_id', 'code_revision', 'policy',
                    'target', 'requested_model', 'acceptance_configuration', 'campaign_treatment')},
                    sort_keys=True, allow_nan=False)
                if identity is not None and signature != identity:
                    raise ValueError('Mixed treatment or epoch')
                identity = signature
                revision = row.get('code_revision')
                commit = revision.get('commit') if isinstance(revision, dict) else revision
                source = commit if isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit) else None
                digest = revision.get('source_sha256') if isinstance(revision, dict) else None
                source_digest = digest if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) else None
                dirty = revision.get('dirty') if isinstance(revision, dict) else None
                source_dirty = dirty if type(dirty) is bool else None
                phase_start = None
                phases = row.get('phases', [])
                if not isinstance(phases, list) or len(phases) > 1024:
                    raise ValueError('Invalid phase count')
                for phase in phases:
                    validate_phase(phase)
                    if phase['status'] != 'started':
                        samples['phase_inclusive:' + phase['stage']].append(round(phase['seconds'] * 1e9))
                    if phase['stage'] == 'observe' and phase['status'] == 'started' and phase_start is None:
                        phase_start = timestamp(phase['at_utc'])
                current_time = timestamp(row['recorded_at_utc']) if row.get('recorded_at_utc') else None
                if current_time is not None and previous_record_time is not None and current_time < previous_record_time:
                    raise ValueError('Recorded time regressed')
                if phase_start is not None and previous_record_time is not None:
                    delta = (phase_start - previous_record_time).total_seconds()
                    if delta < 0:
                        raise ValueError('Observation predates previous record')
                    samples['record_utc_to_next_observe_utc_gap'].append(round(delta * 1e9))
                if current_time is not None and phase_start is not None:
                    delta = (current_time - phase_start).total_seconds()
                    if delta < 0:
                        raise ValueError('Record predates observation')
                    samples['observe_utc_to_record_utc'].append(round(delta * 1e9))
                previous_record_time = current_time
                profiles = row.get('observation_profiles', [])
                if not isinstance(profiles, list) or len(profiles) > 4:
                    raise ValueError('Invalid profile count')
                for profile in profiles:
                    if not isinstance(profile, dict) or type(profile.get('schema')) is not int or profile['schema'] != 1:
                        raise ValueError('Unsupported observation profile')
                    profiles_seen += 1
                    samples['observation_wall'].append(nonnegative(profile['total_ns']))
                    if 'attribution_schema' not in profile:
                        legacy += 1
                    elif type(profile['attribution_schema']) is not int or profile['attribution_schema'] != 1:
                        raise ValueError('Unsupported attribution schema')
                    else:
                        samples['observation_process_cpu'].append(nonnegative(profile['process_cpu_ns']))
                        if profile.get('partition_complete') is True:
                            for key, total_key, label in (
                                    ('wall_partition_ns', 'total_ns', 'observation_exclusive_wall:'),
                                    ('process_cpu_partition_ns', 'process_cpu_ns', 'observation_exclusive_cpu:')):
                                parts = profile[key]
                                if not isinstance(parts, dict) or set(parts) != PARTS:
                                    raise ValueError('Invalid partition')
                                if sum(nonnegative(v) for v in parts.values()) != profile[total_key]:
                                    raise ValueError('Partition does not reconcile')
                                for name, value in parts.items():
                                    samples[label + name].append(value)
                            partitions_seen += 1
                        else:
                            incomplete += 1
                    for group in ('calls', 'subcalls'):
                        table = profile.get(group, {})
                        if not isinstance(table, dict) or set(table) - LABELS:
                            raise ValueError('Unknown profile label')
                        for name, entry in table.items():
                            count = nonnegative(entry['count'])
                            failed = nonnegative(entry.get('failed', 0))
                            if failed > count:
                                raise ValueError('Invalid failure count')
                            prefix = 'observation_' + group + ':' + name
                            samples[prefix + ':inclusive_aggregate'].append(nonnegative(entry['total_ns']))
                            counts[prefix + ':count'] += count
                            counts[prefix + ':failed'] += failed
                            for key in ('request_bytes', 'response_bytes'):
                                if key in entry:
                                    counts[prefix + ':' + key] += nonnegative(entry[key])
                prior = row.get('previous_iteration_timing')
                if prior is None:
                    missing_prior += 1
                else:
                    prior = validate_timing(prior)
                    index = prior['iteration_index']
                    if previous_iteration_index is not None:
                        if index <= previous_iteration_index:
                            raise ValueError('Duplicate or regressed iteration timing')
                        counts['iteration:unpublished_between_records'] += index - previous_iteration_index - 1
                    else:
                        # The input can be a tail of a stream. These indices are
                        # unrepresented here, not proof the runtime lost records.
                        counts['iteration:unobserved_before_first_sample'] += index - 1
                        counts['iteration:unpublished_between_records'] += index - 1
                    previous_iteration_index = index
                    for name, value in prior['native_io'].items():
                        counts['iteration_native_io:' + name] += value
                    if prior['partition_complete']:
                        timed_iterations += 1
                        for clock in CLOCKS:
                            samples['iteration_total:' + clock].append(prior['totals_ns'][clock])
                        for name, values in prior['phases'].items():
                            counts['iteration_phase:' + name + ':calls'] += values['calls']
                            counts['iteration_phase:' + name + ':failed'] += values['failed']
                            for clock in CLOCKS:
                                for scope in ('inclusive', 'exclusive'):
                                    samples['iteration_phase_' + scope + ':' + name + ':' + clock].append(values[clock + '_' + scope + '_ns'])
                    else:
                        incomplete_iterations += 1
                    gap = prior['gap']
                    counts['loop_sleep:calls'] += gap['sleep_calls']
                    counts['loop_sleep:failed'] += gap['sleep_failed']
                    if gap['complete']:
                        timed_gaps += 1
                        for clock in CLOCKS:
                            for name in ('total_ns', 'intentional_sleep_ns', 'other_gap_ns'):
                                samples['iteration_gap:' + name + ':' + clock].append(gap[name][clock])
                            if prior['partition_complete']:
                                samples['iteration_and_following_gap:' + clock].append(prior['totals_ns'][clock] + gap['total_ns'][clock])
                metrics = row.get('performance')
                if metrics is not None:
                    if not isinstance(metrics, dict) or type(metrics.get('schema')) is not int or metrics['schema'] != 1:
                        raise ValueError('Unsupported performance schema')
                    for group in ('calls', 'cpu_calls'):
                        table = metrics.get(group, {})
                        if not isinstance(table, dict) or set(table) - CALLS:
                            raise ValueError('Unknown performance label')
                        for name, entry in table.items():
                            samples['per_record_' + group + ':' + name].append(nonnegative(entry['total_ns']))
                            counts['per_record_' + group + ':' + name + ':count'] += nonnegative(entry['count'])
                    checkpoints = metrics.get('checkpoints', {})
                    if not isinstance(checkpoints, dict) or set(checkpoints) - (CHECKPOINT_STATUSES | {'bytes_written'}):
                        raise ValueError('Unknown checkpoint counter')
                    for key, value in checkpoints.items():
                        counts['checkpoint:' + key] += nonnegative(value)
                    for key in ('capture_calls', 'serialization_calls', 'measured_calls'):
                        value = metrics.get('checkpoint_operations', {}).get(key)
                        if value is not None:
                            counts['checkpoint:' + key] += nonnegative(value)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
                raise ValueError(f'Invalid latency record at line {records}') from error
    if not records:
        raise ValueError('Empty latency capture')
    return {'schema': 1, 'records': records, 'source_commit': source,
            'source_sha256': source_digest, 'source_dirty': source_dirty,
            'iteration_timing': {'complete_iterations': timed_iterations,
                'incomplete_iterations': incomplete_iterations, 'complete_following_gaps': timed_gaps,
                'records_without_prior_timing': missing_prior,
                'publication': 'one_record_lag; final_tail_not_inferred_or_assigned_zero'},
            'observation_profiles': profiles_seen, 'legacy_profiles_without_cpu_partition': legacy,
            'reconciled_partitions': partitions_seen, 'incomplete_partitions': incomplete,
            'distributions': {name: distribution(values) for name, values in sorted(samples.items())},
            'counts': dict(sorted(counts.items())),
            'quantiles': 'median_middle_pair_mean_and_p95_nearest_rank',
            'scopes': {
                'iteration_exclusive': 'nonoverlapping_components_of_completed_decorated_step',
                'iteration_inclusive': 'nested_totals_not_additive_not_individual_call_quantiles',
                'iteration_gap': 'nonoverlapping_intentional_loop_sleep_and_other_gap; not watchdog cadence',
                'missing_iteration_indices': 'not_represented_in_input_since_index_1_including_prefix; not_proof_of_runtime_loss',
                'thread_cpu': 'current_python_thread_not_native_server_cpu',
                'observation_exclusive': 'within_each_single_ordered_observation_only',
                'phase_inclusive': 'nested_phase_durations_not_additive',
                'per_record': 'per_record_aggregates_not_individual_call_quantiles',
                'gap': 'UTC_record_to_next_observation_includes_unmeasured_emission_and_sleep',
                'process_cpu': 'whole_python_process_including_other_threads_not_native_or_host_cpu'},
            'unavailable': ['opaque_helper_transport_decomposition', 'helper_retry_and_backoff',
                            'native_server_cpu', 'final_iteration_tail_without_following_record']
                + ([] if timed_gaps else ['intentional_sleep_separation'])
                + ([] if timed_iterations else ['full_record_construction_emission_partition']),
            'native_acceptance_proven': False, 'deployment_authorized': False}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--max-records', type=int, default=MAX_RECORDS)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(analyze(args.log, max_records=args.max_records), indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError):
        parser.exit(2, 'Cannot report an incomplete, mixed or invalid latency capture.\n')


if __name__ == '__main__':
    main()
