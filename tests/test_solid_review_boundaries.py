"""Regression reproductions for PR #112 review; no native connection or provider."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import json
from types import SimpleNamespace

import pytest

from jev_factorio.memory import load_checkpoint
from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
from jev_factorio.solid_controller import solid_loop_type
from solid_routes_fixtures import INTENTS
from test_solid_route_integration import Backend, FoundationScenario, Loop, controller, native_catalog


def resume(backend, path, kind=Loop):
    return kind(backend, target="rocket_launch", policy="deterministic",
                factory_scheduling="ready-work", tick_seconds=0, checkpoint=str(path),
                resume_controller=True, solid_intents=INTENTS)


class AttachmentSpy(Backend):
    def __init__(self):
        super().__init__()
        self.attachments = []

    def enable_factory(self):
        self.attachments.append("enable_factory")
        self._factory = SimpleNamespace(
            command=lambda _: self.attachments.append("install"),
            call=lambda action, _: self.attachments.append(action))
        return native_catalog()


@pytest.mark.parametrize("field,value", [
    ("version", 999), ("solid_routes_schema", 0),
    ("solid_epoch", {"actor_index": 1}), ("solid_epoch", []),
    ("solid_epoch", {"actor_index": True, "surface_index": 1, "force_index": 1}),
    ("solid_commitments", {"unowned": {}}), ("pending", {"dispatch": "prepared"}),
    ("failures", {"old-project": -1}), ("active_plan", {"id": "invalid"}),
    ("session_id", ""), ("target", "steam_power"),
])
def test_full_resume_validation_precedes_any_backend_attachment(tmp_path, field, value):
    origin = Backend(); loop = controller(origin, tmp_path)
    loop.memory.active_goal = "rocket_launch"
    loop._observe()
    path = origin.checkpoint
    data = json.loads(path.read_text()); data[field] = value
    path.write_text(json.dumps(data)); original = path.read_bytes()
    backend = AttachmentSpy()
    with pytest.raises((ValueError, TypeError)):
        resume(backend, path)
    assert backend.attachments == []
    assert backend.calls == [] and backend.observations == 0
    assert path.read_bytes() == original


@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize("early_save", [False, True])
def test_first_observation_fault_is_reloadable_but_not_automatically_resumable(tmp_path, missing, early_save):
    class EarlySave(FoundationScenario):
        def _observe(self, stage="observe"):
            snapshot = super()._observe(stage)
            self._save()
            return snapshot
    kind = solid_loop_type(EarlySave) if early_save else Loop
    backend = Backend(); loop = controller(backend, tmp_path, kind=kind)
    loop.memory.active_goal = "rocket_launch"
    loop.memory.failures["prior-project"] = 2
    if missing:
        del backend.state.factory["solid_routes"]
    else:
        backend.state.factory["solid_routes"]["protocol"] = 999
    loop._observe()
    assert loop.memory.status == "uncertain" and loop._execution_barrier(backend.state)
    assert backend.calls == []
    path = backend.checkpoint; before = path.read_bytes()
    restored = load_checkpoint(path, backend.state.session_id, "rocket_launch")
    assert restored.solid_epoch == {} and restored.solid_commitments == {}
    assert restored.failures == {"prior-project": 2}
    assert restored.status == "uncertain" and restored.solid_intents == INTENTS
    spy = AttachmentSpy()
    with pytest.raises(ValueError, match="reconciliation"):
        resume(spy, path, kind)
    assert spy.attachments == [] and not spy.calls and spy.observations == 0
    assert path.read_bytes() == before


def test_existing_frozen_configuration_rejects_ordinary_assignment():
    config = RunConfiguration("mock", "hierarchical", "deterministic")
    with pytest.raises(FrozenInstanceError):
        config.solid_routes = True


@pytest.mark.parametrize("exposure", ["input", "property"])
def test_configuration_manifest_snapshot_has_no_caller_alias(tmp_path, exposure):
    config = RunConfiguration("mock", "hierarchical", "deterministic",
                              factory_scheduling="ready-work", solid_routes=False)
    with ResearchLog(tmp_path / "run", config, environ={}) as sink:
        target = config if exposure == "input" else sink.configuration
        # Ordinary assignment is already frozen. Explicit object bypass exposes
        # the alias, not a claim that dataclasses secure arbitrary Python callers.
        object.__setattr__(target, "solid_routes", True)
        assert sink.configuration.solid_routes is False
        backend = AttachmentSpy()
        with pytest.raises(ValueError, match="manifest"):
            Loop(backend, solid_intents=INTENTS, factory_scheduling="ready-work",
                 checkpoint=str(tmp_path / "checkpoint"), policy="deterministic", research_log=sink)
        assert backend.attachments == []
        assert asdict(sink.configuration) == json.loads((sink.run_dir / "manifest.json").read_text())["configuration"]
    assert verify_run(tmp_path / "run")["complete"]


def test_configuration_exposure_matches_validated_redacted_manifest(tmp_path):
    config = RunConfiguration("mock", "hierarchical", "deterministic",
                              requested_model="https://fixture.invalid/model?token=fixture-secret")
    with ResearchLog(tmp_path / "run", config, environ={}) as sink:
        written = json.loads((sink.run_dir / "manifest.json").read_text())["configuration"]
        assert asdict(sink.configuration) == written
        assert sink.configuration is not config
        assert sink.configuration is not sink.configuration


@pytest.mark.parametrize("field,value", [
    ("status", "running"), ("reason", "ordinary diagnostic"),
    ("solid_epoch", {"actor_index": 1}), ("solid_commitments", {"unpaid": {}}),
])
def test_unbound_fault_allowance_cannot_authorize_other_invalid_states(tmp_path, field, value):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop.memory.active_goal = "rocket_launch"
    del backend.state.factory["solid_routes"]
    loop._observe()
    data = json.loads(backend.checkpoint.read_text()); data[field] = value
    backend.checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_checkpoint(backend.checkpoint, backend.state.session_id, "rocket_launch")


def test_checkpoint_changed_during_preflight_never_attaches_backend(tmp_path, monkeypatch):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop.memory.active_goal = "rocket_launch"; loop._observe()
    original = Loop.memory_type.load
    def changing(cls, path, session, target):
        memory = original(path, session, target)
        path.write_bytes(path.read_bytes() + b" ")
        return memory
    monkeypatch.setattr(Loop.memory_type, "load", classmethod(changing))
    spy = AttachmentSpy()
    with pytest.raises(ValueError, match="changed"):
        resume(spy, backend.checkpoint)
    assert spy.attachments == [] and spy.observations == 0


def test_checkpoint_changed_after_construction_is_rejected_before_observation(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop.memory.active_goal = "rocket_launch"; loop._observe()
    resumed = resume(backend, backend.checkpoint)
    before_reads = backend.observations
    backend.checkpoint.write_bytes(backend.checkpoint.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed"):
        resumed.step()
    assert backend.observations == before_reads and not backend.calls


def test_bound_uncertain_resume_still_gets_initial_receipt_reconciliation(tmp_path):
    backend = Backend(); backend.lost_ack = True
    loop = controller(backend, tmp_path); loop.step()
    data = json.loads(backend.checkpoint.read_text())
    data["status"] = "uncertain"
    backend.checkpoint.write_text(json.dumps(data))
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.memory is None and not resumed.terminal
    assert resumed.step()["verified"] and len(backend.calls) == 1
