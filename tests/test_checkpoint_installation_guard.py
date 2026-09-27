"""Install-bound checkpoint regressions on real Linux files; no game/provider I/O."""
from pathlib import Path
import json
import os
import stat

import pytest

from jev_factorio import checkpoint_io as checkpoint
from jev_factorio.memory import CampaignMemory
from jev_factorio.controller import HierarchicalLoop
from test_causal_trace import Backend, Client

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX synchronization contract')


def _change(path, kind):
    if kind == 'delete':
        path.unlink()
    elif kind == 'replace':
        other = path.with_name('external-replacement')
        other.write_bytes(path.read_bytes())
        os.replace(other, path)
    elif kind == 'symlink':
        other = path.with_name('external-target')
        other.write_text('external evidence')
        path.unlink()
        path.symlink_to(other)
    elif kind == 'truncate':
        path.write_bytes(b'{}')
    elif kind == 'edit_restore_mtime':
        before = path.stat()
        data = path.read_bytes()
        changed = data.replace(b'running', b'blocked')
        assert changed != data and len(changed) == len(data)
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        raise AssertionError(kind)
    return path.read_bytes() if path.exists() else None


@pytest.mark.parametrize('boundary', ['temporary', 'installed', 'directory_sync'])
@pytest.mark.parametrize('kind', ['replace', 'delete', 'symlink', 'truncate'])
def test_changed_installation_is_not_cached_as_captured_bytes(tmp_path, monkeypatch, boundary, kind):
    path = tmp_path / 'state.json'
    memory = CampaignMemory('fixture', 'rocket_launch')
    replace, fsync = os.replace, os.fsync
    seen = []

    def alter_replace(source, target):
        if boundary == 'temporary':
            seen.append(_change(Path(source), kind))
        replace(source, target)
        if boundary == 'installed':
            seen.append(_change(Path(target), kind))

    def alter_sync(fd):
        fsync(fd)
        if boundary == 'directory_sync' and stat.S_ISDIR(os.fstat(fd).st_mode):
            seen.append(_change(path, kind))

    # A replacement inside _change targets the same path: gate re-entry.
    active = False
    def guarded_replace(source, target):
        nonlocal active
        if active:
            return replace(source, target)
        active = True
        try:
            return alter_replace(source, target)
        finally:
            active = False

    monkeypatch.setattr(checkpoint.os, 'replace', guarded_replace)
    monkeypatch.setattr(checkpoint.os, 'fsync', alter_sync)
    with pytest.raises((OSError, RuntimeError)):
        memory.save(path)
    assert seen
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'
    if boundary != 'temporary' and kind != 'delete':
        assert path.read_bytes() == seen[-1]
    if boundary != 'temporary' and kind == 'delete':
        assert not path.exists()


def test_same_inode_same_size_edit_with_restored_mtime_during_sync_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'state.json'
    memory = CampaignMemory('fixture', 'rocket_launch')
    fsync = os.fsync
    def alter(fd):
        fsync(fd)
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            _change(path, 'edit_restore_mtime')
    monkeypatch.setattr(checkpoint.os, 'fsync', alter)
    with pytest.raises((RuntimeError, OSError)):
        memory.save(path)
    assert memory._checkpoint_cache is None
    assert json.loads(path.read_bytes())['status'] == 'blocked'


