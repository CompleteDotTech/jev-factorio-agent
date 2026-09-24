"""Offline native-trial consistency and paired measurement gates; never deploys."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import math

from .acceptance_capture import verify
from .acceptance_io import canonical, load_json, sha256, stable_read, write_new
from .dev_preflight import checkpoint_read, inspect_native
from .evidence_audit import measurements
from .telemetry import validate_phase


REVIEW_GATES = ['native_fault_injection_and_restart_matrix', 'actor_idle_attribution',
                'actual_path_distance', 'single_writer_and_application_consistent_handoff',
                'independent_exact_head_review', 'production_cutover_authorization']


def counter_delta(first, last):
    if not isinstance(first, dict) or not isinstance(last, dict): return None
    result = {}
    for key in first.keys() | last.keys():
        a, b = first.get(key, 0), last.get(key, 0)
        if type(a) not in {int, float} or type(b) not in {int, float} or not math.isfinite(a) or not math.isfinite(b) or a < 0 or b < a:
            return None
        result[key] = b - a
    return result


def goal_observed(goal, snapshot, row):
    kind, _, name = goal.partition(':')
    if kind == 'research' and name:
        return name in (snapshot.get('researched') or snapshot.get('factory', {}).get('researched', []))
    if kind == 'milestone' and name:
        achieved = row.get('completed_goals', {}).get(name)
        return type(achieved) is int and 0 <= achieved <= snapshot['tick']
    if goal == 'rocket:launch':
        return snapshot.get('victory') is True and snapshot.get('victory_source') == 'native:base-game-rocket-launch'
    raise ValueError('Unsupported predeclared goal')


def analyze(directory: Path) -> dict:
    data = verify(directory)
    rows, trial, manifest = data['rows'], data['trial'], data['capture-manifest']
    initial, final, preflight = data['initial-checkpoint'], data['final-checkpoint'], data['preflight']
    # Revalidate the exported checkpoints, not only their caller-provided hashes.
    checkpoint_read(directory / 'initial-checkpoint.json')
    checkpoint_read(directory / 'final-checkpoint.json', idle=False)
    metrics = measurements(rows)
    issues = []
    def reject(condition, reason):
        if condition: issues.append(reason)
    reject(preflight.get('ready_for_coordinated_validation') is not True, 'preflight_not_ready')
    reject(preflight.get('deployment_authorized') is not False, 'invalid_preflight_authority')
    reject(preflight.get('vm_uuid') != trial['vm_uuid'] or preflight.get('production_vm_uuid') != trial['production_vm_uuid']
           or trial['vm_uuid'] == trial['production_vm_uuid'], 'development_identity_mismatch')
    reject(preflight.get('checkpoint_sha256') != manifest['source_initial_checkpoint_sha256']
           or trial['initial_checkpoint_sha256'] != manifest['source_initial_checkpoint_sha256']
           or trial['initial_save_sha256'] != manifest['source_save']['sha256'], 'initial_artifact_binding_mismatch')
    issues.extend(inspect_native(preflight.get('native', {}), initial, initial['session_id']))
    first, last = rows[0]['state'], rows[-1]['after_state']
    start, end = first['tick'], last['tick']
    reject(initial['session_id'] != final['session_id'] or initial['session_id'] != rows[0].get('session_id'), 'session_mismatch')
    reject(initial['last_tick'] > start or final['last_tick'] != end, 'checkpoint_window_mismatch')
    reject(start - initial['last_tick'] > 1800, 'stale_initial_checkpoint')
    reject(preflight.get('native', {}).get('tick', -1) > start, 'preflight_after_trial_start')
    reject(any(final.get(k) for k in ('pending', 'attempt', 'active_plan', 'reservations', 'background_job', 'background_attempt')), 'unresolved_final_work')
    reject(final.get('status') not in {'running', 'completed'}, 'terminal_failure')
    try:
        observed = datetime.fromisoformat(preflight['observed_at_utc'].replace('Z', '+00:00'))
        started = datetime.fromisoformat(rows[0]['recorded_at_utc'].replace('Z', '+00:00'))
        reject(observed.utcoffset() != timezone.utc.utcoffset(observed)
               or started.utcoffset() != timezone.utc.utcoffset(started)
               or not 0 <= (started - observed).total_seconds() <= 300, 'preflight_time_not_fresh')
    except (ValueError, TypeError, KeyError, AttributeError):
        issues.append('preflight_time_not_fresh')
    failures = dict(initial.get('failures', {}))
    times, runtimes, seen_observations = [], [], set()
    phase_returns = {}
    produced_series, consumed_series, fair_series = [], [], []
    use_ids = set()
    use_witnesses = []
    qualified_sources, newly_qualified = set(), set()
    first_preferred = {role for role, value in first.get('factory', {}).get('successors', {}).get('sources', {}).items()
                       if value.get('phase') == 'preferred'}
    resolved_models = set()
    initial_uses = {value.get('use', {}).get('job_id') for value in first.get('factory', {}).get('successors', {}).get('sources', {}).values()}
    goal_tick = None
    first_goal = goal_observed(trial['goal'], first, {'completed_goals': initial.get('completed_goals', {})})
    reject(first_goal, 'goal_already_complete_at_baseline')
    for row in rows:
        reject(row.get('world_kind') != 'fle', 'synthetic_or_unknown_world')
        reject(row.get('run_id') != trial['trial_id'], 'trial_run_identity_mismatch')
        if row.get('model_call') is True:
            model = row.get('resolved_model')
            if not isinstance(model, str) or not model:
                issues.append('resolved_model_coverage_missing')
            else:
                resolved_models.add(model)
        reject(row.get('controller') != 'hierarchical' or row.get('target') != 'rocket_launch', 'unexpected_controller_or_target')
        revision = row.get('code_revision') or {}
        reject(revision.get('commit') != trial['expected_commit'] or revision.get('dirty') is True, 'source_revision_mismatch')
        reject(row.get('policy') != trial['expected_policy'], 'policy_mismatch')
        requested = row.get('requested_model')
        reject((requested if requested is not None else 'none') != trial['expected_model'], 'requested_model_mismatch')
        reject(row.get('acceptance_configuration') != trial['configuration'], 'configuration_mismatch_or_missing')
        reject(row.get('status') in {'blocked', 'uncertain'}, 'failure_during_trial')
        budgets = row.get('failure_budgets')
        if not isinstance(budgets, dict):
            issues.append('failure_budget_coverage_missing')
        else:
            reject(any(type(value) is not int or value < failures.get(key, 0) for key, value in budgets.items())
                   or any(key not in budgets for key in failures), 'failure_history_regressed')
            failures = dict(budgets)
        fair_series.append(row.get('fair_action_metrics'))
        for event in row.get('phases', []):
            validate_phase(event)
            if event['status'] != 'started':
                key = (event['stage'], event['at_utc'])
                if key in phase_returns and phase_returns[key] != event:
                    raise ValueError('Conflicting timing event')
                phase_returns[key] = event
        for label in ('state', 'after_state'):
            state = row[label]
            factory = state.get('factory', {})
            tick = state['tick']
            runtime = factory.get('acceptance_runtime')
            reject(not isinstance(runtime, dict), 'native_runtime_coverage_missing')
            if isinstance(runtime, dict):
                reject(type(runtime.get('speed')) not in {int, float} or runtime['speed'] != 1
                       or runtime.get('tick_paused') is not False, 'simulation_speed_or_pause_changed')
                reject(runtime.get('session_id') != initial['session_id'], 'native_runtime_session_mismatch')
                runtimes.append({k: runtime.get(k) for k in ('session_id', 'actor_unit', 'player_index', 'surface_index', 'force_index', 'mods')})
            # Multiple observations at the same tick can differ after a paid mutation;
            # do not collapse them by tick. Identity checks still examine every state.
            stamp = sha256(canonical(state))
            if stamp not in seen_observations:
                seen_observations.add(stamp)
                times.append(tick)
                produced_series.append(factory.get('produced'))
                consumed_series.append(factory.get('consumed'))
            if goal_tick is None and goal_observed(trial['goal'], state, row):
                goal_tick = (row['completed_goals'][trial['goal'].split(':', 1)[1]]
                             if trial['goal'].startswith('milestone:') else tick)
            if 'successors' in factory:
                from types import SimpleNamespace
                from .successors import sources, qualified
                view = SimpleNamespace(session_id=initial['session_id'], tick=tick, factory=factory)
                try:
                    for role, successor in sources(view).items():
                        if successor['phase'] == 'preferred':
                            if not qualified(role, view):
                                issues.append('unqualified_preference')
                            else:
                                qualified_sources.add(role)
                                if role not in first_preferred: newly_qualified.add(role)
                        use = successor['use']
                        if use and use['job_id'] not in initial_uses and use['job_id'] not in use_ids:
                            use_ids.add(use['job_id'])
                            use_witnesses.append({'source': role, **use})
                except (ValueError, KeyError, TypeError):
                    issues.append('invalid_successor_evidence')
    reject(any(final.get('failures', {}).get(k, -1) < value for k, value in failures.items()), 'final_failure_history_regressed')
    reject(len(resolved_models) > 1, 'resolved_model_drift')
    reject(not runtimes or any(canonical(r) != canonical(runtimes[0]) for r in runtimes), 'native_actor_mod_or_surface_drift')
    if runtimes:
        reject(any(type(runtimes[0].get(k)) is not int or runtimes[0][k] <= 0
                   for k in ('actor_unit', 'player_index', 'surface_index', 'force_index'))
               or not isinstance(runtimes[0].get('mods'), dict) or not runtimes[0]['mods'], 'invalid_native_identity')
        reject(any(runtimes[0].get(k) != preflight.get('native', {}).get(k)
                   for k in ('actor_unit', 'player_index', 'surface_index', 'force_index', 'mods')), 'preflight_actor_or_mod_mismatch')
    max_gap = max((b - a for a, b in zip(times, times[1:])), default=0)
    reject(max_gap > 1800, 'observation_gap_exceeds_1800_ticks')
    reject(any(b < a for a, b in zip(times, times[1:])), 'observation_order_regressed')
    for series, label in ((produced_series, 'production'), (consumed_series, 'consumption'), (fair_series, 'fair_metrics')):
        reject(any(counter_delta(a, b) is None for a, b in zip(series, series[1:])), label + '_coverage_or_counter_regression')
    for source, owned in initial.get('input_commitments', {}).items():
        current = final.get('input_commitments', {}).get(source, {})
        reject(any(current.get(k) != owned.get(k) for k in ('layout', 'source_unit'))
               or any(current.get('parts', {}).get(k) != v for k, v in owned.get('parts', {}).items()), 'paid_input_ownership_regressed')
    for role, entity in first.get('factory', {}).get('entities', {}).items():
        if role.startswith('recipe:'):
            for row in rows:
                for label in ('state', 'after_state'):
                    current = row[label].get('factory', {}).get('entities', {}).get(role, {})
                    reject(any(current.get(k) != entity.get(k) for k in ('unit_number', 'name', 'position')), 'predecessor_replaced_or_missing')
    stages = Counter()
    for event in phase_returns.values(): stages[event['stage']] += event['seconds']
    native_ticks = end - start
    reject(native_ticks <= 0, 'no_simulation_progress')
    wall = metrics['wall_span_seconds']
    minimum = 432000 if trial['arm'] == 'soak' else 108000
    reject(native_ticks < minimum and (trial['arm'] == 'soak' or goal_tick is None), 'trial_horizon_incomplete')
    reject(trial['arm'] == 'soak' and wall < 7200, 'soak_wall_horizon_incomplete')
    reject(metrics['model_usage']['input_tokens']['total'] is None
           or metrics['model_usage']['output_tokens']['total'] is None, 'model_usage_incomplete')
    progress = None
    if trial['goal'].startswith('research:'):
        name = trial['goal'].split(':', 1)[1]
        a, b = first.get('factory', {}), last.get('factory', {})
        if a.get('research') == name and b.get('research') == name:
            progress = b.get('research_progress', 0) - a.get('research_progress', 0)
        elif goal_tick is not None:
            progress = 1 - (a.get('research_progress', 0) if a.get('research') == name else 0)
    return {'schema': 'jev-factorio.native-trial-report.v1', 'trial': trial,
        'bundle_sha256': data['bundle_sha256'], 'gameplay_sha256': manifest['decompressed_sha256'],
        'measurement_checks_passed': not issues, 'issues': sorted(set(issues)),
        'native_ticks': native_ticks, 'wall_seconds': wall, 'max_observation_gap_ticks': max_gap,
        'goal_first_observed_elapsed_ticks': goal_tick - start if goal_tick is not None else None,
        'goal_progress_delta': progress, 'runtime_identity': runtimes[0] if runtimes else None,
        'production_delta': counter_delta(produced_series[0], produced_series[-1]),
        'consumption_delta': counter_delta(consumed_series[0], consumed_series[-1]),
        'fair_counter_delta_lower_bound': counter_delta(fair_series[0], fair_series[-1]),
        'phase_seconds_inclusive': dict(stages), 'timings_are_not_additive': True,
        'new_successor_use_witnesses': use_witnesses, 'metrics': metrics,
        'qualified_sources_observed': sorted(qualified_sources),
        'newly_qualified_sources': sorted(newly_qualified), 'resolved_models': sorted(resolved_models),
        'native_acceptance': 'not_accepted', 'outstanding_review_gates': REVIEW_GATES,
        'deployment_authorized': False, 'external_authenticity_proven': False}


def experiment(directories: list[Path], useful_item: str) -> dict:
    if not isinstance(useful_item, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', useful_item):
        raise ValueError('Invalid predeclared output item')
    reports = [analyze(path) for path in directories]
    if not reports: raise ValueError('No trial captures')
    issues = []
    for field in ('bundle_sha256', 'gameplay_sha256'):
        if len({r[field] for r in reports}) != len(reports): raise ValueError('Duplicate capture cannot be a replicate')
    if len({r['trial']['trial_id'] for r in reports}) != len(reports): raise ValueError('Duplicate trial identity')
    if len({r['trial']['experiment_id'] for r in reports}) != 1: raise ValueError('Mixed experiments')
    indexed, soaks = {}, []
    for report in reports:
        trial = report['trial']
        if trial['arm'] == 'soak': soaks.append(report); continue
        key = trial['pair_id'], trial['arm']
        if key in indexed: raise ValueError('Ambiguous pairing')
        indexed[key] = report
    comparisons = []
    for pair in sorted({k[0] for k in indexed}):
        a, b = indexed.get((pair, 'baseline')), indexed.get((pair, 'treatment'))
        reasons = []
        if not a or not b:
            reasons.append('missing_pair_arm')
        else:
            if not a['measurement_checks_passed'] or not b['measurement_checks_passed']: reasons.append('ineligible_pair_arm')
            for key in ('initial_save_sha256', 'initial_checkpoint_sha256', 'expected_policy', 'expected_model', 'goal', 'production_vm_uuid'):
                if a['trial'][key] != b['trial'][key]: reasons.append('pair_mismatch:' + key)
            left, right = dict(a['trial']['configuration']), dict(b['trial']['configuration'])
            if left.pop('ore_side_successors') is not False or right.pop('ore_side_successors') is not True or left != right:
                reasons.append('uncontrolled_configuration_change')
            if a['runtime_identity'] != b['runtime_identity']: reasons.append('runtime_or_mod_mismatch')
            if a['resolved_models'] != b['resolved_models']: reasons.append('resolved_model_pair_mismatch')
            if not b['newly_qualified_sources'] or not b['new_successor_use_witnesses']:
                reasons.append('successor_lifecycle_not_exercised')
            for label in ('production_delta', 'consumption_delta'):
                av, bv = (a[label] or {}).get(useful_item), (b[label] or {}).get(useful_item)
                if av is None or bv is None or av <= 0 or bv <= 0:
                    reasons.append('missing_or_zero_' + label)
                elif a['native_ticks'] <= 0 or b['native_ticks'] <= 0:
                    reasons.append('zero_simulation_horizon')
                elif bv / b['native_ticks'] < av / a['native_ticks']:
                    reasons.append(label + '_rate_regressed')
            ag, bg = a['goal_first_observed_elapsed_ticks'], b['goal_first_observed_elapsed_ticks']
            if ag is not None and (bg is None or bg > ag): reasons.append('next_goal_slower_or_unobserved')
            if ag is None and bg is None:
                ap, bp = a['goal_progress_delta'], b['goal_progress_delta']
                if ap is None or bp is None or bp <= 0 or bp < ap: reasons.append('goal_progress_not_demonstrated')
        comparisons.append({'pair_id': pair, 'passed': not reasons, 'issues': reasons,
                            'baseline': a and a['trial']['trial_id'], 'treatment': b and b['trial']['trial_id']})
    if len(comparisons) < 3 or any(not p['passed'] for p in comparisons): issues.append('three_valid_matched_pairs_required')
    baseline = [r for r in reports if r['trial']['arm'] == 'baseline']
    for field in ('expected_commit', 'expected_policy', 'expected_model', 'configuration', 'goal'):
        if len({sha256(canonical(r['trial'][field])) for r in baseline}) != 1:
            issues.append('baseline_drift:' + field)
    treatment = [r for r in reports if r['trial']['arm'] in {'treatment', 'soak'}]
    for field in ('expected_commit', 'expected_policy', 'expected_model', 'configuration', 'goal'):
        if len({sha256(canonical(r['trial'][field])) for r in treatment}) != 1: issues.append('treatment_drift:' + field)
    if not soaks or any(not r['measurement_checks_passed']
            or not r['trial']['configuration']['ore_side_successors']
            or not r['qualified_sources_observed'] for r in soaks):
        issues.append('complete_treatment_soak_required')
    return {'schema': 'jev-factorio.native-acceptance-report.v1', 'useful_item': useful_item,
            'useful_item_is_a_production_and_consumption_proxy': True,
            'measurement_checks_passed': not issues, 'issues': sorted(set(issues)),
            'pairs': comparisons, 'trials': reports, 'outstanding_review_gates': REVIEW_GATES,
            'native_acceptance': 'not_accepted', 'deployment_authorized': False,
            'note': 'Passed consistency checks do not establish external authenticity, fair play, causal attribution or production authorization.'}


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures', nargs='+', type=Path)
    parser.add_argument('--useful-item', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = experiment(args.captures, args.useful_item)
        write_new(args.output, canonical(result))
    except Exception as error:
        parser.exit(2, f'Native evidence evaluation failed ({type(error).__name__}); deployment not authorized.\n')
    parser.exit(0 if result['measurement_checks_passed'] else 2,
                'Measurement report recorded; native acceptance and deployment remain separate gates.\n')


if __name__ == '__main__': cli()
