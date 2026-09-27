"""New directory entries must be synchronized before checkpoint preparation."""
import errno
import json
import os
import stat
from pathlib import Path

import pytest

from jev_factorio import checkpoint_io as checkpoint
from jev_factorio.memory import CampaignMemory

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX directory entry durability')


def _inode(path):
    value = path.stat()
    return value.st_dev, value.st_ino


@pytest.mark.parametrize('depth', [0, 1, 2, 4])
def test_parent_entry_syncs_precede_temporary_checkpoint_creation(tmp_path, monkeypatch, depth):
    parent = tmp_path
    directories = []
    for index in range(depth):
        parent /= str(index)
        directories.append(parent)
    path = parent / 'state.json'
    events = []
    fsync, mkstemp = os.fsync, checkpoint.tempfile.mkstemp
    def syncing(fd):
        value = os.fstat(fd)
        events.append(('directory' if stat.S_ISDIR(value.st_mode) else 'file',
                       (value.st_dev, value.st_ino)))
        fsync(fd)
    def temporary(*a, **k):
        events.append(('prepare', None))
        return mkstemp(*a, **k)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    monkeypatch.setattr(checkpoint.tempfile, 'mkstemp', temporary)
    memory = CampaignMemory('fixture', 'rocket_launch')
    memory.save(path)
    expected = [('directory', _inode(directory.parent)) for directory in directories]
    assert events[:depth] == expected
    assert events[depth][0] == 'prepare'
    assert [event[0] for event in events[depth:]] == ['prepare', 'file', 'directory']
    assert len(events) == depth + 3
    memory.save(path)
    assert len(events) == depth + 3
    memory.reason = 'changed'
    memory.save(path)
    assert len(events) == depth + 6
    assert json.loads(path.read_bytes())['reason'] == 'changed'


@pytest.mark.parametrize('failure_index', [0, 1, 2])
@pytest.mark.parametrize('interruption', [False, True])
def test_failed_parent_provisioning_never_creates_a_checkpoint(tmp_path, monkeypatch, failure_index, interruption):
    path = tmp_path / 'a' / 'b' / 'c' / 'state.json'
    memory = CampaignMemory('fixture', 'rocket_launch')
    calls = 0
    primary = KeyboardInterrupt() if interruption else OSError(errno.ENOSPC, 'injected parent sync')
    fsync = os.fsync
    def syncing(fd):
        nonlocal calls
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            index = calls; calls += 1
            if index == failure_index: raise primary
        return fsync(fd)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    with pytest.raises(BaseException) as caught: memory.save(path)
    assert caught.value is primary
    assert not path.exists()
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'
    assert not list(tmp_path.rglob('state.json.*'))
