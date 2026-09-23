"""Offline causal/order/fault tests; no provider or Factorio connections."""
import copy
import io
import json
import sys
from contextlib import redirect_stdout
from dataclasses import asdict
from itertools import count
from uuid import UUID

import pytest
import requests

from jev_factorio import main
from jev_factorio.backends.mock import MockBackend
from jev_factorio.causal_trace import CausalTrace
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.jev_client import MockJevClient
from jev_factorio.loop import AgentLoop
from jev_factorio.memory import CampaignMemory
from jev_factorio.research_log import ResearchLog, ResearchLogError, RunConfiguration, verify_run
from jev_factorio.skills import Plan, Step


class Sink:
    def __init__(self, failure=None):
        self.events = []
        self.failure = failure

    def emit(self, kind, payload):
        if kind == self.failure:
            raise ValueError("do not disclose this logging error")
        self.events.append({"event_type": kind, "payload": copy.deepcopy(payload)})


def events(sink, kind):
    return [e["payload"] for e in sink.events if e["event_type"] == kind]


class Backend(MockBackend):
    def __init__(self):
        super().__init__()
        self.session_id = "mock:causal-test"
        self.calls = []

    def observe(self):
        self.calls.append(("observe",))
        return super().observe()

    def act(self, action):
        self.calls.append(("act", action))
        return super().act(action)


class Client(MockJevClient):
    def __init__(self, mode="normal"):
        self.calls = []
        self.mode = mode

    def evaluate(self, state, questions):
        self.calls.append(copy.deepcopy({"state": state, "questions": questions}))
        if self.mode == "timeout":
            raise requests.Timeout("private provider response")
        answers = super().evaluate(state, questions)
        key = "candidate" if "candidate" in questions else "next_action"
        if self.mode == "abstain":
            answers[key]["confidence"] = 0.1
        elif self.mode == "malformed":
            answers[key]["confidence"] = float("nan")
        return answers


def run_case(directory, controller, policy, mode, enabled, monkeypatch):
    directory.mkdir()
    backend, client, sink = Backend(), Client(mode), Sink()
    legacy, checkpoint = directory / "legacy.jsonl", directory / "memory.json"
    options = {"log_file": str(legacy), "tick_seconds": 0}
    if enabled:
        options["research_log"] = sink
    written, records, failure = [], [], None
    original = CampaignMemory.save

    def save(memory, path):
        original(memory, path)
        written.append(path.read_bytes())

    stdout = io.StringIO()
    with monkeypatch.context() as patch, redirect_stdout(stdout):
        import jev_factorio.controller as controller_module
        import jev_factorio.telemetry as telemetry
        identities = count(1)
        patch.setattr(controller_module, "uuid4", lambda: UUID(int=next(identities)))
        patch.setattr(telemetry, "uuid4", lambda: UUID(int=next(identities)))
        patch.setattr(controller_module, "utc_now", lambda: "2026-09-22T00:00:00+00:00")
        patch.setattr(telemetry, "utc_now", lambda: "2026-09-22T00:00:00+00:00")
        patch.setattr(telemetry.time, "perf_counter", lambda: 100.0)
        patch.setattr(telemetry.time, "perf_counter_ns", lambda: 100_000_000_000)
        loop = (AgentLoop(backend, client, **options) if controller == "flat" else
                HierarchicalLoop(backend, client, policy=policy, target="bootstrap_mining",
                                 checkpoint=str(checkpoint), **options))
        patch.setattr(CampaignMemory, "save", save)
        for _ in range(40):
            if getattr(loop, "terminal", False):
                break
            try:
                # Serialize immediately; legacy records can refer to mutable memory.
                records.append(json.dumps(loop.step(), sort_keys=True))
            except requests.Timeout as error:
                failure = (type(error).__name__, str(error))
                break
    comparable = {
        "records": records, "checkpoint_writes": written,
        "legacy": legacy.read_bytes() if legacy.exists() else None,
        "stdout": stdout.getvalue(), "calls": backend.calls, "requests": client.calls,
        "world": asdict(MockBackend.observe(backend)), "failure": failure,
    }
    return comparable, sink


