"""Malformed-input and numeric/epoch gates for the read-only #97 audit.

Temporary proc/cgroup trees are fixtures, not evidence from the controller host.
"""
from copy import deepcopy
from fractions import Fraction
import json
import math

import pytest

from jev_factorio import capacity_audit as audit
from test_capacity_audit import fixture_tree


@pytest.mark.parametrize('field', sorted(audit.MEM_COUNTERS))
@pytest.mark.parametrize('second', [1, 999999])
def test_duplicate_memory_fields_rejected(field, second):
    with pytest.raises(ValueError):
        audit._memory(f'{field}: 1 kB\n{field}: {second} kB\n')


@pytest.mark.parametrize('field', ['avg10', 'avg60', 'avg300', 'total'])
@pytest.mark.parametrize('kind', ['some', 'full'])
def test_duplicate_pressure_tokens_rejected(field, kind):
    with pytest.raises(ValueError):
        audit._pressure(f'{kind} avg10=90 avg60=50 avg300=20 total=100 {field}=0\n')


@pytest.mark.parametrize('resource', ['cpu', 'memory', 'io'])
def test_invalid_pressure_remains_unknown_through_public_report(tmp_path, resource):
    proc, cg, _ = fixture_tree(tmp_path)
    (proc / 'pressure' / resource).write_text(
        'some avg10=99 avg60=1 avg300=0 total=10 avg10=0\n')
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    report = audit.compare_samples(before, after)
    assert report['pressure'][resource] is None
    assert report['pressure_total_delta_usec'][resource]['some'] == {
        'state': 'unknown', 'value': None}
    assert report['native_acceptance_proven'] is False


def test_invalid_memory_is_not_published_as_available_capacity(tmp_path):
    proc, cg, _ = fixture_tree(tmp_path)
    (proc / 'meminfo').write_text('MemAvailable: 1 kB\nMemAvailable: 999999 kB\n')
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert audit.compare_samples(before, after)['memory'] == {}


@pytest.mark.parametrize('text', [
    'cpu 1 2 3 4 5 6 7 8\ncpu 8 7 6 5 4 3 2 1\n',
    'cpu 1 2 3 4 5 6 7 8\ncpu0 0\ncpu0 0\n',
])
def test_duplicate_cpu_accounting_rows_rejected(text):
    with pytest.raises(ValueError):
        audit.parse_proc_stat(text)


@pytest.mark.parametrize('text', ['max 999', 'max 1000001', '999 100000'])
def test_kernel_ineligible_cpu_bandwidth_rejected(text):
    with pytest.raises(ValueError):
        audit.parse_cpu_max(text)


@pytest.mark.parametrize('period', [1000, 100000, 1000000])
def test_minimum_finite_bandwidth_boundary_is_accepted(period):
    assert audit.parse_cpu_max(f'1000 {period}')['quota_us'] == 1000
    assert audit.parse_cpu_max(f'max {period}')['state'] == 'unlimited'


@pytest.mark.parametrize('current', [
    {'quota_us': 999, 'period_us': 100000},
    {'quota_us': 200000, 'period_us': 999},
    {'quota_us': 200000, 'period_us': 1000001},
])
def test_unreplayable_current_allocation_cannot_be_a_rollback(current):
    with pytest.raises(ValueError):
        audit.allocation_plan(current, intended_cpus=2, advertised_vcpus=4, headroom_cpus=0)


@pytest.mark.parametrize('intended', [0.000001, 0.001, 0.00999])
def test_subminimum_proposed_quota_is_not_silently_accepted_or_rounded_up(intended):
    with pytest.raises(ValueError):
        audit.allocation_plan({'quota_us': 1000, 'period_us': 100000},
                              intended_cpus=intended, advertised_vcpus=4, headroom_cpus=10)


@pytest.mark.parametrize('headroom,allowed', [(0, False), (0.000002, False),
                                            (0.000009999999999, False), (0.00001, True)])
def test_headroom_uses_the_actual_integer_quota(headroom, allowed):
    current = {'quota_us': 200000, 'period_us': 100000}
    original = deepcopy(current)
    proposal = audit.allocation_plan(current, intended_cpus=2.000001,
                                     advertised_vcpus=4, headroom_cpus=headroom)
    assert proposal['proposed'] == {'quota_us': 200001, 'period_us': 100000}
    assert proposal['additional_cpu_equivalents'] == 0.00001
    assert proposal['preconditions_satisfied'] is allowed
    assert proposal['rollback'] == original == current
    assert proposal['apply_authorized'] is False


@pytest.mark.parametrize('intended,period', [(0.14, 100000), (0.28, 100000),
                                           (2.000001, 100000), (1.000000000001, 1000)])
def test_request_quantization_uses_decimal_value_not_float_multiplication(intended, period):
    expected = math.ceil(Fraction(str(intended)) * period)
    proposal = audit.allocation_plan({'quota_us': 1000, 'period_us': period},
                                    intended_cpus=intended, advertised_vcpus=4, headroom_cpus=4)
    assert proposal['proposed']['quota_us'] == expected


@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -1, 10 ** 400])
def test_invalid_numeric_requests_have_a_bounded_value_error(value):
    with pytest.raises(ValueError):
        audit.allocation_plan({'quota_us': 200000, 'period_us': 100000},
                              intended_cpus=value, advertised_vcpus=4, headroom_cpus=0)


@pytest.mark.parametrize('boot,state', [(124, 'epoch_changed'), (None, 'unknown')])
def test_cgroup_counter_deltas_need_the_same_known_boot(tmp_path, boot, state):
    proc, cg, _ = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after['proc_stat']['boot_epoch'] = boot
    # Inode numbers alone can be reused across boots; increasing values do not certify continuity.
    after['levels'][0]['cpu_stat']['usage_usec'] += 100
    report = audit.compare_samples(before, after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec'] == {'state': state, 'value': None}
    assert report['proc_cpu_delta']['steal_ticks']['state'] == state


@pytest.mark.parametrize('offset', [0, -1])
def test_cgroup_counter_without_positive_local_window_is_not_measured(tmp_path, offset):
    proc, cg, _ = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after['levels'][0]['sampled_ns'] = before['levels'][0]['sampled_ns'] + offset
    after['levels'][0]['cpu_stat']['usage_usec'] += 100
    report = audit.compare_samples(before, after)
    assert report['levels'][0]['elapsed_ns'] is None
    assert report['levels'][0]['cpu_stat_delta']['usage_usec'] == {'state': 'unknown', 'value': None}


def test_valid_counters_are_preserved_and_private_identity_not_emitted(tmp_path):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    (leaf / 'cpu.stat').write_text('usage_usec 150\nnr_periods 11\nnr_throttled 6\nthrottled_usec 90\n')
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    report = audit.compare_samples(before, after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec'] == {'state': 'measured', 'value': 50}
    payload = json.dumps(report, allow_nan=False)
    for forbidden in [str(tmp_path), 'PRIVATE', 'boot_epoch', 'identity']:
        assert forbidden not in payload
    assert report['capacity']['host_capacity'] == 'unknown'
    assert report['infrastructure_changed'] is False
