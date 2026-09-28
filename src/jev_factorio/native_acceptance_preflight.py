"""Read-only readiness checks for issue #92; never starts or extends a campaign.

The manifest contains operator evidence claims, not remote attestation. Even
successful preflight does not prove native acceptance or authorize deployment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

ISSUES = (98, 99, 100, 101, 102, 93, 94, 95, 96, 97)
MAX_MANIFEST = 128 * 1024


def utc(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Expected bounded UTC timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() != timezone.utc.utcoffset(result):
        raise ValueError('UTC timestamp required')
    return result


def digest(value, length=64):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{%d}' % length, value) is not None


def window_remaining(manifest: dict, now: datetime, required: int, blockers: list) -> float | None:
    """Validate the declared authority scope without creating or extending it."""
    if manifest['schema'] == 2:
        policy = manifest.get('window_policy')
        if (not isinstance(policy, dict)
                or set(policy) != {'mode', 'scope', 'authorization_sha256'}
                or policy['mode'] != 'until_complete'
                or policy['scope'] != 'isolated'
                or not digest(policy['authorization_sha256'])):
            raise ValueError('Explicit isolated until-complete authority evidence required')
        # Both fields must be present and null. A timed campaign must not acquire
        # an extension by being relabelled as an isolated, unbounded manifest.
        if (manifest['original_cutoff_utc'] is not None
                or manifest['runtime_cutoff_utc'] is not None):
            raise ValueError('Until-complete scope cannot replace an existing cutoff')
        return None
    if 'window_policy' in manifest:
        raise ValueError('Window policy requires the explicit v2 manifest')
    original_cutoff = utc(manifest['original_cutoff_utc'])
    proposed_cutoff = utc(manifest['runtime_cutoff_utc'])
    if proposed_cutoff != original_cutoff:
        blockers.append('original_cutoff_changed')
    remaining = (original_cutoff - now).total_seconds()
    if remaining < required:
        blockers.append('authorized_window_insufficient')
    return remaining


def assess(manifest: dict, *, now: datetime) -> dict:
    if not isinstance(manifest, dict) or type(manifest.get('schema')) is not int or manifest['schema'] not in (1, 2):
        raise ValueError('Unsupported preflight manifest')
    if now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError('UTC clock required')
    blockers = []
    window = manifest.get('useful_progress_window_seconds')
    if type(window) is not int or not 1800 <= window <= 86400:
        raise ValueError('Acceptance window must be at least 1800 seconds')
    margin = manifest.get('rollout_margin_seconds')
    if type(margin) is not int or not 0 <= margin <= 86400:
        raise ValueError('Explicit bounded rollout margin required')
    remaining = window_remaining(manifest, now, window + margin, blockers)
    if not digest(manifest.get('predeclared_experiment_sha256')):
        blockers.append('predeclared_experiment_not_bound')
    mode = manifest.get('comparison_kind')
    if mode not in {'matched_native', 'unmatched_production_trend'}:
        raise ValueError('Comparison kind must be explicit')
    baseline = manifest.get('baseline_save_sha256')
    treatment = manifest.get('treatment_initial_save_sha256')
    if not digest(baseline) or not digest(treatment):
        blockers.append('initial_save_evidence_missing')
    elif mode == 'matched_native' and baseline != treatment:
        blockers.append('matched_initial_saves_differ')
    revisions = manifest.get('implementation', {})
    if not isinstance(revisions, dict) or set(revisions) - {str(n) for n in ISSUES}:
        raise ValueError('Invalid implementation map')
    status = {}
    for issue in ISSUES:
        row = revisions.get(str(issue), {})
        if not isinstance(row, dict):
            raise ValueError('Invalid implementation evidence')
        ready = (row.get('implementation_available') is True
                 and row.get('independent_review_verified') is True
                 and row.get('final_head_checks_verified') is True
                 and row.get('ssh_signature_verified') is True
                 and row.get('integration_state') in {'merged', 'authorized_staged'}
                 and digest(row.get('source_commit'), 40))
        status[str(issue)] = 'operator_claimed_ready' if ready else 'not_ready'
        if not ready:
            blockers.append('implementation_not_ready:' + str(issue))
    runtime = manifest.get('runtime', {})
    if not isinstance(runtime, dict):
        raise ValueError('Invalid runtime evidence')
    for key, length in (('deployed_commit', 40), ('configuration_sha256', 64),
                        ('ownership_checkpoint_sha256', 64), ('rollback_record_sha256', 64)):
        if not digest(runtime.get(key), length):
            blockers.append('runtime_evidence_missing:' + key)
    if runtime.get('readback_verified') is not True:
        blockers.append('runtime_readback_unverified')
    if runtime.get('immutable_treatment_preserved') is not True:
        blockers.append('immutable_treatment_unverified')
    if manifest.get('established_operational_handoff_verified') is not True:
        blockers.append('operational_handoff_unverified')
    result = {'schema': manifest['schema'], 'ready_for_operator_review': not blockers,
            'evidence_trust': 'operator_supplied_manifest_not_remote_attestation',
            'implementation': status, 'blockers': blockers,
            'remaining_original_window_seconds': remaining,
            'required_window_seconds': window, 'rollout_margin_seconds': margin,
            'comparison_kind': mode, 'matched_comparison_manifest_ready': mode == 'matched_native' and not blockers,
            'causal_speed_claim_eligible': False,
            'campaign_started': False, 'cutoff_changed': False, 'deployment_authorized': False,
            'native_acceptance_proven': False,
            'still_required': ['actual_controller_native_backend_execution', 'paid_multi_consumer_coal_flow',
                'paid_downstream_ingredient_flow', 'sustained_science_consumption_and_research_milestone',
                'full_timing_and_resource_counters', 'isolated_native_crash_and_route_reconciliation',
                'independent_integrated_review']}
    if manifest['schema'] == 2:
        result['window_policy'] = {'mode': 'until_complete', 'scope': 'isolated',
                                   'authority_verified': False}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args(argv)
    try:
        with args.manifest.open('rb') as stream:
            raw = stream.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            raise ValueError('Manifest exceeds budget')
        result = assess(json.loads(raw), now=datetime.now(timezone.utc))
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if result['blockers']:
            raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, 'Cannot preflight an incomplete or invalid acceptance manifest.\n')


if __name__ == '__main__':
    main()
