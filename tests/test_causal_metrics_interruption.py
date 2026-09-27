"""Instrumentation failures cannot reopen a post-mutation audit boundary."""
from __future__ import annotations

import pytest

from jev_factorio.causal_trace import CausalTrace, TraceStorageError
from jev_factorio.performance import PerformanceCounters
from jev_factorio.research_log import ResearchLogError


class RecordingSink:
    def __init__(self, error=None):
        self.events = []
        self.error = error

    def emit(self, event_type, payload):
        self.events.append((event_type, payload))
        if self.error is not None:
            raise self.error


class OnceFailingCounters(PerformanceCounters):
    def __init__(self, stage, error):
        super().__init__()
        self.stage = stage
        self.error = error
        self.raised = False

    def call(self, name, duration_ns, failed=False, *, cpu_ns=None):
        super().call(name, duration_ns, failed, cpu_ns=cpu_ns)
        if name == self.stage and not self.raised:
            self.raised = True
            raise self.error


ERRORS = [RuntimeError, ValueError, KeyboardInterrupt, SystemExit, GeneratorExit]


@pytest.mark.parametrize('stage', ['action_returned', 'trace_capture', 'trace_emit'])
@pytest.mark.parametrize('error_type', ERRORS)
def test_successful_operation_metric_failure_blocks_all_later_calls(stage, error_type):
    error = error_type('private metric diagnostics')
    trace = CausalTrace(RecordingSink(), 'test')
    trace.metrics = OnceFailingCounters(stage, error)
    effects = []
    expected = ResearchLogError if issubclass(error_type, Exception) else error_type
    with pytest.raises(BaseException) as caught:
        trace.call('action_returned', lambda: effects.append('paid effect'), result=lambda _: {})
    assert isinstance(caught.value, expected)
    if not issubclass(error_type, Exception):
        assert caught.value is error
    else:
        assert 'private metric diagnostics' not in str(caught.value)
    assert trace._failed is True
    with pytest.raises(ResearchLogError, match='Causal trace has failed'):
        trace.call('action_returned', lambda: effects.append('duplicate effect'))
    assert effects == ['paid effect']


@pytest.mark.parametrize('error_type', ERRORS)
def test_operation_exception_survives_secondary_counter_failure(error_type):
    original = ValueError('original operation failure')
    trace = CausalTrace(RecordingSink(), 'test')
    trace.metrics = OnceFailingCounters('action_returned', error_type('private metrics'))
    effects = []

    def operation():
        effects.append('attempt')
        raise original

    with pytest.raises(BaseException) as caught:
        trace.call('action_returned', operation)
    assert caught.value is original
    assert trace._failed is True
    with pytest.raises(ResearchLogError):
        trace.call('action_returned', operation)
    assert effects == ['attempt']


@pytest.mark.parametrize('stage', ['trace_capture', 'trace_emit'])
@pytest.mark.parametrize('error_type', ERRORS)
def test_primary_audit_error_survives_secondary_metric_failure(stage, error_type):
    sink = RecordingSink(ValueError('private sink') if stage == 'trace_emit' else None)
    trace = CausalTrace(sink, 'test')
    trace.metrics = OnceFailingCounters(stage, error_type('private metric'))

    def capture(_):
        if stage == 'trace_capture':
            raise ValueError('private capture')
        return {}

    with pytest.raises(BaseException) as caught:
        trace.call('action_returned', lambda: 1, result=capture)
    assert isinstance(caught.value, ResearchLogError)
    assert 'private' not in str(caught.value)
    assert ('capture' if stage == 'trace_capture' else 'persistence') in str(caught.value)
    assert trace._failed is True


@pytest.mark.parametrize('error_type', ERRORS)
def test_metrics_only_failure_prevents_reuse_without_a_sink(error_type):
    trace = CausalTrace(None, 'test')
    trace.metrics = OnceFailingCounters('observation', error_type('private metric'))
    effects = []
    expected = ResearchLogError if issubclass(error_type, Exception) else error_type
    with pytest.raises(BaseException) as caught:
        trace.call('observation', lambda: effects.append('observe'))
    assert isinstance(caught.value, expected)
    assert trace._failed is True
    with pytest.raises(ResearchLogError):
        trace.call('observation', lambda: effects.append('observe again'))
    assert effects == ['observe']


def test_storage_pressure_classification_survives_secondary_counter_interruption():
    import errno
    trace = CausalTrace(RecordingSink(OSError(errno.ENOSPC, 'private path')), 'test')
    trace.metrics = OnceFailingCounters('trace_emit', KeyboardInterrupt('secondary'))
    with pytest.raises(BaseException) as caught:
        trace.emit('action_prepared', {})
    assert isinstance(caught.value, TraceStorageError)
    assert caught.value.failure_class == 'storage_pressure'
    assert 'private' not in str(caught.value)
    assert trace._failed is True


def test_bad_diagnostic_details_do_not_mask_the_primary_operation_failure():
    original = ValueError('primary')
    trace = CausalTrace(RecordingSink(), 'test')

    def operation():
        raise original

    with pytest.raises(BaseException) as caught:
        trace.call('action_returned', operation, details=['malformed'])
    assert caught.value is original
    assert trace._failed is True