@pytest.mark.parametrize('dispatch', ['prepared', 'returned'])
@pytest.mark.parametrize('boundary', ['installed', 'directory_sync'])
def test_installation_conflict_stops_controller_before_another_mutation(tmp_path, monkeypatch, dispatch, boundary):
    path = tmp_path / 'state.json'
    backend = Backend()
    loop = HierarchicalLoop(backend, Client(), checkpoint=str(path), factory_scheduling='ready-work')
    replace, fsync = os.replace, os.fsync
    fired = False
    foreign = None
    def change_if_due():
        nonlocal fired, foreign
        if fired or not path.exists():
            return
        data = json.loads(path.read_bytes())
        if (data.get('pending') or {}).get('dispatch') != dispatch:
            return
        fired = True
        data['reason'] = 'external checkpoint evidence'
        foreign = json.dumps(data).encode()
        other = path.with_name('foreign.json'); other.write_bytes(foreign)
        replace(other, path)
    def replacing(source, target):
        replace(source, target)
        if boundary == 'installed': change_if_due()
    def syncing(fd):
        fsync(fd)
        if boundary == 'directory_sync' and stat.S_ISDIR(os.fstat(fd).st_mode): change_if_due()
    monkeypatch.setattr(checkpoint.os, 'replace', replacing)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    with pytest.raises((RuntimeError, OSError)): loop.step()
    assert fired
    assert path.read_bytes() == foreign
    calls = list(backend.calls)
    assert sum(c[0] == 'act' for c in calls) == (dispatch == 'returned')
    assert loop.memory._checkpoint_cache is None
    with pytest.raises((RuntimeError, OSError)): loop.step()
    assert backend.calls == calls and path.read_bytes() == foreign


