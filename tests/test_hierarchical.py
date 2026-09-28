import json
import sys

import pytest
import requests

from jev_factorio import main
from jev_factorio.backends.mock import MockBackend
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.evaluation import summarize
from jev_factorio.jev_client import MockJevClient


class CountingBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.actions = []

    def act(self, action):
        self.actions.append(action)
        return super().act(action)


class CountingModel(MockJevClient):
    def __init__(self):
        self.calls = 0

    def evaluate(self, state, questions):
        self.calls += 1
        return super().evaluate(state, questions)


@pytest.mark.parametrize("policy", ["jev", "deterministic"])
def test_complete_mock_bootstrap_with_verified_persistent_goals(tmp_path, policy):
    backend, client = CountingBackend(), CountingModel()
    checkpoint, log = tmp_path / "state.json", tmp_path / "run.jsonl"
    loop = HierarchicalLoop(backend, jev=client, policy=policy, target="bootstrap_mining",
                            checkpoint=str(checkpoint), log_file=str(log), tick_seconds=0)
    loop.run(steps=40)
    assert loop.memory.status == "completed"
    assert list(loop.memory.completed_goals) == ["stockpile_fuel", "bootstrap_mining"]
    assert backend.output >= 5
    assert loop.memory.reservations == {}
    assert backend.actions.count("place_burner_drill") == 1
    assert backend.actions.count("walk_to_coal") == 1
    assert client.calls == (4 if policy == "jev" else 0)
    summary = summarize(log)
    assert summary["evidence_class"] == "synthetic"
    assert summary["native_victory_event_observed"] is False
    assert summary["terminal_status"] == "completed"
    assert json.loads(checkpoint.read_text())["session_id"] == backend.session_id


def test_rocket_target_stops_at_real_capability_gap():
    backend = CountingBackend()
    loop = HierarchicalLoop(backend, jev=MockJevClient(), tick_seconds=0)
    loop.run(steps=40)
    assert loop.memory.status == "blocked"
    assert "rocket" in loop.memory.reason
    assert "bootstrap_mining" in loop.memory.completed_goals
    assert "rocket_launch" not in loop.memory.completed_goals
    assert not any("rocket" in action for action in backend.actions)


def test_committed_plan_does_not_requery_for_each_primitive():
    backend, client = CountingBackend(), CountingModel()
    loop = HierarchicalLoop(backend, jev=client)
    loop.step()
    loop.step()
    assert client.calls == 1
    assert backend.actions == ["walk_to_coal", "mine_coal"]


def test_malformed_answers_do_not_authorize_mutation(tmp_path):
    class BadClient(MockJevClient):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            result["candidate"]["confidence"] = float("nan")
            return result

    backend = CountingBackend()
    log = tmp_path / "invalid.jsonl"
    loop = HierarchicalLoop(backend, jev=BadClient(), log_file=str(log), tick_seconds=0)
    loop.run(steps=10)
    assert loop.memory.status == "blocked"
    assert backend.actions == []
    assert "invalid_numeric" in log.read_text()
    assert len(log.read_text().splitlines()) == 4


