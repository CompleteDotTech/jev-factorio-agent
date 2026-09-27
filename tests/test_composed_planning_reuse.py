"""Issue #93: fresh effective planner per decision, no cross-observation cache."""
from copy import deepcopy

from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.input_routes import InputRoutePlanner
from test_input_route_integration import controller, RouteLoop
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
