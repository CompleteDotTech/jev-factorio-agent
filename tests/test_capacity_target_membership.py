"""Temporary proc/cgroup fixtures, never live migration or infrastructure changes."""
from copy import deepcopy
import json

import pytest

from jev_factorio import capacity_audit as audit
from test_capacity_audit import fixture_tree


BOUNDARIES = ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.current', 'status')
BAD_MEMBERSHIPS = (None, '', '0::relative\n', '0::/vm/agent\n0::/vm/agent\n',
                   '0::/vm/agent (deleted)\n', '0::/../elsewhere\n', '0::/vm/../agent\n',
                   '0::/vm/agent\x00\n')


@pytest.mark.parametrize('boundary', BOUNDARIES)
@pytest.mark.parametrize('change', ['migrate', 'missing', 'malformed', 'duplicate'])
def test_automatic_target_change_invalidates_target_limits_and_affinity(tmp_path, monkeypatch, boundary, change):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    original = audit._text
    changed = False
    target = proc/'self'/'status' if boundary == 'status' else leaf/boundary

    def read(path):
        nonlocal changed
        value = original(path)
        if path == target and not changed:
            changed = True
            membership = proc/'self'/'cgroup'
            if change == 'missing':
                membership.unlink()
            else:
                membership.write_text({'migrate': '0::/another-workload\n',
                                       'malformed': '0::relative\n',
                                       'duplicate': '0::/vm/agent\n0::/vm/agent\n'}[change])
        return value

    monkeypatch.setattr(audit, '_text', read)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert changed
    assert after['levels'] == []
    assert after['affinity'] is None
    report = audit.compare_samples(before, after)
    assert report['capacity']['visible_cpu_quota_ceiling'] is None
    assert report['capacity']['visible_cpu_capacity_upper_bound'] is None
    assert report['capacity']['visible_quota_hierarchy_complete'] is False
    assert report['proc_cpu_delta']['total_ticks']['state'] == 'measured'
    assert report['capacity']['host_capacity'] == 'unknown'
    assert report['infrastructure_changed'] is False
    assert 'another-workload' not in json.dumps(report)
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize('membership', BAD_MEMBERSHIPS)
def test_unavailable_automatic_membership_does_not_mix_in_target_affinity(tmp_path, membership):
    proc, cg, leaf = fixture_tree(tmp_path)
    path = proc/'self'/'cgroup'
    if membership is None:
        path.unlink()
    else:
        path.write_text(membership)
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert sample['levels'] == []
    assert sample['affinity'] is None


@pytest.mark.parametrize('membership', BAD_MEMBERSHIPS + ('0::/other\n',))
def test_explicit_hierarchy_keeps_independent_limits_but_not_unbound_process_affinity(tmp_path, membership):
    proc, cg, leaf = fixture_tree(tmp_path)
    (proc/'self'/'status').write_text('Cpus_allowed_list:\t0\n')
    path = proc/'self'/'cgroup'
    if membership is None:
        path.unlink()
    else:
        path.write_text(membership)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg, leaf=leaf)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg, leaf=leaf)
    assert len(after['levels']) == 3
    assert after['affinity'] is None
    report = audit.compare_samples(before, after)
    assert report['capacity']['visible_cpu_capacity_upper_bound'] == 2
    assert report['capacity']['visible_cpuset_intersection_count'] == 4
    assert report['capacity']['scope'] == 'explicit_cgroup_not_bound_to_target'


@pytest.mark.parametrize('explicit', [False, True])
def test_stable_target_control_keeps_affinity_and_uses_two_bounded_membership_reads(tmp_path, monkeypatch, explicit):
    proc, cg, leaf = fixture_tree(tmp_path)
    (proc/'self'/'status').write_text('Cpus_allowed_list:\t0\n')
    original = audit._text
    reads = []

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(audit, '_text', read)
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg, leaf=leaf if explicit else None)
    assert len(sample['levels']) == 3
    assert sample['affinity'] == {0}
    assert reads.count(proc/'self'/'cgroup') == 2
    assert sample['target_binding']['state'] == 'observed_stable'
    after = deepcopy(sample)
    after['started_ns'] += 1000
    after['ended_ns'] += 1000
    for row in after['levels']:
        row['sampled_ns'] += 1000
    report = audit.compare_samples(sample, after)
    assert report['capacity']['visible_cpu_capacity_upper_bound'] == 1
    assert report['capacity']['scope'] == 'observed_target_membership'


def test_v1_rows_do_not_change_the_single_v2_membership(tmp_path):
    proc, cg, leaf = fixture_tree(tmp_path)
    (proc/'self'/'cgroup').write_text('4:memory:/legacy\n0::/vm/agent\n')
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert len(sample['levels']) == 3
    assert sample['affinity'] == {0, 1, 2, 3}


def test_legacy_comparison_does_not_invent_a_target_binding(tmp_path):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    before.pop('target_binding', None)
    after.pop('target_binding', None)
    report = audit.compare_samples(before, after)
    assert report['capacity']['scope'] == 'legacy_target_binding_unverified'
    assert report['target_binding']['state'] == 'unknown'


def test_explicit_migration_preserves_cgroup_counters_without_mixed_affinity(tmp_path, monkeypatch):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg, leaf=leaf)
    original = audit._text

    def read(path):
        value = original(path)
        if path == proc/'self'/'status':
            (proc/'self'/'cgroup').write_text('0::/other\n')
        return value

    monkeypatch.setattr(audit, '_text', read)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg, leaf=leaf)
    assert len(after['levels']) == 3
    assert after['affinity'] is None
    report = audit.compare_samples(before, after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec']['state'] == 'measured'
    assert report['capacity']['scope'] == 'explicit_cgroup_not_bound_to_target'


@pytest.mark.parametrize('separator', ['\r', '\v', '\f', '\x1c', '\x1d', '\x1e', '\x7f'])
def test_control_characters_cannot_truncate_to_a_valid_membership(tmp_path, separator):
    proc, cg, leaf = fixture_tree(tmp_path)
    (proc/'self'/'cgroup').write_text('0::/vm/agent' + separator + 'private\n')
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert sample['levels'] == []
    assert sample['affinity'] is None


def test_report_does_not_copy_arbitrary_target_binding_metadata(tmp_path):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after['target_binding'] = {'state': '/private/state', 'selection': '/private/selector',
                               'path': '/private/target', 'pid_reuse_checked': True}
    report = audit.compare_samples(before, after)
    assert '/private/' not in json.dumps(report)
    assert report['target_binding']['state'] == 'unknown'
    assert report['target_binding']['pid_reuse_checked'] is False


@pytest.mark.parametrize('binding', [
    {'state': 'observed_stable'},
    {'state': 'observed_stable', 'selection': 'unexpected'},
    {'state': 'explicit_unbound', 'selection': 'automatic'},
])
def test_partial_or_inconsistent_binding_never_claims_a_stable_target(tmp_path, binding):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    after['target_binding'] = binding
    report = audit.compare_samples(before, after)
    assert report['target_binding']['state'] == 'unknown'
    assert report['capacity']['scope'] != 'observed_target_membership'
