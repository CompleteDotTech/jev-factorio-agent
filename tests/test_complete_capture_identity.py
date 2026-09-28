"""A checksum-consistent capture must still belong to one campaign and actor."""
import gzip
import hashlib
import json

import pytest

from jev_factorio.acceptance_io import canonical
from jev_factorio.complete_capture import capture, verify
from test_complete_capture import test_complete_capture_v2_admission_roundtrip as _fixture


def _rewrite_bundle(directory, final, rows):
    raw = b''.join(canonical(row) for row in rows)
    (directory / 'final-checkpoint.json').write_bytes(canonical(final))
    (directory / 'gameplay.jsonl.gz').write_bytes(gzip.compress(raw, mtime=0))
    manifest = json.loads((directory / 'capture-manifest.json').read_bytes())
    manifest.update(decompressed_bytes=len(raw),
                    decompressed_sha256=hashlib.sha256(raw).hexdigest())
    (directory / 'capture-manifest.json').write_bytes(canonical(manifest))
    (directory / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
        for path in sorted(directory.iterdir()) if path.name != 'SHA256SUMS'))


def _change_identity(kind, final, rows):
    if kind == 'final_checkpoint_session':
        final['session_id'] = 'different-session'
        after = rows[-1]['after_state']
        after['session_id'] = final['session_id']
        for family in ('solid_routes', 'coal_supply'):
            after['factory'][family]['session_id'] = final['session_id']
        after['factory']['coal_supply']['admission']['session_id'] = final['session_id']
    elif kind == 'final_checkpoint_target':
        final['target'] = 'automation_science'
    elif kind == 'record_session':
        rows[1]['session_id'] = 'different-session'
    elif kind == 'record_target':
        rows[1]['target'] = 'automation_science'
    elif kind == 'snapshot_session':
        before = rows[1]['state']
        before['session_id'] = 'different-session'
        for family in ('solid_routes', 'coal_supply'):
            before['factory'][family]['session_id'] = before['session_id']
        before['factory']['coal_supply']['admission']['session_id'] = before['session_id']
    elif kind == 'solid_session':
        rows[1]['state']['factory']['solid_routes']['session_id'] = 'different-session'
    elif kind == 'intermediate_actor':
        for family in ('solid_routes', 'coal_supply'):
            rows[1]['state']['factory'][family]['actor_index'] = 2
        rows[1]['state']['factory']['coal_supply']['admission']['actor_index'] = 2
    elif kind == 'final_actor':
        for epoch in ('solid_epoch', 'coal_epoch'):
            final[epoch]['actor_index'] = 2
        for family in ('solid_routes', 'coal_supply'):
            rows[-1]['after_state']['factory'][family]['actor_index'] = 2
        rows[-1]['after_state']['factory']['coal_supply']['admission']['actor_index'] = 2


@pytest.mark.parametrize('kind', [
    'final_checkpoint_session', 'final_checkpoint_target', 'record_session',
    'record_target', 'snapshot_session', 'solid_session', 'intermediate_actor',
    'final_actor',
])
@pytest.mark.parametrize('entrypoint', ['capture', 'verify'])
def test_complete_capture_rejects_cross_campaign_or_actor_binding(tmp_path, kind, entrypoint):
    _fixture(tmp_path)
    directory = tmp_path / 'capture'
    rows = verify(directory)['rows']
    final = json.loads((directory / 'final-checkpoint.json').read_bytes())
    _change_identity(kind, final, rows)
    if entrypoint == 'verify':
        _rewrite_bundle(directory, final, rows)
        with pytest.raises(ValueError, match='identity|epoch|session|target|binding|treatment'):
            verify(directory)
    else:
        (tmp_path / 'final.json').write_bytes(canonical(final))
        (tmp_path / 'gameplay.jsonl').write_bytes(b''.join(canonical(row) for row in rows))
        with pytest.raises(ValueError, match='identity|epoch|session|target|binding|treatment'):
            capture(gameplay=tmp_path / 'gameplay.jsonl', trial_path=tmp_path / 'trial.json',
                    initial_checkpoint=tmp_path / 'initial.json',
                    final_checkpoint=tmp_path / 'final.json', save=tmp_path / 'save.zip',
                    preflight_path=tmp_path / 'preflight.json', output=tmp_path / 'rejected')
        assert not (tmp_path / 'rejected').exists()
