"""Typed recovery routing. An unexplained exit is never evidence of a code defect."""
from __future__ import annotations

import errno
import hashlib
import os
import time
from pathlib import Path

from .operational_safety import atomic_json, read_json, safety_dir

CLASSES = {"provider_unavailable", "account_quota_blocked", "storage_pressure",
           "native_action_failure", "uncertain_outcome", "source_defect",
           "campaign_complete", "campaign_deadline", "unclassified"}


def classify(reason: str, checkpoint: dict, evidence: dict | None = None) -> str:
    if reason == "completed":
        return "campaign_complete"
    if reason == "cutoff":
        return "campaign_deadline"
    if checkpoint.get("status") == "uncertain" or reason in {
        "uncertain", "checkpoint_reconciliation", "checkpoint_invalid_on_restart"}:
        return "uncertain_outcome"
    evidence = evidence or {}
    kind = evidence.get("failure_class")
    if kind == "source_defect":
        # Source repair requires a positive evidence declaration, not an inferred
        # HTTP exception, process exit code or missing heartbeat.
        proof = evidence.get("source_evidence_sha256")
        if (isinstance(proof, str) and len(proof) == 64
                and all(c in "0123456789abcdef" for c in proof)
                and not checkpoint.get("pending") and not checkpoint.get("background_job")
                and not checkpoint.get("background_attempt")):
            return kind
        return "uncertain_outcome" if checkpoint.get("pending") else "unclassified"
    if kind in CLASSES - {"campaign_complete", "campaign_deadline", "source_defect"}:
        return kind
    if checkpoint.get("pending") or checkpoint.get("background_job") or checkpoint.get("background_attempt"):
        return "uncertain_outcome"
    if reason == "blocked":
        return "native_action_failure" if evidence.get("native_failure") else "unclassified"
    return "unclassified"


def exception_class(error: BaseException, *, pending: bool) -> str:
    if getattr(error, "failure_class", None) == "storage_pressure" or (
        isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EDQUOT}
    ):
        return "storage_pressure"
    return "uncertain_outcome" if pending else "unclassified"


def record_exit(loop, error: BaseException, *, clock=time.time) -> None:
    checkpoint = getattr(loop, "checkpoint", None)
    memory = getattr(loop, "memory", None)
    if checkpoint is None or memory is None:
        return
    pending = bool(memory.pending or getattr(memory, "background_job", None)
                   or getattr(memory, "background_attempt", None))
    evidence = {"schema": 1, "at": clock(), "pid": os.getpid(),
                "execution_id": loop.provenance.get("execution_id"),
                "session_id": memory.session_id,
                "failure_class": exception_class(error, pending=pending),
                "pending_owned": pending,
                "native_effect_possible": pending or getattr(error, "native_effect_possible", False),
                "error_type_sha256": hashlib.sha256(type(error).__name__.encode()).hexdigest()}
    try:
        atomic_json(safety_dir(checkpoint) / "exit.json", evidence)
    except (OSError, RuntimeError):
        # ENOSPC can prevent even this compact evidence. The durable prepared
        # checkpoint remains authoritative; supervisor treats unknown as blocked.
        pass


def current_exit(checkpoint: Path, *, session_id: str, execution_id: str | None) -> dict | None:
    evidence = read_json(safety_dir(checkpoint) / "exit.json")
    if (not execution_id or evidence is None or evidence.get("schema") != 1
            or evidence.get("session_id") != session_id
            or evidence.get("execution_id") != execution_id):
        return None
    return evidence


def repair_quota_exhausted(path: Path, offset: int = 0) -> bool:
    """Classify this attempt only; never persist or print raw provider output."""
    try:
        with path.open("rb") as stream:
            size = path.stat().st_size
            stream.seek(max(offset, size - 65536))
            text = stream.read(65536).lower()
    except FileNotFoundError:
        return False
    return any(marker in text for marker in (
        b"you've hit your usage limit", b"usage_limit_reached", b"insufficient_quota",
        b"usage allowance exhausted", b"usage limit has been reached"))
