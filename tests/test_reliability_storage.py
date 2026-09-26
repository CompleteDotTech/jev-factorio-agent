"""Read-only inventory and explicit finite retention tested on temporary files."""
import hashlib
import os
from pathlib import Path
import tempfile

import pytest

from jev_factorio.operational_safety import SafetyStateError
from jev_factorio.retention import archive, digest
from jev_factorio.storage_audit import snapshot, growth


def manifest(root, path):
    return {"schema": 1, "approved": True, "source_root": str(root), "files": [
        {"path": path.name, "sealed": True, "pending_referenced": False,
         "size": path.stat().st_size, "sha256": digest(path)}]}


@pytest.fixture
def distinct_archive(tmp_path):
    directory = Path("/dev/shm")
    if not directory.is_dir() or directory.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("A second local filesystem is needed for archive durability integration")
    with tempfile.TemporaryDirectory(prefix="jev-retention-test-", dir=directory) as path:
        yield Path(path)


def test_actual_distinct_filesystem_copy_then_authorized_prune(tmp_path, distinct_archive):
    source = tmp_path / "sealed-observations.jsonl"
    source.write_text('{"synthetic":true}\n')
    policy = manifest(tmp_path, source)
    copied = archive(policy, distinct_archive, maximum_bytes=1024 * 1024)
    assert source.exists() and copied["files"][0]["source_removed"] is False
    assert digest(distinct_archive / copied["files"][0]["archive"]) == policy["files"][0]["sha256"]
    pruned = archive(policy, distinct_archive, maximum_bytes=1024 * 1024, prune=True)
    assert not source.exists() and pruned["files"][0]["source_removed"]


def test_same_filesystem_relocation_is_not_capacity_recovery(tmp_path):
    source = tmp_path / "sealed.jsonl"; source.write_text("synthetic")
    dest = tmp_path / "elsewhere"; dest.mkdir()
    with pytest.raises(SafetyStateError, match="different filesystem"):
        archive(manifest(tmp_path, source), dest, maximum_bytes=1024**2, prune=True)
    assert source.exists()


def test_archive_capacity_exhaustion_never_prunes(tmp_path, distinct_archive):
    source = tmp_path / "sealed.jsonl"; source.write_text("synthetic")
    with pytest.raises(SafetyStateError, match="capacity"):
        archive(manifest(tmp_path, source), distinct_archive, maximum_bytes=1, prune=True)
    assert source.exists()


@pytest.mark.parametrize("name", ["checkpoint.json", "receipts.json", "auth.json", ".env", "gameplay.log"])
def test_protected_evidence_cannot_be_pruned(tmp_path, distinct_archive, name):
    source = tmp_path / name; source.write_text("synthetic")
    with pytest.raises(SafetyStateError):
        archive(manifest(tmp_path, source), distinct_archive, maximum_bytes=1024**2, prune=True)
    assert source.exists()


def test_active_root_is_protected_even_for_mislabeled_sealed_file(tmp_path, distinct_archive):
    source = tmp_path / "sealed.jsonl"; source.write_text("synthetic")
    with pytest.raises(SafetyStateError, match="protected"):
        archive(manifest(tmp_path, source), distinct_archive, maximum_bytes=1024**2,
                prune=True, protected=(tmp_path,))
    assert source.exists()


def test_failed_archive_receipt_durability_never_removes_source(tmp_path, distinct_archive, monkeypatch):
    source = tmp_path / "sealed.jsonl"; source.write_text("synthetic")
    def fail(*args):
        raise OSError("synthetic receipt ENOSPC")
    monkeypatch.setattr("jev_factorio.retention.atomic_json", fail)
    with pytest.raises(OSError):
        archive(manifest(tmp_path, source), distinct_archive, maximum_bytes=1024**2, prune=True)
    assert source.exists()


def test_symlink_scope_escape_is_refused(tmp_path, distinct_archive):
    source = tmp_path / "sealed.jsonl"; source.write_text("synthetic")
    policy = manifest(tmp_path, source)
    other = tmp_path / "other.jsonl"; other.write_text("synthetic")
    source.unlink(); source.symlink_to(other)
    with pytest.raises(SafetyStateError, match="symlink"):
        archive(policy, distinct_archive, maximum_bytes=1024**2, prune=True)
    assert other.exists()


def test_inventory_is_read_only_deduplicates_hardlinks_and_attributes_growth(tmp_path):
    source = tmp_path / "payload"; source.write_bytes(b"a" * 8192)
    (tmp_path / "alias").hardlink_to(source)
    before = snapshot({"controller": tmp_path})
    assert before["complete"]
    assert before["roots"]["controller"]["logical_bytes"] == 8192
    source.write_bytes(b"b" * 16384)
    after = snapshot({"controller": tmp_path})
    after["at"] = before["at"] + 10
    report = growth(before, after)
    assert report["roots"]["controller"]["comparable"]
    assert report["roots"]["controller"]["allocated_delta"] > 0
    assert source.read_bytes() == b"b" * 16384


def test_partial_inventory_never_claims_a_measured_growth_rate(tmp_path):
    for index in range(5): (tmp_path / str(index)).write_text("synthetic")
    before = snapshot({"container_storage": tmp_path}, maximum_entries=1)
    after = snapshot({"container_storage": tmp_path})
    after["at"] = before["at"] + 10
    assert not before["complete"]
    assert growth(before, after)["roots"]["container_storage"]["bytes_per_second"] is None


def test_inventory_rejects_overlapping_attribution_roots(tmp_path):
    child = tmp_path / "child"; child.mkdir()
    with pytest.raises(ValueError, match="Overlapping"):
        snapshot({"all": tmp_path, "controller": child})
