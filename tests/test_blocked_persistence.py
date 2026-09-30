from __future__ import annotations

from dataclasses import replace

import pytest

from jev_factorio import blocked_persistence as persistence
from jev_factorio.backends.mock import MockBackend
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.judgments import Decision
from jev_factorio.memory import CampaignMemory
from jev_factorio.skills import Plan, Step


SOURCE = {"commit": "2" * 40, "source_sha256": "c" * 64}


def _inputs(tick: int, *, inventory=2, receipt_item="iron-plate", quantity=1,
            deadline=1000, research_horizon=990):
    state = {
        "tick": tick,
        "session_id": "campaign-session",
        "inventory": {"iron-plate": inventory},
        "factory": {
            "deadline_tick": deadline,
            "research_deadline_tick": tick + research_horizon,
            "receipts": {"native:paid:iron-plate": {"quantity": 1, "tick": 7}},
        },
        "history": [{"kind": "blocked_recovery_wait", "tick": tick},
                    {"kind": "native_receipt", "tick": 7, "item": "iron-plate"}],
    }
    receipt = f"{tick}:factory_insert:utility:lab:{receipt_item}"
    plan = {
        "id": "factory:factory_insert:utility:lab",
        "goal": "rocket_launch",
        "steps": [{"action": "factory_insert", "item": receipt_item,
                   "threshold": 0, "timeout_ticks": 1800,
                   "parameters": {"role": "utility:lab", "item": receipt_item,
                                  "quantity": quantity, "receipt": receipt}}],
    }
    wait = {
        "id": f"background-wait:{tick}:factory_insert:utility:lab:iron-plate",
        "goal": "rocket_launch",
        "steps": [{"action": "factory_wait", "timeout_ticks": deadline - tick}],
    }
    return state, [plan, wait]


def _fingerprint(tick: int, **changes) -> str:
    state, plans = _inputs(tick, **changes)
    return persistence.decision_input_sha256(
        state, plans, session_id="campaign-session", source_revision=SOURCE,
        target="rocket_launch", policy="jev", confidence_floor=0.45,
        current_tick=tick)


def test_clock_derived_receipts_and_absolute_horizons_do_not_retrigger_selection():
    baseline = _fingerprint(10)
    assert _fingerprint(110) == baseline
    assert _fingerprint(1010) == baseline

    # A changed absolute horizon, paid receipt identity, quantity, or inventory
    # is decision evidence and must earn one new fingerprint.
    assert _fingerprint(110, deadline=1001) != baseline
    assert _fingerprint(110, research_horizon=989) != baseline
    assert _fingerprint(110, receipt_item="copper-plate") != baseline
    assert _fingerprint(110, quantity=2) != baseline
    assert _fingerprint(110, inventory=3) != baseline


def test_native_receipt_existence_and_item_identity_remain_in_fingerprint():
    state, plans = _inputs(10)
    before = persistence.decision_input_sha256(
        state, plans, session_id="campaign-session", source_revision=SOURCE,
        target="rocket_launch", policy="jev", confidence_floor=0.45,
        current_tick=10)
    state["factory"]["receipts"]["native:paid:copper-plate"] = {
        "quantity": 1, "tick": 10}
    after = persistence.decision_input_sha256(
        state, plans, session_id="campaign-session", source_revision=SOURCE,
        target="rocket_launch", policy="jev", confidence_floor=0.45,
        current_tick=10)
    assert after != before


def test_recovery_ledger_is_durable_unique_and_fail_closed_on_unseeded_block(tmp_path):
    memory = CampaignMemory("campaign-session", "rocket_launch", status="blocked",
                            reason="low choice confidence", stalled_decisions=5)
    with pytest.raises(ValueError, match="prior durable recovery attempt"):
        persistence.validate_memory_state(memory, SOURCE)

    persistence.record_attempt(memory, SOURCE, "a" * 64, memory.reason, 90)
    path = tmp_path / "checkpoint.json"
    memory.save(path)
    restored = CampaignMemory.load(path, memory.session_id, memory.target)
    persistence.validate_memory_state(restored, SOURCE)
    assert restored.stalled_decisions == 5
    assert restored.blocked_recovery["attempts"] == memory.blocked_recovery["attempts"]
    assert persistence.was_attempted(restored, SOURCE, "a" * 64)
    with pytest.raises(ValueError, match="already attempted"):
        persistence.record_attempt(restored, SOURCE, "a" * 64, memory.reason, 91)


