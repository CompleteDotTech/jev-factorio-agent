"""Non-disruptive local maintenance handshake; no adapters or service restarts.

python -m jev_factorio.operations --checkpoint ... request --timeout 120
python -m jev_factorio.operations --checkpoint ... verify REQUEST_ID
python -m jev_factorio.operations --checkpoint ... release REQUEST_ID
"""
from __future__ import annotations

import argparse
import hashlib
from uuid import uuid4
import json
import os
import time
from pathlib import Path

from .operational_safety import (SafetyStateError, atomic_json, maintenance_request,
    read_json, request_maintenance, safety_dir, verify_quiescence, maintenance_lock)


def release(checkpoint: Path, request_id: str):
    with maintenance_lock(checkpoint):
        _release(checkpoint, request_id)


def _release(checkpoint: Path, request_id: str):
    memory = read_json(checkpoint, maximum_bytes=64 * 1024 ** 2)
    if memory is None:
        raise SafetyStateError("Existing checkpoint required")
    request = maintenance_request(checkpoint, memory.get("session_id"))
    if request is None or request["request_id"] != request_id:
        raise SafetyStateError("Active request identity differs")
    # Retain the exact request and explicit release; never erase pending evidence.
    directory = safety_dir(checkpoint)
    cutover = read_json(directory / ('cutover-' + request_id + '.json'))
    if cutover is not None and cutover.get('phase') != 'stopped':
        raise SafetyStateError('Stop outcome unresolved; reconcile it before releasing admission')
    atomic_json(directory / ("maintenance-released-" + request_id + ".json"),
                {"request": request, "released_at": time.time()})
    (directory / "maintenance.json").unlink()
    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request")
    request.add_argument("--timeout", type=float, default=120)
    verify = sub.add_parser("verify")
    verify.add_argument("request_id")
    done = sub.add_parser("release")
    done.add_argument("request_id")
    authorization = sub.add_parser("authorize-provider-probes")
    authorization.add_argument("--incident-id", required=True)
    authorization.add_argument("--evidence", type=Path, required=True,
                               help="Existing reviewed recovery evidence; only its digest is recorded")
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "request":
            value = request_maintenance(args.checkpoint, timeout=args.timeout)
        elif args.command == "verify":
            value = verify_quiescence(args.checkpoint, args.request_id)
        elif args.command == "release":
            release(args.checkpoint, args.request_id)
            value = {"released": args.request_id}
        elif args.command == "authorize-provider-probes":
            circuit = read_json(safety_dir(args.checkpoint) / "provider.json")
            if circuit is None or circuit.get("incident_id") != args.incident_id:
                raise SafetyStateError("Provider incident differs")
            if args.evidence.is_symlink() or not args.evidence.is_file():
                raise SafetyStateError("A regular evidence file is required")
            value = {"request_id": str(uuid4()), "incident_id": args.incident_id,
                     "evidence_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest()}
            atomic_json(safety_dir(args.checkpoint) / "provider-authorization.json", value)
        else:
            value = read_json(safety_dir(args.checkpoint) / "health.json")
        print(json.dumps(value, sort_keys=True))
    except (SafetyStateError, OSError, ValueError) as error:
        parser.exit(2, "Operation refused: " + type(error).__name__ + "\n")


if __name__ == "__main__":
    cli()