@pytest.mark.parametrize("controller,policy", [("flat", "jev"), ("hierarchical", "jev"),
                                               ("hierarchical", "deterministic"),
                                               ("hierarchical", "hybrid")])
@pytest.mark.parametrize("mode", ["normal", "abstain", "malformed", "timeout"])
def test_enabled_disabled_equivalence(tmp_path, monkeypatch, controller, policy, mode):
    before, _ = run_case(tmp_path / "off", controller, policy, mode, False, monkeypatch)
    after, sink = run_case(tmp_path / "on", controller, policy, mode, True, monkeypatch)
    assert before == after
    assert len(events(sink, "observation")) == sum(c[0] == "observe" for c in after["calls"])
    assert len(events(sink, "model_request")) == len(after["requests"])
    for event, request in zip(events(sink, "model_request"), after["requests"]):
        assert event["state"] == json.loads(json.dumps(request["state"]))
        assert event["questions"] == json.loads(json.dumps(request["questions"]))
    assert len(events(sink, "action_prepared")) == sum(c[0] == "act" for c in after["calls"])


def test_successful_causal_chain_and_pending_poll_correlation(tmp_path, monkeypatch):
    result, sink = run_case(tmp_path / "case", "hierarchical", "jev", "normal", True, monkeypatch)
    assert [e["goal"] for e in events(sink, "goal_completed")] == ["stockpile_fuel", "bootstrap_mining"]
    assert len(events(sink, "model_request")) == 4
    assert len(events(sink, "decision")) == 4  # No phantom model decision on committed steps.
    prepared, returned = events(sink, "action_prepared"), events(sink, "action_returned")
    assert [p["action_id"] for p in prepared] == [p["action_id"] for p in returned]
    assert len({p["action_id"] for p in prepared}) == len(prepared)
    waits = [p for p in prepared if p["role"] == "mock_clock_advance"]
    assert waits and all(p["related_action_id"] for p in waits)
    plan_wait = next(p for p in prepared if p["role"] == "plan" and p["action"] == "idle")
    polls = [p for p in events(sink, "verification") if p["phase"] == "pending_poll"]
    assert all(p["action_id"] == plan_wait["action_id"] for p in polls)
    assert polls[-1]["verified"] is True
    kinds = [e["event_type"] for e in sink.events]
    assert kinds.index("model_request") < kinds.index("model_response") < kinds.index("decision")
    assert kinds.index("action_prepared") < kinds.index("action_returned") < kinds.index("verification")
    for e in sink.events:
        if "duration_ns" in e["payload"]:
            assert type(e["payload"]["duration_ns"]) is int and e["payload"]["duration_ns"] >= 0


def test_write_ahead_event_and_checkpoint_precede_real_call(tmp_path):
    checkpoint = tmp_path / "memory.json"
    with ResearchLog(tmp_path / "run", RunConfiguration("mock", "hierarchical", "jev")) as sink:
        class InspectingBackend(Backend):
            def act(self, action):
                pending = json.loads(checkpoint.read_text())["pending"]
                assert pending["dispatch"] == "prepared" and pending["action"] == action
                captured = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
                assert captured[-1]["event_type"] == "action_prepared"
                assert captured[-1]["payload"]["checkpointed"] is True
                return super().act(action)

        HierarchicalLoop(InspectingBackend(), Client(), checkpoint=str(checkpoint), research_log=sink).step()
    assert verify_run(tmp_path / "run")["complete"]


@pytest.mark.parametrize("failure,mutated,dispatch", [("action_prepared", False, "prepared"),
                                                     ("action_returned", True, "prepared"),
                                                     ("verification", True, "returned")])
