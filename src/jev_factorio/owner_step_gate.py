"""Optional, private owner review boundary between steps of one live process.

This is a cooperative admission protocol, not a substitute for the external
single-writer lock or an authentication system. The owner creates each grant
only after independently reviewing the request and native receipt.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import time
from uuid import uuid4


class StepGateClosed(RuntimeError):
    """A later step was not admitted; no new controller decision may start."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _private_file(path: Path, *, maximum: int = 4096) -> bytes:
    if path.is_symlink():
        raise StepGateClosed("Owner gate file is a symlink")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        identity = os.fstat(descriptor)
        current = path.stat()
        if (not stat.S_ISREG(identity.st_mode)
                or (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino)
                or identity.st_uid != os.geteuid()
                or stat.S_IMODE(identity.st_mode) != 0o600
                or identity.st_size > maximum):
            raise StepGateClosed("Owner gate file identity or privacy changed")
        raw = os.read(descriptor, maximum + 1)
        if len(raw) > maximum:
            raise StepGateClosed("Owner gate file exceeds its bound")
        return raw
    finally:
        os.close(descriptor)


def _durable_exclusive(path: Path, payload: dict) -> None:
    raw = (json.dumps(payload, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _process_start_ticks() -> int:
    raw = Path(f"/proc/{os.getpid()}/stat").read_text()
    return int(raw.rsplit(")", 1)[1].split()[19])


class OwnerStepGate:
    """Fail closed between already verified steps; never launches a controller."""

    def __init__(self, directory: Path, checkpoint: Path, receipt: Path,
                 source_root: Path, lock_path: Path, lock_fd: int,
                 *, wait_seconds: float = 120, clock=time.monotonic,
                 sleep=time.sleep, native_readback=None):
        if (not math.isfinite(wait_seconds) or not 0 < wait_seconds <= 120
                or os.name != "posix"):
            raise ValueError("Owner gate requires POSIX and bounded wait")
        self.directory = Path(directory)
        self.checkpoint = Path(checkpoint)
        self.receipt = Path(receipt)
        self.source_root = Path(source_root)
        self.lock_path = Path(lock_path)
        self.lock_fd = lock_fd
        self.wait_seconds = wait_seconds
        self.clock, self.sleep = clock, sleep
        self.native_readback = native_readback
        self.nonce = uuid4().hex
        self.pid = os.getpid()
        self.start_ticks = _process_start_ticks()
        self._check_lock()
        if self.directory.is_symlink() or self.directory.exists() or self.directory.parent.is_symlink():
            raise StepGateClosed("Owner gate directory was already used")
        self.source_commit = self._git("rev-parse", "HEAD")
        self.source_tree = self._git("show", "-s", "--format=%T", "HEAD")
        if self._git("status", "--porcelain"):
            raise StepGateClosed("Owner gate source checkout is dirty")
        self.directory.mkdir(mode=0o700)
        parent = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)

    def _git(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.source_root), *arguments], text=True).strip()

    def _check_lock(self) -> None:
        held, current = os.fstat(self.lock_fd), self.lock_path.lstat()
        if (not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(current.st_mode)
                or (held.st_dev, held.st_ino, held.st_uid, held.st_mode)
                    != (current.st_dev, current.st_ino, current.st_uid, current.st_mode)
                or held.st_uid != os.geteuid()
                or stat.S_IMODE(held.st_mode) != 0o600):
            raise StepGateClosed("Original owner lock identity changed")

    def _checkpoint(self, loop) -> tuple[str, str | None]:
        raw = _private_file(self.checkpoint, maximum=64 * 1024 * 1024)
        saved = loop.memory_type.from_bytes(raw, loop.memory.session_id, loop.target)
        # A grant is valid only at a fully quiescent composed checkpoint. The
        # controller may otherwise retain a paid route, outpost, connector, or
        # funding owner even when the immediate dispatch is verified.
        def quiescent(memory) -> bool:
            null_owners = (
                "pending", "attempt", "active_plan", "background_job",
                "background_attempt", "transfer_recovery", "connector_ownership",
                "capital_investment", "solid_funding", "coal_funding",
            )
            empty_owners = (
                "reservations", "solid_commitments", "coal_commitments",
                "output_commitments", "input_commitments", "outpost_commitments",
                "successor_projects",
            )
            return (memory.status == "running"
                    and all(getattr(memory, name, None) is None for name in null_owners)
                    and all(isinstance(getattr(memory, name, {}), dict)
                            and not getattr(memory, name, {}) for name in empty_owners))

        if not quiescent(saved) or not quiescent(loop.memory):
            raise StepGateClosed("Checkpoint has unresolved ownership")
        if (loop.memory.status != saved.status or loop.memory.pending != saved.pending
                or loop.memory.attempt != saved.attempt
                or loop.memory.last_tick != saved.last_tick):
            raise StepGateClosed("In-memory and durable controller ownership differ")
        outcome = saved.attempt_outcomes[-1] if saved.attempt_outcomes else None
        return (_digest(raw), _digest(json.dumps(outcome, sort_keys=True,
                                                allow_nan=False).encode())
                if outcome is not None else None)

    def _native(self, loop) -> tuple[str, str]:
        provider = getattr(loop, "jev", None)
        state = getattr(provider, "state", None)
        if (getattr(loop, "policy", None) != "deterministic"
                and (not isinstance(state, dict) or state.get("phase") != "healthy"
                     or state.get("in_flight") is not None)):
            raise StepGateClosed("Provider has unresolved work")
        snapshot = loop.backend.observe()
        controls = getattr(snapshot, "_native_controls", None)
        if (snapshot.session_id != loop.memory.session_id
                or not isinstance(controls, dict)
                or controls.get("walking") is not False
                or controls.get("mining") is not False
                or controls.get("status") not in {"idle", "completed", "failed"}
                or snapshot.factory.get("crafting_queue") != 0
                or snapshot.factory.get("player_bound") is not True):
            raise StepGateClosed("Native actor is not independently idle")
        receipts = snapshot.factory.get("receipts")
        if not isinstance(receipts, (dict, list)):
            raise StepGateClosed("Native paid receipt ledger is unavailable")
        receipt_ledger_sha = _digest(json.dumps(receipts, sort_keys=True,
                                                allow_nan=False).encode())
        if self.native_readback is None:
            from .backends.native_attachment import readback
            native = readback(loop.backend._instance.rcon_client,
                              receipt_path=self.receipt,
                              connector_witness_path=self.checkpoint.with_name(
                                  'native-connector-observer-v1.witness.jsonl'))
        else:
            native = self.native_readback(loop)
        if (native.get("qualified") is not True
                or native.get("session_id") != loop.memory.session_id
                or not isinstance(native.get("native_installation"), dict)):
            raise StepGateClosed("Native installation readback is unqualified")
        manifest_sha = _digest(json.dumps(native["native_installation"],
                                          sort_keys=True, allow_nan=False).encode())
        return manifest_sha, receipt_ledger_sha

    def __call__(self, loop, sequence: int, record: dict) -> bool:
        gate_wall_start = time.perf_counter_ns()
        gate_cpu_start = time.process_time_ns()
        if (type(sequence) is not int or sequence < 1 or not isinstance(record, dict)
                or record.get("verified") is not True
                or record.get("status") != "running"):
            raise StepGateClosed("Prior step is not verified and running")
        self._check_lock()
        if (self._git("rev-parse", "HEAD") != self.source_commit
                or self._git("show", "-s", "--format=%T", "HEAD") != self.source_tree
                or self._git("status", "--porcelain")):
            raise StepGateClosed("Owner gate source checkout changed")
        checkpoint_sha, outcome_sha = self._checkpoint(loop)
        if (record.get("attempt_outcomes") and outcome_sha != _digest(json.dumps(
                record["attempt_outcomes"][-1], sort_keys=True,
                allow_nan=False).encode())):
            raise StepGateClosed("Verified record and durable attempt outcome differ")
        receipt_sha = _digest(_private_file(self.receipt, maximum=1048576))
        manifest_sha, receipt_ledger_sha = self._native(loop)
        if (self._checkpoint(loop) != (checkpoint_sha, outcome_sha)
                or _digest(_private_file(self.receipt, maximum=1048576)) != receipt_sha):
            raise StepGateClosed("Native readback changed durable evidence")
        prefix = f"step-{sequence:04d}"
        request = self.directory / f"{prefix}-request.json"
        grant = self.directory / f"{prefix}-grant.json"
        accepted = self.directory / f"{prefix}-accepted.json"
        closed = self.directory / f"{prefix}-closed.json"
        if any(path.exists() or path.is_symlink()
               for path in (request, grant, accepted, closed)):
            raise StepGateClosed("Owner gate step artifact already exists")
        identity = {"schema": "jev.owner-step-gate.v1", "run_nonce": self.nonce,
                    "sequence": sequence, "session_id": loop.memory.session_id,
                    "checkpoint_sha256": checkpoint_sha, "receipt_sha256": receipt_sha,
                    "manifest_sha256": manifest_sha,
                    "attempt_outcome_sha256": outcome_sha,
                    "native_receipt_ledger_sha256": receipt_ledger_sha,
                    "source_commit": self.source_commit,
                    "source_tree": self.source_tree, "owner_pid": self.pid,
                    "owner_start_ticks": self.start_ticks}
        _durable_exclusive(request, {**identity, "phase": "quiescent_request",
                                     "verified": True, "actor_idle": True,
                                     "automatic_continuation": False})
        deadline = self.clock() + self.wait_seconds
        while self.clock() < deadline:
            if grant.exists() or grant.is_symlink():
                break
            self.sleep(min(0.1, max(0, deadline - self.clock())))
        else:
            _durable_exclusive(closed, {**identity, "phase": "grant_timeout",
                                        "gate_wall_ns": time.perf_counter_ns() - gate_wall_start,
                                        "gate_process_cpu_ns": time.process_time_ns() - gate_cpu_start,
                                        "automatic_continuation": False})
            raise StepGateClosed("Owner grant timed out before another step")
        try:
            decision = json.loads(_private_file(grant))
        except (OSError, ValueError, TypeError) as error:
            raise StepGateClosed("Owner grant is unavailable or malformed") from error
        if decision != {**identity, "decision": "continue"}:
            raise StepGateClosed("Owner grant does not bind this exact step")
        self._check_lock()
        if (self._git("rev-parse", "HEAD") != self.source_commit
                or self._git("show", "-s", "--format=%T", "HEAD") != self.source_tree
                or self._git("status", "--porcelain")
                or self._checkpoint(loop) != (checkpoint_sha, outcome_sha)
                or _digest(_private_file(self.receipt, maximum=1048576)) != receipt_sha
                or self._native(loop) != (manifest_sha, receipt_ledger_sha)
                or self._checkpoint(loop) != (checkpoint_sha, outcome_sha)):
            raise StepGateClosed("Owner grant lost source, checkpoint or native identity")
        self._check_lock()
        _durable_exclusive(accepted, {**identity, "phase": "accepted_before_next_step",
                                      "grant_sha256": _digest(_private_file(grant)),
                                      "gate_wall_ns": time.perf_counter_ns() - gate_wall_start,
                                      "gate_process_cpu_ns": time.process_time_ns() - gate_cpu_start})
        return True
