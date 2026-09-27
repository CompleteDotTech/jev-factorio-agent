"""Admission replay uses durable declarations and keeps audit payloads off prompts."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.skills import Plan, Step
from jev_factorio.state import GameSnapshot
from jev_factorio.telemetry import fingerprint
from jev_factorio.solid_funding_evidence import funding_history_issues
from jev_factorio.judgments import question_batch, Decision
from test_solid_kit_acquisition import kit_loop


def replace_step(record, final, step):
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    commit['step'] = asdict(step)
    record['attempt_outcomes'][-1]['step_sha256'] = fingerprint(commit['step'])
    record['after_state']['inventory'][step.item] = step.threshold
    final['history'] = deepcopy(record['history'])
    return commit


def test_claimed_reservations_must_match_durable_owners(tmp_path):
    loop, backend = kit_loop(tmp_path)
    backend.state.inventory['copper-cable'] = 6
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    proof = record['solid_funding']
    snapshot = GameSnapshot(**record['state'])
    row = snapshot.factory['solid_routes']['routes'][proof['route']]
    expected, _ = solid_funding.acquire(row, snapshot, loop.catalog, reserved={'copper-cable': 6})
    commit = replace_step(record, final, expected.steps[0])
    commit['acquisition']['reserved'] = {'copper-cable': 6}
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('recipe', ['electronic-circuit', 'inserter', 'transport-belt'])
def test_later_acquisition_action_exhaustion_blocks_commit(tmp_path, recipe):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    key = 'factory:factory_craft:' + recipe
    initial['failures'][key] = final['failures'][key] = record['failure_budgets'][key] = 2
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('change', ['queue', 'power', 'research'])
def test_extraction_does_not_bypass_fresh_investment_admission(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    backend.state.inventory['copper-plate'] = 0
    backend.state.player_position = (0, 0)
    backend.state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
        output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'factory_extract'
    assert not funding_history_issues(initial, [record], final)
    for label in ('state', 'after_state'):
        factory = record[label]['factory']
        if change == 'queue': factory['crafting_queue'] = 1
        elif change == 'research': factory['research_progress'] = 1
        else: factory['entities'][record['solid_funding']['intent']['source']]['energy'] = 0
    assert funding_history_issues(initial, [record], final)


def test_catalog_rewrite_cannot_redeclare_its_own_binding(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    catalog = deepcopy(loop.catalog)
    catalog.recipes['copper-cable']['ingredients'] = [{'name': 'stone', 'amount': 1, 'type': 'item'}]
    record['state']['inventory']['stone'] = 100
    snapshot = GameSnapshot(**record['state'])
    row = snapshot.factory['solid_routes']['routes'][record['solid_funding']['route']]
    plan, _ = solid_funding.acquire(row, snapshot, catalog)
    commit = replace_step(record, final, plan.steps[0])
    commit['acquisition']['catalog'] = solid_funding.catalog_evidence(row, snapshot, catalog)
    digest = solid_funding.catalog_digest(row, snapshot, catalog)
    for proof in (commit['funding'], record['solid_funding'], final['solid_funding']): proof['catalog_sha256'] = digest
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


def test_observe_verified_label_cannot_clear_active_kit(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    barrier = loop._execution_barrier
    loop._execution_barrier = lambda snapshot: backend.observations == 3 or barrier(snapshot)
    first = loop.step()
    assert first['action'] == 'observe' and loop.memory.active_plan
    second = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [first, second], final)
    second.update(action='observe', verified=True, attempt=None, pending=None,
                  attempt_outcomes=deepcopy(first['attempt_outcomes']), history=deepcopy(first['history']))
    final['history'] = deepcopy(second['history'])
    assert funding_history_issues(initial, [first, second], final)


def test_audit_payload_never_enters_model_history(tmp_path, monkeypatch):
    loop, _ = kit_loop(tmp_path)
    loop.step()
    commit = next(value for value in loop.memory.history if value['kind'] == 'solid_kit_committed')
    commit['acquisition']['technologies']['large-audit-only'] = {'effects': ['x' * 1000] * 100}
    before = deepcopy(commit)
    plan = Plan('fixture-craft', 'rocket_launch', 'Make one gear', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', 1,
             parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    captured = []
    def select(client, state, plans, *args):
        context, questions, selected = question_batch(state, plans)
        assert selected and questions and len(json.dumps(context)) < 32000
        captured.append(deepcopy(state))
        return Decision(None, 'observe', 'Fixture stops before another mutation', state=state)
    monkeypatch.setattr('jev_factorio.controller.select_plan', select)
    loop.policy = 'jev'
    loop._compile_candidates = lambda snapshot: ([plan], '')
    loop.step()
    assert captured and 'large-audit-only' not in json.dumps(captured[0]['history'])
    assert commit == before


def test_unrelated_researched_technology_facts_are_not_captured(tmp_path):
    loop, backend = kit_loop(tmp_path)
    for index in range(50):
        name = f'unrelated-{index}'
        loop.catalog.technologies[name] = {'effects': [], 'ingredients': [{'name': 'x' * 1000, 'amount': 1}]}
        backend.state.researched.append(name)
    record = loop.step()
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    assert not any(name.startswith('unrelated-') for name in commit['acquisition']['technologies'])