def test_preconditions_rechecked_after_model_request():
    backend = CountingBackend()
    backend.inv["coal"] = 5
    backend.at_resource = "iron-ore"

    class ChangingModel(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            backend.inv.pop("wooden-chest")
            return answers

    loop = HierarchicalLoop(backend, jev=ChangingModel())
    record = loop.step()
    assert record["action"] == "observe"
    assert "precondition" in record["outcome"]
    assert backend.actions == []
    assert loop.memory.active_plan is None


def test_model_session_drift_rejected_before_dispatch():
    backend = CountingBackend()

    class ChangingModel(MockJevClient):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            backend.session_id = "different-world"
            return result

    with pytest.raises(ValueError, match="Session"):
        HierarchicalLoop(backend, jev=ChangingModel()).step()
    assert backend.actions == []


def test_mutating_timeout_is_verified_not_replayed(tmp_path):
    class LostAck(CountingBackend):
        def act(self, action):
            result = super().act(action)
            if action == "walk_to_coal":
                raise requests.Timeout("lost acknowledgment")
            return result

    backend = LostAck()
    path = tmp_path / "checkpoint.json"
    first = HierarchicalLoop(backend, jev=MockJevClient(), checkpoint=str(path))
    first.step()
    assert first.memory.pending["dispatch"] == "ambiguous"
    resumed = HierarchicalLoop(backend, jev=MockJevClient(), checkpoint=str(path), resume_controller=True)
    record = resumed.step()
    assert record["verified"] is True
    assert backend.actions.count("walk_to_coal") == 1
    assert resumed.memory.pending is None


def test_no_effect_cannot_be_reported_as_success_or_replayed(tmp_path):
    class NoEffect(CountingBackend):
        def act(self, action):
            self.actions.append(action)
            self.tick += 1
            return "success"

    backend = NoEffect()
    loop = HierarchicalLoop(backend, jev=MockJevClient(), max_pending_polls=2)
    assert not loop.step()["verified"]
    loop.step()
    record = loop.step()
    assert record["status"] == "uncertain"
    assert backend.actions.count("walk_to_coal") == 1
    assert loop.memory.completed_goals == {}


def test_checkpoint_not_overwritten_or_reused_for_another_world(tmp_path):
    checkpoint = tmp_path / "state.json"
    original = HierarchicalLoop(CountingBackend(), jev=MockJevClient(), checkpoint=str(checkpoint))
    original.step()
    with pytest.raises(ValueError, match="exists"):
        HierarchicalLoop(CountingBackend(), jev=MockJevClient(), checkpoint=str(checkpoint))
    resumed = HierarchicalLoop(CountingBackend(), jev=MockJevClient(), checkpoint=str(checkpoint),
                               resume_controller=True)
    with pytest.raises(ValueError, match="mismatch"):
        resumed.step()


def test_tick_regression_stops_execution():
    backend = CountingBackend()
    backend.tick = 10
    loop = HierarchicalLoop(backend, jev=MockJevClient())
    loop.step()
    count = len(backend.actions)
    backend.tick = 2
    with pytest.raises(ValueError, match="regressed"):
        loop.step()
    assert len(backend.actions) == count


def test_mock_model_cannot_drive_live_backend():
    class LiveLookingBackend(CountingBackend):
        def observe(self):
            snapshot = super().observe()
            snapshot.world_kind = "fle"
            return snapshot

    backend = LiveLookingBackend()
    with pytest.raises(ValueError, match="mock model"):
        HierarchicalLoop(backend, jev=MockJevClient()).step()
    assert backend.actions == []


def test_budget_failure_observes_without_a_model_call():
    backend, client = CountingBackend(), CountingModel()
    loop = HierarchicalLoop(backend, jev=client, max_request_bytes=1)
    record = loop.step()
    assert record["action"] == "observe"
    assert client.calls == 0
    assert backend.actions == []


def test_missing_resource_and_partial_construction_are_explicit_blockers():
    backend = CountingBackend()
    backend.known = {}
    loop = HierarchicalLoop(backend, jev=MockJevClient())
    assert loop.step()["status"] == "blocked"
    assert backend.actions == []

    partial = CountingBackend()
    partial.inv["coal"] = 5
    partial.entities = ["burner-mining-drill@iron-ore"]
    loop = HierarchicalLoop(partial, jev=MockJevClient())
    assert "Partial construction" in loop.step()["reason"]


def test_cli_credentials_checked_before_world_start(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("TYPESAFE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--backend", "fle", "--controller", "hierarchical",
                                     "--checkpoint", "state.json", "--tick-seconds", "2"])
    monkeypatch.setattr(main, "make_backend", lambda *a, **k: pytest.fail("must not start game"))
    with pytest.raises(SystemExit):
        main.cli()


def test_cli_mock_is_explicit_and_offline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "configured-but-must-not-be-used")
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--backend", "mock", "--controller", "hierarchical",
                                     "--mock-model", "--target", "bootstrap_mining", "--steps", "40",
                                     "--tick-seconds", "0", "--log-file", "run.jsonl"])
    main.cli()
    assert summarize(tmp_path / "run.jsonl")["terminal_status"] == "completed"


