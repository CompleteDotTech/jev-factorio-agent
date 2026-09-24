"""Synthetic acceptance regressions; no VM, RCON or model requests are made."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.acceptance_boundaries import final_successor_issues, probe_source_sha256
from jev_factorio.acceptance_capture import capture
from jev_factorio.acceptance_io import canonical, load_json
from jev_factorio.native_acceptance import analyze
from test_acceptance_review import compare, reports
from test_native_acceptance import source_files, rewrite
from test_successors import flowing_state, qualify, GROWTH, KIND


@pytest.mark.parametrize('field,size', [('expected_commit', 40), ('expected_source_sha256', 64)])
def test_consistent_but_different_code_across_arms_is_not_a_matched_experiment(monkeypatch, field, size):
    data = reports()
    for report in data.values():
        report['trial'][field] = ('a' if report['trial']['arm'] == 'baseline' else 'b') * size
    result = compare(monkeypatch, data)
    assert not result['measurement_checks_passed']
    assert len(result['pairs']) == 3
    assert all('pair_mismatch:' + field in pair['issues'] for pair in result['pairs'])
    assert not result['deployment_authorized']


@pytest.mark.parametrize('digest', [None, '', 'f' * 64, 'unexpected-query'])
def test_missing_or_other_query_digest_fails_even_with_valid_bundle_hashes(tmp_path, digest):
    args, _ = source_files(tmp_path / 'inputs')
    report = load_json(args['preflight_path'].read_bytes())
    report['query_sha256'] = digest
    args['preflight_path'].write_bytes(canonical(report))
    capture(**args, environ={})
    result = analyze(args['output'])
    assert 'preflight_query_mismatch' in result['issues']
    assert not result['measurement_checks_passed']


def test_packaged_probe_digest_matches_the_actual_fixed_query():
    from importlib.resources import files
    from jev_factorio.acceptance_io import sha256
    source = files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text(encoding='utf-8')
    assert probe_source_sha256() == sha256(source.encode('utf-8'))


@pytest.mark.parametrize('kind', ['research', 'rocket'])
def test_transient_native_goal_cannot_shorten_trial_or_supply_a_completion_time(tmp_path, kind):
    args, rows = source_files(tmp_path / 'inputs', span=3600)
    if kind == 'research':
        rows[0]['after_state']['researched'] = ['automation']
    else:
        trial = load_json(args['trial_path'].read_bytes()); trial['goal'] = 'rocket:launch'
        args['trial_path'].write_bytes(canonical(trial))
        rows[0]['after_state'].update(victory=True, victory_source='native:base-game-rocket-launch')
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    assert 'goal_evidence_regressed' in result['issues']
    assert 'trial_horizon_incomplete' in result['issues']
    assert result['goal_first_observed_elapsed_ticks'] is None
    assert not result['measurement_checks_passed']


def test_goal_disappearing_and_reappearing_still_marks_evidence_inconsistent(tmp_path):
    args, rows = source_files(tmp_path / 'inputs', span=5400)
    rows[0]['after_state']['researched'] = ['automation']
    rows[-1]['after_state']['researched'] = ['automation']
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    assert 'goal_evidence_regressed' in result['issues']
    assert result['goal_first_observed_elapsed_ticks'] is None


@pytest.mark.parametrize('kind', ['research', 'rocket'])
def test_persistent_native_goal_can_end_a_short_trial(tmp_path, kind):
    args, rows = source_files(tmp_path / 'inputs', span=3600)
    if kind == 'rocket':
        trial = load_json(args['trial_path'].read_bytes()); trial['goal'] = 'rocket:launch'
        args['trial_path'].write_bytes(canonical(trial))
    for row in rows:
        for label in ('state', 'after_state'):
            if row[label]['tick'] >= 1800:
                if kind == 'research': row[label]['researched'] = ['automation']
                else: row[label].update(victory=True, victory_source='native:base-game-rocket-launch')
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    assert result['measurement_checks_passed'], result['issues']
    assert result['goal_first_observed_elapsed_ticks'] == 1800
    assert not result['deployment_authorized']


@pytest.mark.parametrize('checkpoint_tick', [None, 2700, 1800])
def test_milestone_completion_must_match_final_checkpoint_tick(tmp_path, checkpoint_tick):
    args, rows = source_files(tmp_path / 'inputs', span=3600)
    trial = load_json(args['trial_path'].read_bytes()); trial['goal'] = 'milestone:steam_power'
    args['trial_path'].write_bytes(canonical(trial))
    for row in rows: row['completed_goals']['steam_power'] = 1800
    final = load_json(args['final_checkpoint'].read_bytes())
    if checkpoint_tick is not None: final['completed_goals']['steam_power'] = checkpoint_tick
    args['final_checkpoint'].write_bytes(canonical(final))
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    if checkpoint_tick == 1800:
        assert result['measurement_checks_passed'], result['issues']
        assert result['goal_first_observed_elapsed_ticks'] == 1800
    else:
        assert 'final_goal_checkpoint_mismatch' in result['issues']
        assert 'trial_horizon_incomplete' in result['issues']
        assert result['goal_first_observed_elapsed_ticks'] is None


def successor_boundary(tick=108000):
    state, row = flowing_state(); qualify(state, row)
    state.tick = tick
    for family in ('successors', 'input_routes', 'output_buffers', 'production_sites'):
        state.factory[family]['tick'] = tick
    memory = KIND.memory_type(state.session_id, 'rocket_launch', last_tick=tick)
    memory.successor_projects[GROWTH] = {
        'anchor': row['anchor'], 'predecessor_unit': row['predecessor_unit'],
        'source_unit': row['source_unit'], 'started_tick': 0, 'deadline_tick': 216000, 'status': 'qualified'}
    retained = {'use': deepcopy(row['use']), 'qualification': deepcopy(row['qualification'])}
    for label, family in (('input', 'input_routes'), ('output', 'output_buffers')):
        route = state.factory[family]['sources'][GROWTH]
        retained[label] = deepcopy(route['parts'])
        retained[label + '_layout'] = route['layout']
    memory.successor_receipts[GROWTH] = retained
    record = {'after_state': state.for_jev(), 'successor_projects': deepcopy(memory.successor_projects),
              'successor_evidence': deepcopy(state.factory['successors'])}
    return asdict(memory), record


def test_valid_paid_source_and_qualified_proofs_agree_without_mutation():
    checkpoint, record = successor_boundary()
    before = deepcopy((checkpoint, record))
    assert not final_successor_issues({}, checkpoint, record, {GROWTH})
    assert (checkpoint, record) == before


@pytest.mark.parametrize('case', ['missing_extension', 'empty_projects', 'missing_receipts',
    'output_receipt', 'input_receipt', 'output_layout', 'input_layout', 'use', 'qualification',
    'source_unit', 'predecessor_unit', 'status', 'paid_entity', 'missing_observation', 'lost_log_projects'])
def test_same_tick_final_checkpoint_cannot_lose_or_reassign_successor(case):
    checkpoint, record = successor_boundary()
    if case == 'missing_extension':
        for key in ('successor_schema', 'successor_projects', 'successor_receipts'): checkpoint.pop(key)
    elif case == 'empty_projects':
        checkpoint['successor_projects'] = {}; checkpoint['successor_receipts'] = {}
    elif case == 'missing_receipts': checkpoint['successor_receipts'] = {}
    elif case in {'output_receipt', 'input_receipt'}:
        parts = checkpoint['successor_receipts'][GROWTH][case.split('_')[0]]
        parts[next(iter(parts))]['receipt'] = 'another-paid-action'
    elif case in {'output_layout', 'input_layout'}:
        checkpoint['successor_receipts'][GROWTH][case] = 'another-layout'
    elif case in {'use', 'qualification'}:
        checkpoint['successor_receipts'][GROWTH][case] = {}
    elif case in {'source_unit', 'predecessor_unit'}:
        checkpoint['successor_projects'][GROWTH][case] = 999
    elif case == 'status': checkpoint['successor_projects'][GROWTH]['status'] = 'active'
    elif case == 'paid_entity':
        paid = next(iter(checkpoint['successor_receipts'][GROWTH]['output'].values()))
        record['after_state']['factory']['entities'].pop(paid['role'])
    elif case == 'missing_observation': record['after_state']['factory']['successors']['sources'] = {}
    else: record.pop('successor_projects')
    assert final_successor_issues({}, checkpoint, record, {GROWTH})


def test_unplaced_acquisition_intent_does_not_require_a_nonexistent_paid_furnace():
    checkpoint, record = successor_boundary()
    checkpoint['successor_projects'][GROWTH].update(source_unit=0, status='active')
    checkpoint['successor_receipts'] = {}
    factory = record['after_state']['factory']
    factory['successors']['sources'] = {}
    for family in ('input_routes', 'output_buffers'): factory[family]['sources'] = {}
    factory['entities'].pop(GROWTH)
    record['successor_projects'] = deepcopy(checkpoint['successor_projects'])
    record['successor_evidence'] = deepcopy(factory['successors'])
    assert not final_successor_issues({}, checkpoint, record, set())


def test_initial_paid_project_cannot_disappear_from_checkpoint_and_native_evidence():
    initial, record = successor_boundary(); final = deepcopy(initial)
    final['successor_projects'] = {}; final['successor_receipts'] = {}
    record['after_state']['factory']['successors']['sources'] = {}
    record['successor_projects'] = {}
    assert 'retained_successor_project_missing' in final_successor_issues(initial, final, record, set())


def test_receipt_types_are_not_coerced_into_matching_paid_counts():
    checkpoint, record = successor_boundary()
    parts = checkpoint['successor_receipts'][GROWTH]['output']
    parts[next(iter(parts))]['paid'] = True
    assert 'final_successor_output_receipts_mismatch' in final_successor_issues({}, checkpoint, record, {GROWTH})


@pytest.mark.parametrize('drop_final_extension', [False, True])
def test_checksummed_capture_requires_final_successor_memory_to_match_logs(tmp_path, drop_final_extension):
    args, rows = source_files(tmp_path / 'inputs', arm='treatment')
    checkpoint, record = successor_boundary()
    session = rows[-1]['session_id']; checkpoint['session_id'] = session
    native_factory = record['after_state']['factory']
    for family in ('successors', 'input_routes', 'output_buffers', 'production_sites'):
        native_factory[family]['session_id'] = session
    # Retain the fixture's native-shaped metering; add the actual shared flow fixture.
    measured = rows[-1]['after_state']['factory']
    preserved = {k: deepcopy(measured[k]) for k in ('produced', 'consumed', 'acceptance_runtime', 'research', 'research_progress')}
    measured.update(deepcopy(native_factory)); measured.update(preserved)
    rows[-1]['successor_projects'] = deepcopy(checkpoint['successor_projects'])
    rows[-1]['successor_evidence'] = deepcopy(measured['successors'])
    if not drop_final_extension: args['final_checkpoint'].write_bytes(canonical(checkpoint))
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    if drop_final_extension:
        assert not result['measurement_checks_passed']
        assert 'final_successor_checkpoint_missing' in result['issues']
    else:
        assert result['measurement_checks_passed'], result['issues']
        assert result['newly_qualified_sources'] == [GROWTH]
    assert not result['deployment_authorized']
