"""Linux process-crash checks for incomplete causal evidence.

These tests exercise the local file and directory durability boundaries. They
do not model machine power loss or make claims about a live Factorio session.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from jev_factorio import research_log as log


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux fsync behavior")


def test_linux_writer_fsyncs_file_and_directory_descriptors(tmp_path, monkeypatch):
    original_fsync = log.os.fsync
    descriptors = []

    def record_descriptor(fd):
        descriptors.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return original_fsync(fd)

    monkeypatch.setattr(log.os, "fsync", record_descriptor)
    writer = log.ResearchLog(
        tmp_path / "run", log.RunConfiguration("mock", "hierarchical", "deterministic"),
        repo_dir=tmp_path, environ={}, monotonic_ns=lambda: 1,
    )
    try:
        writer.emit("observation", {"value": 1})
        writer.finish()
    finally:
        writer.close()

    assert any(descriptors)
    assert any(not is_directory for is_directory in descriptors)
    assert log.verify_run(writer.run_dir)["complete"] is True


def test_linux_directory_sync_failure_poisoned_writer_cannot_continue(tmp_path, monkeypatch):
    writer = log.ResearchLog(
        tmp_path / "run", log.RunConfiguration("mock", "hierarchical", "deterministic"),
        repo_dir=tmp_path, environ={}, monotonic_ns=lambda: 1,
    )

    def fail_directory_sync(_path):
        raise OSError("synthetic directory sync failure")

    monkeypatch.setattr(log, "_sync_directory", fail_directory_sync)
    with pytest.raises(OSError, match="directory sync failure"):
        writer.finish()
    assert writer._failed
    with pytest.raises(RuntimeError, match="failed"):
        writer.emit("observation", {"value": "must not append"})
    writer.close()


@pytest.mark.parametrize(("crash_point", "exit_code"), [("partial", 71), ("after_fsync", 72)])
def test_linux_process_crash_never_resumes_or_hides_uncertain_append(tmp_path, crash_point, exit_code):
    repo_root = Path(__file__).resolve().parents[1]
    run_dir = tmp_path / "run"
    child = r'''
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from jev_factorio import research_log as log

run_dir = Path(sys.argv[1])
crash_point = sys.argv[2]
original = log._write_durable

def crash_after_append(stream, data):
    if b'"event_type":"observation"' not in data:
        return original(stream, data)
    if crash_point == "partial":
        stream.write(data[:max(1, len(data) // 2)])
        stream.flush()
        os._exit(71)
    original(stream, data)
    os._exit(72)

log._write_durable = crash_after_append
writer = log.ResearchLog(
    run_dir, log.RunConfiguration("mock", "hierarchical", "deterministic"),
    repo_dir=Path.cwd(), environ={}, monotonic_ns=lambda: 1,
    utc_now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
)
writer.emit("observation", {"value": 1})
'''
    env = {key: os.environ[key] for key in ("PATH", "PYTHONPATH") if key in os.environ}
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(repo_root / "src"), env.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [sys.executable, "-c", child, str(run_dir), crash_point], cwd=repo_root,
        env=env, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == exit_code, result.stderr
    if crash_point == "partial":
        events = run_dir / "events.jsonl"
        assert not events.read_bytes().endswith(b"\n")
        with pytest.raises(ValueError):
            log.verify_run(run_dir, allow_incomplete=True)
    else:
        result = log.verify_run(run_dir, allow_incomplete=True)
        assert result["complete"] is False
        records = [line for line in (run_dir / "events.jsonl").read_bytes().splitlines()]
        assert [log.json.loads(line)["event_type"] for line in records] == ["run_started", "observation"]

    with pytest.raises(FileExistsError):
        log.ResearchLog(
            run_dir, log.RunConfiguration("mock", "hierarchical", "deterministic"),
            repo_dir=tmp_path, environ={}, monotonic_ns=lambda: 1,
        )
