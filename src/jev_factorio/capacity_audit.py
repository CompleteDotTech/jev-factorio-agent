"""Read-only, privacy-minimized Linux capacity audit; never applies host changes.

Visible cgroup ceilings are upper bounds, not reserved capacity or proof that
an enclosing VM/host is uncapped. Run independently in each authorized scope.
See docs/CAPACITY_AUDIT.md for sampling, owner review and exact rollback.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import stat
import time

MAX_FILE = 256 * 1024
MAX_LEVELS = 64
MAX_CPU = 65535
MIN_BANDWIDTH_US = 1000
MAX_PERIOD_US = 1000000
CPU_COUNTERS = {'usage_usec', 'user_usec', 'system_usec', 'nr_periods', 'nr_throttled', 'throttled_usec'}
VM_COUNTERS = {'pswpin', 'pswpout', 'pgmajfault'}
MEM_COUNTERS = {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}


def _text(path: Path) -> str | None:
    """Only bounded regular files; no symlink/file device/FIFO payloads."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            data = stream.read(MAX_FILE + 1)
        return data.decode('utf-8') if len(data) <= MAX_FILE else None
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _integer(value: str, *, positive=False) -> int:
    if not re.fullmatch(r'[0-9]{1,20}', value):
        raise ValueError('Invalid integer counter')
    result = int(value)
    if positive and result == 0:
        raise ValueError('Positive integer required')
    return result


def parse_cpu_max(text: str) -> dict:
    parts = text.split()
    if len(parts) != 2:
        raise ValueError('Invalid CPU quota')
    period = _integer(parts[1], positive=True)
    quota = None if parts[0] == 'max' else _integer(parts[0], positive=True)
    if not MIN_BANDWIDTH_US <= period <= MAX_PERIOD_US or (
            quota is not None and quota < MIN_BANDWIDTH_US):
        raise ValueError('CPU bandwidth is outside the supported CFS range')
    return {'state': 'unlimited' if quota is None else 'finite',
            'quota_us': quota, 'period_us': period,
            'cpu_equivalents': quota / period if quota is not None else None}


def parse_cpu_set(text: str) -> set[int]:
    result = set()
    if not text.strip():
        return result
    for entry in text.strip().split(','):
        if not re.fullmatch(r'[0-9]{1,5}(?:-[0-9]{1,5})?', entry):
            raise ValueError('Invalid CPU set')
        bounds = entry.split('-')
        first, last = int(bounds[0]), int(bounds[-1])
        if not 0 <= first <= last <= MAX_CPU:
            raise ValueError('CPU set exceeds audit budget')
        result.update(range(first, last + 1))
    return result


def _keyed(text: str | None, allowed: set[str]) -> dict:
    if text is None:
        return {}
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] in allowed:
            if len(parts) != 2 or parts[0] in result:
                raise ValueError('Malformed counter table')
            result[parts[0]] = _integer(parts[1])
    return result


def parse_proc_stat(text: str) -> dict:
    rows = [line.split()[1:] for line in text.splitlines() if line.startswith('cpu ')]
    if len(rows) != 1 or len(rows[0]) < 8:
        raise ValueError('Missing aggregate CPU accounting')
    values = [_integer(value) for value in rows[0]]
    cpu_names = [line.split()[0] for line in text.splitlines() if re.match(r'^cpu[0-9]+ ', line)]
    if len(cpu_names) != len(set(cpu_names)):
        raise ValueError('Duplicate CPU accounting row')
    # guest and guest_nice are already included in user/nice; do not add twice.
    boot = _keyed(text, {'btime'}).get('btime')
    return {'total_ticks': sum(values[:8]), 'steal_ticks': values[7], 'boot_epoch': boot,
            'advertised_cpu_count': len(cpu_names)}


def _pressure(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {'some', 'full'}:
            continue
        values = {}
        for token in parts[1:]:
            key, separator, value = token.partition('=')
            if not separator or key in values:
                raise ValueError('Invalid or duplicate pressure field')
            values[key] = value
        if set(values) != {'avg10', 'avg60', 'avg300', 'total'} or parts[0] in result:
            raise ValueError('Invalid pressure row')
        row = {key: float(values[key]) for key in ('avg10', 'avg60', 'avg300')}
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in row.values()):
            raise ValueError('Invalid pressure percentage')
        row['total_usec'] = _integer(values['total'])
        result[parts[0]] = row
    return result


