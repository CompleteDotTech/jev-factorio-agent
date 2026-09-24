"""Synthetic regression scenarios; none authorizes a native VM cutover."""
from copy import deepcopy

import pytest

from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.service_visits import service_visit
from jev_factorio.skills import Plan
from test_demand_service import visit_fixture
from test_factory import catalog, machine, snapshot
from test_input_routes_lua import execute


def lab_fixture():
    data = catalog()
    data.technologies['study'] = {'enabled': True, 'count': 100, 'effects': [],
        'prerequisites': [], 'energy_ticks': 120,
        'ingredients': [{'name': 'red', 'amount': 1}, {'name': 'green', 'amount': 1}]}
    state = snapshot(inventory={'red': 1, 'green': 20})
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['utility:lab'] = machine('lab', energy=100, input={})
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    first = planner._transfer('utility:lab', 'red', 1)
    return state, data, planner, first


def test_same_visit_delivers_carried_science_without_enlarging_critical_tail():
    state, _, planner, first = lab_fixture()
    before = deepcopy(state)
    visit = service_visit(planner, first)
    assert len(visit.steps) == 2 and visit.steps[0] == first.steps[0]
    assert visit.steps[1].parameters['item'] == 'green'
    assert visit.steps[1].parameters['quantity'] == 20
    assert visit.materials['service_visit']['extra_ticks_estimate'] == 300
    assert visit.materials['service_visit']['collections_are_spendable'] is False
    assert Plan.from_dict(visit.to_dict()) == visit and state == before


@pytest.mark.parametrize('kind', [ReadyWorkPlanner, OutputBufferPlanner])
def test_carried_science_visit_is_reachable_without_missing_item_focus(kind):
    state, data, _, first = lab_fixture()
    planner = kind(data, state, 'rocket_launch')
    planner.plan = lambda: first
    assert planner.focus is None
    assert len(planner.candidates()[0].steps) == 2


def test_reservations_protect_carried_material_from_optional_service():
    state, data, _, planner, first = visit_fixture()
    planner.ledger = SupplyLedger.capture(state, data, reserved={'coal': 50, 'iron-ore': 20})
    assert service_visit(planner, first) == first
    state, data, planner, first = lab_fixture()
    planner.ledger = SupplyLedger.capture(state, data, reserved={'green': 20})
    assert service_visit(planner, first) == first


def test_service_cannot_spend_missing_or_forecast_science():
    state, _, planner, first = lab_fixture()
    state.inventory.pop('green')
    assert service_visit(planner, first) == first


@pytest.mark.parametrize('cause', ['geometry', 'distance', 'other_emergency', 'unknown_research'])
def test_optional_service_falls_back_to_original_transfer(cause):
    state, _, row, planner, first = visit_fixture()
    if cause == 'geometry':
        state.factory['entities'][row['source']].pop('position')
    elif cause == 'distance':
        state.factory['entities'][row['source']]['position'] = {'x': 100, 'y': 100}
    elif cause == 'other_emergency':
        state.factory['entities']['utility:boiler'] = machine('boiler', unit_number=123, fuel={'coal': 0})
    else:
        state.factory['research'] = 'unavailable-technology'
    assert service_visit(planner, first) == first


def test_due_science_blocks_optional_furnace_tasks_but_not_selected_first_step():
    state, data, row, planner, first = visit_fixture()
    _, science_data, _, _ = lab_fixture()
    data.technologies.update(science_data.technologies)
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['utility:lab'] = machine('lab', unit_number=123, energy=100)
    assert service_visit(planner, first) == first


@pytest.mark.parametrize('mode', ['background', 'crafting', 'one_step'])
def test_service_admission_preserves_serial_and_background_barriers(mode):
    state, _, planner, first = lab_fixture()
    if mode == 'background': planner.allow_service_visits = False
    if mode == 'crafting': state.factory['crafting_queue'] = 1
    assert service_visit(planner, first, max_steps=1 if mode == 'one_step' else 3) == first


