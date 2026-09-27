"""Exact-state checkpoint coalescing; never debounce a changed write-ahead state."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import asdict, fields
from pathlib import Path


def _stamp(path: Path) -> tuple | None:
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _same_value(live, captured) -> bool:
    """Exact built-in JSON shape only; unsupported values take the slow path.

    Python equality alone merges bool/int/float and positive/negative zero.
    None of those may suppress a changed persisted representation. Captured
    containers come from the last successful asdict, never from live memory.
    """
    kind = type(live)
    if kind is not type(captured):
        return False
    if kind is dict:
        return (len(live) == len(captured)
                and all(type(key) is str and key in captured
                        and _same_value(value, captured[key]) for key, value in live.items()))
    if kind in {list, tuple}:
        return len(live) == len(captured) and all(_same_value(a, b) for a, b in zip(live, captured))
    if kind is float:
        return math.isfinite(live) and live.hex() == captured.hex()
    return kind in {str, int, bool, type(None)} and live == captured


def _same_memory(memory, captured: dict) -> bool:
    names = [field.name for field in fields(memory)
             if field.name != 'capital_investment' or memory.capital_investment is not None]
    return (len(names) == len(captured)
            and all(name in captured and _same_value(getattr(memory, name), captured[name])
                    for name in names))


def save_checkpoint(memory, path: Path | None) -> None:
    """Cache only successfully synced bytes in this memory object's lifetime.

    One controller owns the path. An external edit/replacement/deletion or a new
    memory instance invalidates coalescing. Exact typed structural comparison
    against the last detached capture avoids copying/serializing unchanged
    state. Every changed authoritative field still uses the durable writer.
    The cache and timings are not fields
    of the checkpoint schema. Directory sync happens once at this common layer,
    including for composed memory extensions. Any failure drops the cache.
    """
    metrics = {'status': 'disabled', 'bytes': 0, 'serialize_ns': 0,
               'file_sync_ns': 0, 'directory_sync_ns': 0, 'total_ns': 0,
               'compare_ns': 0, 'capture_calls': 0, 'serialization_calls': 0}
    memory._checkpoint_metrics = metrics
    if path is None:
        memory._checkpoint_cache = None
        return
    start = time.perf_counter_ns()
    temporary = None
    try:
        path = Path(os.path.abspath(path))
        cache = getattr(memory, '_checkpoint_cache', None)
        if (cache is not None and len(cache) == 4 and cache[0] == path
                and cache[2] is not None):
            began = time.perf_counter_ns()
            identical = _same_memory(memory, cache[3])
            metrics['compare_ns'] = time.perf_counter_ns() - began
            if identical and cache[2] == _stamp(path):
                metrics.update(status='unchanged', bytes=len(cache[1]))
                return
        began = time.perf_counter_ns()
        metrics['capture_calls'] = 1
        data = asdict(memory)
        if data.get('capital_investment') is None:
            data.pop('capital_investment', None)
        metrics['serialization_calls'] = 1
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
        memory._checkpoint_cache = (path, payload, _stamp(path), data)
        metrics['status'] = 'written'
    except BaseException:
        memory._checkpoint_cache = None
        metrics['status'] = 'failed'
        raise
    finally:
        metrics['total_ns'] = time.perf_counter_ns() - start
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
