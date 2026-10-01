"""Archive-aware restore across the solid treatment composition."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest

from jev_factorio import blocked_persistence
from jev_factorio.blocked_recovery_archive import archive_full_tail
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.judgments import Decision
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_controller import solid_loop_type
from test_solid_route_integration import Backend, FoundationScenario, INTENTS


SESSION = "solid-fixture"
TARGET = "rocket_launch"
OLD_SOURCE = {"commit": "1" * 40, "source_sha256": "a" * 64}
NEW_SOURCE = {"commit": "2" * 40, "source_sha256": "b" * 64}
DUPLICATE_INPUT = hashlib.sha256(b"archived duplicate decision").hexdigest()
ACTIVE_INPUT = hashlib.sha256(b"active tail decision").hexdigest()
ACTIVE_INPUT_2 = hashlib.sha256(b"second active tail decision").hexdigest()
NEW_INPUT = hashlib.sha256(b"changed-source decision").hexdigest()


SolidLoop = solid_loop_type(FoundationScenario)


class LiveClient:
    model = "solid-archive-selection-fixture"
    is_mock = False
    uses_http_provider = False


class CountingBackend(Backend):
    def __init__(self):
        super().__init__()
        self.enable_factory_calls = 0
        self.actions = []

    def enable_factory(self):
        self.enable_factory_calls += 1
        return super().enable_factory()

    def act(self, action):
        self.actions.append(action)
        return super().act(action)


def _allow_archive_storage(monkeypatch):
    from jev_factorio import operational_safety
    monkeypatch.setattr(operational_safety, "storage_ready", lambda *_args, **_kwargs: True)


def _saved_archived_checkpoint(tmp_path, monkeypatch):
    _allow_archive_storage(monkeypatch)
    path = Path(tmp_path) / "solid-checkpoint.json"
    memory = SolidLoop.memory_type(
        SESSION, TARGET, active_goal=TARGET, last_tick=2000,
        status="blocked", reason="low choice confidence", stalled_decisions=1026,
        solid_intents=deepcopy(INTENTS),
        solid_epoch={"actor_index": 1, "surface_index": 1, "force_index": 1},
        blocked_recovery={
            "schema": 1, "session_id": SESSION,
            "source_revision": deepcopy(OLD_SOURCE), "attempts": [],
            "last_input_sha256": None, "wait_level": 0,
        })
    attempts = []
    for sequence in range(1024):
        digest = (DUPLICATE_INPUT if sequence == 77 else
                  hashlib.sha256(f"archived decision {sequence}".encode()).hexdigest())
        attempts.append({
            "source_revision": deepcopy(OLD_SOURCE),
            "decision_input_sha256": digest,
            "reason": "low choice confidence",
            "tick": sequence + 1,
            "outcome": "rejected",
        })
    memory.blocked_recovery["attempts"] = attempts
    memory.blocked_recovery["last_input_sha256"] = attempts[-1]["decision_input_sha256"]
    memory.save(path)

    index = archive_full_tail(path, memory)
    try:
        blocked_persistence.record_attempt(
            memory, OLD_SOURCE, ACTIVE_INPUT, memory.reason, 1500,
            archive_index=index)
        blocked_persistence.record_attempt(
            memory, OLD_SOURCE, ACTIVE_INPUT_2, memory.reason, 1501,
            archive_index=index)
        memory.save(path)
        pointer = deepcopy(memory.blocked_recovery_archive)
    finally:
        index.close()
    return path, pointer


def _backend():
    backend = CountingBackend()
    backend.state.world_kind = "fle"
    backend.state.tick = 2000
    backend.state.factory["solid_routes"]["tick"] = 2000
    backend.checkpoint = None
    return backend


def _resume(path, backend, **options):
    return SolidLoop(
        backend, jev=LiveClient(), policy="jev", target=TARGET,
        factory_scheduling="ready-work", tick_seconds=0,
        checkpoint=str(path), resume_controller=True,
        solid_intents=INTENTS, **options)


def _simple_plan():
    return Plan("fixture-plan", TARGET, "Fixture candidate", (
        Step("factory_insert", "transfer", parameters={
            "role": "utility:lab", "item": "iron-plate", "quantity": 1,
            "receipt": "fixture:factory_insert:utility:lab:iron-plate",
        }),))


def _stub_candidate_ranking(monkeypatch, plan):
    from jev_factorio.planning import decision_support
    monkeypatch.setattr(decision_support, "distinct_candidates", lambda plans: list(plans))
    monkeypatch.setattr(
        decision_support, "scheduling_context",
        lambda _snapshot, _catalog, plans, _goal: {
            "deterministic_ranking": [candidate.id for candidate in plans],
            "candidate_evidence": {},
        })
    monkeypatch.setattr(
        decision_support, "defer_gather_until_bill_craft",
        lambda plans, *_args: (plans, None))


def test_solid_composed_archived_duplicate_waits_without_model_or_action(tmp_path, monkeypatch):
    import jev_factorio.controller as controller_module

    monkeypatch.setattr(controller_module, "gameplay_context",
                        lambda: {"code_revision": OLD_SOURCE})
    path, pointer = _saved_archived_checkpoint(tmp_path, monkeypatch)
    backend = _backend()
    loop = _resume(path, backend, persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    plan = _simple_plan()
    loop._work_candidates = lambda _snapshot: ([plan], "")
    _stub_candidate_ranking(monkeypatch, plan)

    monkeypatch.setattr(blocked_persistence, "decision_input_sha256",
                        lambda *_args, **_kwargs: DUPLICATE_INPUT)
    selections = []
    monkeypatch.setattr(controller_module, "select_plan", lambda *_args: selections.append(True))

    record = loop.step()

    assert record["persistent_recovery"]["phase"] == "waiting_for_changed_game_evidence"
    assert record["model_call"] is False
    assert loop.memory.blocked_recovery_archive == pointer
    assert loop.memory._blocked_recovery_archive_index is loop._blocked_recovery_archive_index
    assert loop._blocked_recovery_archive_index.find(
        OLD_SOURCE, DUPLICATE_INPUT, memory=loop.memory) is not None
    assert loop._blocked_recovery_archive_index.find(
        OLD_SOURCE, ACTIVE_INPUT, memory=loop.memory) is not None
    assert selections == []
    assert backend.actions == [] and backend.calls == []


def test_solid_composed_changed_source_resume_keeps_archive_and_wal_before_model(
        tmp_path, monkeypatch):
    import jev_factorio.blocked_reevaluation as reevaluation
    import jev_factorio.controller as controller_module

    monkeypatch.setattr(controller_module, "gameplay_context",
                        lambda: {"code_revision": NEW_SOURCE})
    monkeypatch.setattr(reevaluation, "validate_source_revision", lambda _old: {
        "blocked_source_revision": OLD_SOURCE["commit"],
        "source_head": NEW_SOURCE["commit"],
        "previous_contract_sha256": "c" * 64,
        "decision_contract_sha256": "d" * 64,
    })
    path, pointer = _saved_archived_checkpoint(tmp_path, monkeypatch)
    checkpoint_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    backend = _backend()
    loop = _resume(
        path, backend, persist_recoverable_blocks=True,
        reevaluate_blocked_once=True, exact_checkpoint_sha256=checkpoint_sha256,
        blocked_source_revision=OLD_SOURCE["commit"])
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    plan = _simple_plan()
    loop._work_candidates = lambda _snapshot: ([plan], "")
    _stub_candidate_ranking(monkeypatch, plan)
    monkeypatch.setattr(blocked_persistence, "decision_input_sha256",
                        lambda *_args, **_kwargs: NEW_INPUT)

    model_calls = []

    def reject_after_wal(*_args, prepared_batch=None, **_kwargs):
        assert prepared_batch is not None
        assert len(prepared_batch[2]) == 1
        model_calls.append(True)
        saved = json.loads(path.read_text())
        assert saved["blocked_recovery_archive"] == pointer
        assert saved["blocked_recovery"]["source_revision"] == NEW_SOURCE
        attempt = saved["blocked_recovery"]["attempts"][-1]
        assert {key: attempt[key] for key in (
            "source_revision", "decision_input_sha256", "reason", "tick", "outcome")} == {
            "source_revision": NEW_SOURCE,
            "decision_input_sha256": NEW_INPUT,
            "reason": "low choice confidence",
            "tick": 2000,
            "outcome": "pending",
        }
        assert attempt["selection_batch"]["offered"]
        assert len(attempt["selection_batch"]["request_sha256"]) == 64
        assert saved["blocked_reevaluations"][-1]["state"] == "consumed"
        assert saved["stalled_decisions"] == 1026
        assert saved["failures"] == {}
        assert loop._blocked_recovery_archive_index.find(
            OLD_SOURCE, DUPLICATE_INPUT, memory=loop.memory) is not None
        return Decision(None, "observe", "low choice confidence", model_called=True,
                        diagnostics={"schema": 1, "outcome": "all_candidates_rejected"})

    monkeypatch.setattr(controller_module, "select_plan", reject_after_wal)
    record = loop.step()

    assert model_calls == [True]
    assert record["status"] == "blocked"
    assert loop.memory.stalled_decisions == 1027
    assert loop.memory.blocked_recovery_archive == pointer
    assert loop.memory.blocked_recovery["attempts"][-1]["outcome"] == "rejected"
    assert backend.actions == [] and backend.calls == []


def test_solid_composed_restore_keeps_persistent_source_gate(tmp_path, monkeypatch):
    import jev_factorio.controller as controller_module

    monkeypatch.setattr(controller_module, "gameplay_context",
                        lambda: {"code_revision": NEW_SOURCE})
    path, _pointer = _saved_archived_checkpoint(tmp_path, monkeypatch)
    backend = _backend()
    loop = _resume(path, backend, persist_recoverable_blocks=True)
    if loop._safety is not None:
        loop._safety.admission = lambda *_args: None
    model_calls = []
    monkeypatch.setattr(controller_module, "select_plan",
                        lambda *_args: model_calls.append(True))

    with pytest.raises(ValueError, match="source changed"):
        loop.step()

    assert model_calls == []
    assert loop.memory is None
    assert backend.actions == [] and backend.calls == []


def test_corrupt_solid_archive_fails_before_factory_install_or_observation(tmp_path, monkeypatch):
    path, pointer = _saved_archived_checkpoint(tmp_path, monkeypatch)
    segment = path.with_name(path.name + ".blocked-recovery-archive") / (
        f"segment-{pointer['head_sha256']}.json")
    with segment.open("ab") as output:
        output.write(b"corrupt")

    backend = _backend()
    with pytest.raises(ValueError, match="segment hash mismatch"):
        _resume(path, backend, persist_recoverable_blocks=True)

    assert backend.enable_factory_calls == 0
    assert backend.observations == 0
    assert backend.actions == [] and backend.calls == []
