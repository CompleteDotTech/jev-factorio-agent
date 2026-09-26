"""Local admission/maintenance evidence. Never attaches to the game or clears actions.

These files are single-controller state, not a distributed locking protocol. The
supervisor still owns the process lock. A sidecar failure is a protective stop.
"""
from __future__ import annotations

import errno
from contextlib import contextmanager
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4


class SafetyStateError(RuntimeError):
    """Missing, malformed or conflicting safety evidence; no mutation is allowed."""


class MaintenanceAdmissionClosed(RuntimeError):
    """Exact pre-dispatch rejection, not an ambiguous native return."""


class StoragePressure(OSError):
    failure_class = "storage_pressure"

    def __init__(self):
        super().__init__(errno.ENOSPC, "Storage admission reserve unavailable")


def atomic_json(path: Path, value: dict) -> None:
    """Write private JSON with file and directory durability; never follow a leaf link."""
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise SafetyStateError("Safety state must not be a symlink")
    # Persist newly created directory entries as well as file/rename updates.
    # Otherwise the first circuit/maintenance record could disappear with its
    # parent directory after a power loss even though its own fsync succeeded.
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    if not parent.is_dir():
        raise SafetyStateError("Safety state parent must be a directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise SafetyStateError("Safety directory must not be a symlink")
        if os.name == "posix":
            handle = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
    payload = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    fd, temporary = tempfile.mkstemp(prefix=".safety-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def read_json(path: Path, *, maximum_bytes: int = 1048576) -> dict | None:
    if path.is_symlink():
        raise SafetyStateError("Safety state must not be a symlink")
    try:
        if path.stat().st_size > maximum_bytes:
            raise SafetyStateError("Safety state exceeds its size bound")
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError) as error:
        raise SafetyStateError("Invalid safety state") from error
    if not isinstance(data, dict):
        raise SafetyStateError("Safety state must be an object")
    return data


def safety_dir(checkpoint: Path) -> Path:
    checkpoint = Path(checkpoint)
    return checkpoint.with_name(checkpoint.name + ".safety")


def storage_sample(path: Path) -> dict:
    """Free space available to this user, including inode exhaustion, without writes."""
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    if not hasattr(os, "statvfs"):
        usage = shutil.disk_usage(path)
        return {"free_bytes": usage.free, "total_bytes": usage.total,
                "free_inodes": 0, "total_inodes": 0}
    stat = os.statvfs(path)
    return {"free_bytes": stat.f_bavail * stat.f_frsize,
            "total_bytes": stat.f_blocks * stat.f_frsize,
            "free_inodes": stat.f_favail, "total_inodes": stat.f_files}


def storage_ready(paths, *, minimum_bytes: int = 1024 ** 3,
                  minimum_inodes: int = 1024) -> bool:
    for path in paths:
        value = storage_sample(path)
        if value["free_bytes"] < minimum_bytes or (
            value["total_inodes"] > 0 and value["free_inodes"] < minimum_inodes
        ):
            return False
    return True


@contextmanager
def maintenance_lock(checkpoint: Path):
    """Serialize operator requests/releases/stops without locking the controller."""
    directory = safety_dir(checkpoint)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == 'posix':
        parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    path = directory / 'maintenance.lock'
    if directory.is_symlink() or path.is_symlink():
        raise SafetyStateError('Maintenance lock must not be a symlink')
    with path.open('a+b') as stream:
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise SafetyStateError('Another maintenance operation owns this campaign') from error
        try:
            yield
        finally:
            if os.name == 'posix':
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            else:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def request_maintenance(checkpoint: Path, *, timeout: float = 120, clock=time.time) -> dict:
    with maintenance_lock(checkpoint):
        return _request_maintenance(checkpoint, timeout=timeout, clock=clock)


def _request_maintenance(checkpoint: Path, *, timeout: float, clock) -> dict:
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError("Maintenance timeout must be in (0, 3600] seconds")
    memory = read_json(checkpoint, maximum_bytes=64 * 1024 ** 2)
    if memory is None or not isinstance(memory.get("session_id"), str):
        raise SafetyStateError("An existing identified campaign is required")
    path = safety_dir(checkpoint) / "maintenance.json"
    if path.exists():
        raise SafetyStateError("A maintenance request is already active; release it explicitly")
    requested_at = clock()
    request = {"schema": 1, "request_id": str(uuid4()), "session_id": memory["session_id"],
               "requested_at": requested_at, "deadline": requested_at + timeout}
    # Creation is serialized with release and the guarded stop.
    atomic_json(path, request)
    return request


def maintenance_request(checkpoint: Path, session_id: str) -> dict | None:
    request = read_json(safety_dir(checkpoint) / "maintenance.json")
    if request is None:
        return None
    if (request.get("schema") != 1 or request.get("session_id") != session_id
            or not isinstance(request.get("request_id"), str)
            or len(request["request_id"]) != 36
            or any(c not in "0123456789abcdef-" for c in request["request_id"])
            or type(request.get("deadline")) not in (int, float)
            or type(request.get("requested_at")) not in (int, float)
            or not math.isfinite(request["deadline"])
            or not 0 < request["deadline"] - request["requested_at"] <= 3600):
        raise SafetyStateError("Maintenance request identity or deadline is invalid")
    return request


def verify_quiescence(checkpoint: Path, request_id: str, *, clock=time.time,
                      maximum_age: float = 15) -> dict:
    memory = read_json(checkpoint, maximum_bytes=64 * 1024 ** 2)
    if memory is None:
        raise SafetyStateError("Checkpoint is absent")
    request = maintenance_request(checkpoint, memory.get("session_id"))
    status = read_json(safety_dir(checkpoint) / "health.json") or {}
    if (request is None or request["request_id"] != request_id
            or status.get("request_id") != request_id or status.get("phase") != "quiescent"
            or status.get("session_id") != memory.get("session_id")
            or type(status.get("at")) not in (int, float)
            or not 0 <= clock() - status["at"] <= maximum_age
            or status.get("checkpoint_sha256") != hashlib.sha256(checkpoint.read_bytes()).hexdigest()
            or memory.get("pending") or memory.get("background_job")
            or memory.get("background_attempt")):
        raise SafetyStateError("No fresh matching quiescence acknowledgement")
    return status


class RuntimeSafety:
    def __init__(self, checkpoint: Path, *, outputs=(), clock=time.time):
        self.checkpoint = Path(checkpoint)
        self.directory = safety_dir(self.checkpoint)
        if self.directory.is_symlink():
            raise SafetyStateError("Safety directory must not be a symlink")
        self.outputs = (self.checkpoint.parent, *[Path(p) for p in outputs if p])
        self.clock = clock
        self.minimum_bytes = 1024 ** 3
        self.phase = "healthy"
        self.request = None
        self.last_progress_at = None
        self.last_signature = None
        self.started_at = clock()

    def maintenance(self, memory, snapshot) -> str | None:
        request = maintenance_request(self.checkpoint, memory.session_id)
        self.request = request
        if request is None:
            return None
        outstanding = bool(memory.pending or getattr(memory, "background_job", None)
                           or getattr(memory, "background_attempt", None))
        native = getattr(snapshot, "_native_controls", None)
        # Absence of native-control evidence is NOT proof of a safe FLE boundary.
        native_idle = snapshot.world_kind == "mock" or (
            isinstance(native, dict) and native.get("walking") is False
            and native.get("mining") is False
            and native.get("status") in {"idle", "completed", "failed"}
            and snapshot.factory.get("crafting_queue") == 0
            and snapshot.factory.get("player_bound") is True)
        if self.clock() > request["deadline"]:
            return "quiescence_timeout"  # Remain admission-closed; never force a stop.
        return "quiescent" if not outstanding and native_idle else "quiescing"

    def admission(self, memory, snapshot) -> str | None:
        maintenance = self.maintenance(memory, snapshot)
        if maintenance:
            self.phase = maintenance
            return maintenance
        if not storage_ready(self.outputs, minimum_bytes=self.minimum_bytes):
            self.phase = "storage_pressure"
            return self.phase
        self.phase = "healthy"
        return None

    def before_dispatch(self, session_id: str) -> None:
        if maintenance_request(self.checkpoint, session_id) is not None:
            raise MaintenanceAdmissionClosed("New mutation rejected by maintenance request")
        # A second check closes the long-model-call window; this is advisory
        # reserve, not a guarantee against a competing writer exhausting disk.
        if not storage_ready(self.outputs, minimum_bytes=self.minimum_bytes):
            self.phase = "storage_pressure"
            raise StoragePressure()

    def publish(self, memory, snapshot, *, process_id: str, provenance: dict,
                provider: dict | None = None, verified: bool = False) -> dict:
        now = self.clock()
        phase = self.maintenance(memory, snapshot) or self.phase
        if provider and provider.get("phase") != "healthy" and phase == "healthy":
            phase = "provider_blocked"
        # Use observed movement, receipts and production, not heartbeat freshness,
        # as evidence of progress. Native tick alone does not imply gameplay.
        signature = hashlib.sha256(json.dumps({
            "position": snapshot.player_position, "inventory": snapshot.inventory,
            "produced": snapshot.factory.get("produced", {}),
            "research": snapshot.factory.get("research", {}),
            "goals": memory.completed_goals,
            "outcomes": [(e.get("id"), e.get("outcome")) for e in memory.attempt_outcomes],
        }, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if self.last_signature is not None and (signature != self.last_signature or verified):
            self.last_progress_at = now
        self.last_signature = signature
        pending = memory.pending or {}
        status = {"schema": 1, "at": now, "pid": os.getpid(), "process_id": process_id,
                  "execution_id": provenance.get("execution_id"),
                  "session_id": memory.session_id, "phase": phase,
                  "request_id": self.request["request_id"] if self.request else None,
                  "native_tick": snapshot.tick, "last_progress_at": self.last_progress_at,
                  "observed_progress": self.last_progress_at is not None,
                  "pending_action": pending.get("action"),
                  "pending_dispatch": pending.get("dispatch"),
                  "background_owned": bool(getattr(memory, "background_job", None)),
                  "provider": provider,
                  "checkpoint_sha256": hashlib.sha256(self.checkpoint.read_bytes()).hexdigest()}
        atomic_json(self.directory / "health.json", status)
        return status
