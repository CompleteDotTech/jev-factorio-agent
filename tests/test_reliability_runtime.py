"""Fault injection against actual controller code with synthetic worlds only."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import errno
import json

import pytest

from jev_factorio.backends.mock import MockBackend
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.causal_trace import TraceStorageError
from jev_factorio.operational_safety import (RuntimeSafety, SafetyStateError, StoragePressure,
    atomic_json, read_json, request_maintenance, safety_dir, storage_ready, verify_quiescence)
from jev_factorio.operations import release
from jev_factorio.recovery_policy import record_exit
from attempt_helpers import ReceiptBackend, controller as transfer_controller, install_plan
from test_background_work import ReceiptBackend as CraftBackend, controller as craft_controller
from test_attempts import prepared_native_transfer_checkpoint


def test_maximum_maintenance_timeout_survives_advancing_clock(tmp_path):
    from jev_factorio.operational_safety import maintenance_request
    checkpoint = tmp_path / 'checkpoint.json'
    atomic_json(checkpoint, {'session_id': 'same-session'})
    now = [1790417240.0]
    def advancing_clock():
        result = now[0]
        now[0] += 0.000001
        return result
    created = request_maintenance(checkpoint, timeout=3600, clock=advancing_clock)
    # Exercise the reader that previously rejected a writer-created request.
    accepted = maintenance_request(checkpoint, 'same-session')
    assert accepted == created
    assert accepted['deadline'] - accepted['requested_at'] == 3600


class DeferredBackend(MockBackend):
    def __init__(self, mining=False):
        super().__init__()
        if mining:
            self.at_resource = "coal"
        self.commands = []
        self.owned = None

    def act(self, action):
        if action == "idle":
            return super().act(action)
        self.commands.append(action)
        self.owned = action
        return "Synthetic owned action still running"

    def complete(self):
        result = super().act(self.owned)
        self.owned = None
        return result


@pytest.mark.parametrize("mining", [False, True])
def test_maintenance_restart_drains_walk_or_mining_without_new_mutation(tmp_path, mining):
    backend = DeferredBackend(mining)
    checkpoint = tmp_path / "checkpoint.json"
    first = HierarchicalLoop(backend, policy="deterministic", target="bootstrap_mining",
                             checkpoint=str(checkpoint), tick_seconds=0)
    first.step()
    attempt = deepcopy(first.memory.attempt)
    request = request_maintenance(checkpoint)
    restored = HierarchicalLoop(backend, policy="deterministic", target="bootstrap_mining",
        checkpoint=str(checkpoint), resume_controller=True, tick_seconds=0)
    restored.step()
    assert len(backend.commands) == 1
    assert restored.memory.attempt["id"] == attempt["id"]
    assert read_json(safety_dir(checkpoint) / "health.json")["phase"] == "quiescing"
    with pytest.raises(SafetyStateError):
        verify_quiescence(checkpoint, request["request_id"])
    backend.complete()
    restored.step()
    restored.step()
    assert restored.memory.pending is None
    assert verify_quiescence(checkpoint, request["request_id"])["phase"] == "quiescent"
    assert len(backend.commands) == 1 and restored.memory.failures == {}
    release(checkpoint, request["request_id"])
    restored.step()
    assert len(backend.commands) == 2


def test_restart_during_transfer_verifies_exact_receipt_without_redispatch(tmp_path, monkeypatch):
    backend = ReceiptBackend("delayed")
    install_plan(monkeypatch, backend)
    first = transfer_controller(tmp_path, backend)
    first.step()
    attempt = deepcopy(first.memory.attempt)
    checkpoint = tmp_path / "checkpoint.json"
    request = request_maintenance(checkpoint)
    restored = transfer_controller(tmp_path, backend, resume=True)
    restored.step()
    assert len(backend.calls) == 1 and restored.memory.attempt["id"] == attempt["id"]
    backend.publish()
    restored.step()
    restored.step()
    assert len(backend.calls) == 1
    assert restored.memory.attempt_outcomes[-1]["id"] == attempt["id"]
    assert verify_quiescence(checkpoint, request["request_id"])


def test_prepared_transfer_recovery_cannot_cross_maintenance_gate(tmp_path):
    backend = ReceiptBackend("immediate")
    backend.state.world_kind = "fle"
    backend.state.factory["player_bound"] = True
    original = prepared_native_transfer_checkpoint(tmp_path, backend)
    request_maintenance(tmp_path / "checkpoint.json")
    restored = transfer_controller(tmp_path, backend, resume=True)
    restored.step()
    assert backend.calls == []
    assert restored.memory.attempt["id"] == original.attempt["id"]
    assert restored.memory.pending["dispatch"] == "prepared"
    assert restored.memory.reservations == original.reservations


def test_background_crafting_drains_before_acknowledgement(tmp_path):
    backend = CraftBackend()
    backend.state._native_controls = {"walking": False, "mining": False, "status": "idle"}
    backend.state.factory["player_bound"] = True
    first = craft_controller(backend, tmp_path)
    first.step()
    checkpoint = tmp_path / "state.json"
    request = request_maintenance(checkpoint)
    restored = craft_controller(backend, tmp_path, resume=True)
    restored.step()
    assert len(backend.calls) == 1 and restored.memory.background_job
    with pytest.raises(SafetyStateError):
        verify_quiescence(checkpoint, request["request_id"])
    backend.complete()
    restored.step()
    assert len(backend.calls) == 1 and restored.memory.background_job is None
    assert verify_quiescence(checkpoint, request["request_id"])


def test_native_controls_missing_cannot_be_acknowledged_as_idle(tmp_path):
    backend = ReceiptBackend()
    backend.state.world_kind = "fle"
    backend.state.factory.update(player_bound=True, crafting_queue=0)
    loop = transfer_controller(tmp_path, backend)
    current = loop._observe()
    loop._save()
    request_maintenance(loop.checkpoint)
    assert loop._safety.maintenance(loop.memory, current) == "quiescing"
    current._native_controls = {"status": "completed", "walking": False, "mining": False}
    assert loop._safety.maintenance(loop.memory, current) == "quiescent"


def test_timeout_keeps_admission_closed_and_original_request(tmp_path):
    backend = DeferredBackend()
    loop = HierarchicalLoop(backend, policy="deterministic", target="bootstrap_mining",
                             checkpoint=str(tmp_path / "cp.json"), tick_seconds=0)
    loop.step()
    request = request_maintenance(loop.checkpoint, timeout=1, clock=lambda: 100)
    loop._safety.clock = lambda: 102
    loop.step()
    assert len(backend.commands) == 1
    assert read_json(safety_dir(loop.checkpoint) / "maintenance.json") == request
    assert read_json(safety_dir(loop.checkpoint) / "health.json")["phase"] == "quiescence_timeout"


def test_wrong_session_and_corrupt_request_fail_closed(tmp_path):
    backend = DeferredBackend()
    loop = HierarchicalLoop(backend, policy="deterministic", target="bootstrap_mining",
                             checkpoint=str(tmp_path / "cp.json"), tick_seconds=0)
    loop._observe(); loop._save()
    request = request_maintenance(loop.checkpoint)
    request["session_id"] = "another-world"
    atomic_json(safety_dir(loop.checkpoint) / "maintenance.json", request)
    with pytest.raises(SafetyStateError):
        loop.step()
    assert backend.commands == []


def test_storage_gate_pauses_without_consuming_gameplay_budgets(tmp_path, monkeypatch):
    backend = DeferredBackend()
    loop = HierarchicalLoop(backend, policy="deterministic", target="bootstrap_mining",
                             checkpoint=str(tmp_path / "cp.json"), tick_seconds=0)
    monkeypatch.setattr("jev_factorio.operational_safety.storage_ready", lambda *a, **k: False)
    loop.step(); loop.step()
    assert backend.commands == [] and loop.memory.pending is None
    assert loop.memory.failures == {} and loop.memory.stalled_decisions == 0
    assert read_json(safety_dir(loop.checkpoint) / "health.json")["phase"] == "storage_pressure"
    monkeypatch.setattr("jev_factorio.operational_safety.storage_ready", lambda *a, **k: True)
    loop.step()
    assert len(backend.commands) == 1


def test_final_storage_guard_records_proven_pre_dispatch_rejection(tmp_path, monkeypatch):
    backend = ReceiptBackend()
    install_plan(monkeypatch, backend)
    loop = transfer_controller(tmp_path, backend)
    def deny():
        raise StoragePressure()
    loop._trace.admission_check = deny
    record = loop.step()
    assert backend.calls == [] and loop.memory.pending is None
    assert loop.memory.active_plan is not None
    assert loop.memory.failures == {}
    assert record["attempt_outcomes"][-1]["outcome"] == "storage_preflight_rejected"


@pytest.mark.parametrize("failure_event,effect", [("action_prepared", False), ("action_returned", True)])
def test_enospc_keeps_prepared_action_and_never_blindly_replays(tmp_path, monkeypatch, failure_event, effect):
    class Sink:
        def emit(self, event, payload):
            if event == failure_event:
                raise OSError(errno.ENOSPC, "secret storage information")
    backend = ReceiptBackend()
    install_plan(monkeypatch, backend)
    first = transfer_controller(tmp_path, backend, research_log=Sink())
    with pytest.raises(TraceStorageError) as caught:
        first.step()
    saved = json.loads(first.checkpoint.read_text())
    assert saved["pending"] and saved["attempt"]
    assert bool(backend.calls) is effect
    assert caught.value.native_effect_possible is effect
    record_exit(first, caught.value)
    exit_data = read_json(safety_dir(first.checkpoint) / "exit.json")
    assert exit_data["failure_class"] == "storage_pressure"
    assert "secret" not in json.dumps(exit_data)
    restored = transfer_controller(tmp_path, backend, resume=True)
    restored.step()
    assert len(backend.calls) == int(effect)
    assert (restored.memory.pending is None) is effect


def test_low_inodes_are_storage_pressure(monkeypatch, tmp_path):
    monkeypatch.setattr("jev_factorio.operational_safety.storage_sample", lambda p:
        {"free_bytes": 10**12, "total_bytes": 10**13, "free_inodes": 0, "total_inodes": 100})
    assert not storage_ready([tmp_path])

def test_dashboard_filesystem_is_checked_before_live_attachment(tmp_path, monkeypatch):
    import sys
    from jev_factorio import main, operational_safety
    dashboard = tmp_path / "separate-volume" / "events.jsonl"
    seen = []
    def ready(paths, **kwargs):
        seen.append(tuple(paths))
        return dashboard.parent not in paths
    monkeypatch.setattr(operational_safety, "storage_ready", ready)
    monkeypatch.setattr(main, "make_backend", lambda *a, **k: pytest.fail("Attached before storage guard"))
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--controller", "hierarchical",
        "--backend", "fle", "--policy", "deterministic", "--tick-seconds", "1", "--target", "bootstrap_mining",
        "--checkpoint", str(tmp_path / "checkpoint.json"), "--dashboard-events", str(dashboard)])
    with pytest.raises(SystemExit) as stopped:
        main.cli()
    assert stopped.value.code == 2
    assert seen and dashboard.parent in seen[-1]


def test_dashboard_filesystem_remains_in_runtime_admission(tmp_path, monkeypatch):
    import sys
    from jev_factorio import main, operational_safety
    dashboard = tmp_path / "separate-volume" / "events.jsonl"
    seen = []
    def ready(paths, **kwargs):
        seen.append(tuple(paths))
        return True
    monkeypatch.setattr(operational_safety, "storage_ready", ready)
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--controller", "hierarchical",
        "--backend", "mock", "--mock-model", "--target", "bootstrap_mining",
        "--steps", "1", "--tick-seconds", "0",
        "--checkpoint", str(tmp_path / "checkpoint.json"), "--dashboard-events", str(dashboard)])
    main.cli()
    assert seen and all(dashboard.parent in paths for paths in seen)
