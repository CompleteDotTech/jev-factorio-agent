"""Selection regressions are synthetic, not native performance measurements."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.controller import HierarchicalLoop
from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.jev_client import MockJevClient
from jev_factorio.planning.decision_support import candidate_evidence, distinct_candidates, scheduling_context
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.skills import Plan, Step
from test_factory import FactorySimulation, catalog, machine, snapshot
from test_causal_trace import Sink, events
from test_deadline_scheduling import scenario
from test_hierarchical import CountingModel


def transfers(state=None, data=None):
    state, data = state or snapshot(), data or catalog()
    state.inventory['iron-plate'] = 0
    state.factory['entities'].update({
        'far': machine('wooden-chest', unit_number=81, position={'x': 50, 'y': 0},
                       output={'iron-plate': 10}),
        'near': machine('wooden-chest', unit_number=82, position={'x': 5, 'y': 0},
                        output={'iron-plate': 10}),
    })
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    return state, data, [worker._transfer(role, 'iron-plate', 10, extracting=True)
                         for role in ('far', 'near')]


def test_focus_is_structured_without_mutating_material_bill():
    state, data = snapshot(), catalog()
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    plan = worker._need('iron-plate', 20)
    assert plan.materials['local_objective'] == {
        'item': 'iron-plate', 'inventory_target': 20, 'ultimate_goal': 'rocket_launch'}
    assert 'local_objective' not in (worker.materials or {})
    serial = FactoryPlanner(data, state, 'rocket_launch')._need('iron-plate', 20)
    assert 'local_objective' not in (serial.materials or {})


def test_local_rubric_does_not_require_one_pickup_to_launch_a_rocket():
    state, data, plans = transfers()
    support = scheduling_context(state, data, plans, 'rocket_launch')
    context, questions, offered = question_batch({'facts': state.for_jev(), **support}, plans)
    assert offered == plans
    assert 'local_objective' in questions['candidate']['instructions']
    assert 'all actions needed' not in str(questions[plans[0].id + '/benefit'])
    assert context['candidate_evidence'][plans[1].id]['travel_tiles_lower_bound'] == 5
    assert support['local_objective']['success_authority'].startswith('unchanged native')


def test_rank_prefers_near_collection_without_mutation_or_claiming_a_path():
    state, data, plans = transfers()
    before = deepcopy((state, plans))
    support = scheduling_context(state, data, plans, 'rocket_launch')
    assert support['deterministic_ranking'] == [plans[1].id, plans[0].id]
    assert support['candidate_evidence'][plans[1].id]['actor_ticks_estimate'] == 400
    assert (state, plans) == before
    assert support['selection_contract']['heuristics_are_not_native_timing_measurements']


@pytest.mark.parametrize('position', [None, {}, {'x': float('nan'), 'y': 0}, {'x': 1}])
def test_missing_geometry_is_unknown_not_zero_cost(position):
    state, data, plans = transfers()
    state.factory['entities']['near']['position'] = position
    support = scheduling_context(state, data, plans, 'rocket_launch')
    row = support['candidate_evidence'][plans[1].id]
    assert row['travel_tiles_lower_bound'] is None and row['actor_ticks_estimate'] is None
    assert row['unknowns']
    assert support['deterministic_ranking'][0] == plans[0].id


def test_urgent_fuel_wins_over_nearby_collection():
    state, data, plans = transfers()
    state.inventory['coal'] = 5
    state.factory['entities']['burner'] = machine(position={'x': 200, 'y': 0}, fuel={'coal': 0})
    fuel = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('burner', 'coal', 5)
    support = scheduling_context(state, data, [*plans, fuel], 'rocket_launch')
    assert support['deterministic_ranking'][0] == fuel.id
    assert support['candidate_evidence'][fuel.id]['urgency'] == 3


def test_due_science_delivery_precedes_speculative_stock_collection():
    state, data = scenario(available=0)
    state.inventory['red'] = 20
    state, data, plans = transfers(state, data)
    lab = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('utility:lab', 'red', 20)
    support = scheduling_context(state, data, [*plans, lab], 'rocket_launch')
    assert support['deterministic_ranking'][0] == lab.id
    assert 'due_research_delivery:red' in support['candidate_evidence'][lab.id]['reasons']


@pytest.mark.parametrize('fuel,expected', [(0, 0), (5, 2)])
def test_last_ingredient_only_unblocks_a_supplied_machine(fuel, expected):
    state, data = snapshot(inventory={'iron-ore': 10}), catalog()
    state.factory['entities']['furnace'] = machine(recipe='iron-plate', fuel={'coal': fuel})
    plan = ReadyWorkPlanner(data, state, 'rocket_launch')._transfer('furnace', 'iron-ore', 10)
    assert candidate_evidence(state, data, [plan])[plan.id]['urgency'] == expected


def test_passive_wait_cannot_outrank_ready_work_due_to_zero_actor_cost():
    state, data, plans = transfers()
    wait = ReadyWorkPlanner(data, state, 'rocket_launch')._wait('machine_output', 'iron-plate', 20, 'far')
    support = scheduling_context(state, data, [wait, *plans], 'rocket_launch')
    assert support['deterministic_ranking'][-1] == wait.id


def test_identical_options_collapse_but_different_receipts_remain_distinct():
    _, _, plans = transfers()
    duplicate = replace(plans[0], id='duplicate')
    assert distinct_candidates([plans[0], duplicate]) == [plans[0]]
    changed = replace(duplicate, steps=(replace(duplicate.steps[0], parameters={
        **duplicate.steps[0].parameters, 'receipt': 'new-receipt'}),))
    assert len(distinct_candidates([plans[0], changed])) == 2


@pytest.mark.parametrize('policy,scheduling,source,calls', [
    ('hybrid', 'ready-work', 'deterministic-singleton', 0),
    ('jev', 'ready-work', 'mock', 1),
    ('hybrid', 'serial', 'mock', 1),
    ('deterministic', 'ready-work', 'deterministic', 0),
])
def test_singleton_elision_preserves_explicit_policy_and_model_attribution(policy, scheduling, source, calls):
    backend, client, sink = FactorySimulation(), CountingModel(), Sink()
    backend.state.inventory['stone-furnace'] = 1
    client.last_model, client.last_usage = 'previous-request', {'input_tokens': 999}
    loop = HierarchicalLoop(backend, client, policy=policy, target='iron_smelting',
                            factory_scheduling=scheduling, research_log=sink, tick_seconds=0)
    record = loop.step()
    assert client.calls == calls and record['decision']['source'] == source
    assert record['model_call'] is bool(calls)
    assert len(events(sink, 'model_request')) == calls
    assert events(sink, 'decision')[0]['model_called'] is bool(calls)
    if not calls:
        assert record['resolved_model'] is None and record['usage'] is None
        assert events(sink, 'decision')[0]['diagnostics']['model_skipped']
    assert backend.actions  # Real controller dispatched the admitted synthetic action.


def test_hybrid_fallback_uses_recorded_ranking_and_retains_abstention_evidence():
    state, data, plans = transfers()
    class Client(CountingModel):
        def evaluate(self, state, questions):
            result = super().evaluate(state, questions)
            result['candidate']['confidence'] = 0.1
            return result
    backend, client = FactorySimulation(), Client()
    backend.state.factory['entities'] = deepcopy(state.factory['entities'])
    loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                            factory_scheduling='ready-work', tick_seconds=0)
    loop._compile_candidates = lambda observation: (plans, '')
    record = loop.step()
    assert client.calls == 1 and backend.actions[0][1]['role'] == 'near'
    assert record['decision']['source'] == 'deterministic-fallback'
    assert record['decision']['diagnostics']['outcome'] == 'low_choice_confidence'
    assert record['decision']['answers']['candidate']['confidence'] == 0.1


def test_fresh_observation_still_prevents_spending_changed_inventory():
    backend, client = FactorySimulation(), CountingModel()
    backend.state.inventory['stone-furnace'] = 1
    original = backend.observe
    observations = 0
    def observe():
        nonlocal observations
        observations += 1
        if observations == 2:
            backend.state.inventory['stone-furnace'] = 0
        return original()
    backend.observe = observe
    loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                            factory_scheduling='ready-work', tick_seconds=0)
    record = loop.step()
    assert client.calls == 0 and not backend.actions
    assert record['action'] == 'observe' and 'precondition' in record['outcome']


@pytest.mark.parametrize('cause,outcome', [
    ('observe', 'model_abstention'), ('confidence', 'low_choice_confidence'),
    ('missing', 'all_candidates_rejected'), ('benefit', 'all_candidates_rejected'),
    ('disruption', 'all_candidates_rejected'), ('malformed', 'invalid_answer'),
    ('json', 'invalid_provider_payload'),
])
def test_distinct_failure_causes_are_auditable_without_lowering_confidence(cause, outcome):
    state, data, plans = transfers()
    class Client(MockJevClient):
        def evaluate(self, state, questions):
            if cause == 'json':
                raise ValueError('invalid JSON payload')
            answers = super().evaluate(state, questions)
            if cause == 'observe':
                answers['candidate']['choice'] = 'observe'
                answers['candidate']['probabilities'] = {key: float(key == 'observe')
                                                         for key in questions['candidate']['criteria']}
            elif cause == 'confidence':
                answers['candidate']['confidence'] = 0.1
            elif cause == 'malformed':
                answers.pop('candidate')
            else:
                for plan in plans:
                    if cause == 'missing':
                        answers[plan.id + '/needs_observation']['noul'] = 0.8
                    else:
                        answers[plan.id + '/' + cause]['confidence'] = 0.1
            return answers
    result = select_plan(Client(), scheduling_context(state, data, plans, 'rocket_launch'), plans)
    assert result.model_called and result.plan_id is None
    assert result.diagnostics['outcome'] == outcome
    if outcome == 'all_candidates_rejected':
        assert set(result.diagnostics['candidate_rejections']) == {p.id for p in plans}


def test_request_pruning_removes_unoffered_feature_rows_and_reports_ids():
    state, data, plans = transfers()
    plans = [replace(plans[0], id=f'candidate-{i}') for i in range(20)]
    support = scheduling_context(state, data, plans, 'rocket_launch')
    context, _, offered = question_batch(support, plans)
    assert set(context['candidate_evidence']) == {p.id for p in offered}
    assert set(context['deterministic_ranking']) == {p.id for p in offered}
    result = select_plan(MockJevClient(), support, plans)
    assert result.diagnostics['offered_candidates'] < 20
    assert len(result.diagnostics['pruned_candidate_ids']) + result.diagnostics['offered_candidates'] == 20


def test_canonical_replay_keeps_singleton_non_model_decision(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
    from jev_factorio.replay import replay_log
    path = tmp_path / 'research'
    backend, client = FactorySimulation(), CountingModel()
    backend.state.inventory['stone-furnace'] = 1
    with ResearchLog(path, RunConfiguration('mock', 'hierarchical', 'hybrid'), environ={}) as sink:
        loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                                factory_scheduling='ready-work', research_log=sink, tick_seconds=0)
        loop.step()
    verify_run(path)
    report = replay_log(path, format='research-v1')
    assert report.status != 'invalid', report.to_dict()
    assert not any('model' in finding.code for finding in report.findings)
    assert client.calls == 0
