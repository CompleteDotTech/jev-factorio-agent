"""Offline causal tracing over native protocol fixtures and composed controllers."""
from copy import deepcopy
import json
from uuid import UUID

import pytest

from jev_factorio.background import BackgroundMemory, BackgroundWorkLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.dashboard import EventWriter, Monitor, attach
from jev_factorio.input_controller import input_loop_type
from jev_factorio.memory import CampaignMemory
from jev_factorio.research_log import ResearchLogError
from test_background_work import ReceiptBackend, ScenarioLoop as CraftScenario
from test_causal_trace import Backend, Client, Sink, events
from test_input_route_integration import RouteBackend, ScenarioLoop as RouteScenario
from test_output_buffer_integration import BufferBackend, need


def native_case(kind, path, sink=None):
    if kind == "background":
        backend, loop_type, target = ReceiptBackend(), CraftScenario, "automation_science"
    elif kind == "buffer":
        backend = BufferBackend()
        loop_type, target = buffered_loop_type(HierarchicalLoop), "rocket_launch"
    else:
        backend, target = RouteBackend(), "rocket_launch"
        loop_type = RouteScenario
        if kind == "combined":
            loop_type = input_loop_type(buffered_loop_type(BackgroundWorkLoop))
    calls = []
    original = backend.observe

    def observe():
        calls.append("observe")
        return original()

    backend.observe = observe
    loop = loop_type(backend, policy="deterministic", target=target,
                     factory_scheduling="ready-work", tick_seconds=0,
                     checkpoint=str(path), research_log=sink)
    memory_type = getattr(loop, "memory_type", CampaignMemory)
    loop.memory = memory_type(
        backend.state.session_id, target, active_goal=target,
        completed_goals={goal: 0 for goal in loop.order[:-1]},
        last_tick=backend.state.tick)
    if kind in {"route", "combined"}:
        # This fixture starts with a fully paid output buffer. Its durable
        # owner must predate the route action under test.
        source, row = next(iter(backend.state.factory['output_buffers']['sources'].items()))
        loop.memory.output_commitments = {source: {
            'source_unit': row['source_unit'], 'layout': row['layout'],
            'parts': deepcopy(row['parts'])}}
    if kind == "buffer":
        loop._compile_candidates = lambda state: ([need(state, backend.catalog)], "")
    elif kind == "combined":
        loop._compile_candidates = lambda state: RouteScenario._compile_candidates(loop, state)
    return backend, loop, calls


@pytest.mark.parametrize("kind", ["background", "buffer", "route", "combined"])
def test_native_trace_preserves_calls_and_gameplay(tmp_path, monkeypatch, kind):
    monkeypatch.setattr("jev_factorio.background.uuid4", lambda: UUID(int=1))
    results = []
    for enabled in (False, True):
        sink = Sink()
        backend, loop, observations = native_case(
            kind, tmp_path / f"{enabled}.json", sink if enabled else None)
        record = loop.step()
        results.append((deepcopy(backend.calls), list(observations), deepcopy(backend.state),
                        {key: record.get(key) for key in
                         ("action", "verified", "status", "outcome", "source")},
                        deepcopy(loop.memory.pending), deepcopy(loop.memory.reservations)))
        if enabled:
            assert len(events(sink, "observation")) == len(observations)
            assert len(events(sink, "action_prepared")) == len(backend.calls)
            assert len(events(sink, "action_returned")) == len(backend.calls)
            assert not events(sink, "model_request")
            assert not events(sink, "model_response")
    assert results[0] == results[1]


@pytest.mark.parametrize("kind", ["buffer", "route", "combined"])
def test_native_post_dispatch_barrier_preserves_pending_without_replay(tmp_path, kind):
    sink = Sink()
    backend, loop, _ = native_case(kind, tmp_path / "checkpoint.json", sink)
    if kind == "buffer":
        backend.mode = "fault"
    else:
        backend.fault_after_build = True
    record = loop.step()
    assert record["status"] == "uncertain" and not record["verified"]
    pending = deepcopy(loop.memory.pending)
    reservations = deepcopy(loop.memory.reservations)
    assert pending["dispatch"] == "returned" and reservations
    loop.step()
    assert loop.memory.pending == pending
    assert loop.memory.reservations == reservations
    assert len(backend.calls) == len(events(sink, "action_prepared")) == 1
    assert not any(event["verified"] for event in events(sink, "verification"))


def test_background_completion_is_observed_once_and_correlated(tmp_path):
    sink = Sink()
    backend, loop, observations = native_case("background", tmp_path / "checkpoint.json", sink)
    assert isinstance(loop.memory, BackgroundMemory)
    assert not loop.step()["verified"]
    craft_action = events(sink, "action_prepared")[0]
    receipt = backend.calls[0][1]["receipt"]
    assert not events(sink, "background_job_completed")
    assert loop.step()["action"] == "factory_gather"
    backend.complete()
    assert loop.step()["status"] == "completed"
    loop.step()
    completed = events(sink, "background_job_completed")
    assert len(completed) == 1
    assert completed[0]["receipt"] == receipt
    assert completed[0]["action_id"] == craft_action["action_id"]
    assert completed[0]["action_origin"] == "current_trace"
    assert completed[0]["attempt_id"] == craft_action["attempt_id"]
    verified = [event for event in events(sink, "background_job_observed") if event["verified"]]
    assert len(verified) == 1 and verified[0]["receipt"] == receipt
    assert len(backend.calls) == 2
    assert len(events(sink, "observation")) == len(observations)
    assert loop._trace._attempt_actions == {}