def test_sink_failure_never_replays_or_becomes_a_backend_error(tmp_path, failure, mutated, dispatch):
    backend, sink = Backend(), Sink(failure)
    path = tmp_path / "checkpoint.json"
    loop = HierarchicalLoop(backend, Client(), checkpoint=str(path), research_log=sink)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert bool([c for c in backend.calls if c[0] == "act"]) is mutated
    assert json.loads(path.read_text())["pending"]["dispatch"] == dispatch
    assert not any(e["kind"] == "dispatch_error" for e in loop.memory.history)
    calls = list(backend.calls)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == calls


def test_checkpoint_failure_prevents_action_preparation(tmp_path, monkeypatch):
    original = CampaignMemory.save

    def fail(memory, path):
        if memory.pending:
            raise OSError("private storage path")
        return original(memory, path)

    monkeypatch.setattr(CampaignMemory, "save", fail)
    backend, sink = Backend(), Sink()
    with pytest.raises(OSError):
        HierarchicalLoop(backend, Client(), checkpoint=str(tmp_path / "cp.json"), research_log=sink).step()
    assert not events(sink, "action_prepared")
    assert not [c for c in backend.calls if c[0] == "act"]
    assert events(sink, "checkpoint_written")[-1]["status"] == "error"
    assert "private storage path" not in json.dumps(sink.events)


@pytest.mark.parametrize("failure", ["lost_ack", "lost_observation"])
def test_resume_reconciles_without_inventing_cross_process_identity(tmp_path, failure):
    class InterruptedBackend(Backend):
        def act(self, action):
            result = super().act(action)
            if failure == "lost_ack" and action == "walk_to_coal":
                raise requests.Timeout("private acknowledgment")
            return result

        def observe(self):
            if failure == "lost_observation" and sum(c[0] == "observe" for c in self.calls) == 2:
                self.calls.append(("observe",))
                raise requests.Timeout("private observation")
            return super().observe()

    backend, first_sink = InterruptedBackend(), Sink()
    path = tmp_path / "cp.json"
    first = HierarchicalLoop(backend, Client(), checkpoint=str(path), research_log=first_sink)
    if failure == "lost_observation":
        with pytest.raises(requests.Timeout):
            first.step()
    else:
        first.step()
    saved = json.loads(path.read_text())
    assert saved["pending"] is not None
    sink, client = Sink(), Client()
    second = HierarchicalLoop(backend, client, checkpoint=str(path), resume_controller=True, research_log=sink)
    assert second.step()["verified"] is True
    verification = events(sink, "verification")[0]
    assert verification["action_id"] is None
    assert verification["action_origin"] == "checkpoint_or_external"
    assert verification["attempt_id"] == saved["attempt"]["id"]
    assert verification["attempt_id"] == events(first_sink, "action_prepared")[0]["attempt_id"]
    assert not client.calls and not events(sink, "action_prepared")
    assert backend.calls.count(("act", "walk_to_coal")) == 1


def test_returned_success_is_not_verification_and_poll_budget_unchanged():
    class NoEffect(Backend):
        def act(self, action):
            self.calls.append(("act", action))
            self.tick += 1
            return "success"

    backend, sink = NoEffect(), Sink()
    loop = HierarchicalLoop(backend, Client(), research_log=sink, max_pending_polls=2)
    assert not loop.step()["verified"]
    loop.step()
    assert loop.step()["status"] == "uncertain"
    assert backend.calls.count(("act", "walk_to_coal")) == 1
    assert not any(p["verified"] for p in events(sink, "verification"))
    assert events(sink, "pending_expired")[0]["polls"] == 2
    assert not events(sink, "goal_completed")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 503, 529])
@pytest.mark.parametrize("policy", ["jev", "hybrid"])
def test_provider_status_handling_and_sanitized_error_metadata(status, policy):
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError("secret response https://private.invalid?token=secret", response=response)

    class FailingClient(Client):
        last_usage = {"input_tokens": 999}
        last_model = "stale-model"

        def evaluate(self, state, questions):
            raise error

    backend, sink = Backend(), Sink()
    loop = HierarchicalLoop(backend, FailingClient(), policy=policy, research_log=sink)
    if status in {408, 429, 503, 529}:
        result = loop.step()
        assert bool([c for c in backend.calls if c[0] == "act"]) is (policy == "hybrid")
        assert result["decision"]["model_called"] is True
    else:
        with pytest.raises(requests.HTTPError) as caught:
            loop.step()
        assert caught.value is error
    captured = events(sink, "model_response")[0]
    assert captured["error"] == {"category": "http", "http_status": status}
    assert captured["usage"] is None and captured["resolved_model"] is None
    assert "private.invalid" not in json.dumps(sink.events)


