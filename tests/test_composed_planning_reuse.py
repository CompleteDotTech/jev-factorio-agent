"""Issue #93: fresh effective planner per decision, no cross-observation cache."""
from copy import deepcopy

import pytest

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


def test_completed_research_changes_composed_frontier_after_checkpoint_reload(tmp_path):
    backend, _ = progress_scenario(science=0)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    before, _ = loop._work_candidates(backend.state)
    assert before[0].steps[0].action == 'factory_craft'
    loop.memory.save(tmp_path / 'state.json')

    backend.state.researched.append('study')
    resumed = controller(backend, tmp_path, kind=RouteLoop, resume=True)
    resumed.memory = resumed.memory_type.load(
        tmp_path / 'state.json', backend.state.session_id, 'rocket_launch')
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
        changed.factory['input_routes']['sources']['recipe:iron-plate']['topology'] = False
    else:
        changed.factory['entities']['input:drill']['unit_number'] = 999
    after, _ = loop._work_candidates(changed)
    assert not any(p.steps[0].action == 'factory_craft' for p in after)
