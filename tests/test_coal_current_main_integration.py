"""Current-main composition regressions; modeled APIs, never engine acceptance."""
from copy import deepcopy

import pytest

from jev_factorio import coal_supply as coal, solid_routes as solid
from jev_factorio.planning import solid_investment
from test_coal_solid_composition import (
    mixed_state, paid_solid, coal_candidates, solid_candidates, paid_source, ROUTE,
)
from test_solid_routes_lua import runtime as solid_runtime, LUA
from test_coal_supply_lua import runtime as coal_runtime
from test_coal_mixed_transport import native, ROOT


def partial_coal_with_missing_downstream_kit():
    state, data, history = mixed_state()
    plan = coal_candidates(state, 'rocket_launch')[0]
    paid_source(state, plan.steps[0].parameters)
    corridor = next(p for p in solid_candidates(state, 'rocket_launch')
                    if p.steps[0].parameters['route'] != ROUTE)
    paid_solid(state, corridor.steps[0].parameters)
    held = coal.remaining_kit(coal.sources(state), state)
    state.inventory.update({'inserter': held['inserter'], 'transport-belt': held['transport-belt'],
                            'iron-plate': 200, 'copper-plate': 100})
    return state, data, history, held


def test_paid_coal_prefix_retains_fresh_downstream_kit_permission():
    state, data, history, held = partial_coal_with_missing_downstream_kit()
    plans, evidence = solid_investment.candidates(state, data, 'rocket_launch',
                                                  outcomes=history, reserved=held)
    assert plans, evidence
    plan = plans[0]
    assert plan.materials[solid_investment.MARKER]['stage'] == 'kit'
    assert solid_investment.fresh_permission(plan, plan.steps[0], state, data,
                                             outcomes=history, reserved=held)
    assert not plan.steps[0].costs.get('electric-mining-drill')


def test_pending_coal_corridor_still_revokes_downstream_kit():
    state, data, history, held = partial_coal_with_missing_downstream_kit()
    plan = solid_investment.candidates(state, data, 'rocket_launch', outcomes=history, reserved=held)[0][0]
    row = next(row for key, row in solid.routes(state).items() if key != ROUTE)
    step = next(spec for spec in row['steps'] if spec['part'] not in row['parts'])
    row['pending'] = {'part': step['part'], 'receipt': 'pending-other-project', 'phase': 'prepared'}
    assert not solid_investment.fresh_permission(plan, plan.steps[0], state, data,
                                                outcomes=history, reserved=held)
    assert not solid_investment.candidates(state, data, 'rocket_launch', outcomes=history, reserved=held)[0]


def test_documented_power_method_supports_both_route_families():
    lua = solid_runtime()
    lua.execute('assert(pole.prototype.supply_area_distance==nil);assert(offer().state=="proposed")')
    lua = coal_runtime()
    lua.execute('assert(pole1.prototype.supply_area_distance==nil);assert(coal_args("alpha","chest"))')


@pytest.mark.parametrize('value', ['nil', '-1', 'math.huge', '0/0', '"10"'])
def test_invalid_runtime_power_radius_refuses_before_payment(value):
    lua = solid_runtime()
    lua.execute(f'pole.prototype.get_supply_area_distance=function() return {value} end;'
                'assert(next(campaign.observe().solid_routes.routes)==nil and paid_calls==0)')
    lua = coal_runtime()
    lua.execute(f'pole1.prototype.get_supply_area_distance=function() return {value} end;'
                f'pole2.prototype.get_supply_area_distance=function() return {value} end;'
                'assert(next(campaign.observe().coal_supply.sources)==nil and paid_calls==0)')


def test_old_solid_revision_retains_state_without_reattaching_new_functions():
    lua = solid_runtime()
    lua.execute('local row=offer();local p=args(row);campaign.prepare_solid_route(p);'
                'retained=storage.solid_routes;observer=campaign.observe;retained.implementation_revision=3')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute(LUA.read_text())
    lua.execute('assert(storage.solid_routes==retained and campaign.observe==observer and paid_calls==0)')


