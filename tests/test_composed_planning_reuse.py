"""Issue #93: fresh effective planner per decision, no cross-observation cache."""
from copy import deepcopy

import pytest

from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio import input_routes
from test_input_route_integration import controller, RouteLoop, RouteBackend, ScenarioLoop
from test_maintenance_progress import progress_scenario


def test_composed_frontier_only_constructs_one_effective_planner(tmp_path, monkeypatch):
    backend,_=progress_scenario()
    loop=controller(backend,tmp_path,kind=RouteLoop)
    constructions=[]
    original=ReadyWorkPlanner.__init__
    def counted(self,*args,**kwargs):
        constructions.append(type(self).__name__)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(ReadyWorkPlanner,'__init__',counted)
    plans,_=loop._work_candidates(backend.state)
    assert plans and plans[0].steps[0].parameters['role']=='utility:lab'
    assert constructions==['InputRoutePlanner']


def test_new_observation_inventory_change_cannot_reuse_old_frontier(tmp_path):
    backend,_=progress_scenario()
    loop=controller(backend,tmp_path,kind=RouteLoop)
    plans,_=loop._work_candidates(backend.state)
    assert plans[0].steps[0].action=='factory_insert'
    changed=deepcopy(backend.state)
    changed.inventory['logistic-science-pack']=0
    plans,_=loop._work_candidates(changed)
    assert plans and plans[0].steps[0].action=='factory_craft'


def test_changed_catalog_is_used_by_next_decision(tmp_path):
    backend,data=progress_scenario(science=0)
    loop=controller(backend,tmp_path,kind=RouteLoop)
    plans,_=loop._work_candidates(backend.state)
    assert plans and plans[0].steps[0].action=='factory_craft'
    data.recipes['logistic-science-pack']['enabled']=False
    later,_=loop._work_candidates(deepcopy(backend.state))
    assert not any(p.steps[0].action=='factory_craft'
                   and p.steps[0].parameters['recipe']=='logistic-science-pack' for p in later)


def test_completed_research_changes_composed_frontier_after_checkpoint_reload(tmp_path):
    backend, _ = progress_scenario(science=0)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    before, _ = loop._work_candidates(backend.state)
    assert before[0].steps[0].action == 'factory_craft'
    loop.memory.save(tmp_path / 'state.json')

    resumed = controller(backend, tmp_path, kind=RouteLoop, resume=True)
    resumed.memory = resumed.memory_type.load(
        tmp_path / 'state.json', backend.state.session_id, 'rocket_launch')
    warm, _ = resumed._work_candidates(backend.state)
    assert [p.to_dict() for p in warm] == [p.to_dict() for p in before]
    backend.state.researched.append('study')
    backend.state.factory['research'] = ''
    backend.state.factory['research_progress'] = 0
    after, _ = resumed._work_candidates(backend.state)
    assert after[0].steps[0].action == 'factory_explore'
    assert [p.to_dict() for p in after] != [p.to_dict() for p in before]


@pytest.mark.parametrize('change', ['route_topology', 'paid_unit_identity'])
def test_changed_route_evidence_invalidates_composed_craft(change, tmp_path):
    backend, _ = progress_scenario(science=0)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    before, _ = loop._work_candidates(backend.state)
    assert before[0].steps[0].action == 'factory_craft'

    changed = deepcopy(backend.state)
    if change == 'route_topology':
        route = changed.factory['input_routes']['sources']['recipe:iron-plate']
        route['state'] = 'fault'
        route['topology'] = False
    else:
        changed.factory['entities']['input:drill']['unit_number'] = 999
    assert 'recipe:iron-plate' in input_routes.sources(changed)
    after, blocker = loop._work_candidates(changed)
    assert 'Inconsistent input-route' not in blocker
    assert not any(p.steps[0].action == 'factory_craft' for p in after)


def test_ambiguous_pending_restart_reconciles_before_recompiling(tmp_path, monkeypatch):
    backend = RouteBackend()
    backend.raise_after_build = True
    loop = controller(backend, tmp_path, kind=ScenarioLoop)
    first = loop.step()
    assert first['verified'] is False
    assert loop.memory.pending['dispatch'] == 'ambiguous'
    saved_failures = deepcopy(loop.memory.failures)

    resumed = controller(backend, tmp_path, kind=ScenarioLoop, resume=True)

    def unexpected_compilation(_snapshot):
        raise AssertionError('Pending mutation must reconcile before any new frontier')

    monkeypatch.setattr(resumed, '_work_candidates', unexpected_compilation)
    result = resumed.step()
    assert result['verified'] is True
    assert resumed.memory.pending is None
    assert resumed.memory.failures == saved_failures
    assert len(backend.calls) == 1


def test_exhausted_failure_budget_rejects_fresh_composed_candidate(tmp_path):
    backend = RouteBackend()
    loop = controller(backend, tmp_path, kind=ScenarioLoop)
    plans, _ = loop._work_candidates(backend.state)
    assert len(plans) == 1
    loop.memory.failures[plans[0].id] = 2

    result = loop.step()
    assert result['status'] == 'blocked'
    assert loop.memory.failures[plans[0].id] == 2
    assert loop._planning_diagnostics['failure_budget_rejections'] == [
        {'plan_id': plans[0].id, 'reason': 'plan_failure_budget', 'failures': 2}]
    assert backend.calls == []
