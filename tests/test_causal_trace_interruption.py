"""Interrupted audit boundaries cannot authorize later work; all backends are doubles."""
from __future__ import annotations

import copy
import json

import pytest

from jev_factorio.backends.mock import MockBackend
from jev_factorio.causal_trace import CausalTrace
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.jev_client import MockJevClient
from jev_factorio.performance import PerformanceCounters
from jev_factorio.research_log import ResearchLogError


class OnceInterruptedSink:
    def __init__(self, event_type: str, interruption: BaseException, *, after_append: bool = False):
        self.event_type = event_type
        self.interruption = interruption
        self.after_append = after_append
        self.calls: list[str] = []
        self.events: list[dict] = []
        self.raised = False

    def emit(self, event_type: str, payload: dict) -> None:
        self.calls.append(event_type)
        interrupt = event_type == self.event_type and not self.raised
        if not interrupt or self.after_append:
            self.events.append({"event_type": event_type, "payload": copy.deepcopy(payload)})
        if interrupt:
            self.raised = True
            raise self.interruption


class CountingBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def observe(self):
        self.calls.append("observe")
        return super().observe()

    def act(self, action):
        self.calls.append("act")
        return super().act(action)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("after_append", [False, True])
def test_interrupted_emit_preserves_exception_and_poisons_reuse(exception_type, after_append):
    error = exception_type("private interrupted sink")
    sink = OnceInterruptedSink("action_returned", error, after_append=after_append)
    trace = CausalTrace(sink, "test")
    trace.metrics = PerformanceCounters()
    with pytest.raises(exception_type) as caught:
        trace.emit("action_returned", {"outcome": "ok"})
    assert caught.value is error
    calls = list(sink.calls)
    with pytest.raises(ResearchLogError, match="Causal trace has failed"):
        trace.begin_step()
    assert sink.calls == calls
    assert trace.metrics.calls["trace_emit"]["failed"] == 1
    assert "private interrupted sink" not in json.dumps(sink.events)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interrupted_capture_blocks_next_operation(exception_type):
    error = exception_type("private capture state")
    sink = OnceInterruptedSink("unused", error)
    trace = CausalTrace(sink, "test")
    trace.metrics = PerformanceCounters()
    operations = []

    def operation():
        operations.append("effect")
        return 42

    def capture(value):
        assert value == 42
        raise error

    with pytest.raises(exception_type) as caught:
        trace.call("action_returned", operation, result=capture)
    assert caught.value is error
    with pytest.raises(ResearchLogError, match="Causal trace has failed"):
        trace.call("action_returned", operation)
    assert operations == ["effect"]
    assert not sink.calls
    assert trace.metrics.calls["trace_capture"]["failed"] == 1


@pytest.mark.parametrize("value", [None, [], 4, "invalid", object()])
def test_malformed_capture_is_recording_failure_not_backend_error(value):
    sink = OnceInterruptedSink("unused", KeyboardInterrupt())
    trace = CausalTrace(sink, "test")
    trace.metrics = PerformanceCounters()
    effects = []
    with pytest.raises(ResearchLogError, match="Cannot capture a causal event"):
        trace.call("action_returned", lambda: effects.append(1), result=lambda _: value)
    with pytest.raises(ResearchLogError):
        trace.call("action_returned", lambda: effects.append(2))
    assert effects == [1]
    assert not sink.calls
    assert trace.metrics.calls["trace_capture"]["failed"] == 1


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_secondary_error_recording_interrupt_does_not_replace_primary(exception_type):
    original = RuntimeError("private primary backend error")
    secondary = exception_type("private secondary sink error")
    sink = OnceInterruptedSink("action_returned", secondary)
    trace = CausalTrace(sink, "test")

    def operation():
        raise original

    with pytest.raises(BaseException) as caught:
        trace.call("action_returned", operation)
    assert caught.value is original
    assert trace._failed
    assert sink.calls == ["action_returned"]
    assert not sink.events