def test_changed_source_requires_explicit_one_use_authorization():
    memory = CampaignMemory("campaign-session", "rocket_launch", status="blocked",
                            reason="Candidate evidence insufficient", stalled_decisions=4)
    persistence.record_attempt(memory, SOURCE, "a" * 64, memory.reason, 90)
    newer = {"commit": "3" * 40, "source_sha256": "d" * 64}
    with pytest.raises(ValueError, match="source changed"):
        persistence.validate_memory_state(memory, newer)
    persistence.validate_memory_state(memory, newer, allow_source_change=True)
    assert not persistence.was_attempted(memory, newer, "b" * 64,
                                         allow_source_change=True)
    assert memory.blocked_recovery["source_revision"] == SOURCE
    persistence.record_attempt(memory, newer, "b" * 64, memory.reason, 91,
                               allow_source_change=True)
    assert memory.blocked_recovery["source_revision"] == newer
    assert len(memory.blocked_recovery["attempts"]) == 2


def test_wait_backoff_caps_at_five_minutes_and_keeps_blocked_status():
    memory = CampaignMemory("campaign-session", "rocket_launch", status="blocked",
                            reason="low choice confidence", stalled_decisions=5)
    persistence.record_attempt(memory, SOURCE, "a" * 64, memory.reason, 90)
    delays = []
    for _ in range(12):
        delays.append(persistence.record_wait(memory, SOURCE, "a" * 64))
    assert delays[:4] == [4.0, 8.0, 16.0, 32.0]
    assert delays[-1] == 300.0
    assert memory.status == "blocked" and memory.stalled_decisions == 5


class LiveMockBackend(MockBackend):
    def __init__(self):
        super().__init__()
        self.actions = []
        self.observations = 0

    def observe(self):
        self.observations += 1
        return replace(super().observe(), world_kind="fle")

    def act(self, action):
        self.actions.append(action)
        return super().act(action)


class LiveClient:
    model = "selection-test-double"
    is_mock = False
    uses_http_provider = False


