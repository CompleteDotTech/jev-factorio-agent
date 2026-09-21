"""Use the real repository controller in CI; no provider or Factorio calls."""
import copy
import json

import pytest
import requests

from jev_factorio.backends.mock import MockBackend
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.dashboard import EventWriter, Monitor, attach
from jev_factorio.jev_client import MockJevClient


class TrackedBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.session_id = "mock:dashboard-equivalence"
        self.calls = []

    def observe(self):
        self.calls.append(("observe",))
        return super().observe()

    def act(self, action):
        self.calls.append(("act", action))
        return super().act(action)


class TrackedModel(MockJevClient):
    def __init__(self, mode):
        self.mode, self.calls = mode, []

    def evaluate(self, state, questions):
        self.calls.append(copy.deepcopy((state, questions)))
        if self.mode == "timeout":
            raise requests.Timeout("SECRET ERROR MUST NOT ENTER THE UI")
        if self.mode == "malformed":
            return {}
        return super().evaluate(state, questions)


@pytest.mark.parametrize("policy", ["deterministic", "jev", "hybrid"])
@pytest.mark.parametrize("mode", ["normal", "timeout", "malformed"])
def test_real_controller_equivalence_and_producer_consumer(tmp_path, capsys, policy, mode):
    results = []
    path = tmp_path / "events.jsonl"
    for instrumented in (False, True):
        backend, model = TrackedBackend(), TrackedModel(mode)
        checkpoint = tmp_path / f"checkpoint-{instrumented}.json"
        log = tmp_path / f"legacy-{instrumented}.jsonl"
        loop = HierarchicalLoop(backend, jev=None if policy == "deterministic" else model,
                                policy=policy, target="bootstrap_mining", tick_seconds=0,
                                checkpoint=str(checkpoint), log_file=str(log))
        records, saves = [], []
        with EventWriter(path) as writer:
            if instrumented:
                attach(loop, writer)
            for _ in range(40):
                if loop.terminal:
                    break
                records.append(copy.deepcopy(loop.step()))
                saves.append(checkpoint.read_bytes())
        output = capsys.readouterr().out
        results.append((records, saves, log.read_bytes(), output, backend.calls, model.calls,
                        copy.deepcopy(backend.inv), copy.deepcopy(loop.memory.__dict__)))
    assert results[0] == results[1]
    assert "SECRET ERROR" not in path.read_text()
    monitor = Monitor(path)
    for _ in range(30):
        monitor.poll()
        if monitor.tail.status == "following":
            break
    snapshot = monitor.snapshot()
    assert snapshot["source"]["invalid"] == 0
    assert snapshot["view"]["lifecycle"] == "returned"
    assert snapshot["view"]["state"]["world_kind"] == "mock"
    assert snapshot["view"]["status"] == results[1][-1]["status"]
    events = [json.loads(line) for line in path.read_text().splitlines()]
    dispatches = [row for row in events if row["kind"] == "dispatch_started"]
    assert len(dispatches) == sum(call[0] == "act" for call in results[1][4])


def test_cli_flag_generates_feed_and_rejects_alias_before_backend(tmp_path, monkeypatch):
    from jev_factorio import main
    path = tmp_path / "events.jsonl"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JEV_DASHBOARD_EVENTS", raising=False)
    monkeypatch.setattr("sys.argv", ["jev-factorio", "--controller", "hierarchical", "--backend", "mock",
                                    "--mock-model", "--target", "bootstrap_mining", "--steps", "3",
                                    "--tick-seconds", "0", "--dashboard-events", str(path)])
    main.cli()
    assert any(json.loads(line)["kind"] == "model_request" for line in path.read_text().splitlines())
    original = path.read_bytes()
    monkeypatch.setattr(main, "make_backend", lambda *a, **k: pytest.fail("backend must not start"))
    monkeypatch.setattr("sys.argv", ["jev-factorio", "--controller", "hierarchical", "--mock-model",
                                    "--dashboard-events", str(path), "--log-file", str(path)])
    with pytest.raises(SystemExit):
        main.cli()
    assert path.read_bytes() == original


def test_optional_sink_failure_does_not_change_gameplay(tmp_path, monkeypatch):
    loop = HierarchicalLoop(TrackedBackend(), jev=MockJevClient(), target="bootstrap_mining", tick_seconds=0)
    with EventWriter(tmp_path / "events") as writer:
        attach(loop, writer)
        monkeypatch.setattr(writer, "emit", lambda *a, **k: (_ for _ in ()).throw(OSError("sink down")))
        record = loop.step()
        assert record["action"]
        # Close via the real context-manager implementation, not the failed test double.
        monkeypatch.undo()