def test_service_identity_does_not_depend_on_receipt_tick():
    state, _, planner, first = lab_fixture()
    original = service_visit(planner, first)
    state.tick += 1
    changed = service_visit(planner, planner._transfer('utility:lab', 'red', 1))
    assert changed.id == original.id
    assert changed.steps[0].parameters['receipt'] != original.steps[0].parameters['receipt']


def test_empty_survey_diagnostics_are_cached_without_repeating_native_resource_queries(tmp_path):
    execute('''
        resources={}
        local scans=0; local original=source.surface.find_entities_filtered
        source.surface.find_entities_filtered=function(q)
            if q.radius==40 then scans=scans+1 end
            return original(q)
        end
        local a=storage.campaign.observe().input_routes.diagnostics["recipe:iron-plate"]
        assert(a.reason=="ore_outside_local_survey" and a.placement_checks==0)
        assert(a.resource_count==0 and a.survey_tick==game.tick)
        game.tick=game.tick+1
        local b=storage.campaign.observe().input_routes.diagnostics["recipe:iron-plate"]
        assert(b.survey_tick==a.survey_tick and b.cached and scans==1)
        assert(placements==0)
    ''', tmp_path)


def test_rotation_discovers_ninth_resource_without_changing_limits(tmp_path):
    execute('''
        resources={}
        for n=1,8 do resources[n]={name="iron-ore",position={x=2+n*0.5,y=4.5},
            amount=50,valid=true,minable=true} end
        resources[9]={name="iron-ore",position={x=-10.5,y=0.5},amount=1000,valid=true,minable=true}
        local a=storage.campaign.observe().input_routes
        assert(next(a.sources)==nil)
        assert(a.diagnostics["recipe:iron-plate"].sampled_resources==8)
        assert(a.diagnostics["recipe:iron-plate"].reason=="resource_depleted_sample")
        game.tick=game.tick+300
        local b=storage.campaign.observe().input_routes
        local row=b.sources["recipe:iron-plate"]
        assert(row and #row.steps<=66 and placements==0)
        local d=b.diagnostics["recipe:iron-plate"]
        assert(d.reason=="route_available" and d.survey_radius==40 and d.max_belts==64)
        local id=row.layout
        assert(storage.campaign.observe().input_routes.sources["recipe:iron-plate"].layout==id)
    ''', tmp_path)


def test_owned_outpost_conflict_is_not_misreported_as_missing_ore(tmp_path):
    execute('''
        storage.mining_outposts={cells={["iron-ore"]={}}}
        local result=storage.campaign.observe().input_routes
        assert(next(result.sources)==nil)
        assert(result.diagnostics["recipe:iron-plate"].reason=="outpost_conflict")
        assert(placements==0)
    ''', tmp_path)


def test_obstructed_surveys_expose_hard_work_budgets(tmp_path):
    execute('''
        obstacle=function(q) return q.name=="transport-belt" end
        local d=storage.campaign.observe().input_routes.diagnostics["recipe:iron-plate"]
        assert(d.sampled_resources<=8 and d.path_attempts<=128)
        assert(d.path_expansions<=16384 and d.path_probes<=4096)
        assert(d.placement_checks<=4324 and placements==0)
    ''', tmp_path)


def test_mixed_resource_rejection_is_explicit_and_does_not_place(tmp_path):
    execute('''
        local original=source.surface.find_entities_filtered
        source.surface.find_entities_filtered=function(q)
            local result=original(q)
            if q.type=="resource" then
                result[#result+1]={name="copper-ore",valid=true,minable=true,amount=1000,position={x=0,y=0}}
            end
            return result
        end
        local d=storage.campaign.observe().input_routes.diagnostics["recipe:iron-plate"]
        assert(d.reason=="mixed_resource_sample" and d.mixed_resources>0 and placements==0)
    ''', tmp_path)


def test_invalid_survey_never_exports_raw_engine_exception(tmp_path):
    execute('''
        source.surface.find_entities_filtered=function() error("sensitive-engine-payload") end
        local d=storage.campaign.observe().input_routes.diagnostics["recipe:iron-plate"]
        assert(d.reason=="survey_evidence_invalid" and placements==0)
        for _,v in pairs(d) do assert(type(v)~="string" or not string.find(v,"sensitive")) end
    ''', tmp_path)
