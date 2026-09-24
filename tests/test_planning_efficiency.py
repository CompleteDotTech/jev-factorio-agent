"""Bounded scheduler/diagnostic regressions, not native gameplay benchmarks."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning import economics
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_factory import FactorySimulation, catalog, machine, snapshot
from test_hierarchical import CountingModel
from test_local_decisions import transfers


def scoped_transfers():
    state, data, _ = transfers()
    state.factory['entities']['near']['output']['copper-plate'] = 200
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    worker.focus = ('iron-plate', 2)
    immediate = worker._transfer('far', 'iron-plate', 2, extracting=True)
    worker.speculative = True
    optional = worker._transfer('near', 'copper-plate', 200, extracting=True)
    return state, data, immediate, optional


def test_large_optional_handling_volume_does_not_hide_current_prerequisite():
    state, data, immediate, optional = scoped_transfers()
    before = deepcopy((state, immediate, optional))
    support = scheduling_context(state, data, [optional, immediate], 'rocket_launch')
    assert support['deterministic_ranking'] == [immediate.id, optional.id]
    row = support['candidate_evidence'][optional.id]
    assert row['work_scope'] == 'lookahead'
    assert row['processed_units'] == 200
    assert row['processed_units_basis'] == 'handling_volume_not_useful_production'
    assert (state, immediate, optional) == before


def test_urgent_native_supply_still_precedes_immediate_nonurgent_work():
    state, data, immediate, _ = scoped_transfers()
    state.inventory['coal'] = 5
    state.factory['entities']['burner'] = machine(fuel={'coal': 0})
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    worker.focus, worker.speculative = ('coal', 5), True
    fuel = worker._transfer('burner', 'coal', 5)
    support = scheduling_context(state, data, [immediate, fuel], 'rocket_launch')
    assert support['deterministic_ranking'][0] == fuel.id
    assert support['candidate_evidence'][fuel.id]['urgency'] == 3


def test_stale_intent_does_not_buy_priority_or_penalize_a_candidate():
    state, data, immediate, optional = scoped_transfers()
    optional = replace(optional, materials={**optional.materials, 'work_intent': {
        'scope': 'lookahead', 'observed_tick': state.tick - 1}})
    support = scheduling_context(state, data, [immediate, optional], 'rocket_launch')
    assert support['candidate_evidence'][optional.id]['work_scope'] == 'unclassified'
    assert support['deterministic_ranking'][0] == optional.id


@pytest.mark.parametrize('planner_type', [ReadyWorkPlanner, OutputBufferPlanner, InputRoutePlanner])
def test_candidate_forks_compute_economic_workload_once_and_keep_capabilities(monkeypatch, planner_type):
    calls = []
    def workload(state, data):
        calls.append(state.tick)
        return {'iron-gear-wheel': 40}
    monkeypatch.setattr(economics, 'remaining_products', workload)
    state, data = snapshot(), catalog()
    worker = planner_type(data, state, 'rocket_launch')
    worker.focus, worker.allow_service_visits = ('iron-plate', 20), False
    for _ in range(12):
        child = worker._candidate_worker()
        assert type(child) is planner_type and child.speculative
        assert child.ledger is worker.ledger and not child.allow_service_visits
        assert child._workload('iron-gear-wheel') == 40
        child._economic_products['iron-gear-wheel'] = 1
    assert worker._workload('iron-gear-wheel') == 40 and len(calls) == 1
    planner_type(data, deepcopy(state), 'rocket_launch')._candidate_worker()
    assert len(calls) == 2  # A new frontier does not reuse stale game facts.


def test_background_diagnostics_preserve_capital_intent_without_mutation():
    loop = BackgroundWorkLoop.__new__(BackgroundWorkLoop)
    loop.memory = SimpleNamespace(capital_investment={'stage': 'supply'},
        background_schema=2, background_job=None, background_attempt=None)
    extras = loop._record_extras()
    assert extras['capital_investment'] == {'stage': 'supply'}
    assert extras['background_work'] is True
    extras['capital_investment']['stage'] = 'changed'
    assert loop.memory.capital_investment['stage'] == 'supply'


def test_frontier_reports_budget_rejection_and_dedup_without_resetting_history():
    state, _, plans = transfers()
    bad, good = plans
    duplicate = replace(good, id='same-executable-option')
    backend, client = FactorySimulation(), CountingModel()
    backend.state.factory['entities'] = deepcopy(state.factory['entities'])
    loop = HierarchicalLoop(backend, client, policy='hybrid', target='iron_smelting',
                            factory_scheduling='ready-work', tick_seconds=0)
    loop._observe()
    loop.memory.failures[bad.id] = 2
    loop._compile_candidates = lambda observation: ([bad, good, duplicate], '')
    record = loop.step()
    details = record['planning_diagnostics']
    assert details['boundary'] == 'post_capability_frontier'
    assert details['generated_plan_ids'] == [bad.id, good.id, duplicate.id]
    assert details['eligible_plan_ids'] == [good.id, duplicate.id]
    assert details['duplicate_plan_ids'] == [duplicate.id]
    assert details['ranked_plan_ids'] == [good.id]
    assert details['failure_budget_rejections'] == [
        {'plan_id': bad.id, 'reason': 'plan_failure_budget', 'failures': 2}]
    assert record['failure_budgets'][bad.id] == loop.memory.failures[bad.id] == 2
    assert record['mining_outposts'] is False
    assert client.calls == 0 and record['usage'] is None


def test_nearer_bulk_pickup_of_same_current_material_remains_eligible_for_priority():
    state, data, immediate, _ = scoped_transfers()
    state.factory['entities']['near']['output']['iron-plate'] = 200
    worker = ReadyWorkPlanner(data, state, 'rocket_launch')
    worker.focus, worker.speculative = ('iron-plate', 2), True
    shared = worker._transfer('near', 'iron-plate', 200, extracting=True)
    support = scheduling_context(state, data, [immediate, shared], 'rocket_launch')
    assert support['deterministic_ranking'][0] == shared.id
    evidence = support['candidate_evidence'][shared.id]
    assert evidence['work_scope'] == 'shared_prerequisite'
    assert evidence['processed_units'] == 200
    assert evidence['current_prerequisite_units'] == 2