def test_persistent_controller_skips_same_tick_only_retry_and_retries_changed_inventory(
        tmp_path, monkeypatch):
    import jev_factorio.controller as controller

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining", last_tick=0,
                            status="blocked", reason="low choice confidence",
                            stalled_decisions=5, failures={"old-plan": 2},
                            history=[{"kind": "preserved", "marker": "history"}])
    persistence.record_attempt(memory, SOURCE, "f" * 64, memory.reason, 0)
    memory.save(checkpoint)
    loop = HierarchicalLoop(
        backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
        persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    loop._work_candidates = lambda snapshot: ([Plan(
        "lab-insert", "bootstrap_mining", "Insert a paid lab item",
        (Step("factory_insert", "transfer", parameters={
            "role": "utility:lab", "item": "iron-plate", "quantity": 1,
            "receipt": f"{snapshot.tick}:factory_insert:utility:lab:iron-plate",
        }),))], "")

    calls = []

    def reject(_client, _state, plans, *_args):
        calls.append(plans[0].id)
        return Decision(None, "observe", "low choice confidence", model_called=True,
                        diagnostics={"schema": 1, "outcome": "all_candidates_rejected"})

    monkeypatch.setattr(controller, "select_plan", reject)
    first = loop.step()
    assert first["status"] == "blocked" and first["model_call"] is True
    assert loop.memory.stalled_decisions == 6
    assert len(loop.memory.blocked_recovery["attempts"]) == 2
    assert backend.actions == []

    # The next observation has a different Factorio tick and a correspondingly
    # new planned receipt, but no changed game fact. It must not buy a call.
    backend.tick = 100
    waiting = loop.step()
    assert waiting["persistent_recovery"]["phase"] == "waiting_for_changed_game_evidence"
    assert waiting["model_call"] is False
    assert len(calls) == 1
    assert loop.memory.stalled_decisions == 6

    # New native inventory is meaningful evidence and earns one durable attempt.
    backend.tick = 101
    backend.inv["iron-ore"] = backend.inv.get("iron-ore", 0) + 1
    changed = loop.step()
    assert changed["model_call"] is True
    assert len(calls) == 2
    assert len(loop.memory.blocked_recovery["attempts"]) == 3
    assert loop.memory.stalled_decisions == 7
    assert loop.memory.failures == {"old-plan": 2}
    assert loop.memory.history[0] == {"kind": "preserved", "marker": "history"}
    saved = CampaignMemory.load(checkpoint, backend.session_id, "bootstrap_mining")
    assert len(saved.blocked_recovery["attempts"]) == 3
    assert saved.status == "blocked" and saved.stalled_decisions == 7


def test_running_decision_that_reaches_blocked_threshold_is_seeded_before_wait(
        tmp_path, monkeypatch):
    import jev_factorio.controller as controller

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining", last_tick=0,
                            status="running", stalled_decisions=3,
                            failures={"old-plan": 2})
    memory.save(checkpoint)
    loop = HierarchicalLoop(
        backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
        persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    loop._work_candidates = lambda snapshot: ([Plan(
        "lab-insert", "bootstrap_mining", "Insert a paid lab item",
        (Step("factory_insert", "transfer", parameters={
            "role": "utility:lab", "item": "iron-plate", "quantity": 1,
            "receipt": f"{snapshot.tick}:factory_insert:utility:lab:iron-plate",
        }),))], "")
    calls = []

    def reject(_client, _state, plans, *_args):
        calls.append(plans[0].id)
        return Decision(None, "observe", "low choice confidence", model_called=True,
                        diagnostics={"schema": 1, "outcome": "all_candidates_rejected"})

    monkeypatch.setattr(controller, "select_plan", reject)
    first = loop.step()
    assert first["status"] == "blocked" and first["model_call"] is True
    assert first["persistent_recovery"]["phase"] == "waiting_for_changed_game_evidence"
    assert loop.memory.stalled_decisions == 4
    assert len(loop.memory.blocked_recovery["attempts"]) == 1
    assert loop.memory.failures == {"old-plan": 2}

    # The first eligible blocked request was ordinary while status was running.
    # Its exact rejected input is still saved before the next observation cycle.
    backend.tick = 100
    waiting = loop.step()
    assert waiting["persistent_recovery"]["phase"] == "waiting_for_changed_game_evidence"
    assert waiting["model_call"] is False
    assert len(calls) == 1
    saved = CampaignMemory.load(checkpoint, backend.session_id, "bootstrap_mining")
    assert len(saved.blocked_recovery["attempts"]) == 1
    assert saved.status == "blocked" and saved.stalled_decisions == 4


def test_running_threshold_request_is_write_ahead_and_never_replayed_after_crash(
        tmp_path, monkeypatch):
    import jev_factorio.controller as controller

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining", last_tick=0,
                            status="running", reason="prior running reason",
                            stalled_decisions=3,
                            history=[{"kind": "preserved", "marker": "pre-crash"}])
    memory.save(checkpoint)

    def make_loop():
        loop = HierarchicalLoop(
            backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
            checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
            persist_recoverable_blocks=True)
        if loop._safety is not None:
            loop._safety.admission = lambda *_args: None
        loop._work_candidates = lambda snapshot: ([Plan(
            "lab-insert", "bootstrap_mining", "Insert a paid lab item",
            (Step("factory_insert", "transfer", parameters={
                "role": "utility:lab", "item": "iron-plate", "quantity": 1,
                "receipt": f"{snapshot.tick}:factory_insert:utility:lab:iron-plate",
            }),))], "")
        return loop

    loop = make_loop()
    calls = []

    def crash_after_write_ahead(_client, _state, _plans, *_args):
        calls.append("first")
        saved = CampaignMemory.load(checkpoint, backend.session_id, "bootstrap_mining")
        assert saved.status == "running" and saved.reason == "prior running reason"
        assert saved.stalled_decisions == 3
        assert saved.blocked_recovery["attempts"][-1]["outcome"] == "pending"
        raise RuntimeError("simulated process interruption after request start")

    monkeypatch.setattr(controller, "select_plan", crash_after_write_ahead)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        loop.step()

    # A new process sees the durable pending fingerprint. It observes again,
    # but does not repeat a provider call whose answer may have been lost.
    resumed = make_loop()

    def forbidden_replay(*_args):
        calls.append("replayed")
        raise AssertionError("same-input provider request was replayed")

    monkeypatch.setattr(controller, "select_plan", forbidden_replay)
    record = resumed.step()
    assert calls == ["first"]
    assert record["status"] == "running"
    assert record["reason"] == "prior running reason"
    assert record["outcome"] == "Decision outcome unresolved; observing for changed evidence"
    assert record["persistent_recovery"]["phase"] == "evaluation_outcome_unknown_waiting"
    assert record["persistent_recovery"]["model_call"] is False
    assert resumed.memory.stalled_decisions == 3
    assert resumed.memory.history[0] == {"kind": "preserved", "marker": "pre-crash"}

    # A genuinely changed native fact makes a new request eligible even though
    # the earlier response remains unknown; its exact old input is never replayed.
    backend.tick = 101
    backend.inv["iron-ore"] = backend.inv.get("iron-ore", 0) + 1

    def reject_changed_input(_client, _state, _plans, *_args):
        calls.append("changed")
        return Decision(None, "observe", "low choice confidence", model_called=True,
                        diagnostics={"schema": 1, "outcome": "all_candidates_rejected"})

    monkeypatch.setattr(controller, "select_plan", reject_changed_input)
    changed = resumed.step()
    assert calls == ["first", "changed"]
    assert changed["status"] == "blocked"
    assert len(resumed.memory.blocked_recovery["attempts"]) == 2
    assert resumed.memory.blocked_recovery["attempts"][0]["outcome"] == "pending"
    assert resumed.memory.blocked_recovery["attempts"][1]["outcome"] == "rejected"