def test_late_effect_can_resolve_uncertain_action_without_replay():
    class DelayedBackend(CountingBackend):
        def act(self, action):
            self.actions.append(action)
            self.tick += 1
            return "accepted"

    backend = DelayedBackend()
    loop = HierarchicalLoop(backend, jev=MockJevClient(), max_pending_polls=1)
    loop.step()
    assert loop.step()["status"] == "uncertain"
    backend.at_resource = "coal"
    record = loop.step()
    assert record["verified"]
    assert loop.memory.status == "running"
    assert backend.actions.count("walk_to_coal") == 1


def test_after_dispatch_observation_failure_keeps_write_ahead_record(tmp_path):
    class LostObservation(CountingBackend):
        def __init__(self):
            super().__init__()
            self.observations = 0

        def observe(self):
            self.observations += 1
            if self.observations == 3:
                raise requests.Timeout("observation transport")
            return super().observe()

    backend = LostObservation()
    path = tmp_path / "memory.json"
    first = HierarchicalLoop(backend, jev=MockJevClient(), checkpoint=str(path))
    with pytest.raises(requests.Timeout):
        first.step()
    assert json.loads(path.read_text())["pending"]["action"] == "walk_to_coal"
    resumed = HierarchicalLoop(backend, jev=MockJevClient(), checkpoint=str(path), resume_controller=True)
    assert resumed.step()["verified"]
    assert backend.actions.count("walk_to_coal") == 1


def test_budget_failure_not_counted_as_api_call(tmp_path):
    log = tmp_path / "budget.jsonl"
    client = CountingModel()
    agent = HierarchicalLoop(CountingBackend(), jev=client, max_request_bytes=1, log_file=str(log))
    record = agent.step()
    assert record["model_call"] is False
    assert summarize(log)["model_calls"] == 0


def test_fle_output_telemetry_counts_only_connected_chest(monkeypatch):
    import types
    from jev_factorio.backends.fle import FleBackend

    position = lambda x, y: types.SimpleNamespace(x=x, y=y)
    drill = types.SimpleNamespace(name="burner-mining-drill", status=types.SimpleNamespace(value="working"),
                                  fuel={"coal": 4}, drop_position=position(5, 5))
    connected = types.SimpleNamespace(name="wooden-chest", position=position(5, 5.203125), ore=7)
    unrelated = types.SimpleNamespace(name="wooden-chest", position=position(50, 50), ore=99)
    entities = [drill, connected, unrelated]
    fake_env = types.ModuleType("fle.env")
    fake_env.Prototype = types.SimpleNamespace(BurnerMiningDrill="drill", WoodenChest="chest")
    fake_env.Resource = types.SimpleNamespace(Coal="coal", IronOre="iron")
    monkeypatch.setitem(sys.modules, "fle", types.ModuleType("fle"))
    monkeypatch.setitem(sys.modules, "fle.env", fake_env)
    tools = types.SimpleNamespace(
        inspect_inventory=lambda entity=None: {"iron-ore": entity.ore} if entity else {},
        nearest=lambda resource: position(0, 0),
        get_entities=lambda prototypes: entities,
    )
    backend = FleBackend()
    backend._fair = types.SimpleNamespace(call=lambda function: {})
    backend._instance = types.SimpleNamespace(
        namespace=tools, rcon_client=types.SimpleNamespace(
            send_command=lambda command: '{"tick":10,"session_id":"test","position":[0,0]}'
        )
    )
    observed = backend.observe()
    assert observed.iron_ore_collected == 7
    assert observed.drill_output_connected is True
    assert observed.world_kind == "fle" and observed.session_id == "test"
    entities.remove(connected)
    observed = backend.observe()
    assert observed.iron_ore_collected == 0
    assert observed.drill_output_connected is False


def test_unrelated_stockpiles_do_not_prove_bootstrap():
    from jev_factorio.planning.goals import completed
    from jev_factorio.state import GameSnapshot

    snapshot = GameSnapshot(placed_entities=["burner-mining-drill", "wooden-chest"],
                            drill_status="working", iron_ore_collected=100)
    assert not completed("bootstrap_mining", snapshot)
    snapshot.drill_output_connected = True
    assert completed("bootstrap_mining", snapshot)


def test_log_mixing_is_rejected(tmp_path):
    log = tmp_path / "mixed.jsonl"
    for _ in range(2):
        HierarchicalLoop(CountingBackend(), jev=MockJevClient(), log_file=str(log)).step()
    with pytest.raises(ValueError, match="mix"):
        summarize(log)