def test_budget_rejection_does_not_fabricate_model_events():
    backend, client, sink = Backend(), Client(), Sink()
    loop = HierarchicalLoop(backend, client, max_request_bytes=1, research_log=sink)
    assert loop.step()["action"] == "observe"
    assert not client.calls and not events(sink, "model_request")
    assert not events(sink, "model_response")
    assert events(sink, "decision")[0]["model_called"] is False


def test_request_budget_retains_generated_and_actually_offered_candidates(monkeypatch):
    import jev_factorio.controller as controller
    plans = [Plan(f"candidate-{i}", "stockpile_fuel", "Walk to coal", (Step("walk_to_coal", "near", "coal"),))
             for i in range(18)]
    monkeypatch.setattr(controller, "compile_plans", lambda *args: (plans, ""))
    sink, client = Sink(), Client()
    HierarchicalLoop(Backend(), client, max_request_bytes=100000, research_log=sink).step()
    assert len(events(sink, "candidate_set_created")[0]["plans"]) == 18
    request = events(sink, "model_request")[0]
    assert len(request["state"]["candidate_plans"]) == 16
    assert request["state"] == json.loads(json.dumps(client.calls[0]["state"]))


def test_factory_execute_parameters_and_verifier_are_unchanged(monkeypatch):
    import jev_factorio.controller as controller
    parameters = {"resource": "coal", "quantity": 5}
    step = Step("factory_gather", "inventory", "coal", 5, parameters=parameters)
    plan = Plan("gather", "stockpile_fuel", "Gather coal", (step,))
    monkeypatch.setattr(controller, "compile_plans", lambda *args: ([plan], ""))

    class FactoryBackend(Backend):
        def observe(self):
            snapshot = super().observe()
            snapshot.factory = {"player_connected": True, "player_bound": True}
            return snapshot

        def execute(self, action, received):
            self.calls.append(("execute", action, copy.deepcopy(received)))
            self.inv["coal"] = 5
            self.tick += 1
            return {"accepted": True}

    backend, sink = FactoryBackend(), Sink()
    loop = HierarchicalLoop(backend, policy="deterministic", research_log=sink)
    assert loop.step()["verified"]
    assert ("execute", "factory_gather", parameters) in backend.calls
    assert events(sink, "action_prepared")[0]["parameters"] == parameters
    assert events(sink, "verification")[-1]["predicate"]["parameters"] == parameters


def test_timing_excludes_payload_and_sink_work(monkeypatch):
    import jev_factorio.causal_trace as module
    clock = [0]
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: clock[0])

    class SlowSink(Sink):
        def emit(self, kind, payload):
            clock[0] += 1000
            super().emit(kind, payload)

    sink, trace = SlowSink(), None
    trace = CausalTrace(sink, "test")

    def operation():
        clock[0] += 17
        return 42

    def capture(value):
        clock[0] += 3000
        return {"value": value}

    assert trace.call("operation", operation, result=capture) == 42
    assert events(sink, "operation")[0]["duration_ns"] == 17


def test_disabled_instrumentation_does_not_consult_clock_or_randomness(monkeypatch):
    import jev_factorio.causal_trace as module
    monkeypatch.setattr(module.time, "perf_counter_ns", lambda: pytest.fail("clock consulted"))
    monkeypatch.setattr(module, "uuid4", lambda: pytest.fail("trace identity allocated"))
    AgentLoop(Backend(), Client()).step()
    HierarchicalLoop(Backend(), Client()).step()


