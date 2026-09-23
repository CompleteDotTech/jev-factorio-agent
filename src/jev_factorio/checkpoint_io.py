"""Exact-state checkpoint coalescing; never debounce a changed write-ahead state."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict
from pathlib import Path


def _stamp(path: Path) -> tuple | None:
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def save_checkpoint(memory, path: Path | None) -> None:
    """Cache only successfully synced bytes in this memory object's lifetime.

    One controller owns the path. An external edit/replacement/deletion or a new
    memory instance invalidates coalescing. The cache and timings are not fields
    of the checkpoint schema. Directory sync happens once at this common layer,
    including for composed memory extensions. Any failure drops the cache.
    """
    metrics = {'status': 'disabled', 'bytes': 0, 'serialize_ns': 0,
               'file_sync_ns': 0, 'directory_sync_ns': 0, 'total_ns': 0}
    memory._checkpoint_metrics = metrics
    if path is None:
        memory._checkpoint_cache = None
        return
    start = time.perf_counter_ns()
    temporary = None
    try:
        path = Path(os.path.abspath(path))
        began = time.perf_counter_ns()
        data = asdict(memory)
        if data.get('capital_investment') is None:
            data.pop('capital_investment', None)
        payload = json.dumps(data, sort_keys=True, allow_nan=False).encode('utf-8')
        metrics.update(bytes=len(payload), serialize_ns=time.perf_counter_ns() - began)
        cache = getattr(memory, '_checkpoint_cache', None)
        if (cache is not None and cache[0] == path and cache[1] == payload
                and cache[2] is not None and cache[2] == _stamp(path)):
            metrics['status'] = 'unchanged'
            return
        memory._checkpoint_cache = None
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            began = time.perf_counter_ns()
            os.fsync(stream.fileno())
            metrics['file_sync_ns'] = time.perf_counter_ns() - began
        os.replace(temporary, path)
        temporary = None
        if os.name == 'posix':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                began = time.perf_counter_ns()
                os.fsync(descriptor)
                metrics['directory_sync_ns'] = time.perf_counter_ns() - began
            finally:
                os.close(descriptor)
        memory._checkpoint_cache = (path, payload, _stamp(path))
        metrics['status'] = 'written'
    except BaseException:
        memory._checkpoint_cache = None
        metrics['status'] = 'failed'
        raise
    finally:
        metrics['total_ns'] = time.perf_counter_ns() - start
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
