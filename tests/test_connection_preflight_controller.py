"""Only explicit pre-mutation rejection may release a prepared connection."""
from copy import deepcopy

import pytest

from jev_factorio.backends.errors import ConnectionPreflightRejected
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.memory import CampaignMemory
from jev_factorio.skills import Plan, Step
from jev_factorio.telemetry import validate_attempt
from test_factory import machine, snapshot


def controller(tmp_path, error, research_log=None):
    state = snapshot(inventory={"pipe": 51})
    state.factory["entities"] = {
        "water": machine("offshore-pump"), "sulfur": machine("chemical-plant"),
    }
    # Existing pipes must not authorize reconciliation of a generic failure.
    state.factory["force_entity_counts"] = {"pipe": 10}
    plan = Plan("connect:test", "rocket_launch", "connect water", (Step(
        "factory_connect", "connection", costs={"pipe": 51},
        parameters={"source": "water", "target": "sulfur", "kind": "pipe", "fluid": "water"},
    ),))

    class Backend:
        calls = 0

        def observe(self):
            return deepcopy(state)

        def execute(self, action, parameters):
            self.calls += 1
            raise error

        def act(self, action):
            assert action == "idle"
            state.tick += 1
            return "clock advanced"

    backend = Backend()
    loop = HierarchicalLoop(backend, policy="deterministic", checkpoint=str(tmp_path / "state.json"),
                            research_log=research_log)
    loop._work_candidates = lambda _: ([plan], "")
    loop.memory = CampaignMemory(state.session_id, loop.target, last_tick=state.tick)
    loop.memory.completed_goals = {"stockpile_fuel": 0, "bootstrap_mining": 0}
    loop.memory.active_goal = "rocket_launch"
    return loop, backend, plan


def test_known_preflight_rejection_is_durable_failure_not_success_or_poll(tmp_path):
    loop, backend, plan = controller(tmp_path, ConnectionPreflightRejected("missing_fluid_port"))
    record = loop.step()
    assert not record["verified"]
    assert loop.memory.pending is None
    assert loop.memory.reservations == {}
    assert loop.memory.failures[plan.id] == 1
    outcome = loop.memory.attempt_outcomes[-1]
    assert outcome["outcome"] == "connection_preflight_rejected"
    validate_attempt(outcome, finished=True)
    saved = CampaignMemory.load(loop.checkpoint, loop.memory.session_id, loop.target)
    assert saved.attempt_outcomes[-1] == outcome
    assert saved.failures == loop.memory.failures
    # Replanning consumes the existing budget; it cannot loop forever.
    loop.step()
    record = loop.step()
    assert record["status"] == "blocked"
    assert backend.calls == 2
    assert loop.memory.failures[plan.id] == 2


@pytest.mark.parametrize("error", [ValueError("no route"), TimeoutError("lost reply")])
def test_generic_connection_failure_stays_ambiguous_and_is_not_replayed(tmp_path, error):
    loop, backend, _ = controller(tmp_path, error)
    loop.step()
    assert loop.memory.pending["dispatch"] == "ambiguous"
    loop.step()
    assert backend.calls == 1
    assert loop.memory.pending is not None
    assert not loop.memory.attempt_outcomes


def test_rejection_subclass_is_not_the_explicit_backend_contract(tmp_path):
    class UntrustedRejection(ConnectionPreflightRejected):
        pass

    loop, _, _ = controller(tmp_path, UntrustedRejection("missing_fluid_port"))
    loop.step()
    assert loop.memory.pending["dispatch"] == "ambiguous"


def test_preflight_exception_does_not_release_an_unrelated_action(tmp_path):
    from jev_factorio.backends.mock import MockBackend

    class Backend(MockBackend):
        def act(self, action):
            raise ConnectionPreflightRejected("missing_fluid_port")

    loop = HierarchicalLoop(Backend(), policy="deterministic", target="stockpile_fuel",
                            checkpoint=str(tmp_path / "unrelated.json"))
    loop.step()
    assert loop.memory.pending["action"] != "factory_connect"
    assert loop.memory.pending["dispatch"] == "ambiguous"


def test_rejected_connection_outcome_cannot_label_other_actions(tmp_path):
    loop, _, _ = controller(tmp_path, ConnectionPreflightRejected("missing_fluid_port"))
    loop.step()
    outcome = deepcopy(loop.memory.attempt_outcomes[-1])
    outcome["action"] = "factory_insert"
    with pytest.raises(ValueError, match="Invalid attempt outcome"):
        validate_attempt(outcome, finished=True)


@pytest.mark.parametrize("corruption", [None, "missing", "plan_id", "attempt_id", "session_id", "code", "mutation_started"])
def test_causal_replay_requires_exact_preflight_rejection_evidence(tmp_path, corruption):
    import json
    from jev_factorio.research_log import ResearchLog, RunConfiguration
    from jev_factorio.replay import replay_log, ReplayReport
    from jev_factorio.replay_causal import audit_producer

    run = tmp_path / "research"
    with ResearchLog(run, RunConfiguration("mock", "hierarchical", "deterministic")) as sink:
        loop, _, _ = controller(tmp_path, ConnectionPreflightRejected("missing_fluid_port"), sink)
        loop.step()
    report = replay_log(run / "events.jsonl")
    action = report.decisions[0]["actions"][0]
    assert action["acknowledgment"] == "rejected_before_mutation"
    assert action["verified"] is False
    assert not any(f["code"] == "unverified_action" for f in report.to_dict()["findings"])
    if corruption is None:
        return
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    rejection = next(e for e in events if e["event_type"] == "connection_preflight_rejected")
    if corruption == "missing":
        events.remove(rejection)
    elif corruption == "session_id":
        rejection["session_id"] = "wrong-session"
    else:
        rejection["payload"][corruption] = True if corruption == "mutation_started" else "wrong"
    # Test semantic validation directly, after the independent hash-chain layer.
    bad = ReplayReport()
    audit_producer(events, bad)
    action = bad.decisions[0]["actions"][0]
    assert action["acknowledgment"] == "ambiguous"
    assert action["verified"] is not True
    assert any(f["code"] == "unverified_action" for f in bad.to_dict()["findings"])
