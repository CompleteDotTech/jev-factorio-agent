"""An explicit isolated window cannot silently remove production deadlines."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from jev_factorio.native_acceptance_preflight import assess, main
from test_native_acceptance_preflight import manifest, NOW


def isolated():
    data = manifest()
    data.update(schema=2, original_cutoff_utc=None, runtime_cutoff_utc=None,
                window_policy={'mode': 'until_complete', 'scope': 'isolated',
                               'authorization_sha256': '9' * 64})
    return data


def test_until_complete_has_no_invented_deadline_and_does_not_authorize_execution():
    data = isolated()
    before = deepcopy(data)
    report = assess(data, now=NOW + timedelta(days=30))
    assert report['ready_for_operator_review']
    assert report['schema'] == 2
    assert report['remaining_original_window_seconds'] is None
    assert report['required_window_seconds'] == 1800
    assert not report['window_policy']['authority_verified']
    assert all(report[key] is False for key in (
        'campaign_started', 'cutoff_changed', 'deployment_authorized',
        'native_acceptance_proven', 'causal_speed_claim_eligible'))
    assert data == before
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('key', ['original_cutoff_utc', 'runtime_cutoff_utc'])
def test_timed_campaign_cannot_be_relabelled_to_remove_its_deadline(key):
    data = isolated()
    data[key] = manifest()[key]
    with pytest.raises(ValueError, match='existing cutoff'):
        assess(data, now=NOW)


@pytest.mark.parametrize('change', [
    {'scope': 'production'}, {'mode': 'forever'}, {'mode': 'until_complete '},
    {'authorization_sha256': None}, {'authorization_sha256': True},
    {'authorization_sha256': 'private-credential'}, {'extra': True},
])
def test_ambiguous_or_unbound_authority_is_rejected(change):
    data = isolated()
    data['window_policy'].update(change)
    with pytest.raises(ValueError):
        assess(data, now=NOW)


@pytest.mark.parametrize('key', ['original_cutoff_utc', 'runtime_cutoff_utc', 'window_policy'])
def test_omitted_authority_fields_are_not_interpreted_as_no_deadline(key):
    data = isolated()
    del data[key]
    with pytest.raises((ValueError, KeyError)):
        assess(data, now=NOW)


def test_until_complete_retains_science_window_and_all_source_runtime_gates():
    data = isolated()
    data['useful_progress_window_seconds'] = 1799
    with pytest.raises(ValueError, match='1800'):
        assess(data, now=NOW)
    data['useful_progress_window_seconds'] = 1800
    data['implementation']['101']['independent_review_verified'] = False
    data['runtime']['readback_verified'] = False
    data['established_operational_handoff_verified'] = False
    report = assess(data, now=NOW)
    assert not report['ready_for_operator_review']
    assert set(report['blockers']) == {'implementation_not_ready:101',
        'runtime_readback_unverified', 'operational_handoff_unverified'}


def test_legacy_deadline_contract_is_retained():
    data = manifest()
    report = assess(data, now=NOW)
    assert report['schema'] == 1
    assert report['remaining_original_window_seconds'] == 3600
    assert 'window_policy' not in report
    assert 'authorized_window_insufficient' in assess(data, now=NOW + timedelta(hours=2))['blockers']
    data['window_policy'] = isolated()['window_policy']
    with pytest.raises(ValueError, match='explicit v2'):
        assess(data, now=NOW)


def test_cli_round_trip_and_invalid_manifest_redaction(tmp_path, capsys):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(isolated()))
    main([str(path)])
    report = json.loads(capsys.readouterr().out)
    assert report['ready_for_operator_review']
    data = isolated()
    data['window_policy']['authorization_sha256'] = 'private-credential'
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit) as failure:
        main([str(path)])
    assert failure.value.code == 2
    output = capsys.readouterr()
    assert 'private-credential' not in output.err + output.out
