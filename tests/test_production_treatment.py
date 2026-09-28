"""The opt-in treatment is checked before the native backend is attached."""
import json
import sys

import pytest

from jev_factorio.coal_supply import intents
from jev_factorio.treatment import SCHEMA, SCHEMA_V2, load
from jev_factorio import main


def treatment():
    return {'schema': SCHEMA, 'solid_intents': intents(['burner-a', 'burner-b']),
            'coal_targets': ['burner-a', 'burner-b'],
            'solid_science_policy': False, 'coal_kit_policy': True}


def test_v2_economic_admission_is_immutable_and_requires_a_coal_kit(tmp_path):
    path = tmp_path / 'treatment.json'
    baseline = treatment()
    path.write_text(json.dumps(baseline))
    legacy_digest = load(path)[1]
    economic = {**baseline, 'schema': SCHEMA_V2, 'coal_economic_admission': True}
    path.write_text(json.dumps(economic))
    assert load(path)[0] == economic
    assert load(path)[1] != legacy_digest
    for invalid in ({**economic, 'coal_economic_admission': False},
                    {**economic, 'coal_kit_policy': False},
                    {**economic, 'coal_economic_admission': 1}):
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            load(path)


def test_canonical_treatment_digest_and_exact_coal_binding(tmp_path):
    path = tmp_path / 'treatment.json'
    path.write_text(json.dumps(treatment()))
    value, digest = load(path)
    assert value == treatment() and len(digest) == 64
    path.write_text(json.dumps(dict(reversed(list(treatment().items()))), indent=2))
    assert load(path)[1] == digest
    altered = treatment()
    altered['coal_targets'] = ['burner-b', 'burner-a']
    path.write_text(json.dumps(altered))
    assert load(path)[1] != digest
    altered['solid_intents'] = altered['solid_intents'][:1]
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='corridor'):
        load(path)


def test_duplicate_policy_key_and_unbound_policy_fail_closed(tmp_path):
    path = tmp_path / 'treatment.json'
    path.write_text('{"schema":"' + SCHEMA + '","schema":"' + SCHEMA + '"}')
    with pytest.raises(ValueError, match='Duplicate'):
        load(path)
    altered = treatment()
    altered['coal_targets'] = []
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match='requires coal targets'):
        load(path)


def test_invalid_composed_checkpoint_rejected_before_backend_attach(tmp_path, monkeypatch):
    path = tmp_path / 'treatment.json'
    path.write_text(json.dumps(treatment()))
    checkpoint = tmp_path / 'checkpoint.json'
    checkpoint.write_text(json.dumps({'session_id': 'original', 'solid_intents': treatment()['solid_intents'],
        'solid_science_policy': False, 'coal_targets': treatment()['coal_targets'],
        'coal_kit_policy': True, 'coal_epoch': {'actor_index': 1},
        'solid_epoch': {'actor_index': 1}}))  # Matching labels but no valid paid/pending memory.
    attached = []
    monkeypatch.setattr(main, 'make_backend', lambda *args, **kwargs: attached.append(True))
    monkeypatch.setattr(sys, 'argv', ['jev-factorio', '--backend', 'fle', '--resume',
        '--resume-controller', '--controller', 'hierarchical', '--policy', 'deterministic',
        '--factory-scheduling', 'ready-work', '--tick-seconds', '1', '--checkpoint', str(checkpoint),
        '--production-treatment', str(path)])
    with pytest.raises(SystemExit) as stopped:
        main.cli()
    assert stopped.value.code == 2 and not attached


@pytest.mark.parametrize('adopt_session', [False, True])
def test_treatment_cannot_attach_fresh_memory_to_existing_world(tmp_path, monkeypatch, adopt_session):
    path = tmp_path / 'treatment.json'
    path.write_text(json.dumps(treatment()))
    attached = []
    monkeypatch.setattr(main, 'make_backend', lambda *args, **kwargs: attached.append(True))
    argv = ['jev-factorio', '--backend', 'fle', '--resume', '--controller', 'hierarchical',
            '--policy', 'deterministic', '--factory-scheduling', 'ready-work',
            '--tick-seconds', '1', '--checkpoint', str(tmp_path / 'new-checkpoint.json'),
            '--production-treatment', str(path)]
    if adopt_session:
        argv.append('--adopt-session')
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(SystemExit) as stopped:
        main.cli()
    assert stopped.value.code == 2 and not attached
