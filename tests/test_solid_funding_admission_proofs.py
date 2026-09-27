"""Funding transitions must describe the admitted acquisition and paid mutation."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.skills import Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from jev_factorio.telemetry import fingerprint
from solid_routes_fixtures import row
from test_solid_kit_acquisition import kit_loop
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for, assert_rejected


@pytest.mark.parametrize('change', ['unrelated', 'quantity', 'cost'])
def test_consistent_step_hash_cannot_replace_actual_acquisition(tmp_path, change):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    record = loop.step()
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    if change == 'unrelated':
        commit['step'] = asdict(Step('factory_craft', 'inventory', 'stone', 1,
                                    parameters={'recipe': 'stone', 'batches': 1}))
    elif change == 'quantity':
        commit['step']['parameters']['batches'] += 1
        commit['step']['threshold'] += 1
    else: commit['step']['costs'] = {}
    step = commit['step']
    record['after_state']['inventory'][step['item']] = step['threshold']
    record['attempt_outcomes'][-1]['step_sha256'] = fingerprint(step)
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('change', ['index', 'hash'])
def test_paid_component_requires_exact_dispatched_build_step(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    records = []
    for _ in range(20):
        records.append(loop.step())
        if row(backend.state)['parts']: break
    final = asdict(loop.memory)
    assert row(backend.state)['parts']
    assert not funding_history_issues(initial, records, final)
    attempt = records[-1]['attempt_outcomes'][-1]
    if change == 'index': attempt['step_index'] = 31
    else: attempt['step_sha256'] = '1' * 64
    assert funding_history_issues(initial, records, final)


def test_unobservable_failure_cannot_clear_active_kit(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    allowed = loop._step_allowed
    loop._step_allowed = lambda step, snapshot: False if backend.observations >= 3 else allowed(step, snapshot)
    record = loop.step()
    assert record['solid_funding'] is not None and record['action'] == 'observe'
    assert funding_history_issues(initial, [record], asdict(loop.memory))


@pytest.mark.parametrize('prior', [0, 1])
def test_observable_input_loss_accounts_for_exact_failure(tmp_path, prior):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    key = solid_funding.project_key(row(backend.state)) + ':kit'
    loop.memory.failures[key] = prior
    initial = asdict(loop.memory)
    def lose_input():
        if backend.observations == 3:
            step = loop.memory.active_plan['steps'][0]
            backend.state.inventory[next(iter(step['costs']))] = 0
    backend.before_observe = lose_input
    record = loop.step()
    assert record['failure_budgets'][key] == prior + 1
    assert (record['solid_funding'] is None) == (prior == 1)
    assert not backend.calls
    assert not funding_history_issues(initial, [record], asdict(loop.memory))


def test_action_limit_cannot_release_new_active_commit():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] = solid_funding.MAX_ACTIONS - 1
    initial['solid_funding'] = deepcopy(proof)
    for record in records[:3]: record['solid_funding'] = deepcopy(proof)
    proof['actions'] += 1
    tick = records[3]['state']['tick']
    records[3].update(action='observe', verified=False)
    decision_for(records[3], proof)
    history = [event('solid_kit_committed', proof, tick), plan_event(proof, tick),
               event('solid_kit_abandoned', proof, records[3]['after_state']['tick'], reason='kit_action_budget')]
    for record in records[3:]:
        record.update(solid_funding=None, history=deepcopy(history))
        for label in ('state', 'after_state'): record[label]['inventory']['transport-belt'] = 0
        record['failure_budgets'][proof['key'] + ':kit'] = 2
    final.update(solid_funding=None, failures=deepcopy(records[-1]['failure_budgets']))
    assert_rejected(data)


@pytest.mark.parametrize('change', ['handoff', 'commit', 'future', 'malformed'])
def test_final_checkpoint_history_cannot_contradict_retained_ownership(change):
    data = funded_evidence()
    _, _, _, final = data
    proof = deepcopy(final['solid_funding'])
    if change == 'handoff': value = event('solid_kit_paid_handoff', proof, final['last_tick'])
    else:
        proof['actions'] += 1
        value = event('solid_kit_committed', proof, final['last_tick'])
        if change == 'future': value['tick'] += 1
        if change == 'malformed': value.pop('funding')
    final['history'].append(value)
    assert_rejected(data)


@pytest.mark.parametrize('change', ['missing_acquisition', 'catalog_hash', 'omitted_final_history'])
def test_new_acquisition_metadata_and_final_history_are_required(tmp_path, change):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    record = loop.step()
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    if change == 'omitted_final_history':
        final['history'] = []
    else:
        commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
        if change == 'missing_acquisition': commit.pop('acquisition')
        else: commit['acquisition']['catalog']['version'] = '2.0.999'
        final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


def test_acquisition_catalog_is_detached_from_live_catalog(tmp_path):
    loop, _ = kit_loop(tmp_path)
    record = loop.step()
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    catalog = deepcopy(commit['acquisition']['catalog'])
    name = next(iter(catalog['recipes']))
    loop.catalog.recipes[name]['enabled'] = not loop.catalog.recipes[name]['enabled']
    assert commit['acquisition']['catalog'] == catalog
    saved = next(value for value in loop.memory.history if value['kind'] == 'solid_kit_committed')
    assert saved['acquisition']['catalog'] == catalog
