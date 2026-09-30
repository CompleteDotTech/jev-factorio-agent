"""Small content-free clock samples used by optional latency diagnostics."""
from __future__ import annotations

import threading
import time


def sample_clocks(*, wall_clock=None, process_clock=None, thread_clock=None) -> dict:
    """Read monotonic wall and Python CPU clocks without propagating failures."""
    clocks = ((time.perf_counter_ns if wall_clock is None else wall_clock),
              (time.process_time_ns if process_clock is None else process_clock),
              (thread_clock if thread_clock is not None else getattr(time, 'thread_time_ns', None)))
    values = {}
    for key, clock in zip(('wall_ns', 'process_cpu_ns', 'thread_cpu_ns'), clocks):
        try:
            value = clock() if callable(clock) else None
        except Exception:
            value = None
        values[key] = value if type(value) is int and value >= 0 else None
    values['_thread_id'] = threading.get_ident()
    return values


def elapsed_clocks(start: dict | None, end: dict, scope: str) -> dict:
    """Return measured deltas; unavailable/regressed clocks stay null."""
    def delta(key):
        before = start.get(key) if isinstance(start, dict) else None
        after = end.get(key) if isinstance(end, dict) else None
        if type(before) is int and type(after) is int and after >= before:
            return after - before
        return None

    wall = delta('wall_ns')
    process = delta('process_cpu_ns')
    same_thread = (isinstance(start, dict)
                   and isinstance(end, dict)
                   and start.get('_thread_id') == end.get('_thread_id'))
    thread = delta('thread_cpu_ns') if same_thread else None
    return {
        'schema': 1,
        'complete': wall is not None and process is not None,
        'clocks': {'wall': 'perf_counter_ns', 'process_cpu': 'process_time_ns',
                   'thread_cpu': 'thread_time_ns' if thread is not None else None},
        'wall_ns': wall,
        'process_cpu_ns': process,
        'thread_cpu_ns': thread,
        'scope': scope,
        'cpu_scope': 'process_cpu_includes_other_python_threads; thread_cpu_is_current_thread',
    }
