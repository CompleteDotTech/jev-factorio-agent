"""Synthetic terminal-boundary controls; no native trial is executed."""
from copy import deepcopy

import pytest

from jev_factorio.acceptance_boundaries import final_successor_issues, project_history_issues, successor_history_issues
from jev_factorio.acceptance_capture import capture
from jev_factorio.acceptance_io import canonical, load_json
from jev_factorio.native_acceptance import analyze
from test_acceptance_regression_gates import successor_boundary
from test_native_acceptance import source_files, rewrite
from test_successors import GROWTH


@pytest.mark.parametrize('goal', ['research:automation', 'rocket:launch'])
@pytest.mark.parametrize('duplicate_final', [False, True])
def test_goal_first_seen_in_last_native_tick_has_no_early_completion_credit(tmp_path, goal, duplicate_final):
    args, rows = source_files(tmp_path / 'inputs', span=3600)
    trial = load_json(args['trial_path'].read_bytes()); trial['goal'] = goal
    args['trial_path'].write_bytes(canonical(trial))
    def finish(state):
        if goal.startswith('research:'): state['researched'] = ['automation']
        else: state.update(victory=True, victory_source='native:base-game-rocket-launch')
    finish(rows[-1]['after_state'])
    if duplicate_final:
        # Same-tick repetitions are not independent later native evidence.
        finish(rows[-1]['state']); finish(rows[-2]['after_state'])
    rewrite(args, rows); capture(**args, environ={})
    result = analyze(args['output'])
    assert not result['measurement_checks_passed']
    assert 'goal_confirmation_missing' in result['issues']
    assert 'trial_horizon_incomplete' in result['issues']
    assert result['goal_first_observed_elapsed_ticks'] is None


def paused_boundary():
    checkpoint, record = successor_boundary()
    row = record['after_state']['factory']['successors']['sources'][GROWTH]
    row['phase'] = 'producing'; row['qualification'] = {}
    checkpoint['successor_projects'][GROWTH]['status'] = 'paused'
    checkpoint['successor_receipts'][GROWTH]['qualification'] = {}
    record['successor_projects'] = deepcopy(checkpoint['successor_projects'])
    record['successor_evidence'] = deepcopy(record['after_state']['factory']['successors'])
    return checkpoint, record


@pytest.mark.parametrize('status', ['active', 'qualified'])
def test_initial_paused_project_cannot_reactivate_in_the_acceptance_trial(status):
    initial, _ = paused_boundary()
    final, record = successor_boundary()
    if status == 'active':
        final, record = paused_boundary()
        final['successor_projects'][GROWTH]['status'] = 'active'
        record['successor_projects'] = deepcopy(final['successor_projects'])
    assert 'paused_successor_reactivated' in final_successor_issues(initial, final, record, {GROWTH})


def test_unplaced_paused_project_cannot_acquire_a_furnace_even_while_status_stays_paused():
    initial, _ = paused_boundary()
    initial['successor_projects'][GROWTH]['source_unit'] = 0
    initial['successor_receipts'] = {}
    final, record = paused_boundary()
    assert 'paused_successor_reactivated' in final_successor_issues(initial, final, record, {GROWTH})


def test_unchanged_paused_project_remains_valid_retained_state():
    checkpoint, record = paused_boundary()
    assert not final_successor_issues(checkpoint, deepcopy(checkpoint), record, {GROWTH})


@pytest.mark.parametrize('status', ['paused', 'qualified'])
def test_active_project_may_pause_or_qualify_without_being_called_reactivation(status):
    final, record = paused_boundary() if status == 'paused' else successor_boundary()
    initial = deepcopy(final); initial['successor_projects'][GROWTH]['status'] = 'active'
    initial['successor_receipts'][GROWTH]['qualification'] = {}
    assert not final_successor_issues(initial, final, record, {GROWTH})


def test_midtrial_pause_cannot_be_hidden_by_returning_to_active_before_final_boundary():
    checkpoint, _ = paused_boundary()
    active = deepcopy(checkpoint['successor_projects']); active[GROWTH]['status'] = 'active'
    records = [{'successor_projects': deepcopy(active)},
               {'successor_projects': deepcopy(checkpoint['successor_projects'])},
               {'successor_projects': deepcopy(active)}]
    initial = {'successor_projects': active}; final = {'successor_projects': active}
    before = deepcopy((initial, records, final))
    assert 'paused_successor_reactivated' in project_history_issues(initial, records, final)
    assert (initial, records, final) == before


@pytest.mark.parametrize('field,value', [('source_unit', 999), ('anchor', 'cell-site:replacement'),
                                         ('predecessor_unit', 999), ('started_tick', 1)])
def test_native_successor_identity_cannot_change_under_the_same_role(field, value):
    _, record = successor_boundary()
    record['state'] = deepcopy(record['after_state'])
    current = deepcopy(record)
    current['after_state']['factory']['successors']['sources'][GROWTH][field] = value
    assert 'successor_identity_history_changed' in successor_history_issues([record, current])
