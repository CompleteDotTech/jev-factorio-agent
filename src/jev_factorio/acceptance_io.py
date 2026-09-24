"""Local evidence IO; no backend initialization, shell commands or network."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat

MAX_JSON = 16 * 1024 * 1024
MAX_LOG = 256 * 1024 * 1024
MAX_RECORDS = 50000


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(raw: bytes | str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Non-finite JSON value')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    def validate(node, depth=0):
        if depth > 64:
            raise ValueError('JSON nesting budget exceeded')
        if type(node) is float and not math.isfinite(node):
            raise ValueError('Non-finite JSON value')
        if isinstance(node, dict):
            for item in node.values(): validate(item, depth + 1)
        elif isinstance(node, list):
            for item in node: validate(item, depth + 1)
    validate(value)
    return value


def stable_read(path: Path, maximum: int = MAX_JSON, *, private: bool = False) -> bytes:
    """Reject symlink/nonregular endpoints and reads changed during collection."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError('Input is not a bounded regular file')
        if private and (before.st_mode & 0o077 or before.st_uid != os.geteuid()):
            raise ValueError('Private configuration must be owned by this user with mode 0600 or stricter')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if (path.is_symlink() or len(raw) > maximum or len(raw) != before.st_size
            or any(getattr(before, k) != getattr(after, k) for k in keys)):
        raise ValueError('Input changed during capture')
    return raw


def hash_file(path: Path, maximum: int = 16 * 1024**3) -> dict:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    digest, size = hashlib.sha256(), 0
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError('Save is not a bounded nonempty regular file')
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            if size > maximum: raise ValueError('Save size exceeded budget')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if path.is_symlink() or size != before.st_size or any(getattr(before, k) != getattr(after, k)
            for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
        raise ValueError('Save changed during capture')
    return {'sha256': digest.hexdigest(), 'bytes': size}


def records(raw: bytes) -> list[dict]:
    if not raw or len(raw) > MAX_LOG or not raw.endswith(b'\n'):
        raise ValueError('Expected a bounded complete JSONL stream')
    lines = raw.splitlines()
    if len(lines) > MAX_RECORDS: raise ValueError('Record count budget exceeded')
    result = []
    for line in lines:
        if not line or len(line) > MAX_JSON: raise ValueError('Invalid or oversized JSONL record')
        row = load_json(line)
        if not isinstance(row, dict): raise ValueError('JSONL records must be objects')
        result.append(row)
    return result


def write_new(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