def test_running_threshold_checkpoint_failure_stops_before_model_call(tmp_path, monkeypatch):
    import jev_factorio.controller as controller

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining", last_tick=0,
                            status="running", reason="prior running reason",
                            stalled_decisions=3)
    memory.save(checkpoint)
    original_bytes = checkpoint.read_bytes()
    loop = HierarchicalLoop(
        backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
        persist_recoverable_blocks=True)
    loop._work_candidates = lambda snapshot: ([Plan(
        "lab-insert", "bootstrap_mining", "Insert a paid lab item",
        (Step("factory_insert", "transfer", parameters={
            "role": "utility:lab", "item": "iron-plate", "quantity": 1,
            "receipt": f"{snapshot.tick}:factory_insert:utility:lab:iron-plate",
        }),))], "")
    calls = []

    def forbidden_call(*_args):
        calls.append(True)
        raise AssertionError("selection must follow the durable write-ahead save")

    monkeypatch.setattr(controller, "select_plan", forbidden_call)

    def fail_save(_self, _path):
        raise OSError("injected pre-request checkpoint failure")

    monkeypatch.setattr(CampaignMemory, "save", fail_save)
    with pytest.raises(OSError, match="pre-request checkpoint failure"):
        loop.step()

    assert calls == []
    assert checkpoint.read_bytes() == original_bytes
    assert loop.memory.status == "running"
    assert loop.memory.reason == "prior running reason"
    assert loop.memory.stalled_decisions == 3
    assert loop.memory.blocked_recovery is None