@pytest.mark.parametrize("event_type,action_count,dispatch", [
    ("action_prepared", 0, "prepared"),
    ("action_returned", 1, "prepared"),
    ("verification", 1, "returned"),
])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("after_append", [False, True])
def test_controller_retains_checkpoint_and_stops_after_interrupted_audit(
    tmp_path, event_type, action_count, dispatch, exception_type, after_append
):
    error = exception_type("private storage interruption")
    sink = OnceInterruptedSink(event_type, error, after_append=after_append)
    backend = CountingBackend()
    checkpoint = tmp_path / "checkpoint.json"
    loop = HierarchicalLoop(backend, MockJevClient(), checkpoint=str(checkpoint), research_log=sink)
    with pytest.raises(exception_type) as caught:
        loop.step()
    assert caught.value is error
    assert backend.calls.count("act") == action_count
    before = checkpoint.read_bytes()
    pending = json.loads(before)["pending"]
    assert pending["dispatch"] == dispatch
    assert not any(e["kind"] == "dispatch_error" for e in loop.memory.history)
    before_calls, before_events = list(backend.calls), copy.deepcopy(sink.events)
    with pytest.raises(ResearchLogError, match="Causal trace has failed"):
        loop.step()
    assert backend.calls == before_calls
    assert checkpoint.read_bytes() == before
    assert sink.events == before_events
    assert not any(e["event_type"] == "step_failed" for e in sink.events)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_operation_interrupt_remains_primary_when_recording_succeeds(exception_type):
    error = exception_type("private operation interruption")
    sink = OnceInterruptedSink("unused", error)
    trace = CausalTrace(sink, "test")
    trace.metrics = PerformanceCounters()

    def operation():
        raise error

    with pytest.raises(exception_type) as caught:
        trace.call("action_returned", operation)
    assert caught.value is error
    assert not trace._failed  # The recorder succeeded; operation recovery is the caller's duty.
    assert sink.events[0]["payload"]["status"] == "error"
    assert trace.metrics.calls["action_returned"]["failed"] == 1
    assert "trace_capture" not in trace.metrics.calls
    assert "private operation interruption" not in json.dumps(sink.events)


@pytest.mark.parametrize("metrics_enabled", [False, True])
def test_disabled_sink_does_not_capture_or_change_success(metrics_enabled):
    trace = CausalTrace(None, "test")
    trace.metrics = PerformanceCounters() if metrics_enabled else None
    value = {"private": object()}

    def forbidden_capture(_):
        pytest.fail("Disabled recording must not capture")

    assert trace.call("action_returned", lambda: value, result=forbidden_capture) is value


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("event_type,action_count", [("action_prepared", 0), ("action_returned", 1)])
def test_flat_controller_also_stops_before_any_later_backend_call(exception_type, event_type, action_count):
    from jev_factorio.loop import AgentLoop

    backend = CountingBackend()
    error = exception_type("private flat-controller interruption")
    sink = OnceInterruptedSink(event_type, error, after_append=True)
    loop = AgentLoop(backend, MockJevClient(), research_log=sink)
    with pytest.raises(exception_type) as caught:
        loop.step()
    assert caught.value is error
    assert backend.calls.count("act") == action_count
    before = list(backend.calls)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == before


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_controller_capture_interruption_preserves_prepared_checkpoint(tmp_path, monkeypatch, exception_type):
    backend = CountingBackend()
    error = exception_type("private action capture")
    sink = OnceInterruptedSink("unused", error)
    path = tmp_path / "checkpoint.json"
    loop = HierarchicalLoop(backend, MockJevClient(), checkpoint=str(path), research_log=sink)
    original = loop._trace.call

    def call(event_type, operation, *, details=None, result=None):
        if event_type == "action_returned":
            def interrupt_capture(_):
                raise error
            result = interrupt_capture
        return original(event_type, operation, details=details, result=result)

    monkeypatch.setattr(loop._trace, "call", call)
    with pytest.raises(exception_type) as caught:
        loop.step()
    assert caught.value is error
    assert backend.calls.count("act") == 1
    before = path.read_bytes()
    assert json.loads(before)["pending"]["dispatch"] == "prepared"
    calls = list(backend.calls)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == calls
    assert path.read_bytes() == before
    assert not any(e["kind"] == "dispatch_error" for e in loop.memory.history)
    assert not any(e["event_type"] == "action_returned" for e in sink.events)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_secondary_step_failure_logging_preserves_original_in_wrapper(exception_type):
    from jev_factorio.causal_trace import traced_step

    original = ValueError("private original step error")
    sink = OnceInterruptedSink("step_failed", exception_type("private secondary step error"))

    class Loop:
        _trace = CausalTrace(sink, "test")

        @traced_step
        def step(self):
            raise original

    loop = Loop()
    with pytest.raises(BaseException) as caught:
        loop.step()
    assert caught.value is original
    assert loop._trace._failed
    assert sink.calls == ["step_started", "step_failed"]
    with pytest.raises(ResearchLogError):
        loop.step()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, ValueError])
def test_error_capture_failure_also_poisoned_before_sink_call(monkeypatch, exception_type):
    import jev_factorio.causal_trace as module

    original = RuntimeError("private original error")
    sink = OnceInterruptedSink("unused", KeyboardInterrupt())
    trace = CausalTrace(sink, "test")

    def fail_capture(_):
        raise exception_type("private error classifier failure")

    def operation():
        raise original

    monkeypatch.setattr(module, "error_facts", fail_capture)
    with pytest.raises(BaseException) as caught:
        trace.call("action_returned", operation)
    assert caught.value is original
    assert trace._failed
    assert not sink.calls
    with pytest.raises(ResearchLogError):
        trace.begin_step()
