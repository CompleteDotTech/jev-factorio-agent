from pathlib import Path


def edit(name, old, new):
    path=Path(name);value=path.read_text();assert value.count(old)==1,(name,old[:90])
    path.write_text(value.replace(old,new,1))

edit('src/jev_factorio/acceptance_capture.py','from .acceptance_io import MAX_LOG,', 'from .acceptance_io import MAX_JSON, MAX_LOG,')
edit('src/jev_factorio/acceptance_capture.py',"        value = stable_read(directory / name, MAX_LOG)",
     "        value = stable_read(directory / name, MAX_LOG if name.endswith('.gz') else MAX_JSON)")
edit('src/jev_factorio/dev_preflight.py',"or row.get('parts') != expected.get('parts')):",
     "or canonical(row.get('parts')) != canonical(expected.get('parts'))):")
edit('src/jev_factorio/native_acceptance.py','from datetime import datetime','from datetime import datetime, timezone')
edit('src/jev_factorio/native_acceptance.py','import re\n','import re\nimport math\n')
edit('src/jev_factorio/native_acceptance.py',
     "        if type(a) not in {int, float} or type(b) not in {int, float} or a < 0 or b < a:",
     "        if type(a) not in {int, float} or type(b) not in {int, float} or not math.isfinite(a) or not math.isfinite(b) or a < 0 or b < a:")
edit('src/jev_factorio/native_acceptance.py',
     "        return name in row.get('completed_goals', {})",
     "        achieved = row.get('completed_goals', {}).get(name)\n        return type(achieved) is int and 0 <= achieved <= snapshot['tick']")
edit('src/jev_factorio/native_acceptance.py',
     "    failures = dict(initial.get('failures', {}))",
'''    try:
        observed = datetime.fromisoformat(preflight['observed_at_utc'].replace('Z', '+00:00'))
        started = datetime.fromisoformat(rows[0]['recorded_at_utc'].replace('Z', '+00:00'))
        reject(observed.utcoffset() != timezone.utc.utcoffset(observed)
               or started.utcoffset() != timezone.utc.utcoffset(started)
               or not 0 <= (started - observed).total_seconds() <= 300, 'preflight_time_not_fresh')
    except (ValueError, TypeError, KeyError, AttributeError):
        issues.append('preflight_time_not_fresh')
    failures = dict(initial.get('failures', {}))''')
edit('src/jev_factorio/native_acceptance.py',
     "    use_witnesses = []",
'''    use_witnesses = []
    qualified_sources, newly_qualified = set(), set()
    first_preferred = {role for role, value in first.get('factory', {}).get('successors', {}).get('sources', {}).items()
                       if value.get('phase') == 'preferred'}
    resolved_models = set()''')
edit('src/jev_factorio/native_acceptance.py',
     "        reject(row.get('world_kind') != 'fle', 'synthetic_or_unknown_world')",
'''        reject(row.get('world_kind') != 'fle', 'synthetic_or_unknown_world')
        reject(row.get('run_id') != trial['trial_id'], 'trial_run_identity_mismatch')
        if row.get('model_call') is True:
            model = row.get('resolved_model')
            if not isinstance(model, str) or not model:
                issues.append('resolved_model_coverage_missing')
            else:
                resolved_models.add(model)''')
edit('src/jev_factorio/native_acceptance.py',
     "            if goal_tick is None and goal_observed(trial['goal'], state, row): goal_tick = tick",
'''            if goal_tick is None and goal_observed(trial['goal'], state, row):
                goal_tick = (row['completed_goals'][trial['goal'].split(':', 1)[1]]
                             if trial['goal'].startswith('milestone:') else tick)''')
edit('src/jev_factorio/native_acceptance.py',
     "                        if successor['phase'] == 'preferred' and not qualified(role, view): issues.append('unqualified_preference')",
'''                        if successor['phase'] == 'preferred':
                            if not qualified(role, view):
                                issues.append('unqualified_preference')
                            else:
                                qualified_sources.add(role)
                                if role not in first_preferred: newly_qualified.add(role)''')
edit('src/jev_factorio/native_acceptance.py',
     "    reject(not runtimes or any(r != runtimes[0] for r in runtimes), 'native_actor_mod_or_surface_drift')",
'''    reject(len(resolved_models) > 1, 'resolved_model_drift')
    reject(not runtimes or any(canonical(r) != canonical(runtimes[0]) for r in runtimes), 'native_actor_mod_or_surface_drift')''')
edit('src/jev_factorio/native_acceptance.py',
     "    native_ticks = end - start",
     "    native_ticks = end - start\n    reject(native_ticks <= 0, 'no_simulation_progress')")
edit('src/jev_factorio/native_acceptance.py',
     "        'new_successor_use_witnesses': use_witnesses, 'metrics': metrics,",
'''        'new_successor_use_witnesses': use_witnesses, 'metrics': metrics,
        'qualified_sources_observed': sorted(qualified_sources),
        'newly_qualified_sources': sorted(newly_qualified), 'resolved_models': sorted(resolved_models),''')
edit('src/jev_factorio/native_acceptance.py',
     "                elif bv / b['native_ticks'] < av / a['native_ticks']:",
'''                elif a['native_ticks'] <= 0 or b['native_ticks'] <= 0:
                    reasons.append('zero_simulation_horizon')
                elif bv / b['native_ticks'] < av / a['native_ticks']:''')
edit('src/jev_factorio/native_acceptance.py',
     "            if a['runtime_identity'] != b['runtime_identity']: reasons.append('runtime_or_mod_mismatch')",
'''            if a['runtime_identity'] != b['runtime_identity']: reasons.append('runtime_or_mod_mismatch')
            if a['resolved_models'] != b['resolved_models']: reasons.append('resolved_model_pair_mismatch')
            if not b['newly_qualified_sources'] or not b['new_successor_use_witnesses']:
                reasons.append('successor_lifecycle_not_exercised')''')
edit('src/jev_factorio/native_acceptance.py',
     "    treatment = [r for r in reports if r['trial']['arm'] in {'treatment', 'soak'}]",
'''    baseline = [r for r in reports if r['trial']['arm'] == 'baseline']
    for field in ('expected_commit', 'expected_policy', 'expected_model', 'configuration', 'goal'):
        if len({sha256(canonical(r['trial'][field])) for r in baseline}) != 1:
            issues.append('baseline_drift:' + field)
    treatment = [r for r in reports if r['trial']['arm'] in {'treatment', 'soak'}]''')
edit('src/jev_factorio/native_acceptance.py',
     "    if not soaks or any(not r['measurement_checks_passed'] or not r['trial']['configuration']['ore_side_successors'] for r in soaks):",
'''    if not soaks or any(not r['measurement_checks_passed']
            or not r['trial']['configuration']['ore_side_successors']
            or not r['qualified_sources_observed'] for r in soaks):''')
print('Applied bounded evidence and paired-comparison hardening')
