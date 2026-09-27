"""Intra-sample cgroup replacement checks using private filesystem fixtures."""
import json
from pathlib import Path

import pytest

from jev_factorio import capacity_audit as audit
from test_capacity_audit import fixture_tree


FILES = ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.current')


def assert_unavailable_level(sample, before, cg):
    row = sample['levels'][0]
    assert row['identity'] is None
    assert row['cpu_max'] == {'state': 'unknown'}
    assert row['cpu_stat'] == {}
    assert row['cpus'] is None
    assert row['memory_max'] == {'state': 'unknown'}
    assert row['memory_current_bytes'] is None
    ancestor = sample['levels'][1]
    for field in ('identity', 'cpu_max', 'cpu_stat', 'cpus', 'memory_max', 'memory_current_bytes'):
        assert ancestor[field] == before['levels'][1][field]
    report = audit.compare_samples(before, sample)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec'] == {
        'state': 'unknown', 'value': None}
    assert report['capacity']['visible_cpu_quota_ceiling'] == 2
    assert report['capacity']['restricting_quota_levels'] == [1]
    assert report['capacity']['visible_quota_hierarchy_complete'] is False
    assert report['capacity']['visible_cpuset_intersection_count'] == 4
    assert report['capacity']['visible_memory_ceiling_bytes'] is None
    assert report['capacity']['host_capacity'] == 'unknown'
    assert report['infrastructure_changed'] is False
    assert str(cg) not in json.dumps(report)


@pytest.mark.parametrize('boundary', FILES)
@pytest.mark.parametrize('when', ['before', 'after'])
@pytest.mark.parametrize('change', ['replace', 'remove', 'symlink'])
def test_changed_directory_discards_whole_level_without_discarding_valid_ancestors(
        tmp_path, monkeypatch, boundary, when, change):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    original = audit._text
    changed = False

    def replace_directory():
        nonlocal changed
        changed = True
        retained = leaf.with_name('retained-agent')
        leaf.rename(retained)
        if change == 'symlink':
            leaf.symlink_to(retained, target_is_directory=True)
        elif change == 'replace':
            leaf.mkdir()
            replacement = {'cpu.max': '100000 100000', 'cpu.stat': 'usage_usec 900',
                           'cpuset.cpus.effective': '0', 'memory.max': '4096',
                           'memory.current': '3072'}
            for name, text in replacement.items():
                (leaf / name).write_text(text)

    def read(path):
        matching = path == leaf / boundary and not changed
        if matching and when == 'before':
            replace_directory()
        value = original(path)
        if matching and when == 'after':
            replace_directory()
        return value

    monkeypatch.setattr(audit, '_text', read)
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert changed
    assert_unavailable_level(sample, before, cg)


@pytest.mark.parametrize('failed_stat', [1, 2])
def test_missing_directory_metadata_never_validates_values(tmp_path, monkeypatch, failed_stat):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    original = Path.stat
    calls = 0

    def inspect(path, *args, **kwargs):
        nonlocal calls
        # Resolve may stat the selected path before the sampled boundary.
        if path == leaf and kwargs.get('follow_symlinks') is False:
            calls += 1
            if calls == failed_stat:
                raise PermissionError('Synthetic unavailable directory metadata')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', inspect)
    sample = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert_unavailable_level(sample, before, cg)


def test_unchanged_directory_retains_real_accounting_and_limits(tmp_path):
    proc, cg, leaf = fixture_tree(tmp_path)
    before = audit.read_sample(proc_root=proc, cgroup_root=cg)
    (leaf / 'cpu.stat').write_text('usage_usec 150\n')
    (leaf / 'cpu.max').write_text('100000 100000\n')
    (leaf / 'memory.max').write_text('4096\n')
    after = audit.read_sample(proc_root=proc, cgroup_root=cg)
    assert after['levels'][0]['identity'] == before['levels'][0]['identity']
    report = audit.compare_samples(before, after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec'] == {'state': 'measured', 'value': 50}
    assert report['capacity']['visible_cpu_quota_ceiling'] == 1
    assert report['capacity']['visible_memory_ceiling_bytes'] == 4096
    assert report['capacity']['visible_quota_hierarchy_complete'] is True
