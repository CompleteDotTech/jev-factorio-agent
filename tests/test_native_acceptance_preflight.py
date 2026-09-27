"""No manifest can claim to have performed a native acceptance run."""
from datetime import datetime, timezone
from copy import deepcopy
import json

import pytest

from jev_factorio.native_acceptance_preflight import assess, ISSUES

NOW=datetime(2026,9,26,20,tzinfo=timezone.utc)


def manifest():
    return {'schema':1,'useful_progress_window_seconds':1800,'rollout_margin_seconds':300,
        'original_cutoff_utc':'2026-09-26T21:00:00Z','runtime_cutoff_utc':'2026-09-26T21:00:00Z',
        'predeclared_experiment_sha256':'a'*64,'comparison_kind':'matched_native',
        'baseline_save_sha256':'b'*64,'treatment_initial_save_sha256':'b'*64,
        'implementation':{str(n):{'implementation_available':True,'independent_review_verified':True,
            'final_head_checks_verified':True,'ssh_signature_verified':True,'integration_state':'merged',
            'source_commit':'c'*40} for n in ISSUES},
        'runtime':{'deployed_commit':'c'*40,'configuration_sha256':'d'*64,
            'ownership_checkpoint_sha256':'e'*64,'rollback_record_sha256':'f'*64,
            'readback_verified':True,'immutable_treatment_preserved':True},
        'established_operational_handoff_verified':True}


def test_valid_manifest_is_preflight_not_native_success_or_authority():
    report=assess(manifest(),now=NOW)
    assert report['ready_for_operator_review']
    assert not report['native_acceptance_proven'] and not report['deployment_authorized']
    assert not report['campaign_started'] and not report['cutoff_changed']
    assert 'operator_supplied' in report['evidence_trust']


@pytest.mark.parametrize('damage,blocker',[
    ('window','authorized_window_insufficient'),('cutoff','original_cutoff_changed'),
    ('saves','matched_initial_saves_differ'),('native','runtime_readback_unverified'),
    ('treatment','immutable_treatment_unverified'),('signing','implementation_not_ready:98'),
    ('review','implementation_not_ready:100'),('missing','implementation_not_ready:102'),
    ('operational','operational_handoff_unverified')])
def test_missing_evidence_never_passes_or_extends_cutoff(damage,blocker):
    data=manifest()
    if damage=='window':
        data['original_cutoff_utc']=data['runtime_cutoff_utc']='2026-09-26T20:29:59Z'
    elif damage=='cutoff': data['runtime_cutoff_utc']='2026-09-26T22:00:00Z'
    elif damage=='saves': data['treatment_initial_save_sha256']='a'*64
    elif damage=='native': data['runtime']['readback_verified']=False
    elif damage=='treatment': data['runtime']['immutable_treatment_preserved']=False
    elif damage=='signing': data['implementation']['98']['ssh_signature_verified']=False
    elif damage=='review': data['implementation']['100']['independent_review_verified']=False
    elif damage=='missing': data['implementation'].pop('102')
    elif damage=='operational': data['established_operational_handoff_verified']=False
    before=deepcopy(data)
    result=assess(data,now=NOW)
    assert not result['ready_for_operator_review'] and blocker in result['blockers']
    assert data==before


def test_short_probe_is_rejected_instead_of_relabelled_thirty_minutes():
    data=manifest();data['useful_progress_window_seconds']=1799
    with pytest.raises(ValueError): assess(data,now=NOW)


def test_unmatched_production_trend_cannot_qualify_for_causal_speed_claim():
    data=manifest();data['comparison_kind']='unmatched_production_trend'
    data['treatment_initial_save_sha256']='c'*64
    result=assess(data,now=NOW)
    assert result['ready_for_operator_review'] and not result['causal_speed_claim_eligible']


def test_private_arbitrary_manifest_text_is_not_in_public_report():
    data=manifest();data['private_notes']='private-host-and-path'
    assert 'private-host-and-path' not in json.dumps(assess(data,now=NOW))