def test_until_complete_waits_for_changed_evidence_and_stops_on_interrupt(tmp_path, monkeypatch):
    import jev_factorio.controller as controller
    import jev_factorio.loop as loop_module

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining", last_tick=0,
                            status="blocked", reason="Candidate evidence insufficient",
                            stalled_decisions=5)
    persistence.record_attempt(memory, SOURCE, "e" * 64, memory.reason, 0)
    memory.save(checkpoint)
    loop = HierarchicalLoop(
        backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
        persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    loop._work_candidates = lambda snapshot: ([Plan(
        "same-plan", "bootstrap_mining", "Same candidate",
        (Step("walk_to_iron", "near", "iron-ore"),))], "")
    requests = []

    def reject(*_args):
        requests.append(True)
        return Decision(None, "observe", "Candidate evidence insufficient",
                        model_called=True,
                        diagnostics={"schema": 1, "outcome": "all_candidates_rejected"})

    monkeypatch.setattr(controller, "select_plan", reject)
    waits = []

    def interrupt_after_observation_cycle(delay):
        waits.append(delay)
        if len(waits) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(loop_module, "_interruptible_sleep", interrupt_after_observation_cycle)
    with pytest.raises(KeyboardInterrupt):
        loop.run(until_complete=True)

    assert backend.observations == 2
    assert requests == [True]
    assert waits == [2.0, 4.0]
    assert loop.memory.status == "blocked" and loop.memory.stalled_decisions == 6
    assert loop.memory.active_plan is None and backend.actions == []


def test_persistent_selection_enters_normal_verified_execution_without_reset_shortcut(
        tmp_path, monkeypatch):
    import jev_factorio.controller as controller

    monkeypatch.setattr(controller, "gameplay_context", lambda: {"code_revision": SOURCE})
    backend = LiveMockBackend()
    checkpoint = tmp_path / "checkpoint.json"
    memory = CampaignMemory(backend.session_id, "bootstrap_mining",
                            active_goal="bootstrap_mining",
                            completed_goals={"stockpile_fuel": 0}, last_tick=0,
                            status="blocked", reason="low choice confidence",
                            stalled_decisions=5, failures={"old-plan": 2})
    persistence.record_attempt(memory, SOURCE, "d" * 64, memory.reason, 0)
    memory.save(checkpoint)
    loop = HierarchicalLoop(
        backend, jev=LiveClient(), policy="jev", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0,
        persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    plan = Plan("walk-to-iron", "bootstrap_mining", "Approach iron ore",
                (Step("walk_to_iron", "near", "iron-ore"),))
    loop._work_candidates = lambda _snapshot: ([plan], "")
    selections = []

    def choose(_client, _state, plans, *_args):
        selections.append(plans[0].id)
        return Decision(plans[0].id, "jev", model_called=True,
                        diagnostics={"schema": 1, "outcome": "selected"})

    monkeypatch.setattr(controller, "select_plan", choose)
    record = loop.step()

    assert selections == [plan.id]
    assert backend.actions == ["walk_to_iron"]
    assert record["verified"] is True
    assert loop.memory.status == "running"
    assert loop.memory.active_plan is None and loop.memory.pending is None
    assert loop.memory.stalled_decisions == 0  # Only the ordinary verified receipt clears it.
    assert loop.memory.failures == {"old-plan": 2}
    saved = CampaignMemory.load(checkpoint, backend.session_id, "bootstrap_mining")
    assert saved.blocked_recovery["attempts"][-1]["reason"] == "low choice confidence"
    assert saved.attempt_outcomes[-1]["outcome"] == "verified"


def test_persistent_mode_needs_a_continuous_live_resume_configuration():
    from dataclasses import asdict
    from jev_factorio.research_log import RunConfiguration, _configuration

    valid = RunConfiguration(
        backend="fle", controller="hierarchical", policy="jev", target="rocket_launch",
        resume=True, resume_controller=True, checkpoint_enabled=True, until_complete=True,
        persist_recoverable_blocks=True)
    _configuration(asdict(valid))
    with pytest.raises(ValueError, match="Persistent blocked recovery"):
        _configuration(asdict(replace(valid, until_complete=False)))
    with pytest.raises(ValueError, match="Persistent blocked recovery"):
        _configuration(asdict(replace(valid, backend="mock")))
