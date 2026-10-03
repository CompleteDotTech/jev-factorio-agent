"""Explicit metadata-race fixtures; no exploit or native acceptance claim."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio import compatible_recovery as recovery


@pytest.mark.parametrize("field", ["st_uid", "st_mode", "st_nlink"])
def test_authorization_capture_rejects_metadata_drift_with_identical_bytes(tmp_path, monkeypatch, field):
    path = tmp_path / "authority.json"
    raw = json.dumps({key: None for key in recovery._KEYS}).encode()
    path.write_bytes(raw)
    path.chmod(0o400)
    original = Path.lstat
    count = []

    def changed(self):
        value = original(self)
        if self == path:
            count.append(True)
            if len(count) == 2:
                fields = {name: getattr(value, name) for name in (
                    "st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")}
                fields[field] += 1
                return SimpleNamespace(**fields)
        return value

    monkeypatch.setattr(Path, "lstat", changed)
    with pytest.raises(ValueError, match="changed during read"):
        recovery.read_authorization(path, hashlib.sha256(raw).hexdigest())
