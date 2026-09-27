"""Parent entry publication precedes the authoritative checkpoint write."""
import json
import os
from pathlib import Path
import stat

import pytest

import jev_factorio.checkpoint_io as io
import jev_factorio.iteration_timing as timing
from jev_factorio.memory import CampaignMemory
from jev_factorio.controller import HierarchicalLoop
from test_causal_trace import Backend, Client

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Directory fsync is POSIX-specific')


def directory_id(path):
    value = path.stat()
    return value.st_dev, value.st_ino


def record_sync(monkeypatch):
    calls = []
    original = io.os.fsync
    def sync(fd):
        value = os.fstat(fd)
        calls.append(('directory' if stat.S_ISDIR(value.st_mode) else 'file', value.st_dev, value.st_ino))
        return original(fd)
    monkeypatch.setattr(io.os, 'fsync', sync)
    return calls


@pytest.mark.parametrize('depth', [0, 1, 3])
def test_new_parent_entries_sync_in_order_before_checkpoint(tmp_path, monkeypatch, depth):
    path = tmp_path.joinpath(*['level'+str(i) for i in range(depth)], 'state.json')
    memory = CampaignMemory('fixture', 'rocket_launch')
    calls = record_sync(monkeypatch)
    memory.save(path)
    directories = [tmp_path.joinpath(*['level'+str(i) for i in range(n)]) for n in range(depth)]
    assert calls[:depth] == [('directory', *directory_id(p)) for p in directories]
    assert len(calls) == depth + 2
    assert calls[-2][0] == 'file'
    assert calls[-1] == ('directory', *directory_id(path.parent))
    before = path.read_bytes()
    memory.save(path)
    assert len(calls) == depth + 2 and path.read_bytes() == before
    memory.reason = 'new authoritative state'
    memory.save(path)
    assert len(calls) == depth + 4
    assert json.loads(path.read_bytes())['reason'] == memory.reason


@pytest.mark.parametrize('fail_at', [1, 2, 3])
def test_failed_parent_sync_prevents_temp_checkpoint_and_native_action(tmp_path, monkeypatch, fail_at):
    path = tmp_path/'one'/'two'/'three'/'state.json'
    backend = Backend()
    loop = HierarchicalLoop(backend, Client(), checkpoint=str(path), factory_scheduling='ready-work')
    original = io.os.fsync
    directories = 0
    failure = OSError('injected parent synchronization failure')
    def sync(fd):
        nonlocal directories
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directories += 1
            if directories == fail_at:
                raise failure
        return original(fd)
    monkeypatch.setattr(io.os, 'fsync', sync)
    with pytest.raises(OSError) as error:
        loop.step()
    assert error.value is failure
    assert not path.exists()
    assert not list(tmp_path.rglob('state.json.*'))
    assert not any(c[0] == 'act' for c in backend.calls)
    before = list(backend.calls)
    with pytest.raises(RuntimeError, match='persistence failed'):
        loop.step()
    assert before == backend.calls


def test_parent_work_has_separate_cpu_wall_partition(tmp_path):
    ledger = timing.Ledger()
    token = timing._CURRENT.set(ledger)
    try:
        with timing.span('iteration'):
            CampaignMemory('fixture', 'rocket_launch').save(tmp_path/'one'/'two'/'state.json')
    finally:
        timing._CURRENT.reset(token)
    result = ledger.snapshot(1, 'returned')
    assert result['partition_complete']
    assert result['phases']['checkpoint_parent_sync']['calls'] == 2
    assert result['phases']['checkpoint_directory_sync']['calls'] == 1
    assert result['phases']['checkpoint_installation_check']['calls'] == 2
    for clock in timing.CLOCKS:
        assert sum(row[clock+'_exclusive_ns'] for row in result['phases'].values()) == result['totals_ns'][clock]


def test_concurrent_parent_creation_is_not_silently_adopted(tmp_path, monkeypatch):
    level = tmp_path/'one'
    original_mkdir = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == level and not path.exists():
            original_mkdir(path)
            (path/'other-owner.txt').write_text('preserve')
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    memory = CampaignMemory('fixture', 'rocket_launch')
    with pytest.raises(FileExistsError):
        memory.save(level/'state.json')
    assert sorted(p.name for p in level.iterdir()) == ['other-owner.txt']
    assert (level/'other-owner.txt').read_text() == 'preserve'
    assert memory._checkpoint_cache is None


def test_failed_file_sync_records_attempted_duration(tmp_path, monkeypatch):
    from itertools import count
    clock = count(0, 100)
    monkeypatch.setattr(io.time, 'perf_counter_ns', lambda: next(clock))
    primary = OSError('file sync failure')
    monkeypatch.setattr(io.os, 'fsync', lambda fd: (_ for _ in ()).throw(primary))
    memory = CampaignMemory('fixture', 'rocket_launch')
    with pytest.raises(OSError) as error:
        memory.save(tmp_path/'state.json')
    assert error.value is primary
    assert memory._checkpoint_metrics['file_sync_ns'] > 0
    assert memory._checkpoint_metrics['status'] == 'failed'


def test_directory_close_failure_does_not_mask_sync_failure(tmp_path, monkeypatch):
    original_sync, original_close = io.os.fsync, io.os.close
    primary = OSError('directory sync failure')
    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise primary
        return original_sync(fd)
    def close(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        original_close(fd)
        if directory:
            raise OSError('secondary close failure')
    monkeypatch.setattr(io.os, 'fsync', sync)
    monkeypatch.setattr(io.os, 'close', close)
    memory = CampaignMemory('fixture', 'rocket_launch')
    with pytest.raises(OSError) as error:
        memory.save(tmp_path/'state.json')
    assert error.value is primary
    assert memory._checkpoint_cache is None