@pytest.mark.parametrize('boundary', ['file_sync', 'replace', 'directory_sync'])
def test_cleanup_does_not_replace_primary_storage_error(tmp_path, monkeypatch, boundary):
    import errno
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    primary = OSError(errno.ENOSPC, 'injected primary')
    fsync, replace = os.fsync, os.replace
    def syncing(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory == (boundary == 'directory_sync') and boundary != 'replace':
            raise primary
        return fsync(fd)
    def replacing(source, target):
        if boundary == 'replace': raise primary
        return replace(source, target)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    monkeypatch.setattr(checkpoint.os, 'replace', replacing)
    monkeypatch.setattr(checkpoint.os, 'unlink', lambda *a, **k: (_ for _ in ()).throw(PermissionError('cleanup')))
    with pytest.raises(OSError) as caught: memory.save(path)
    assert caught.value is primary
    assert memory._checkpoint_cache is None


@pytest.mark.parametrize('project', ['coal', 'downstream'])
@pytest.mark.parametrize('dispatch', ['prepared', 'returned'])
@pytest.mark.parametrize('boundary', ['installed', 'directory_sync'])
@pytest.mark.parametrize('kind', ['replace', 'delete', 'symlink'])
def test_mixed_paid_action_retains_state_and_stops_on_installation_conflict(
        tmp_path, monkeypatch, project, dispatch, boundary, kind):
    from copy import deepcopy
    from jev_factorio import coal_supply
    from test_coal_mixed_transport import Backend as MixedBackend, controller, ROUTE
    backend = MixedBackend(); loop = controller(backend, tmp_path)
    path = backend.checkpoint
    compile_original = loop._compile_candidates
    def selected(snapshot):
        plans, reason = compile_original(snapshot)
        plans = [plan for plan in plans if
                 (plan.steps[0].action == coal_supply.COMMAND if project == 'coal' else
                  plan.steps[0].parameters.get('route') == ROUTE)]
        assert plans, 'The unchanged executable frontier must offer the selected paid project'
        return plans, reason
    monkeypatch.setattr(loop, '_compile_candidates', selected)
    replace, fsync = os.replace, os.fsync
    fired = False
    retained = None
    def change_if_due():
        nonlocal fired, retained
        if fired or not path.exists(): return
        data = json.loads(path.read_bytes())
        if (data.get('pending') or {}).get('dispatch') != dispatch: return
        fired = True
        retained = _change(path, kind)
    active = False
    def replacing(source, target):
        nonlocal active
        replace(source, target)
        if boundary == 'installed' and not active:
            active = True
            try: change_if_due()
            finally: active = False
    def syncing(fd):
        fsync(fd)
        if boundary == 'directory_sync' and stat.S_ISDIR(os.fstat(fd).st_mode): change_if_due()
    monkeypatch.setattr(checkpoint.os, 'replace', replacing)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    with pytest.raises((RuntimeError, OSError)): loop.step()
    assert fired
    calls = deepcopy(backend.calls)
    assert len(calls) == (dispatch == 'returned')
    native = deepcopy(backend.state)
    commitments = (deepcopy(loop.memory.solid_commitments), deepcopy(loop.memory.coal_commitments))
    with pytest.raises((RuntimeError, OSError)): loop.step()
    assert calls == backend.calls and backend.state == native
    assert commitments == (loop.memory.solid_commitments, loop.memory.coal_commitments)
    assert loop.memory._checkpoint_cache is None
    assert (path.read_bytes() if path.exists() else None) == retained


@pytest.mark.parametrize('interruption', [False, True])
def test_directory_close_failure_cannot_mask_primary_sync_failure(tmp_path, monkeypatch, interruption):
    import errno
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    primary = KeyboardInterrupt() if interruption else OSError(errno.ENOSPC, 'primary')
    fsync, close = os.fsync, os.close
    def syncing(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise primary
        fsync(fd)
    def closing(fd):
        close(fd)
        raise PermissionError('secondary directory close')
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    monkeypatch.setattr(checkpoint.os, 'close', closing)
    with pytest.raises(BaseException) as caught: memory.save(path)
    assert caught.value is primary
    assert memory._checkpoint_cache is None


def test_substituted_temporary_file_is_preserved_after_write_failure(tmp_path, monkeypatch):
    import errno
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    original = OSError(errno.ENOSPC, 'primary')
    foreign = b'owner evidence from another writer'
    fsync = os.fsync
    replacement = []
    def syncing(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            temporary, = list(tmp_path.glob('state.json.*'))
            other = tmp_path / 'external'; other.write_bytes(foreign)
            os.replace(other, temporary); replacement.append(temporary)
            raise original
        fsync(fd)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    with pytest.raises(OSError) as caught: memory.save(path)
    assert caught.value is original
    assert replacement[0].read_bytes() == foreign
    assert memory._checkpoint_cache is None


@pytest.mark.parametrize('boundary', ['file_sync', 'replace', 'directory_sync'])
def test_elapsed_clock_failure_cannot_mask_primary_storage_failure(tmp_path, monkeypatch, boundary):
    import errno
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    primary = OSError(errno.ENOSPC, 'authoritative storage error')
    clock, fsync, replace = checkpoint.time.perf_counter_ns, os.fsync, os.replace
    failed = False
    def timing():
        if failed: raise ValueError('secondary timing failure')
        return clock()
    def syncing(fd):
        nonlocal failed
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if boundary != 'replace' and directory == (boundary == 'directory_sync'):
            failed = True
            raise primary
        fsync(fd)
    def replacing(source, target):
        nonlocal failed
        if boundary == 'replace':
            failed = True
            raise primary
        replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.time, 'perf_counter_ns', timing)
        patch.setattr(checkpoint.os, 'fsync', syncing)
        patch.setattr(checkpoint.os, 'replace', replacing)
        with pytest.raises(BaseException) as caught: memory.save(path)
    assert caught.value is primary
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'


@pytest.mark.parametrize('new_parent', [False, True])
def test_non_posix_keeps_close_before_replace_and_file_sync(tmp_path, monkeypatch, new_parent):
    # Exercise the compatibility branch, not native Windows filesystem semantics.
    real_os = os
    fd_seen = []
    events = []
    mkstemp = checkpoint.tempfile.mkstemp
    def temporary(*a, **k):
        fd, name = mkstemp(*a, **k); fd_seen.append(fd)
        return fd, name
    def replacing(source, target):
        with pytest.raises(OSError): real_os.fstat(fd_seen[-1])
        events.append('replace')
        real_os.replace(source, target)
    def syncing(fd):
        assert stat.S_ISREG(real_os.fstat(fd).st_mode)
        events.append('file_sync'); real_os.fsync(fd)
    class OtherOS:
        name = 'nt'
        replace = staticmethod(replacing)
        fsync = staticmethod(syncing)
        def __getattr__(self, key): return getattr(real_os, key)
    monkeypatch.setattr(checkpoint, 'os', OtherOS())
    monkeypatch.setattr(checkpoint.tempfile, 'mkstemp', temporary)
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = (tmp_path / 'new' if new_parent else tmp_path) / 'state.json'
    memory.save(path)
    memory.save(path)
    assert memory._checkpoint_metrics['status'] == 'unchanged'
    assert events == ['file_sync', 'replace']