def test_sink_cannot_mutate_request_or_controller_state():
    class MutatingSink:
        def emit(self, kind, payload):
            if "snapshot" in payload:
                payload["snapshot"]["inventory"].clear()
            if "state" in payload:
                payload["state"].clear()
            payload.clear()

    client, backend = Client(), Backend()
    record = HierarchicalLoop(backend, client, research_log=MutatingSink()).step()
    assert record["action"] == "walk_to_coal"
    assert client.calls[0]["state"]["facts"]["inventory"]["burner-mining-drill"] == 1


def test_secrets_and_invalid_numbers_are_safe_without_changing_raw_answers():
    class SecretClient(Client):
        api_key = "explicit-client-secret"

        def evaluate(self, state, questions):
            answer = super().evaluate(state, questions)
            answer["candidate"]["confidence"] = float("nan")
            answer["debug"] = {"note": self.api_key, "Authorization": "Bearer hidden"}
            return answer

    sink, backend = Sink(), Backend()
    result = HierarchicalLoop(backend, SecretClient(), research_log=sink).step()
    assert not [c for c in backend.calls if c[0] == "act"]
    text = json.dumps(sink.events, allow_nan=False)
    assert "explicit-client-secret" not in text and "Bearer hidden" not in text
    assert "invalid_numeric" in text
    assert result["decision"]["answers"]["debug"]["note"] == "explicit-client-secret"


@pytest.mark.parametrize("failure", ["existing", "legacy_collision", "checkpoint_collision"])
def test_cli_bad_research_path_is_rejected_before_backend_start(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "run"
    args = ["jev-factorio", "--backend", "mock", "--run-dir", str(run_dir)]
    if failure == "existing":
        run_dir.mkdir()
    elif failure == "legacy_collision":
        args += ["--log-file", str(run_dir / "events.jsonl")]
    else:
        args += ["--controller", "hierarchical", "--mock-model", "--checkpoint", str(run_dir / "manifest.json")]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(main, "make_backend", lambda *args, **kwargs: pytest.fail("backend started"))
    with pytest.raises(SystemExit):
        main.cli()


def test_cli_research_is_opt_in_and_legacy_file_remains_readable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "configured-but-not-used")
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--backend", "mock", "--controller", "hierarchical",
                                     "--mock-model", "--target", "bootstrap_mining", "--steps", "40",
                                     "--run-dir", "run", "--log-file", "legacy.jsonl", "--tick-seconds", "0"])
    main.cli()
    records = [json.loads(line) for line in (tmp_path / "legacy.jsonl").read_text().splitlines()]
    assert records[-1]["status"] == "completed"
    assert "event_type" not in records[0]
    assert verify_run(tmp_path / "run")["complete"]
    assert "configured-but-not-used" not in (tmp_path / "run/manifest.json").read_text()


@pytest.mark.parametrize("controller", [AgentLoop, HierarchicalLoop])
def test_programmatic_legacy_path_alias_cannot_corrupt_sink(tmp_path, controller):
    with ResearchLog(tmp_path / "run", RunConfiguration("mock", "hierarchical", "jev")) as sink:
        alias = tmp_path / "alias.jsonl"
        alias.hardlink_to(tmp_path / "run/events.jsonl")
        with pytest.raises(ValueError, match="overwrite"):
            controller(Backend(), Client(), research_log=sink, log_file=str(alias))
    assert verify_run(tmp_path / "run")["complete"]


def test_programmatic_checkpoint_cannot_replace_manifest(tmp_path):
    with ResearchLog(tmp_path / "run", RunConfiguration("mock", "hierarchical", "jev")) as sink:
        with pytest.raises(ValueError, match="overwrite"):
            HierarchicalLoop(Backend(), Client(), research_log=sink,
                             checkpoint=str(tmp_path / "run/manifest.json"), resume_controller=True)
    assert verify_run(tmp_path / "run")["complete"]


