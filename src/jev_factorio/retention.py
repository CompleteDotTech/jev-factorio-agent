"""Manifest-authorized archive/prune for explicitly sealed, inactive files only.

No broad globs, automatic discovery/deletion, secret-bearing paths, active logs,
checkpoints, receipts, or same-filesystem archive destinations are accepted.
The caller must approve the finite retention manifest after proving the files
are inactive and not referenced by pending actions/incidents. This module never
asserts that an arbitrary supplied file has been sealed by its current writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

from .operational_safety import SafetyStateError, atomic_json

PROTECTED = ("checkpoint", "supervisor", "maintenance", "pending", "receipt", "auth",
             "credential", "secret", ".env", "provider", "repair.log", "gameplay.log")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def selected(root: Path, entry: dict) -> Path:
    relative = Path(entry.get("path", ""))
    if (relative.is_absolute() or not relative.parts or ".." in relative.parts
            or entry.get("sealed") is not True or entry.get("pending_referenced") is not False
            or any(part in str(relative).lower() for part in PROTECTED)):
        raise SafetyStateError("Only approved sealed files outside protected evidence are eligible")
    path = root / relative
    current = path
    while current != root:
        if current.is_symlink():
            raise SafetyStateError("Retention never follows symlinks")
        current = current.parent
    return path


def archive(manifest: dict, destination: Path, *, maximum_bytes: int,
            prune: bool = False, protected: tuple[Path, ...] = ()) -> dict:
    if manifest.get("schema") != 1 or manifest.get("approved") is not True:
        raise SafetyStateError("An explicitly approved retention manifest is required")
    root = Path(manifest["source_root"])
    destination = Path(destination)
    if (root.is_symlink() or not root.is_dir() or destination.is_symlink()
            or not destination.is_dir() or maximum_bytes <= 0):
        raise SafetyStateError("Existing ordinary source/archive directories and capacity bound required")
    if root.stat().st_dev == destination.stat().st_dev:
        raise SafetyStateError("Archive destination must be on a different filesystem")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 0 < len(entries) <= 1000:
        raise SafetyStateError("A nonempty, bounded explicit file list is required")
    protected = tuple(Path(p).resolve() for p in protected)
    resolved, total = [], 0
    for entry in entries:
        path = selected(root, entry)
        if path.resolve() in protected or any(p in path.resolve().parents for p in protected):
            raise SafetyStateError("Active or referenced evidence is protected")
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or type(entry.get("size")) is not int or entry["size"] != info.st_size
                or digest(path) != entry.get("sha256")):
            raise SafetyStateError("Sealed source identity or digest differs")
        if path in [item[0] for item in resolved]:
            raise SafetyStateError("Duplicate retention entry")
        resolved.append((path, entry, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)))
        total += info.st_size
    # The destination must be a dedicated capacity-controlled archive root.
    used = 0
    began = time.monotonic()
    for number, existing in enumerate(destination.rglob("*"), 1):
        if number > 200000 or time.monotonic() - began > 15:
            raise SafetyStateError("Archive capacity scan exceeded its bound")
        if existing.is_symlink():
            raise SafetyStateError("Archive directory contains a symlink")
        if existing.is_file():
            used += existing.stat().st_size
    if used + total + 65536 > maximum_bytes or shutil.disk_usage(destination).free < total + 65536:
        raise SafetyStateError("Archive capacity budget would be exceeded")
    identifier = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    report = {"schema": 1, "manifest_sha256": identifier, "files": [], "prune_requested": prune}
    for path, entry, identity in resolved:
        archived = destination / (entry["sha256"] + ".sealed")
        if archived.exists():
            if digest(archived) != entry["sha256"]:
                raise SafetyStateError("Archive content-address conflict")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".sealed-copy-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as output, path.open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if digest(Path(temporary)) != entry["sha256"]:
                    raise SafetyStateError("Archive copy digest mismatch")
                # O_EXCL-style publication: never overwrite another archiver.
                os.link(temporary, archived)
            finally:
                os.unlink(temporary)
        info = path.lstat()
        if ((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != identity
                or digest(archived) != entry["sha256"] or digest(path) != entry["sha256"]):
            raise SafetyStateError("Source changed during archive; no source removed")
        report["files"].append({"path": entry["path"], "sha256": entry["sha256"],
                                "archive": archived.name, "source_removed": False})
    receipt = destination / (identifier + ".receipt.json")
    # Fsync the archive directory BEFORE any deletion (including existing copies).
    atomic_json(receipt, report)
    if prune:
        # A separate --prune option authorizes only this already approved finite
        # scope. Every source is rechecked immediately before unlink, and every
        # deletion's receipt is synced. Active writers must be excluded upstream.
        for (path, entry, identity), row in zip(resolved, report["files"]):
            info = path.lstat()
            if ((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != identity
                    or digest(path) != entry["sha256"]):
                raise SafetyStateError("Source changed; remaining prune refused")
            path.unlink()
            if os.name == "posix":
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            row["source_removed"] = True
            atomic_json(receipt, report)
    return report


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--maximum-bytes", type=int, required=True)
    parser.add_argument("--protect", type=Path, action="append", required=True,
                        help="Active run/checkpoint/incident roots; repeat for each")
    parser.add_argument("--prune", action="store_true", help="Explicitly authorize deletion within this manifest only")
    args = parser.parse_args()
    report = archive(json.loads(args.manifest.read_text()), args.destination,
                     maximum_bytes=args.maximum_bytes, prune=args.prune, protected=tuple(args.protect))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    cli()