def _parsed(path: Path, parser, default=None):
    value = _text(path)
    if value is None:
        return default
    try:
        return parser(value)
    except (ValueError, TypeError, OverflowError):
        return default


def _memory(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        parts = line.split()
        key = parts[0].rstrip(':') if parts else None
        if key in MEM_COUNTERS:
            if len(parts) != 3 or parts[2] != 'kB' or key + '_bytes' in result:
                raise ValueError('Invalid memory counter')
            result[key + '_bytes'] = _integer(parts[1]) * 1024
    return result


def _limit(text: str) -> dict:
    value = text.strip()
    return {'state': 'unlimited' if value == 'max' else 'finite',
            'bytes': None if value == 'max' else _integer(value)}


def _directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) else None


def _unified_membership(text: str | None) -> str | None:
    """Parse one live v2 membership without retaining a public identifying path."""
    if text is None:
        return None
    rows = [line for line in text.split('\n') if line.startswith('0:')]
    if len(rows) != 1 or not rows[0].startswith('0::/'):
        return None
    member = rows[0][3:]
    # An out-of-namespace/deleted/ambiguous membership is not a root binding.
    # The explicit --leaf path can still be audited independently of the PID.
    if (member.endswith(' (deleted)') or '..' in member.split('/')
            or any(ord(char) < 32 or ord(char) == 127 for char in member)):
        return None
    return member