def test_successful_metrics_preserve_counts_and_operation_result():
    trace = CausalTrace(RecordingSink(), 'test')
    trace.metrics = PerformanceCounters()
    value = object()
    assert trace.call('action_returned', lambda: value, result=lambda _: {}) is value
    assert trace._failed is False
    assert set(trace.metrics.calls) == {'action_returned', 'trace_capture', 'trace_emit'}
    assert all(row['count'] == 1 and row['failed'] == 0 for row in trace.metrics.calls.values())


def test_disabling_metrics_does_not_reopen_a_failed_trace():
    trace = CausalTrace(None, 'test')
    trace.metrics = OnceFailingCounters('observation', KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        trace.call('observation', lambda: None)
    trace.metrics = None
    effects = []
    with pytest.raises(ResearchLogError, match='Causal trace has failed'):
        trace.call('action_returned', lambda: effects.append('forbidden'))
    assert effects == []


@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt])
def test_checkpoint_counter_failure_retains_written_pending_state(error_type, tmp_path, monkeypatch):
    import json
    import jev_factorio.performance as performance
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.jev_client import MockJevClient
    from test_causal_trace_interruption import CountingBackend

    backend = CountingBackend()
    original = error_type('private checkpoint counter')
    raised = []

    class Counters(PerformanceCounters):
        def checkpoint(self, metrics):
            super().checkpoint(metrics)
            if metrics.get('status') == 'written' and backend.calls.count('act') == 1 and not raised:
                raised.append(True)
                raise original

    monkeypatch.setattr(performance, 'PerformanceCounters', Counters)
    checkpoint = tmp_path / 'checkpoint.json'
    loop = HierarchicalLoop(backend, MockJevClient(), checkpoint=str(checkpoint),
                            research_log=RecordingSink(), factory_scheduling='ready-work')
    with pytest.raises(BaseException) as caught:
        loop.step()
    assert raised == [True]
    assert isinstance(caught.value, ResearchLogError if error_type is RuntimeError else error_type)
    if error_type is KeyboardInterrupt:
        assert caught.value is original
    assert loop._trace._failed and loop._persistence_failed
    saved = checkpoint.read_bytes()
    assert json.loads(saved)['pending']['dispatch'] == 'prepared'
    prior_calls = list(backend.calls)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == prior_calls and checkpoint.read_bytes() == saved


def test_primary_checkpoint_audit_failure_survives_secondary_counter_interruption(tmp_path, monkeypatch):
    import errno
    import json
    import jev_factorio.performance as performance
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.jev_client import MockJevClient
    from test_causal_trace_interruption import CountingBackend

    backend = CountingBackend()
    secondary = KeyboardInterrupt('private checkpoint counter')
    raised = []

    class Counters(PerformanceCounters):
        def checkpoint(self, metrics):
            super().checkpoint(metrics)
            if backend.calls.count('act') == 1 and not raised:
                raised.append(True)
                raise secondary

    class FailingSink(RecordingSink):
        def emit(self, event_type, payload):
            super().emit(event_type, payload)
            if event_type == 'checkpoint_written' and backend.calls.count('act') == 1:
                raise OSError(errno.ENOSPC, 'private sink path')

    monkeypatch.setattr(performance, 'PerformanceCounters', Counters)
    checkpoint = tmp_path / 'checkpoint.json'
    loop = HierarchicalLoop(backend, MockJevClient(), checkpoint=str(checkpoint),
                            research_log=FailingSink(), factory_scheduling='ready-work')
    with pytest.raises(TraceStorageError) as caught:
        loop.step()
    assert caught.value.failure_class == 'storage_pressure'
    assert 'private' not in str(caught.value)
    assert raised == [True] and loop._trace._failed and loop._persistence_failed
    saved = checkpoint.read_bytes()
    assert json.loads(saved)['pending']['dispatch'] == 'prepared'
    prior_calls = list(backend.calls)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == prior_calls and checkpoint.read_bytes() == saved


@pytest.mark.parametrize('stage', ['action_returned', 'trace_capture', 'trace_emit'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_hierarchical_counter_failure_preserves_prepared_checkpoint(stage, error_type, tmp_path, monkeypatch):
    import json
    import jev_factorio.performance as performance
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.jev_client import MockJevClient
    from test_causal_trace_interruption import CountingBackend

    backend = CountingBackend()
    original = error_type('private performance failure')
    raised = []

    class Counters(PerformanceCounters):
        def call(self, name, duration_ns, failed=False, *, cpu_ns=None):
            super().call(name, duration_ns, failed, cpu_ns=cpu_ns)
            if name == stage and backend.calls.count('act') == 1 and not raised:
                raised.append(True)
                raise original

    monkeypatch.setattr(performance, 'PerformanceCounters', Counters)
    checkpoint = tmp_path / 'checkpoint.json'
    loop = HierarchicalLoop(backend, MockJevClient(), checkpoint=str(checkpoint),
                            research_log=RecordingSink(), factory_scheduling='ready-work')
    with pytest.raises(BaseException) as caught:
        loop.step()
    assert raised == [True]
    assert isinstance(caught.value, ResearchLogError if error_type is RuntimeError else error_type)
    if error_type is not RuntimeError:
        assert caught.value is original
    assert backend.calls.count('act') == 1
    saved = checkpoint.read_bytes()
    assert json.loads(saved)['pending']['dispatch'] == 'prepared'
    prior_calls = list(backend.calls)
    assert not any(e['kind'] == 'dispatch_error' for e in loop.memory.history)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == prior_calls
    assert checkpoint.read_bytes() == saved
