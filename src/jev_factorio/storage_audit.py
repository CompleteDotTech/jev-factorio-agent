"""Bounded, read-only disk attribution and growth measurements; never deletes.

Roots are explicitly named by the operator, not inferred from historical paths.
Symlinks, filesystem crossings and duplicate hardlinks are not traversed/counted.
A partial scan cannot establish a growth rate.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import time
from pathlib import Path

from .operational_safety import atomic_json, storage_sample


def snapshot(roots: dict[str, Path], *, maximum_entries: int = 200000,
             seconds: float = 15, clock=time.monotonic) -> dict:
    if not roots or maximum_entries < 1 or not 0 < seconds <= 3600:
        raise ValueError("Positive bounded scan parameters and named roots required")
    paths = {name: Path(path).absolute() for name, path in roots.items()}
    if any(p.is_symlink() or not p.is_dir() for p in paths.values()):
        raise ValueError("Inventory roots must be existing, non-symlink directories")
    values = [p.resolve() for p in paths.values()]
    if any(a == b or a in b.parents or b in a.parents
           for i, a in enumerate(values) for b in values[i + 1:]):
        raise ValueError("Overlapping inventory roots would double count growth")
    started = clock()
    remaining = maximum_entries
    seen = set()
    rows = {}
    for name, path in sorted(paths.items()):
        device = path.stat().st_dev
        row = {"path": str(path), "device": device, "allocated_bytes": 0,
               "logical_bytes": 0, "entries": 0, "complete": True,
               "errors": 0, "cross_filesystem_skips": 0}
        pending = [path]
        while pending:
            if remaining <= 0 or clock() - started >= seconds:
                row["complete"] = False
                break
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if remaining <= 0 or clock() - started >= seconds:
                            row["complete"] = False
                            break
                        remaining -= 1
                        row["entries"] += 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if info.st_dev != device:
                                row["cross_filesystem_skips"] += 1
                                continue
                            identity = info.st_dev, info.st_ino
                            if identity in seen:
                                continue
                            seen.add(identity)
                            if stat.S_ISREG(info.st_mode):
                                row["logical_bytes"] += info.st_size
                                row["allocated_bytes"] += getattr(info, "st_blocks", 0) * 512
                            elif stat.S_ISDIR(info.st_mode):
                                pending.append(Path(entry.path))
                        except OSError:
                            row["errors"] += 1
                            row["complete"] = False
            except OSError:
                row["errors"] += 1
                row["complete"] = False
        row["filesystem"] = storage_sample(path)
        rows[name] = row
    cpu = {}
    for name, file in (("cpu_pressure", "/proc/pressure/cpu"), ("load_average", "/proc/loadavg")):
        try:
            cpu[name] = Path(file).read_text()[:1024].strip()
        except OSError:
            cpu[name] = None
    return {"schema": 1, "at": time.time(), "scan_seconds": clock() - started,
            "complete": all(row["complete"] for row in rows.values()),
            "roots": rows, "cpu": cpu}


def growth(before: dict, after: dict) -> dict:
    elapsed = after["at"] - before["at"]
    if elapsed <= 0 or set(before["roots"]) != set(after["roots"]):
        raise ValueError("Snapshots must have matching roots and increasing timestamps")
    rows = {}
    for name, row in after["roots"].items():
        old = before["roots"][name]
        comparable = (old["complete"] and row["complete"]
                      and old["path"] == row["path"] and old["device"] == row["device"])
        delta = row["allocated_bytes"] - old["allocated_bytes"] if comparable else None
        rows[name] = {"comparable": comparable, "allocated_delta": delta,
                      "bytes_per_second": delta / elapsed if delta is not None else None}
    return {"elapsed_seconds": elapsed, "roots": rows,
            "note": "Changes attribute measured roots, not the initiating application fault."}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", required=True, metavar="LABEL=PATH")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--maximum-entries", type=int, default=200000)
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()
    roots = {}
    for value in args.root:
        name, path = value.split("=", 1)
        if not name or name in roots:
            parser.error("Each root needs a unique nonempty label")
        roots[name] = Path(path)
    report = snapshot(roots, maximum_entries=args.maximum_entries, seconds=args.seconds)
    if args.previous:
        report["growth"] = growth(json.loads(args.previous.read_text()), report)
    atomic_json(args.output, report)
    print(json.dumps({"complete": report["complete"], "scan_seconds": report["scan_seconds"]}))


if __name__ == "__main__":
    cli()
