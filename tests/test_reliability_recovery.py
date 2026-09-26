"""Failure-specific supervisor routing; no service, game, provider or repair CLI."""
from copy import deepcopy
import json
import os

import pytest

from jev_factorio.operational_safety import atomic_json, safety_dir
from jev_factorio.recovery_policy import classify, current_exit, repair_quota_exhausted
from test_supervisor import supervisor, FakeProcess


@pytest.mark.parametrize("kind", ["provider_unavailable", "account_quota_blocked", "storage_pressure",
                                  "native_action_failure", "uncertain_outcome"])
def test_operational_failures_never_request_code_repair(kind):
    assert classify("process_exit: 1", {"status": "running"}, {"failure_class": kind}) == kind


def test_code_repair_requires_positive_source_proof_and_no_owned_effect():
    proof = {"failure_class": "source_defect", "source_evidence_sha256": "a" * 64}
    assert classify("process_exit: 1", {"status": "running"}, proof) == "source_defect"
    assert classify("process_exit: 1", {"status": "running"}) == "unclassified"
    assert classify("process_exit: 1", {"pending": {"dispatch": "prepared"}}, proof) == "uncertain_outcome"
    assert classify("process_exit: 1", {}, {"failure_class": "source_defect"}) == "unclassified"
    assert classify("completed", {}) == "campaign_complete"
    assert classify("cutoff", {}) == "campaign_deadline"


def test_exit_from_another_invocation_is_never_reused(supervisor):
    path = safety_dir(supervisor.config.checkpoint) / "exit.json"
    atomic_json(path, {"schema": 1, "session_id": "fresh", "execution_id": "old",
                      "failure_class": "source_defect", "source_evidence_sha256": "a" * 64})
    assert current_exit(supervisor.config.checkpoint, session_id="fresh", execution_id="new") is None
    assert current_exit(supervisor.config.checkpoint, session_id="fresh", execution_id=None) is None
    assert current_exit(supervisor.config.checkpoint, session_id="other", execution_id="old") is None


def test_repair_quota_denial_stops_identical_attempts_and_survives_restart(supervisor, monkeypatch):
    calls = []
    def launch(command, phase, prompt=None):
        calls.append(phase)
        (supervisor.config.state_dir / "repair.log").write_text("You've hit your usage limit. private-secret")
        supervisor.process = FakeProcess(1)
    monkeypatch.setattr(supervisor, "launch", launch)
    cutoff = supervisor.state["cutoff"]
    assert supervisor.repair("source_defect") is False
    original = json.loads(json.dumps(supervisor.state["incident"]))
    assert supervisor.state["repair_account_blocked"] is True
    assert supervisor.repair("source_defect") is False
    assert calls == ["repair"]
    supervisor.initialize()
    assert supervisor.run() == 2
    assert calls == ["repair"] and supervisor.state["cutoff"] == cutoff
    assert supervisor.state["incident"] == original
    assert "private-secret" not in (supervisor.config.state_dir / "supervisor.json").read_text()
    assert "private-secret" not in (supervisor.config.state_dir / "events.jsonl").read_text()


def test_old_repair_quota_message_does_not_poison_a_new_attempt(tmp_path):
    path = tmp_path / "repair.log"
    path.write_text("usage_limit_reached\n")
    offset = path.stat().st_size
    with path.open("a") as stream:
        stream.write("a different operational failure\n")
    assert repair_quota_exhausted(path)
    assert not repair_quota_exhausted(path, offset)


def test_source_repair_has_persistent_per_incident_cap(supervisor, monkeypatch):
    calls = []
    def launch(command, phase, prompt=None):
        calls.append(phase)
        supervisor.process = FakeProcess(1)
    monkeypatch.setattr(supervisor, "launch", launch)
    supervisor.config.max_repair_attempts = 2
    assert supervisor.repair("source_defect") is False
    baseline = deepcopy(supervisor.state["incident"])
    assert supervisor.repair("source_defect") is False
    assert supervisor.repair("source_defect") is False
    assert calls == ["repair", "repair"]
    assert supervisor.state["phase"] == "blocked"
    assert supervisor.state["incident"] == baseline
    assert supervisor.state["incident_repair_attempts"] == 2


@pytest.mark.parametrize("phase", ["provider_blocked", "storage_pressure", "quiescing", "quiescent", "quiescence_timeout"])
def test_runtime_hold_health_requires_exact_child_and_freshness(supervisor, phase):
    supervisor.save(execution_id="execution", process={"pid": 123})
    path = safety_dir(supervisor.config.checkpoint) / "health.json"
    value = {"schema": 1, "execution_id": "execution", "session_id": "fresh",
             "pid": 123, "at": supervisor.clock(), "phase": phase}
    atomic_json(path, value)
    assert supervisor.runtime_health() == value
    atomic_json(path, {**value, "execution_id": "stale"})
    assert supervisor.runtime_health() is None
    atomic_json(path, {**value, "at": value["at"] - 100})
    assert supervisor.runtime_health() is None


def test_same_incident_stays_blocked_without_resetting_deadline(supervisor, monkeypatch):
    monkeypatch.setattr(supervisor, "watch_game", lambda: "process_exit: 1")
    monkeypatch.setattr(supervisor, "repair", lambda reason: pytest.fail("unclassified exit is not a source defect"))
    cutoff = supervisor.state["cutoff"]
    assert supervisor.run() == 2
    incident = deepcopy(supervisor.state["operational_incident"])
    assert supervisor.run() == 2
    assert supervisor.state["operational_incident"] == incident
    assert supervisor.state["cutoff"] == cutoff
