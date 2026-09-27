"""Primary storage failures survive diagnostic/descriptor cleanup failures."""
import errno
import os
import stat
from types import SimpleNamespace

import pytest

from jev_factorio import checkpoint_io as checkpoint
from jev_factorio.memory import CampaignMemory

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX local-file fault boundaries')


@pytest.mark.parametrize('boundary', ['file_sync', 'replace', 'verification', 'directory_sync'])
@pytest.mark.parametrize('primary_interrupt', [False, True])
@pytest.mark.parametrize('close_interrupt', [False, True])
def test_stream_close_never_masks_primary_storage_failure(
        tmp_path, monkeypatch, boundary, primary_interrupt, close_interrupt):
    memory = CampaignMemory('fixture', 'rocket_launch')
    primary = KeyboardInterrupt('primary') if primary_interrupt else OSError(errno.ENOSPC, 'primary')
    secondary = KeyboardInterrupt('close') if close_interrupt else OSError(errno.EIO, 'close')
    real_open, real_sync, real_replace = os.fdopen, os.fsync, os.replace
    streams = []
    raised = []

    def fail():
        raised.append(primary)
        raise primary

    class ClosingFailure:
        def __init__(self, stream): self.stream = stream
        def __getattr__(self, name): return getattr(self.stream, name)
        def __enter__(self): return self
        def __exit__(self, *_): self.close()
        def close(self):
            self.stream.close()
            raise secondary
        def read(self, *args):
            if boundary == 'verification': fail()
            return self.stream.read(*args)

    def opened(*args, **kwargs):
        stream = ClosingFailure(real_open(*args, **kwargs))
        streams.append(stream)
        return stream

    def syncing(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if boundary == ('directory_sync' if directory else 'file_sync'): fail()
        return real_sync(fd)

    def replacing(*args):
        if boundary == 'replace': fail()
        return real_replace(*args)

    monkeypatch.setattr(checkpoint.os, 'fdopen', opened)
    monkeypatch.setattr(checkpoint.os, 'fsync', syncing)
    monkeypatch.setattr(checkpoint.os, 'replace', replacing)
    with pytest.raises(BaseException) as caught:
        memory.save(tmp_path / 'state.json')
    assert raised == [primary]
    assert caught.value is primary
    assert streams and all(stream.closed for stream in streams)
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'


@pytest.mark.parametrize('primary_interrupt', [False, True])
@pytest.mark.parametrize('close_interrupt', [False, True])
def test_failed_fdopen_closes_owned_descriptor_without_masking_error(
        tmp_path, monkeypatch, primary_interrupt, close_interrupt):
    memory = CampaignMemory('fixture', 'rocket_launch')
    primary = KeyboardInterrupt('open') if primary_interrupt else OSError(errno.EMFILE, 'open')
    secondary = KeyboardInterrupt('close') if close_interrupt else OSError(errno.EIO, 'close')
    real_close, real_fstat = os.close, os.fstat
    descriptors, closed = [], []

    def opening(fd, *_):
        descriptors.append(fd)
        raise primary

    def closing(fd):
        closed.append(fd)
        real_close(fd)
        raise secondary

    monkeypatch.setattr(checkpoint.os, 'fdopen', opening)
    monkeypatch.setattr(checkpoint.os, 'close', closing)
    try:
        with pytest.raises(BaseException) as caught:
            memory.save(tmp_path / 'state.json')
        assert caught.value is primary
        assert closed == descriptors and len(closed) == 1
        with pytest.raises(OSError): real_fstat(descriptors[0])
        assert memory._checkpoint_cache is None
        assert memory._checkpoint_metrics['status'] == 'failed'
    finally:
        for fd in descriptors:
            try: real_close(fd)
            except OSError: pass


@pytest.mark.parametrize('primary_interrupt', [False, True])
@pytest.mark.parametrize('cleanup_interrupt', [False, True])
def test_temp_cleanup_never_replaces_primary_interruption(
        tmp_path, monkeypatch, primary_interrupt, cleanup_interrupt):
    memory = CampaignMemory('fixture', 'rocket_launch')
    primary = KeyboardInterrupt('primary') if primary_interrupt else OSError(errno.EDQUOT, 'primary')
    secondary = KeyboardInterrupt('cleanup') if cleanup_interrupt else OSError(errno.EIO, 'cleanup')
    def failed_sync(_): raise primary
    def failed_unlink(*_, **__): raise secondary
    monkeypatch.setattr(checkpoint.os, 'fsync', failed_sync)
    monkeypatch.setattr(checkpoint.os, 'unlink', failed_unlink)
    with pytest.raises(BaseException) as caught:
        memory.save(tmp_path / 'state.json')
    assert caught.value is primary
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'


@pytest.mark.parametrize('boundary', ['first', 'final'])
def test_lone_timing_failure_invalidates_cache_and_status(tmp_path, monkeypatch, boundary):
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    memory.save(path)
    before = path.read_bytes()
    memory.reason = 'new authoritative state'
    original_clock, original_sync = checkpoint.time.perf_counter_ns, checkpoint._directory_sync
    failure = ValueError('injected clock failure')
    fail_now = boundary == 'first'
    def syncing(*args, **kwargs):
        nonlocal fail_now
        original_sync(*args, **kwargs)
        fail_now = True
    def clock():
        if fail_now: raise failure
        return original_clock()
    monkeypatch.setattr(checkpoint, 'time', SimpleNamespace(perf_counter_ns=clock))
    monkeypatch.setattr(checkpoint, '_directory_sync', syncing)
    with pytest.raises(ValueError) as caught:
        memory.save(path)
    assert caught.value is failure
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'
    if boundary == 'first': assert path.read_bytes() == before


@pytest.mark.parametrize('reused', [False, True])
def test_fdopen_failure_does_not_close_a_lost_descriptor_identity(tmp_path, monkeypatch, reused):
    memory = CampaignMemory('fixture', 'rocket_launch')
    foreign = tmp_path / 'other-owner'
    foreign.write_bytes(b'other descriptor owner')
    foreign_identity = foreign.stat()
    primary = OSError(errno.EMFILE, 'fdopen failed after releasing ownership')
    real_close, real_open, real_fstat, real_dup2 = os.close, os.open, os.fstat, os.dup2
    descriptors, close_attempts = [], []

    def opening(fd, *_):
        descriptors.append(fd)
        real_close(fd)
        if reused:
            other = real_open(foreign, os.O_RDONLY)
            if other != fd:
                real_dup2(other, fd)
                real_close(other)
        raise primary

    def closing(fd):
        close_attempts.append(fd)
        real_close(fd)

    monkeypatch.setattr(checkpoint.os, 'fdopen', opening)
    monkeypatch.setattr(checkpoint.os, 'close', closing)
    try:
        with pytest.raises(OSError) as caught:
            memory.save(tmp_path / 'state.json')
        assert caught.value is primary
        assert close_attempts == []
        if reused:
            actual = real_fstat(descriptors[0])
            assert (actual.st_dev, actual.st_ino) == (foreign_identity.st_dev, foreign_identity.st_ino)
            assert foreign.read_bytes() == b'other descriptor owner'
        else:
            with pytest.raises(OSError): real_fstat(descriptors[0])
        assert memory._checkpoint_cache is None
        assert memory._checkpoint_metrics['status'] == 'failed'
    finally:
        if reused and descriptors:
            try: real_close(descriptors[0])
            except OSError: pass


@pytest.mark.parametrize('substitute', [False, True])
def test_fdopen_failure_cleans_only_original_temporary_entry(tmp_path, monkeypatch, substitute):
    primary = OSError(errno.EMFILE, 'wrapper failure')
    memory = CampaignMemory('fixture', 'rocket_launch')
    path = tmp_path / 'state.json'
    retained = []

    def opening(fd, *_):
        temporary = next(tmp_path.glob('state.json.*'))
        retained.append(temporary)
        if substitute:
            other = tmp_path / 'other'
            other.write_bytes(b'other owner evidence')
            os.replace(other, temporary)
        raise primary

    monkeypatch.setattr(checkpoint.os, 'fdopen', opening)
    with pytest.raises(OSError) as caught:
        memory.save(path)
    assert caught.value is primary
    assert not path.exists()
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'
    assert len(retained) == 1
    if substitute:
        assert retained[0].read_bytes() == b'other owner evidence'
    else:
        assert not retained[0].exists()


@pytest.mark.parametrize('interrupt', [False, True])
@pytest.mark.parametrize('close_fails', [False, True])
def test_initial_identity_failure_closes_raw_descriptor_and_preserves_unknown_entry(
        tmp_path, monkeypatch, interrupt, close_fails):
    primary = KeyboardInterrupt('identity') if interrupt else OSError(errno.EIO, 'identity')
    real_create, real_stat, real_close = checkpoint.tempfile.mkstemp, os.fstat, os.close
    created, closed = [], []

    def create(*args, **kwargs):
        result = real_create(*args, **kwargs)
        created.append(result)
        return result

    def inspect(fd):
        if created and fd == created[0][0]:
            raise primary
        return real_stat(fd)

    def close(fd):
        closed.append(fd)
        real_close(fd)
        if close_fails:
            raise OSError(errno.EIO, 'secondary close')

    monkeypatch.setattr(checkpoint.tempfile, 'mkstemp', create)
    monkeypatch.setattr(checkpoint.os, 'fstat', inspect)
    monkeypatch.setattr(checkpoint.os, 'close', close)
    memory = CampaignMemory('fixture', 'rocket_launch')
    with pytest.raises(BaseException) as caught:
        memory.save(tmp_path / 'state.json')
    assert caught.value is primary
    assert closed == [created[0][0]]
    with pytest.raises(OSError):
        real_stat(created[0][0])
    assert not (tmp_path / 'state.json').exists()
    assert len(list(tmp_path.glob('state.json.*'))) == 1
    assert memory._checkpoint_cache is None
    assert memory._checkpoint_metrics['status'] == 'failed'
