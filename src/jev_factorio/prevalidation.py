"""Private, same-host full-suite evidence; not a boundary against a malicious owner."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
import time
import uuid
import xml.etree.ElementTree as ET

MAX_TTL = 3600
COMMAND = ["-m", "pytest", "tests/"]
ENVIRONMENT = ("PATH", "HOME", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")


def environment_values() -> dict:
    return {key: os.environ[key] for key in ENVIRONMENT if key in os.environ}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(cwd: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Prevalidation requires a clean checkout")
    packages = []
    for distribution in importlib.metadata.distributions():
        files = []
        for entry in distribution.files or []:
            path = Path(distribution.locate_file(entry))
            if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
                metadata = path.stat()
                files.append((str(entry), digest(path), metadata.st_mode,
                              metadata.st_uid, metadata.st_gid))
        packages.append((distribution.metadata["Name"], distribution.version, sorted(files)))
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "python": str(Path(sys.executable).resolve()), "python_hash": digest(Path(sys.executable)),
            "version": sys.version, "abi": sysconfig.get_config_var("SOABI"),
            "platform": platform.platform(), "host": platform.node(),
            "host_state": {str(path): digest(path) for path in
                           (Path("/proc/self/mountinfo"), Path("/proc/sys/kernel/random/boot_id"))
                           if path.is_file()},
            "environment": hashlib.sha256(canonical(environment_values())).hexdigest(),
            "dependencies": hashlib.sha256(canonical({"packages": sorted(packages)})).hexdigest()}


def private(path: Path, directory=False) -> None:
    stat = path.lstat()
    if path.is_symlink() or (not path.is_dir() if directory else not path.is_file()):
        raise ValueError("Invalid evidence path")
    if os.name == "posix" and (stat.st_uid != os.getuid() or stat.st_mode & 0o077):
        raise ValueError("Evidence must be private and owned by the verifier user")


def junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {key: sum(int(s.get(key, "0")) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    if counts["tests"] <= counts["skipped"] or counts["failures"] or counts["errors"]:
        raise ValueError("Full suite did not pass")
    return counts


@contextmanager
def run_lock(cache: Path):
    import fcntl
    path = cache / "run.lock"
    if path.is_symlink():
        raise ValueError("Invalid verifier lock")
    with path.open("a+b") as stream:
        private(path)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run(cwd: Path, state_dir: Path, ttl: int = MAX_TTL) -> str:
    if not 0 < ttl <= MAX_TTL:
        raise ValueError("Invalid expiry interval")
    if (cwd / ".env").exists():
        raise ValueError("Staging checkout must not contain a dotenv file")
    previous_mask = os.umask(0o077)
    try:
        cache = state_dir / "prevalidation"
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        private(cache, True)
        with run_lock(cache):
            return _run(cwd, cache, ttl)
    finally:
        os.umask(previous_mask)


def _run(cwd: Path, cache: Path, ttl: int) -> str:
    output = cache / ("pending-" + uuid.uuid4().hex)
    output.mkdir(mode=0o700)
    before = fingerprint(cwd)
    started = time.time()
    environment = environment_values()
    temporary = output / "tmp"
    temporary.mkdir(mode=0o700)
    environment.update(PYTHONPATH=str(cwd / "src"), TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary))
    imported = subprocess.check_output([sys.executable, "-c",
        "import jev_factorio; print(jev_factorio.__file__)"], cwd=cwd, env=environment, text=True).strip()
    if Path(imported).resolve() != (cwd / "src/jev_factorio/__init__.py").resolve():
        raise ValueError("Tests would import a different source checkout")
    report, log = output / "junit.xml", output / "pytest.log"
    with log.open("xb") as stream:
        result = subprocess.run([sys.executable, *COMMAND, "--junitxml=" + str(report)], cwd=cwd,
                                env=environment, stdin=subprocess.DEVNULL, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=3600)
        stream.flush()
        os.fsync(stream.fileno())
    if result.returncode != 0 or before != fingerprint(cwd):
        raise ValueError("Validation failed or source/runtime changed")
    counts = junit(report)
    with report.open("rb") as stream:
        os.fsync(stream.fileno())
    value = {"schema": 1, "fingerprint": before, "command": COMMAND, "started": started,
             "completed": time.time(), "ttl": ttl, "returncode": 0, "counts": counts,
             "log_sha256": digest(log), "junit_sha256": digest(report)}
    data = canonical(value)
    reference = hashlib.sha256(data).hexdigest()
    with (output / "manifest.json").open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        descriptor = os.open(output, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    output.rename(cache / reference)
    if os.name == "posix":
        descriptor = os.open(cache, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return reference


def validate(cwd: Path, state_dir: Path, reference: str, now: float | None = None) -> bool:
    try:
        if not isinstance(reference, str) or not re.fullmatch("[0-9a-f]{64}", reference):
            return False
        cache = state_dir / "prevalidation"
        output = cache / reference
        for directory in (cache, output):
            private(directory, True)
        for name in ("manifest.json", "pytest.log", "junit.xml"):
            private(output / name)
        data = (output / "manifest.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != reference:
            return False
        value = json.loads(data)
        now = time.time() if now is None else now
        return (value["schema"] == 1 and value["command"] == COMMAND and value["returncode"] == 0
                and 0 < value["ttl"] <= MAX_TTL
                and value["started"] <= value["completed"] <= now <= value["completed"] + value["ttl"]
                and value["fingerprint"] == fingerprint(cwd)
                and value["log_sha256"] == digest(output / "pytest.log")
                and value["junit_sha256"] == digest(output / "junit.xml")
                and value["counts"] == junit(output / "junit.xml"))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ET.ParseError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "check"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=MAX_TTL)
    parser.add_argument("--artifact-id")
    args = parser.parse_args()
    if args.mode == "check":
        return 0 if validate(Path.cwd(), args.state_dir, args.artifact_id) else 1
    print(json.dumps({"artifact_id": run(Path.cwd(), args.state_dir, args.ttl_seconds)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