def test_background_resume_keeps_attempt_without_inventing_action_id(tmp_path):
    path = tmp_path / "checkpoint.json"
    first_sink = Sink()
    backend, first, _ = native_case("background", path, first_sink)
    first.step()
    attempt_id = first.memory.background_attempt["id"]
    backend.complete()
    resumed_sink = Sink()
    resumed = CraftScenario(backend, policy="deterministic", target="automation_science",
                            factory_scheduling="ready-work", checkpoint=str(path),
                            resume_controller=True, tick_seconds=0, research_log=resumed_sink)
    resumed.step()
    completed = events(resumed_sink, "background_job_completed")
    assert len(completed) == 1
    assert completed[0]["attempt_id"] == attempt_id
    assert completed[0]["action_id"] is None
    assert completed[0]["action_origin"] == "checkpoint_or_external"
    assert len(backend.calls) == 1


def test_native_trace_failure_blocks_dispatch_and_subsequent_observation(tmp_path):
    sink = Sink("action_prepared")
    backend, loop, observations = native_case("combined", tmp_path / "checkpoint.json", sink)
    with pytest.raises(ResearchLogError):
        loop.step()
    saved = json.loads((tmp_path / "checkpoint.json").read_text())
    assert saved["pending"]["dispatch"] == "prepared"
    assert not backend.calls
    observed = len(observations)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert len(observations) == observed and not backend.calls


def test_background_fault_after_independent_dispatch_keeps_pending(tmp_path):
    sink = Sink()
    backend, loop, _ = native_case("background", tmp_path / "checkpoint.json", sink)
    loop.step()
    execute = backend.execute

    def invalidate_after_execute(action, parameters):
        result = execute(action, parameters)
        backend.state.factory["craft_job"]["status"] = "invalid"
        return result

    backend.execute = invalidate_after_execute
    record = loop.step()
    assert record["status"] == "uncertain" and not record["verified"]
    pending = deepcopy(loop.memory.pending)
    assert pending["dispatch"] == "returned" and pending["action"] == "factory_gather"
    loop.step()
    assert loop.memory.pending == pending
    assert len(backend.calls) == len(events(sink, "action_prepared")) == 2
    assert not events(sink, "background_job_completed")
    assert not any(event.get("verified") for event in events(sink, "background_job_observed"))


def test_dashboard_and_causal_trace_delegate_model_and_backend_once(tmp_path):
    results = []
    for dashboard in (False, True):
        backend, client, sink = Backend(), Client(), Sink()
        loop = HierarchicalLoop(backend, client, research_log=sink, tick_seconds=0)
        path = tmp_path / f"dashboard-{dashboard}.jsonl"
        with EventWriter(path) as writer:
            if dashboard:
                attach(loop, writer)
            record = loop.step()
        results.append((backend.calls, client.calls, record["action"], record["verified"]))
        assert len(events(sink, "observation")) == sum(call[0] == "observe" for call in backend.calls)
        assert len(events(sink, "model_request")) == len(client.calls) == 1
        assert len(events(sink, "action_prepared")) == sum(call[0] == "act" for call in backend.calls)
    assert results[0] == results[1]
    captured = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(event["kind"] == "model_request" for event in captured) == 1
    assert sum(event["kind"] == "dispatch_started" for event in captured) == 1
    monitor = Monitor(path)
    monitor.poll()
    assert monitor.snapshot()["source"]["invalid"] == 0


@pytest.mark.parametrize("traced", [False, True])
def test_dashboard_native_traced_dispatch_preserves_optional_capability(tmp_path, traced):
    sink = Sink()
    backend, loop, _ = native_case("background", tmp_path / "checkpoint.json", sink)
    callbacks = []
    if traced:
        def execute_traced(action, parameters, trace):
            callbacks.append(trace)
            return backend.execute(action, parameters)
        backend.execute_traced = execute_traced
    path = tmp_path / "dashboard.jsonl"
    with EventWriter(path) as writer:
        attach(loop, writer)
        assert hasattr(loop.backend, "execute_traced") is traced
        loop.step()
    assert len(backend.calls) == len(events(sink, "action_prepared")) == 1
    assert len(callbacks) == int(traced)
    if traced:
        assert callbacks[0] == loop._diagnostic_trace
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(record["kind"] == "action" for record in records) == 1
    assert sum(record["kind"] == "dispatch_started" for record in records) == 1
    assert sum(record["kind"] == "dispatch_returned" for record in records) == 1
