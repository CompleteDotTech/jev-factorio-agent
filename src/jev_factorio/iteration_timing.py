"""Bounded sequential timing ledger; diagnostics never authorize an action.

Complete iterations are published one record later, after their actual record
write, console emission, intentional loop sleep and remaining gap are observable.
No timer value is persisted in authoritative checkpoint state.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import time
import threading
from typing import Callable, Iterator

NAMES = frozenset({
    'iteration', 'other', 'observe', 'pre_dispatch_observe', 'post_dispatch_observe',
    'planning', 'selection', 'verification', 'dispatch', 'entity_lookup', 'approach', 'transfer_rpc',
    'observation', 'candidate_set_created', 'model_response', 'action_returned',
    'checkpoint_written', 'trace_capture', 'trace_emit', 'trace_normalize_redact',
    'record', 'record_construct', 'legacy_encode', 'legacy_write', 'record_console',
    'research_append', 'research_redact_validate', 'research_validate',
    'research_serialize', 'research_hash', 'research_assemble', 'research_write', 'research_fsync',
    'checkpoint', 'checkpoint_compare', 'checkpoint_capture', 'checkpoint_serialize',
    'checkpoint_write', 'checkpoint_file_sync', 'checkpoint_replace', 'checkpoint_directory_sync',
    'native_command', 'native_batch', 'native_decode', 'fle_helper', 'action_poll_wait',
    'operational_state', 'operational_encode', 'operational_write',
    'operational_file_sync', 'operational_directory_sync', 'operational_replace',
})
CLOCKS = ('wall', 'process_cpu', 'thread_cpu')
IO_KEYS = {'command_calls', 'batch_calls', 'failed_calls', 'request_bytes', 'response_bytes',
           'unknown_request_size_calls', 'unknown_response_size_calls'}
_CURRENT: ContextVar['Ledger | None'] = ContextVar('jev_iteration_timing', default=None)


class Ledger:
    def __init__(self, *, clock=None, cpu_clock=None, thread_clock=None):
        self.clocks = (clock or time.perf_counter_ns, cpu_clock or time.process_time_ns,
                       thread_clock or getattr(time, 'thread_time_ns', lambda: 0))
        self.thread_available = thread_clock is not None or callable(getattr(time, 'thread_time_ns', None))
        self.thread_id = threading.get_ident()
        self.rows: dict[str, dict] = {}
        self.io = dict.fromkeys(IO_KEYS, 0)
        self.stack: list[dict] = []
        self.valid = self.thread_available
        self.start = self.end = None

    def read(self):
        try:
            if threading.get_ident() != self.thread_id:
                raise ValueError("Timing crossed Python threads")
            value = tuple(clock() for clock in self.clocks)
            if any(type(v) is not int or v < 0 for v in value):
                raise ValueError('invalid clock')
            return value
        except Exception:
            self.valid = False
            return None

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        name = name if name in NAMES else 'other'
        if len(self.stack) >= 64:
            self.valid = False
            yield
            return
        frame = {'name': name, 'start': self.read(), 'children': [0, 0, 0]}
        if not self.stack:
            self.start = frame['start']
        self.stack.append(frame)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            end = self.read()
            self.stack.pop()
            if not self.stack:
                self.end = end
            if frame['start'] is not None and end is not None:
                values = tuple(b - a for a, b in zip(frame['start'], end))
                exclusive = tuple(v - child for v, child in zip(values, frame['children']))
                if any(v < 0 for v in (*values, *exclusive)):
                    self.valid = False
                else:
                    row = self.rows.setdefault(name, {'calls': 0, 'failed': 0,
                        **{clock + '_' + scope + '_ns': 0 for clock in CLOCKS
                           for scope in ('inclusive', 'exclusive')}})
                    row['calls'] += 1
                    row['failed'] += int(failed)
                    for i, clock in enumerate(CLOCKS):
                        row[clock + '_inclusive_ns'] += values[i]
                        row[clock + '_exclusive_ns'] += exclusive[i]
                    if self.stack:
                        self.stack[-1]['children'] = [a + b for a, b in zip(self.stack[-1]['children'], values)]

    def snapshot(self, index: int, status: str) -> dict:
        complete = bool(self.valid and not self.stack and self.start is not None and self.end is not None)
        totals = {clock: self.end[i] - self.start[i] for i, clock in enumerate(CLOCKS)} if complete else None
        if complete:
            complete = all(total >= 0 and sum(row[clock + '_exclusive_ns'] for row in self.rows.values()) == total
                           for clock, total in totals.items())
        return {'schema': 1, 'iteration_index': index, 'status': status,
                'clock': 'perf_counter_ns', 'cpu_clock': 'process_time_ns',
                'thread_clock': 'thread_time_ns' if self.thread_available else None,
                'partition_complete': complete,
                'totals_ns': totals if complete else None,
                'phases': deepcopy(self.rows) if complete else {},
                'native_io': dict(self.io),
                'scope': 'decorated_step_through_return_or_error',
                'inclusive_values_are_not_additive': True}


@contextmanager
def span(name: str) -> Iterator[None]:
    ledger = _CURRENT.get()
    if ledger is None:
        yield
    else:
        with ledger.span(name):
            yield


def measured(name: str):
    """Instrument a fixed boundary without changing return or exception values."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with span(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def enabled(loop) -> bool:
    return (getattr(loop, 'factory_scheduling', None) == 'ready-work'
            or (getattr(loop, 'factory_scheduling', None) == 'serial'
                and getattr(getattr(loop, 'backend', None), 'profile_observations', False) is True))