def test_old_coal_revision_retains_paid_state_without_reattachment():
    lua = native()
    lua.execute('coal_build("alpha","chest");retained=storage.coal_supply;'
                'builder=campaign.build_coal_source;retained.revision=2')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute((ROOT/'src/jev_factorio/lua/coal_supply.lua').read_text())
    lua.execute('assert(storage.coal_supply==retained and campaign.build_coal_source==builder and paid_calls==1)')


@pytest.mark.parametrize('distance,allowed', [(0, False), (1, False), (2, True)])
def test_future_coal_corridors_keep_the_full_reserved_clearance(distance, allowed):
    state, _, _ = mixed_state()
    rows = coal.sources(state)
    cell = deepcopy(solid.routes(state)[ROUTE])
    # Exercise every future cell, including the unpaid interior of the corridor.
    selected = rows['alpha']['corridor'][1]['position']
    cell['steps'] = [{'position': {'x': selected['x'], 'y': selected['y'] + distance}}]
    assert coal.corridor_reservations_clear(cell, {'alpha': rows['alpha']}) is allowed


def test_native_downstream_preparation_respects_committed_future_coal_cells():
    lua = native()
    lua.execute('coal_build("alpha","chest");d=downstream_args();'
                'local cell=storage.solid_routes.offers[d.route];'
                'storage.coal_supply.rows.alpha.corridor[2].position=cell.steps[2].position;before=paid_calls')
    # Future geometry is deliberately corrupted to cross another proposal. The
    # fresh prepare boundary must refuse before journaling or paying that route.
    with pytest.raises(Exception, match='reservation conflict'):
        lua.execute('campaign.prepare_solid_route(d)')
    lua.execute('assert(paid_calls==before and storage.solid_routes.cells[d.route]==nil)')


def test_native_coal_preparation_respects_paid_future_downstream_cells():
    lua = native()
    lua.execute('d=downstream_args();campaign.prepare_solid_route(d);campaign.build_solid_route(d);'
                'p=coal_args("alpha","chest");local row=storage.coal_supply.rows.alpha;'
                'storage.solid_routes.cells[d.route].steps[2].position=row.corridor[2].position;before=paid_calls')
    with pytest.raises(Exception, match='reservation conflict'):
        lua.execute('campaign.prepare_coal_source(p)')
    lua.execute('assert(paid_calls==before and not storage.coal_supply.committed)')


@pytest.mark.parametrize('fault', ['manual_pending', 'fault'])
def test_native_downstream_never_bypasses_uncertain_coal_work(fault):
    lua = native()
    lua.execute('coal_build("alpha","chest");p=downstream_args();before=paid_calls')
    if fault == 'manual_pending':
        lua.execute('storage.coal_supply.rows.alpha.manual_pending={receipt="ambiguous",'
                    'quantity=1,phase="dispatching",unit_number=burner1.unit_number}')
    else:
        lua.execute('storage.coal_supply.rows.alpha.fault="manual_transfer_ambiguous"')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute('campaign.prepare_solid_route(p)')
    lua.execute('assert(paid_calls==before and storage.solid_routes.cells[p.route]==nil)')


def test_stale_manual_receipt_cannot_credit_an_unperformed_transfer():
    lua = coal_runtime()
    lua.execute('coal_all();game.tick=game.tick+1;before=stock.coal;'
                'campaign.receipts.old={role="alpha",item="coal",quantity=3,'
                'unit_number=burner1.unit_number,extracting=false,tick=game.tick-1}')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute('campaign.transfer("alpha","coal",3,"old",false)')
    lua.execute('assert(stock.coal==before and storage.coal_supply.rows.alpha.manual_total==0 '
                'and storage.coal_supply.rows.alpha.manual_pending==nil and transfers==0)')


def test_unpaid_coal_bundle_cannot_commit_adjacent_future_corridors():
    lua = native()
    lua.execute('p=coal_args("alpha","chest");local a=storage.coal_supply.rows.alpha;'
                'local b=storage.coal_supply.rows.beta;'
                'b.corridor[2].position={x=a.corridor[2].position.x,y=a.corridor[2].position.y+1}')
    with pytest.raises(Exception, match='reservation conflict'):
        lua.execute('campaign.prepare_coal_source(p)')
    lua.execute('assert(paid_calls==0 and not storage.coal_supply.committed)')
