"""The FLE audit reports source identity, never runtime or transport timing."""
import hashlib
from pathlib import Path

from jev_factorio import fle_compat_audit as audit


class DistributionFixture:
    def __init__(self, root: Path, version: str):
        self.version = version
        self.files = [Path(relative) for relative in audit.SOURCE_SHA256]
        self.root = root

    def locate_file(self, relative):
        return self.root / relative


def test_pinned_fle_source_audit_matches_files_without_emitting_install_paths(tmp_path, monkeypatch):
    payload = b'pinned fixture source'
    monkeypatch.setattr(audit, 'SOURCE_SHA256', {
        relative: hashlib.sha256(payload).hexdigest()
        for relative in audit.SOURCE_SHA256
    })
    distribution = DistributionFixture(tmp_path, audit.RELEASE_VERSION)
    for relative in audit.SOURCE_SHA256:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    result = audit.audit_distribution(distribution)

    assert result['compatible'] is True
    assert all(row['matches_release'] for row in result['source_files'].values())
    assert result['scope'] == 'installed_source_identity_only_not_native_performance'
    assert result['private_metadata_emitted'] is False
    assert str(tmp_path) not in str(result)


def test_pinned_fle_source_audit_rejects_changed_source_and_wrong_version(tmp_path, monkeypatch):
    payload = b'expected fixture source'
    source_hashes = {
        relative: hashlib.sha256(payload).hexdigest()
        for relative in audit.SOURCE_SHA256
    }
    monkeypatch.setattr(audit, 'SOURCE_SHA256', source_hashes)
    distribution = DistributionFixture(tmp_path, '0.4.4')
    for relative in source_hashes:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    changed = tmp_path / next(iter(source_hashes))
    changed.write_bytes(b'changed source')

    result = audit.audit_distribution(distribution)

    assert result['compatible'] is False
    assert result['observed_version'] == '0.4.4'
    assert sum(not row['matches_release'] for row in result['source_files'].values()) == 1


def test_audit_command_reports_missing_dependency_without_private_details(monkeypatch, capsys):
    def missing(_name):
        raise audit.metadata.PackageNotFoundError

    monkeypatch.setattr(audit.metadata, 'distribution', missing)

    assert audit.main([]) == 2
    output = capsys.readouterr().out
    assert 'distribution_not_installed' in output
    assert 'private_metadata_emitted' in output
    assert 'site-packages' not in output
