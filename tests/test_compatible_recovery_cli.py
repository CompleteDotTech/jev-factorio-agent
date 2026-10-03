"""CLI ordering with real migration/checkpoints and an explicit offline backend.

The native checkpoint installation barrier needs POSIX semantics; Windows does
not qualify these tests by replacing that barrier with a mock.
"""
import hashlib
import json
import os
import sys

import pytest
import requests

from jev_factorio import compatible_recovery as recovery, main, operational_safety
from jev_factorio.background import BackgroundMemory
from test_background_work import controller
from test_compatible_source_recovery import NEW, OWNER, setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Real checkpoint durability requires POSIX")


def invoke(tmp_path, monkeypatch, path, authority, backend, *, failure=None):
    monkeypatch.chdir(tmp_path)
    for key in ("TYPESAFE_API_KEY", "JEV_RUN_DIR", "JEV_LOG_FILE", "JEV_DASHBOARD_EVENTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("Provider dispatched"))
    monkeypatch.setattr(operational_safety, "storage_ready", lambda roots: True)
    monkeypatch.setattr("jev_factorio.jev_client.make_client", lambda **kw: object())
    authorization = tmp_path / "authority.json"
    authorization.write_bytes(json.dumps(authority, sort_keys=True).encode())
    authorization.chmod(0o400)
    pin = hashlib.sha256(authorization.read_bytes()).hexdigest()
    calls = []

    def attach(*args, **kwargs):
        installed = BackgroundMemory.from_bytes(path.read_bytes(), authority["session_id"], authority["target"])
        assert installed.compatible_source_recoveries[-1]["authorization_id"] == authority["authorization_id"]
        assert installed.blocked_recovery["source_revision"] == NEW
        assert installed.background_job == authority["scope"]["background_job"]
        assert installed.background_attempt == authority["scope"]["background_attempt"]
        calls.append("attachment_after_durable_migration")
        return backend

    monkeypatch.setattr(main, "make_backend", attach)

    class Loop:
        memory_type = BackgroundMemory

        def __init__(self, native, **options):
            self.backend = native

        def run(self, **limits):
            assert limits == {"until_complete": True}
            prior = list(backend.calls)
            restored = controller(backend, tmp_path, resume=True)
            result = restored.reconcile_only()
            assert backend.calls == prior
            assert restored.memory.background_job is not None
            calls.append("pending_craft_reconciled_without_dispatch")

    monkeypatch.setattr("jev_factorio.controller.HierarchicalLoop", Loop)
    monkeypatch.setattr("jev_factorio.background.BackgroundWorkLoop", Loop)
    if failure == "save_ambiguity":
        save = BackgroundMemory.save

        def ambiguous(memory, output):
            save(memory, output)
            raise OSError("Explicit offline installed-save ambiguity")

        monkeypatch.setattr(BackgroundMemory, "save", ambiguous)
    elif failure == "provider_drift":
        reads = iter((None, "f" * 64))
        monkeypatch.setattr(recovery, "provider_state_digest", lambda output: next(reads))
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--backend", "fle", "--controller", "hierarchical",
        "--policy", "jev", "--target", authority["target"], "--resume", "--resume-controller", "--checkpoint", str(path),
        "--tick-seconds", "1", "--until-complete", "--persist-recoverable-blocks",
        "--background-work", "--factory-scheduling", "ready-work",
        "--compatible-source-authorization", str(authorization),
        "--compatible-source-authorization-sha256", pin, "--compatible-source-lock-fd", "9"])
    return calls


def test_cli_pending_craft_migrates_durably_before_attachment(tmp_path, monkeypatch):
    backend, original, path, authority = setup(tmp_path, monkeypatch)
    calls = invoke(tmp_path, monkeypatch, path, authority, backend)
    main.cli()
    assert calls == ["attachment_after_durable_migration", "pending_craft_reconciled_without_dispatch"]


@pytest.mark.parametrize("failure", ["invalid_authority", "save_ambiguity", "provider_drift"])
def test_cli_failed_migration_never_attaches_or_dispatches(tmp_path, monkeypatch, failure):
    backend, original, path, authority = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    if failure == "invalid_authority":
        authority["checkpoint_sha256"] = "f" * 64
    calls = invoke(tmp_path, monkeypatch, path, authority, backend, failure=failure)
    native_calls = list(backend.calls)
    with pytest.raises(SystemExit) as error:
        main.cli()
    assert error.value.code == 2 and calls == [] and backend.calls == native_calls
    if failure == "invalid_authority":
        assert path.read_bytes() == before
    else:
        installed = BackgroundMemory.from_bytes(path.read_bytes(), authority["session_id"], authority["target"])
        assert installed.compatible_source_recoveries  # Consumed, never retry old authority.


def test_original_flock_lost_during_validation_prevents_checkpoint_installation(tmp_path, monkeypatch):
    backend, original, path, authority = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    held = {"value": True}

    def ownership(fd, lock_path):
        if not held["value"]:
            raise ValueError("Original inherited flock lost")

    def lose_lock(*args):
        held["value"] = False

    monkeypatch.setattr(recovery, "require_writer_lock", ownership)
    monkeypatch.setattr(recovery, "validate_budget_contract", lose_lock)
    with pytest.raises(ValueError, match="flock lost"):
        recovery.migrate_checkpoint(path, authority, BackgroundMemory, NEW, OWNER, lock_fd=9)
    assert path.read_bytes() == before


def test_original_flock_lost_after_installation_requires_reconciliation(tmp_path, monkeypatch):
    backend, original, path, authority = setup(tmp_path, monkeypatch)
    checks = []

    def ownership(fd, lock_path):
        checks.append(fd)
        if len(checks) == 3:
            raise ValueError("Original inherited flock lost after installation")

    monkeypatch.setattr(recovery, "require_writer_lock", ownership)
    with pytest.raises(ValueError, match="after installation"):
        recovery.migrate_checkpoint(path, authority, BackgroundMemory, NEW, OWNER, lock_fd=9)
    installed = BackgroundMemory.from_bytes(path.read_bytes(), authority["session_id"], authority["target"])
    assert installed.compatible_source_recoveries