def read_sample(*, proc_root: Path = Path('/proc'), cgroup_root: Path = Path('/sys/fs/cgroup'),
                pid: str = 'self', leaf: Path | None = None) -> dict:
    if pid != 'self' and (not isinstance(pid, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', pid)):
        raise ValueError('Invalid process selector')
    began = time.monotonic_ns()
    proc_root, cgroup_root = Path(proc_root), Path(cgroup_root).resolve()
    process = proc_root / pid
    explicit_leaf = leaf is not None
    membership = _unified_membership(_text(process / 'cgroup'))
    member_leaf = cgroup_root / membership.lstrip('/') if membership is not None else None
    if not explicit_leaf:
        leaf = member_leaf
    levels = []
    if leaf is not None:
        leaf = Path(leaf)
        leaf = (leaf if leaf.is_absolute() else cgroup_root / leaf).resolve()
        if leaf != cgroup_root and cgroup_root not in leaf.parents:
            raise ValueError('Selected cgroup is outside the visible root')
        paths = [leaf] + list(leaf.parents)[:len(leaf.parts)-len(cgroup_root.parts)]
        if len(paths) > MAX_LEVELS:
            raise ValueError('Cgroup hierarchy exceeds audit budget')
        for index, path in enumerate(paths):
            start = time.monotonic_ns()
            identity = _directory_identity(path)
            row = {'level': index, 'identity': identity,
                'cpu_max': _parsed(path/'cpu.max', parse_cpu_max, {'state': 'unknown'}),
                'cpu_stat': _parsed(path/'cpu.stat', lambda s: _keyed(s, CPU_COUNTERS), {}),
                'cpus': _parsed(path/'cpuset.cpus.effective', parse_cpu_set),
                'memory_max': _parsed(path/'memory.max', _limit, {'state': 'unknown'}),
                'memory_current_bytes': _parsed(path/'memory.current', lambda s: _integer(s.strip()))}
            installed = _directory_identity(path)
            if identity is None or installed != identity:
                # Never use mixed-directory limits as a capacity bound or attach
                # replacement accounting to the previous directory's identity.
                # Other independently sampled hierarchy levels remain usable.
                row.update(identity=None, cpu_max={'state': 'unknown'}, cpu_stat={},
                           cpus=None, memory_max={'state': 'unknown'}, memory_current_bytes=None)
            row['sampled_ns'] = (start + time.monotonic_ns()) // 2
            levels.append(row)
    status = _text(process/'status')
    affinity = None
    if status is not None:
        matches = [line.split(':',1)[1].strip() for line in status.splitlines()
                   if line.startswith('Cpus_allowed_list:')]
        if len(matches) == 1:
            try:
                affinity = parse_cpu_set(matches[0])
            except ValueError:
                pass
    sample = {'started_ns': began, 'levels': levels, 'affinity': affinity,
              'proc_stat': _parsed(proc_root/'stat', parse_proc_stat, {}),
              'memory': _parsed(proc_root/'meminfo', _memory, {}),
              'vmstat': _parsed(proc_root/'vmstat', lambda s: _keyed(s, VM_COUNTERS), {}),
              'pressure': {key: _parsed(proc_root/'pressure'/key, _pressure)
                           for key in ('cpu', 'memory', 'io')}}
    try:
        sample['page_size_bytes'] = os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError):
        sample['page_size_bytes'] = None
    # Bracket the complete sequential sample, not just individual cgroup files.
    # Stable directory inodes do not prove that the selected PID stayed in them.
    final_membership = _unified_membership(_text(process / 'cgroup'))
    if membership is None or final_membership is None:
        binding_state = 'unknown'
    elif membership != final_membership:
        binding_state = 'changed'
    elif explicit_leaf and member_leaf != leaf:
        binding_state = 'explicit_unbound'
    else:
        binding_state = 'observed_stable'
    if binding_state != 'observed_stable':
        sample['affinity'] = None
        if not explicit_leaf:
            sample['levels'] = []
    sample['target_binding'] = {'state': binding_state,
        'selection': 'explicit_leaf' if explicit_leaf else 'automatic',
        'pid_reuse_checked': False,
        'semantics': 'two_membership_reads_not_atomic_no_PID_reuse_check'}
    sample['ended_ns'] = time.monotonic_ns()
    return sample


def _same_epoch(before, after) -> bool | None:
    # Missing evidence is not proof that a counter reset or a boot changed.
    return None if before is None or after is None else before == after


def _delta(before, after, *, same_epoch=True) -> dict:
    if same_epoch is None:
        return {'state': 'unknown', 'value': None}
    if not same_epoch:
        return {'state': 'epoch_changed', 'value': None}
    if type(before) is not int or type(after) is not int:
        return {'state': 'unknown', 'value': None}
    if after < before:
        return {'state': 'reset', 'value': None}
    return {'state': 'measured', 'value': after - before}


def compare_samples(before: dict, after: dict) -> dict:
    elapsed = ((after['started_ns'] + after['ended_ns']) -
               (before['started_ns'] + before['ended_ns'])) // 2
    if elapsed <= 0:
        raise ValueError('Sampling interval must be positive')
    levels = []
    proc_before, proc_after = before['proc_stat'], after['proc_stat']
    boot_same = _same_epoch(proc_before.get('boot_epoch'), proc_after.get('boot_epoch'))
    old = {row['level']: row for row in before['levels']}
    for row in after['levels']:
        previous = old.get(row['level'], {})
        identity_same = _same_epoch(row['identity'], previous.get('identity'))
        level_elapsed = row['sampled_ns'] - previous.get('sampled_ns', row['sampled_ns'])
        if boot_same is False or identity_same is False:
            same = False
        elif boot_same is None or identity_same is None or level_elapsed <= 0:
            same = None
        else:
            same = True
        delta = {key: _delta(previous.get('cpu_stat', {}).get(key), row['cpu_stat'].get(key), same_epoch=same)
                 for key in sorted(CPU_COUNTERS)}
        levels.append({'level': row['level'], 'cpu_max': row['cpu_max'], 'cpu_stat_delta': delta,
                       'elapsed_ns': level_elapsed if level_elapsed > 0 else None,
                       'cpuset_cpu_count': len(row['cpus']) if row['cpus'] is not None else None,
                       'memory_max': row['memory_max'], 'memory_current_bytes': row['memory_current_bytes']})
    memory_limits = [row['memory_max']['bytes'] for row in after['levels']
                     if row['memory_max']['state'] == 'finite']
    limits = [(row['cpu_max'].get('cpu_equivalents'), row['level']) for row in after['levels']
              if row['cpu_max']['state'] == 'finite']
    ceiling = min((value for value, _ in limits), default=None)
    affinity = after['affinity']
    eligible = None if affinity is None else set(affinity)
    for row in after['levels']:
        if row['cpus'] is not None:
            eligible = set(row['cpus']) if eligible is None else eligible & row['cpus']
    upper_bounds = ([ceiling] if ceiling is not None else []) + ([len(eligible)] if eligible is not None else [])
    cpu_delta = {key: _delta(proc_before.get(key), proc_after.get(key), same_epoch=boot_same)
                 for key in ('total_ticks', 'steal_ticks')}
    total, steal = cpu_delta['total_ticks']['value'], cpu_delta['steal_ticks']['value']
    steal_ratio = steal / total if total and steal is not None and steal <= total else None
    pressure_delta = {}
    for resource in ('cpu', 'memory', 'io'):
        pressure_delta[resource] = {kind: _delta(
            (before['pressure'].get(resource) or {}).get(kind, {}).get('total_usec'),
            (after['pressure'].get(resource) or {}).get(kind, {}).get('total_usec'), same_epoch=boot_same)
            for kind in ('some', 'full')}
    raw_binding = after.get('target_binding')
    raw_binding = raw_binding if isinstance(raw_binding, dict) else {}
    state = raw_binding.get('state')
    selection = raw_binding.get('selection')
    if (selection not in ('automatic', 'explicit_leaf')
            or state == 'explicit_unbound' and selection != 'explicit_leaf'):
        state = 'unknown'
    # The report emits only this bounded vocabulary, never the private path or
    # arbitrary additive input metadata. Old samples have no verified binding.
    target_binding = {
        'state': state if state in ('observed_stable', 'changed', 'unknown', 'explicit_unbound') else 'unknown',
        'selection': selection if selection in ('automatic', 'explicit_leaf') else 'legacy',
        'pid_reuse_checked': False,
        'semantics': 'two_membership_reads_not_atomic_no_PID_reuse_check'
                    if raw_binding else 'legacy_target_binding_unverified'}
    if target_binding.get('state') == 'observed_stable':
        capacity_scope = 'observed_target_membership'
    elif target_binding.get('selection') == 'explicit_leaf':
        capacity_scope = 'explicit_cgroup_not_bound_to_target'
    elif target_binding.get('selection') == 'legacy':
        capacity_scope = 'legacy_target_binding_unverified'
    else:
        capacity_scope = 'target_binding_unavailable'
    return {'schema': 1, 'elapsed_ns': elapsed, 'clock': 'monotonic_ns',
        'target_binding': dict(target_binding),
        'sampling_scope': 'sequential_local_reads_not_atomic_or_cross_host_aligned',
        'capacity': {'scope': capacity_scope, 'visible_cpu_quota_ceiling': ceiling,
            'restricting_quota_levels': [level for value, level in limits if value == ceiling],
            'visible_quota_hierarchy_complete': bool(levels) and all(row['cpu_max']['state'] != 'unknown' for row in levels),
            'visible_cpu_capacity_upper_bound': min(upper_bounds) if upper_bounds else None,
            'affinity_cpu_count': len(affinity) if affinity is not None else None,
            'visible_cpuset_intersection_count': len(eligible) if eligible is not None else None,
            'advertised_cpu_count': proc_after.get('advertised_cpu_count'),
            'visible_memory_ceiling_bytes': min(memory_limits) if memory_limits else None,
            'host_capacity': 'unknown', 'reserved_capacity_verified': False},
        'levels': levels, 'memory': after['memory'], 'pressure': after['pressure'],
        'pressure_total_delta_usec': pressure_delta,
        'proc_cpu_delta': cpu_delta, 'steal_fraction_of_accounting_ticks': steal_ratio,
        'vmstat_delta': {key: _delta(before['vmstat'].get(key), after['vmstat'].get(key), same_epoch=boot_same)
                         for key in sorted(VM_COUNTERS)},
        'page_size_bytes': after['page_size_bytes'],
        'counter_semantics': {'cpu_stat': 'per_level_never_sum_ancestor_and_child_counters',
            'throttled_usec': 'accounting_not_wall_percentage',
            'pressure': 'stalled_task_time_not_CPU_utilization_or_controller_specific_delay',
            'vmstat': 'system_wide_swap_pages_and_major_faults_not_attributed_to_one_process'},
        'infrastructure_changed': False, 'native_acceptance_proven': False}


def allocation_plan(current: dict, *, intended_cpus: float, advertised_vcpus: int,
                    headroom_cpus: float | None = None) -> dict:
    """Review artifact only. Headroom is an operator estimate, not an attestation."""
    if (set(current) != {'quota_us', 'period_us'} or type(current['period_us']) is not int
            or not MIN_BANDWIDTH_US <= current['period_us'] <= MAX_PERIOD_US
            or current['quota_us'] is not None and (type(current['quota_us']) is not int
                or not MIN_BANDWIDTH_US <= current['quota_us'] < 10**20)):
        raise ValueError('Explicit current quota and period are required')
    if (type(advertised_vcpus) is not int or not 1 <= advertised_vcpus <= 65536
            or type(intended_cpus) not in {float, int}
            or not 0 < intended_cpus <= advertised_vcpus or not math.isfinite(intended_cpus)):
        raise ValueError('Invalid intended allocation')
    if headroom_cpus is not None:
        try:
            valid_headroom = (type(headroom_cpus) in {int, float}
                              and math.isfinite(headroom_cpus) and headroom_cpus >= 0)
        except OverflowError:
            valid_headroom = False
        if not valid_headroom:
            raise ValueError('Invalid declared headroom')
    # Fraction(str(...)) preserves the supplied decimal request without a
    # floating-product rounding error adding an extra microsecond of quota.
    requested = Fraction(str(intended_cpus))
    proposed_quota = math.ceil(requested * current['period_us'])
    if proposed_quota < MIN_BANDWIDTH_US:
        raise ValueError('Requested quota is below the supported CFS minimum')
    proposed = {'quota_us': proposed_quota, 'period_us': current['period_us']}
    increase = (Fraction(max(0, proposed_quota - current['quota_us']), current['period_us'])
                if current['quota_us'] is not None else None)
    return {'schema': 1, 'current': dict(current), 'proposed': proposed, 'rollback': dict(current),
            'additional_cpu_equivalents': float(increase) if increase is not None else None,
            'declared_headroom_cpus': headroom_cpus,
            'requested_cpu_equivalents': intended_cpus,
            'proposed_cpu_equivalents': proposed_quota / current['period_us'],
            'quota_rounding': 'ceil_decimal_request_to_whole_microseconds',
            'preconditions_satisfied': (increase is not None and headroom_cpus is not None
                                        and increase <= Fraction(str(headroom_cpus))),
            'preconditions_scope': 'numeric_allocation_and_declared_headroom_only',
            'apply_authorized': False, 'requires': ['infrastructure_owner_review', 'memory_pressure_assessment',
                'affected_workload_ownership', 'active_and_persistent_readback', 'matched_useful_output_comparison']}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proc-root', type=Path, default=Path('/proc'))
    parser.add_argument('--cgroup-root', type=Path, default=Path('/sys/fs/cgroup'))
    parser.add_argument('--leaf', type=Path)
    parser.add_argument('--pid', default='self')
    parser.add_argument('--seconds', type=float, default=2.0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 0.01 <= args.seconds <= 60:
        parser.error('Sampling window must be between 0.01 and 60 seconds')
    try:
        values = dict(proc_root=args.proc_root, cgroup_root=args.cgroup_root, pid=args.pid, leaf=args.leaf)
        before = read_sample(**values)
        time.sleep(args.seconds)
        after = read_sample(**values)
        print(json.dumps(compare_samples(before, after), indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError):
        parser.exit(2, 'Cannot audit the selected bounded, visible resource hierarchy.\n')


if __name__ == '__main__':
    main()
