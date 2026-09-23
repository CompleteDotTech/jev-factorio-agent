"""Offline, streaming controller-overhead report; never initializes a backend.

Durations are inclusive: observation/dispatch may contain checkpoint calls;
selection contains model evaluation. They must not be added as disjoint time.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path

CALLS = {'observation', 'model_response', 'candidate_set_created', 'checkpoint_written'}
CHECKPOINT_STATUSES = {'written', 'unchanged', 'failed', 'disabled'}


class PerformanceCounters:
    def __init__(self):
        self.calls: dict[str, dict] = {}
        self.checkpoints = Counter()
        self.checkpoint_ns = Counter()

    def call(self, name: str, duration_ns: int, failed: bool = False) -> None:
        if name not in CALLS:
            return
        row = self.calls.setdefault(name, {'count': 0, 'failed': 0, 'total_ns': 0, 'max_ns': 0})
        row['count'] += 1
        row['failed'] += int(failed)
        row['total_ns'] += max(0, duration_ns)
        row['max_ns'] = max(row['max_ns'], duration_ns)

    def checkpoint(self, metrics: dict) -> None:
        status = metrics.get('status')
        if status not in CHECKPOINT_STATUSES:
            return
        self.checkpoints[status] += 1
        if status == 'written':
            self.checkpoints['bytes_written'] += metrics['bytes']
        for key in ('serialize_ns', 'file_sync_ns', 'directory_sync_ns', 'total_ns'):
            self.checkpoint_ns[key] += metrics.get(key, 0)

    def snapshot(self) -> dict:
        return {'schema': 1, 'clock': 'perf_counter_ns', 'durations_are_inclusive': True,
                'scope': 'current_iteration_through_checkpoint_before_record',
                'calls': deepcopy(self.calls), 'checkpoints': dict(self.checkpoints),
                'checkpoint_ns': dict(self.checkpoint_ns)}


def summarize(path: Path) -> dict:
    """Aggregate incremental per-record metrics, not repeated attempt histories.

    Legacy records are counted separately, never assigned zero overhead. This
    reports the supplied stream as-is; pass one nonduplicated campaign export.
    Incomplete or malformed records fail explicitly instead of disappearing.
    """
    summary = {'schema': 1, 'records': 0, 'instrumented_records': 0,
               'legacy_records': 0, 'calls': {}, 'phases': {}, 'checkpoints': {},
               'checkpoint_ns': {}, 'actions': {}, 'capacity_reasons': {},
               'durations_are_inclusive': True,
               'wall_time_or_speedup_inferred': False}
    def merge(destination, key, count, total, maximum):
        if any(type(v) is not int or v < 0 for v in (count, total, maximum)):
            raise ValueError('Invalid nonnegative performance counter')
        result = destination.setdefault(key, {'count': 0, 'total_ns': 0, 'max_ns': 0})
        result['count'] += count
        result['total_ns'] += total
        result['max_ns'] = max(result['max_ns'], maximum)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError('Invalid record')
                summary['records'] += 1
                action = row.get('action', 'unknown')
                # Fixed vocabulary, not arbitrary native payloads or error messages.
                from .skills import ACTIONS
                from .factory_contract import COMMAND_FIELDS
                action = action if action in ACTIONS | COMMAND_FIELDS.keys() | {'observe', 'verify', 'reconcile'} else 'unknown'
                summary['actions'][action] = summary['actions'].get(action, 0) + 1
                metrics = row.get('performance')
                if metrics is None:
                    summary['legacy_records'] += 1
                    continue
                if not isinstance(metrics, dict) or type(metrics.get('schema')) is not int or metrics['schema'] != 1:
                    raise ValueError('Unsupported performance schema')
                summary['instrumented_records'] += 1
                for name, value in metrics.get('calls', {}).items():
                    if name not in CALLS:
                        raise ValueError('Unknown timed call')
                    merge(summary['calls'], name, value['count'], value['total_ns'], value['max_ns'])
                    failed = value.get('failed', 0)
                    if type(failed) is not int or not 0 <= failed <= value['count']:
                        raise ValueError('Invalid failed-call count')
                    summary['calls'][name]['failed'] = summary['calls'][name].get('failed', 0) + failed
                for category, permitted in (('checkpoints', CHECKPOINT_STATUSES | {'bytes_written'}),
                                           ('checkpoint_ns', {'serialize_ns', 'file_sync_ns', 'directory_sync_ns', 'total_ns'})):
                    for key, value in metrics.get(category, {}).items():
                        if key not in permitted or type(value) is not int or value < 0:
                            raise ValueError('Invalid checkpoint metric')
                        summary[category][key] = summary[category].get(key, 0) + value
                from .telemetry import validate_phase
                for event in row.get('phases', []):
                    validate_phase(event)
                    if event['status'] == 'started':
                        continue
                    elapsed = event['seconds']
                    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed):
                        raise ValueError('Invalid phase duration')
                    ns = round(elapsed * 1_000_000_000)
                    merge(summary['phases'], event['stage'], 1, ns, ns)
                for value in row.get('capacity_evidence', {}).get('producers', {}).values():
                    reason = value.get('reason')
                    if reason not in {'sustained_supplied_production', 'insufficient_history',
                                      'supply_or_activity_constraint', 'output_or_transport_constraint',
                                      'rate_outside_supported_band', 'missing_or_unsupported_evidence'}:
                        raise ValueError('Invalid capacity reason')
                    summary['capacity_reasons'][reason] = summary['capacity_reasons'].get(reason, 0) + 1
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise ValueError(f'Invalid performance record at line {number}') from error
    for kind in ('calls', 'phases'):
        for row in summary[kind].values():
            row['mean_ns'] = row['total_ns'] / row['count'] if row['count'] else None
    return summary


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path, help='One nonduplicated JSONL or JSONL.gz gameplay stream')
    args = parser.parse_args()
    try:
        print(json.dumps(summarize(args.log), indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError):
        parser.exit(2, 'Could not read a complete, valid performance stream.\n')


if __name__ == '__main__':
    cli()
