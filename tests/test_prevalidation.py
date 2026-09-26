import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio import prevalidation as p


@pytest.fixture
def runner(tmp_path, monkeypatch):
    cwd, state = tmp_path / "checkout", tmp_path / "state"
    cwd.mkdir()
    identity = {"commit": "a" * 40, "tree": "b" * 40, "runtime": "same"}
    monkeypatch.setattr(p, "fingerprint", lambda _: dict(identity))
    monkeypatch.setattr(p.subprocess, "check_output", lambda *a, **kw: str(cwd / "src/jev_factorio/__init__.py"))
    monkeypatch.setenv("RCON_PASSWORD", "not-for-child")
    monkeypatch.setenv("TYPESAFE_API_KEY", "not-for-child")
    calls = []
    def subprocess_run(command, **kwargs):
        calls.append((command, kwargs))
        assert "RCON_PASSWORD" not in kwargs["env"]
        assert "TYPESAFE_API_KEY" not in kwargs["env"]
        assert command[1:4] == p.COMMAND
        Path(command[-1].split("=", 1)[1]).write_text(
            '<testsuites><testsuite tests="4" skipped="1" failures="0" errors="0"/></testsuites>')
        kwargs["stdout"].write(b"3 passed, 1 skipped")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(p, "execute_suite", subprocess_run)
    return cwd, state, identity, calls


def test_completed_suite_reused_and_drift_rejected(runner):
    cwd, state, identity, calls = runner
    reference = p.run(cwd, state)
    assert p.validate(cwd, state, reference)
    assert len(calls) == 1
    identity["runtime"] = "changed"
    assert not p.validate(cwd, state, reference)


@pytest.mark.parametrize("file", ["pytest.log", "junit.xml", "manifest.json"])
def test_evidence_tampering_rejected(runner, file):
    cwd, state, _, _ = runner
    reference = p.run(cwd, state)
    with (state / "prevalidation" / reference / file).open("ab") as stream:
        stream.write(b"tampered")
    assert not p.validate(cwd, state, reference)


def test_expiry_and_clock_rollback_rejected(runner):
    cwd, state, _, _ = runner
    reference = p.run(cwd, state, ttl=5)
    value = json.loads((state / "prevalidation" / reference / "manifest.json").read_text())
    assert not p.validate(cwd, state, reference, now=value["completed"] + 6)
    assert not p.validate(cwd, state, reference, now=value["started"] - 1)


def test_partial_and_path_reference_rejected(runner):
    cwd, state, _, _ = runner
    assert not p.validate(cwd, state, "../manifest.json")
    assert not p.validate(cwd, state, "a" * 64)


def test_failed_suite_does_not_publish(runner, monkeypatch):
    cwd, state, _, _ = runner
    monkeypatch.setattr(p, "execute_suite", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError):
        p.run(cwd, state)
    assert all(path.name == "run.lock" or path.name.startswith("pending-")
               for path in (state / "prevalidation").iterdir())


def test_source_change_during_suite_does_not_publish(runner, monkeypatch):
    cwd, state, identity, _ = runner
    original = p.execute_suite
    def changed(*a, **kw):
        result = original(*a, **kw)
        identity["commit"] = "c" * 40
        return result
    monkeypatch.setattr(p, "execute_suite", changed)
    with pytest.raises(ValueError):
        p.run(cwd, state)


def test_dotenv_staging_rejected_before_tests(runner):
    cwd, state, _, calls = runner
    (cwd / ".env").write_text("secret")
    with pytest.raises(ValueError):
        p.run(cwd, state)
    assert not calls


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership/mode contract")
def test_public_artifact_rejected(runner):
    cwd, state, _, _ = runner
    reference = p.run(cwd, state)
    (state / "prevalidation" / reference / "manifest.json").chmod(0o644)
    assert not p.validate(cwd, state, reference)


def test_other_checkout_import_rejected(runner, monkeypatch):
    cwd, state, _, calls = runner
    monkeypatch.setattr(p.subprocess, "check_output", lambda *a, **kw: "/other/src/jev_factorio/__init__.py")
    with pytest.raises(ValueError):
        p.run(cwd, state)
    assert not calls


def test_concurrent_runner_rejected(runner):
    cwd, state, _, calls = runner
    cache = state / "prevalidation"
    cache.mkdir(parents=True, mode=0o700)
    (cache / "run.lock").touch(mode=0o600)
    with p.run_lock(cache):
        with pytest.raises(BlockingIOError):
            p.run(cwd, state)
    assert not calls


def test_fingerprint_tracks_dependency_content_environment_and_clean_source(tmp_path, monkeypatch):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    for command in (["git", "init"], ["git", "config", "user.name", "Test"],
                    ["git", "config", "user.email", "test@example.invalid"]):
        subprocess.run(command, cwd=cwd, check=True, capture_output=True)
    (cwd / "source.py").write_text("original")
    subprocess.run(["git", "add", "source.py"], cwd=cwd, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-m", "initial"],
                   cwd=cwd, check=True, capture_output=True)
    dependency = tmp_path / "dependency.py"
    dependency.write_text("first")
    distribution = SimpleNamespace(metadata={"Name": "test-dependency"}, version="1",
                                   files=[Path("dependency.py")], locate_file=lambda _: dependency)
    monkeypatch.setattr(p.importlib.metadata, "distributions", lambda: [distribution])
    initial = p.fingerprint(cwd)
    distribution.read_text = lambda _: '{"dir_info":{"editable":true}}'
    with pytest.raises(ValueError, match="Editable"):
        p.fingerprint(cwd)
    distribution.read_text = lambda _: None
    dependency.write_text("second")
    assert p.fingerprint(cwd)["dependencies"] != initial["dependencies"]
    monkeypatch.setenv("LANG", "test-locale")
    assert p.fingerprint(cwd)["environment"] != initial["environment"]
    (cwd / "unexpected.py").write_text("untracked")
    with pytest.raises(ValueError, match="clean"):
        p.fingerprint(cwd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink test")
def test_artifact_symlink_rejected(runner):
    cwd, state, _, _ = runner
    reference = p.run(cwd, state)
    cache = state / "prevalidation"
    destination = cache / "relocated"
    (cache / reference).rename(destination)
    (cache / reference).symlink_to(destination, target_is_directory=True)
    assert not p.validate(cwd, state, reference)


@pytest.mark.parametrize("attributes", ['tests="0"', 'tests="2" skipped="2"',
                                        'tests="2" failures="1"', 'tests="2" errors="1"'])
def test_nonpassing_junit_rejected(tmp_path, attributes):
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuites><testsuite {attributes}/></testsuites>")
    with pytest.raises(ValueError):
        p.junit(report)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_suite_timeout_kills_only_owned_group_including_child(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = ("import subprocess,sys,time; from pathlib import Path; "
              "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
              "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        p.execute_suite([sys.executable, "-c", script, str(pidfile)], timeout=1,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
    child_pid = int(pidfile.read_text())
    status = Path(f"/proc/{child_pid}/stat")
    # A briefly unreaped orphan zombie cannot execute or consume test resources.
    try:
        state = status.read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return  # The orphan was already reaped; avoid an exists/read race.
    assert state == "Z"