def _gap(pending: dict, current_start) -> dict:
    end = pending['end']
    complete = (pending['sleep_valid'] and end is not None and current_start is not None
                and pending['thread_id'] == threading.get_ident())
    totals = dict(zip(CLOCKS, (b - a for a, b in zip(end, current_start)))) if complete else None
    sleep = pending['sleep_ns']
    if complete:
        complete = all(0 <= sleep[key] <= totals[key] for key in CLOCKS)
    return {'complete': bool(complete), 'total_ns': totals if complete else None,
            'intentional_sleep_ns': dict(sleep) if complete else None,
            'other_gap_ns': {key: totals[key] - sleep[key] for key in CLOCKS} if complete else None,
            'sleep_calls': pending['sleep_calls'], 'sleep_failed': pending['sleep_failed'],
            'requested_sleep_ns': pending['requested_sleep_ns'],
            'scope': 'previous_decorated_step_end_to_current_decorated_step_start'}


def profiled_iteration(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not enabled(self) or _CURRENT.get() is not None:
            return method(self, *args, **kwargs)
        ledger = Ledger()
        index = getattr(self, '_timing_index', 0) + 1
        self._timing_index = index
        pending = getattr(self, '_timing_pending', None)
        status = 'returned'
        token = _CURRENT.set(ledger)
        try:
            with ledger.span('iteration'):
                if pending is not None:
                    self._timing_previous = {**pending['summary'], 'gap': _gap(pending, ledger.start)}
                return method(self, *args, **kwargs)
        except BaseException:
            status = 'error'
            raise
        finally:
            _CURRENT.reset(token)
            self._timing_pending = {'summary': ledger.snapshot(index, status), 'end': ledger.end,
                'clocks': ledger.clocks, 'thread_id': ledger.thread_id, 'sleep_ns': dict.fromkeys(CLOCKS, 0),
                'sleep_valid': True, 'sleep_calls': 0, 'sleep_failed': 0, 'requested_sleep_ns': 0}
    return wrapped


def previous_timing(loop) -> dict | None:
    """The current record carries a completed prior cycle, never a guessed tail."""
    value = getattr(loop, '_timing_previous', None)
    return deepcopy(value) if value is not None else None


def loop_sleep(loop, delay: float, operation: Callable[[], object]):
    pending = getattr(loop, '_timing_pending', None)
    if pending is None:
        return operation()
    def read():
        try:
            if pending['thread_id'] != threading.get_ident():
                raise ValueError('Timing crossed Python threads')
            value = tuple(clock() for clock in pending['clocks'])
            if any(type(v) is not int or v < 0 for v in value): raise ValueError('invalid clock')
            return value
        except Exception:
            pending['sleep_valid'] = False
            return None
    start = read()
    failed = False
    try:
        return operation()
    except BaseException:
        failed = True
        raise
    finally:
        end = read()
        pending['sleep_calls'] += 1
        pending['sleep_failed'] += int(failed)
        try:
            pending['requested_sleep_ns'] += round(delay * 1e9)
        except (ValueError, OverflowError, TypeError):
            pending['sleep_valid'] = False
        if start is not None and end is not None:
            values = [b - a for a, b in zip(start, end)]
            if any(v < 0 for v in values) or pending['end'] is None or any(a < b for a, b in zip(start, pending['end'])):
                pending['sleep_valid'] = False
            else:
                for name, value in zip(CLOCKS, values): pending['sleep_ns'][name] += value


def validate_timing(value: object) -> dict:
    """Validate the fixed numeric report shape; never echo arbitrary values."""
    fields = {'schema','iteration_index','status','clock','cpu_clock','thread_clock',
              'partition_complete','totals_ns','phases','native_io','scope','inclusive_values_are_not_additive','gap'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid iteration timing fields')
    if (type(value['schema']) is not int or value['schema'] != 1
            or type(value['iteration_index']) is not int or not 1 <= value['iteration_index'] <= 2**63 - 1
            or type(value['status']) is not str or value['status'] not in {'returned','error'} or value['clock'] != 'perf_counter_ns'
            or value['cpu_clock'] != 'process_time_ns' or value['thread_clock'] not in ('thread_time_ns',None)
            or value['scope'] != 'decorated_step_through_return_or_error'
            or value['inclusive_values_are_not_additive'] is not True
            or type(value['partition_complete']) is not bool):
        raise ValueError('Invalid iteration timing identity')
    def numeric(data, keys):
        if not isinstance(data,dict) or set(data)!=set(keys) or any(type(v) is not int or not 0<=v<=2**63-1 for v in data.values()):
            raise ValueError('Invalid timing counters')
    numeric(value['native_io'],IO_KEYS)
    requests=value['native_io']['command_calls']+value['native_io']['batch_calls']
    if any(value['native_io'][k]>requests for k in ('failed_calls','unknown_request_size_calls','unknown_response_size_calls')):
        raise ValueError('Invalid native operation counts')
    if value['partition_complete']:
        if value['thread_clock'] != 'thread_time_ns':
            raise ValueError('Thread clock unavailable')
        numeric(value['totals_ns'],CLOCKS)
        phases=value['phases']
        if not isinstance(phases,dict) or not phases or set(phases)-NAMES:
            raise ValueError('Invalid timing labels')
        keys={'calls','failed'} | {c+'_'+s+'_ns' for c in CLOCKS for s in ('inclusive','exclusive')}
        for row in phases.values():
            numeric(row,keys)
            if row['failed']>row['calls'] or row['calls']<1 or any(row[c+'_exclusive_ns']>row[c+'_inclusive_ns'] for c in CLOCKS):
                raise ValueError('Invalid timing nesting')
        if 'iteration' not in phases or phases['iteration']['calls'] != 1:
            raise ValueError('Missing iteration root')
        for clock,total in value['totals_ns'].items():
            if phases['iteration'][clock+'_inclusive_ns'] != total:
                raise ValueError('Invalid iteration root total')
            if sum(row[clock+'_exclusive_ns'] for row in phases.values())!=total:
                raise ValueError('Timing partition does not reconcile')
    elif value['totals_ns'] is not None or value['phases'] != {}:
        raise ValueError('Incomplete timing must remain unknown')
    gap=value['gap']
    gap_keys={'complete','total_ns','intentional_sleep_ns','other_gap_ns','sleep_calls','sleep_failed','requested_sleep_ns','scope'}
    if (not isinstance(gap,dict) or set(gap)!=gap_keys or type(gap['complete']) is not bool
            or gap['scope']!='previous_decorated_step_end_to_current_decorated_step_start'):
        raise ValueError('Invalid timing gap')
    numeric({k:gap[k] for k in ('sleep_calls','sleep_failed','requested_sleep_ns')}, ('sleep_calls','sleep_failed','requested_sleep_ns'))
    if gap['sleep_failed']>gap['sleep_calls']:raise ValueError('Invalid sleep counters')
    if gap['complete']:
        for key in ('total_ns','intentional_sleep_ns','other_gap_ns'):numeric(gap[key],CLOCKS)
        if any(gap['total_ns'][c] != gap['intentional_sleep_ns'][c]+gap['other_gap_ns'][c] for c in CLOCKS):
            raise ValueError('Timing gap does not reconcile')
    elif any(gap[key] is not None for key in ('total_ns','intentional_sleep_ns','other_gap_ns')):
        raise ValueError('Incomplete gap must remain unknown')
    return value


_NATIVE_DEPTH: ContextVar[int] = ContextVar('jev_native_timing_depth', default=0)
_NATIVE_REQUEST: ContextVar[dict | None] = ContextVar('jev_native_request_size', default=None)


@contextmanager
def _request_frame(ledger: Ledger, request_bytes: int | None):
    """Count terminal delegated content once, retaining partial known bytes."""
    known = type(request_bytes) is int and 0 <= request_bytes <= 2**63 - 1
    frame = {'bytes': request_bytes if known else 0,
             'unknown': not known, 'delegated': False}
    parent = _NATIVE_REQUEST.get()
    token = _NATIVE_REQUEST.set(frame)
    try:
        yield
    finally:
        _NATIVE_REQUEST.reset(token)
        if parent is not None:
            if not parent['delegated']:
                parent.update(bytes=0, unknown=False, delegated=True)
            parent['bytes'] += frame['bytes']
            parent['unknown'] = parent['unknown'] or frame['unknown']
        else:
            ledger.io['request_bytes'] += frame['bytes']
            ledger.io['unknown_request_size_calls'] += int(frame['unknown'])


def request_size(value: object) -> int | None:
    """Best-effort UTF-8 content bytes, not framing/packet size or permission."""
    try:
        if type(value) is str:
            return len(value.encode('utf-8'))
        if type(value) is dict and all(type(v) is str for v in value.values()):
            return sum(len(v.encode('utf-8')) for v in value.values())
    except (UnicodeError, TypeError, ValueError):
        pass
    return None


def native_io(name: str, operation: Callable[[], object], *, request_bytes: int | None = None,
              check_response: Callable[[object], None] | None = None):
    """One logical call, including response-status validation, not a packet count.

    A returned rejection still has measurable response bytes. A transport failure
    does not. Validation also runs when timing is disabled or a wrapper is nested;
    instrumentation never supplies permission or changes exception behavior.
    """
    ledger = _CURRENT.get()
    if ledger is None:
        result = operation()
        if check_response is not None:
            check_response(result)
        return result
    with _request_frame(ledger, request_bytes):
        if _NATIVE_DEPTH.get():
            result = operation()
            if check_response is not None:
                check_response(result)
            return result
        token = _NATIVE_DEPTH.set(1)
        name = name if name in {'native_command', 'native_batch'} else 'native_command'
        ledger.io['batch_calls' if name == 'native_batch' else 'command_calls'] += 1
        try:
            with span(name):
                received = False
                try:
                    result = operation()
                    received = True
                    size = request_size(result)
                    if size is None:
                        ledger.io['unknown_response_size_calls'] += 1
                    else:
                        ledger.io['response_bytes'] += size
                    if check_response is not None:
                        check_response(result)
                    return result
                except BaseException:
                    ledger.io['failed_calls'] += 1
                    if not received:
                        ledger.io['unknown_response_size_calls'] += 1
                    raise
        finally:
            _NATIVE_DEPTH.reset(token)


def decode_native(value):
    import json
    with span('native_decode'):
        return json.loads(value)
