"""Read-only Linux/cgroup fixtures; no production capacity or remediation claim."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from jev_factorio.capacity_audit import (parse_cpu_max, parse_cpu_set, parse_proc_stat,
                                        read_sample, compare_samples, allocation_plan)


def fixture_tree(tmp_path):
    proc=tmp_path/'proc';cg=tmp_path/'cgroup';leaf=cg/'vm'/'agent'
    (proc/'self').mkdir(parents=True);(proc/'pressure').mkdir()
    leaf.mkdir(parents=True)
    (proc/'self'/'cgroup').write_text('0::/vm/agent\n')
    (proc/'self'/'status').write_text('Name:\tPRIVATE\nCpus_allowed_list:\t0-3\n')
    (proc/'stat').write_text('cpu 100 20 30 40 5 6 7 8 9 10\ncpu0 0\nbtime 123\n')
    (proc/'meminfo').write_text('MemTotal: 8192 kB\nMemAvailable: 1024 kB\nSwapTotal: 4096 kB\nSwapFree: 1024 kB\n')
    (proc/'vmstat').write_text('pswpin 10\npswpout 5\npgmajfault 20\n')
    for resource in ('cpu','memory','io'):
        (proc/'pressure'/resource).write_text('some avg10=2.50 avg60=1.00 avg300=0.50 total=100\n')
    for path,quota in [(cg,'max 100000'),(cg/'vm','200000 100000'),(leaf,'max 100000')]:
        (path/'cpu.max').write_text(quota+'\n')
        (path/'cpuset.cpus.effective').write_text('0-3\n')
        (path/'memory.max').write_text('max\n')
        (path/'memory.current').write_text('2048\n')
        (path/'cpu.stat').write_text('usage_usec 100\nnr_periods 10\nnr_throttled 5\nthrottled_usec 80\n')
    return proc,cg,leaf


def test_unlimited_leaf_does_not_hide_ancestor_cap_or_prove_host_capacity(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    sample=read_sample(proc_root=proc,cgroup_root=cg)
    report=compare_samples(sample,read_sample(proc_root=proc,cgroup_root=cg))
    assert report['capacity']['visible_cpu_quota_ceiling']==2
    assert report['capacity']['restricting_quota_levels']==[1]
    assert report['capacity']['host_capacity']=='unknown'
    assert report['capacity']['affinity_cpu_count']==4
    assert report['levels'][0]['cpu_max']['state']=='unlimited'
    assert '/vm/' not in json.dumps(report) and 'PRIVATE' not in json.dumps(report)
    assert report['infrastructure_changed'] is False


@pytest.mark.parametrize('text', ['0 100000','-1 100000','max 0','3','yes 10','nan 10','1 2 3'])
def test_malformed_cpu_max_is_not_unlimited(text):
    with pytest.raises(ValueError): parse_cpu_max(text)


def test_finite_unlimited_and_cpuset_parsers():
    assert parse_cpu_max('200000 100000')['cpu_equivalents']==2
    assert parse_cpu_max('max 100000')['cpu_equivalents'] is None
    assert parse_cpu_set('0-3,6,8-9')=={0,1,2,3,6,8,9}
    for text in ('4-2','-1','1,,2','999999999','1-999999999'):
        with pytest.raises(ValueError): parse_cpu_set(text)


def test_guest_ticks_are_not_added_twice():
    data=parse_proc_stat('cpu 100 20 30 40 5 6 7 8 9 10\n')
    assert data['total_ticks']==216
    assert data['steal_ticks']==8


def test_counter_resets_and_missing_measurements_remain_unknown(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    before=read_sample(proc_root=proc,cgroup_root=cg)
    (leaf/'cpu.stat').write_text('usage_usec 1\nnr_periods 1\nnr_throttled 0\nthrottled_usec 0\n')
    (proc/'vmstat').write_text('pswpin 1\npswpout 6\n')
    after=read_sample(proc_root=proc,cgroup_root=cg)
    report=compare_samples(before,after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec']['state']=='reset'
    assert report['vmstat_delta']['pswpin']['value'] is None
    assert report['vmstat_delta']['pgmajfault']['state']=='unknown'
    assert 'throttled_wall_percent' not in json.dumps(report)


def test_changed_hierarchy_invalidates_deltas_even_if_counters_increase(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    before=read_sample(proc_root=proc,cgroup_root=cg)
    after=deepcopy(before)
    after['levels'][0]['identity']=(999,999)
    after['ended_ns']+=10
    after['levels'][0]['cpu_stat']['usage_usec']+=10
    report=compare_samples(before,after)
    assert report['levels'][0]['cpu_stat_delta']['usage_usec']['state']=='epoch_changed'


def test_unknown_or_inaccessible_host_is_not_reported_unlimited(tmp_path):
    sample=read_sample(proc_root=tmp_path/'absent',cgroup_root=tmp_path/'absent')
    result=compare_samples(sample,read_sample(proc_root=tmp_path/'absent',cgroup_root=tmp_path/'absent'))
    assert result['capacity']['visible_cpu_quota_ceiling'] is None
    assert result['capacity']['host_capacity']=='unknown'
    assert not result['capacity']['visible_quota_hierarchy_complete']


def test_read_only_collection_preserves_all_files(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    read_sample(proc_root=proc,cgroup_root=cg)
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_proposal_is_parameterized_and_preserves_exact_rollback():
    result=allocation_plan({'quota_us':200000,'period_us':100000},
                           intended_cpus=3,advertised_vcpus=4,headroom_cpus=1.5)
    assert result['proposed']=={'quota_us':300000,'period_us':100000}
    assert result['rollback']=={'quota_us':200000,'period_us':100000}
    assert result['apply_authorized'] is False
    assert result['preconditions_satisfied'] is True
    blocked=allocation_plan({'quota_us':200000,'period_us':100000},
                             intended_cpus=4,advertised_vcpus=4,headroom_cpus=.5)
    assert not blocked['preconditions_satisfied']


def test_missing_quota_is_unknown_even_below_known_finite_limit(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    (leaf/'cpu.max').unlink()
    first=read_sample(proc_root=proc,cgroup_root=cg)
    report=compare_samples(first,read_sample(proc_root=proc,cgroup_root=cg))
    assert report['capacity']['visible_cpu_quota_ceiling']==2
    assert not report['capacity']['visible_quota_hierarchy_complete']
    assert report['levels'][0]['cpu_max']['state']=='unknown'


def test_foreign_leaf_and_symlink_payload_fail_closed(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    with pytest.raises(ValueError): read_sample(proc_root=proc,cgroup_root=cg,leaf=tmp_path)
    outside=tmp_path/'private';outside.write_text('999 100000')
    (leaf/'cpu.max').unlink();(leaf/'cpu.max').symlink_to(outside)
    sample=read_sample(proc_root=proc,cgroup_root=cg)
    assert sample['levels'][0]['cpu_max']['state']=='unknown'


def test_finite_ancestor_memory_and_cpuset_intersection_are_visible_upper_bounds(tmp_path):
    proc,cg,leaf=fixture_tree(tmp_path)
    (cg/'vm'/'cpuset.cpus.effective').write_text('0-1')
    (cg/'vm'/'memory.max').write_text('4096')
    sample=read_sample(proc_root=proc,cgroup_root=cg)
    report=compare_samples(sample,read_sample(proc_root=proc,cgroup_root=cg))
    assert report['capacity']['visible_cpuset_intersection_count']==2
    assert report['capacity']['visible_memory_ceiling_bytes']==4096