def test_preconditions_still_rechecked_after_model_changes_world():
    backend, sink = Backend(), Sink()
    backend.inv["coal"] = 5
    backend.at_resource = "iron-ore"

    class ChangingClient(Client):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            backend.inv.pop("wooden-chest")
            return result

    record = HierarchicalLoop(backend, ChangingClient(), research_log=sink).step()
    assert record["action"] == "observe" and "precondition" in record["outcome"]
    assert events(sink, "precondition_checked")[-1]["allowed"] is False
    assert not events(sink, "action_prepared")
    assert not [call for call in backend.calls if call[0] == "act"]


def test_logging_failure_during_provider_error_prevents_hybrid_mutation():
    backend, sink = Backend(), Sink("model_response")
    loop = HierarchicalLoop(backend, Client("timeout"), policy="hybrid", research_log=sink)
    with pytest.raises(ResearchLogError):
        loop.step()
    assert not [call for call in backend.calls if call[0] == "act"]


def test_interrupt_preserved_and_no_response_body_logged():
    error = KeyboardInterrupt("private interruption")

    class InterruptedClient(Client):
        def evaluate(self, state, questions):
            raise error

    backend, sink = Backend(), Sink()
    with pytest.raises(KeyboardInterrupt) as caught:
        HierarchicalLoop(backend, InterruptedClient(), research_log=sink).step()
    assert caught.value is error
    assert events(sink, "model_response")[0]["error"]["category"] == "interrupted"
    assert "private interruption" not in json.dumps(sink.events)
    assert not events(sink, "action_prepared")


@pytest.mark.parametrize("enabled", [False, True])
def test_flat_low_confidence_missing_choice_retains_original_fallback(enabled):
    class MissingChoiceClient(Client):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            result["next_action"]["confidence"] = 0.1
            del result["next_action"]["choice"]
            return result

    sink = Sink()
    record = AgentLoop(Backend(), MissingChoiceClient(),
                       research_log=sink if enabled else None).step()
    assert record["source"] == "fallback" and record["action"] == "walk_to_coal"
    if enabled:
        assert events(sink, "decision")[0]["requested_action"] is None


@pytest.mark.parametrize("controller", [AgentLoop, HierarchicalLoop])
def test_canonical_events_join_frozen_supervisor_context(tmp_path, monkeypatch, controller):
    from jev_factorio.provenance import CONTEXT_ENV
    context = {"run_id": "supervised-run", "segment_id": "segment-2",
               "execution_id": "execution-3",
               "code_revision": {"commit": "a" * 40, "source_sha256": "b" * 64}}
    monkeypatch.setenv(CONTEXT_ENV, json.dumps(context))
    with ResearchLog(tmp_path / "run", RunConfiguration("mock", "hierarchical", "jev")) as sink:
        loop = controller(Backend(), Client(), research_log=sink)
        monkeypatch.setenv(CONTEXT_ENV, json.dumps({**context, "segment_id": "later"}))
        loop.provenance["segment_id"] = "mutated"
        loop.step()
        assert loop._trace._attempt_actions == {}
    assert verify_run(tmp_path / "run")["complete"]
    records = [json.loads(line) for line in (tmp_path / "run/events.jsonl").read_text().splitlines()]
    traced = [record for record in records if "trace_id" in record["payload"]]
    assert traced and all(record["payload"]["supervisor_provenance"] == context for record in traced)
    assert all(record["run_id"] != context["run_id"] for record in records)
    prepared = next(record for record in traced if record["event_type"] == "action_prepared")
    assert prepared["correlation"]["action_id"] == prepared["payload"]["action_id"]
    assert prepared["session_id"] == "mock:causal-test"
    assert prepared["time"]["factorio_tick"] == 0


def test_discarded_foreground_attempt_releases_trace_reference():
    class NoEffect(Backend):
        def act(self, action):
            self.calls.append(("act", action))
            return "not applied"

    loop = HierarchicalLoop(NoEffect(), Client(), research_log=Sink())
    loop.step()
    assert loop._trace._attempt_actions
    loop._fail_plan("Observed safe absence")
    assert loop._trace._attempt_actions == {}
