"""Partial corridors must not remove manual science/fuel service before takeover."""
from copy import deepcopy
import pytest

from jev_factorio import solid_routes as contract
from solid_routes_fixtures import fixture, row, build, full, SOURCE, TARGET
from test_solid_routes_lua import runtime


@pytest.mark.parametrize('action,role', [('factory_extract', SOURCE), ('factory_insert', TARGET)])
@pytest.mark.parametrize('paid_count', [1, 2, 3])
def test_partial_route_keeps_endpoint_item_service(action, role, paid_count):
    state = fixture()
    for _ in range(paid_count):
        build(state)
    assert contract.permits(action, {'role': role, 'item': row(state)['item']}, state)


@pytest.mark.parametrize('action,role', [('factory_extract', SOURCE), ('factory_insert', TARGET)])
def test_complete_route_still_owns_endpoint_item(action, role):
    state = fixture(); full(state)
    assert not contract.permits(action, {'role': role, 'item': row(state)['item']}, state)


def test_partial_route_keeps_recipe_and_component_locks():
    state = fixture(); build(state)
    assert not contract.permits('factory_configure', {'role': TARGET}, state)
    assert not contract.permits('factory_extract', {'role': row(state)['parts']['receive']['role'], 'item': 'coal'}, state)


@pytest.mark.parametrize('phase', ['prepared', 'dispatching', 'placed'])
def test_partial_route_pending_does_not_allow_manual_service(phase):
    state = fixture(); build(state)
    row(state)['pending'] = {'part': 'belt:2', 'receipt': 'pending-test', 'phase': phase}
    assert not contract.permits('factory_extract', {'role': SOURCE, 'item': row(state)['item']}, state)


def test_native_partial_route_keeps_manual_service_until_sender_is_paid():
    runtime().execute('''
        local proposed=offer();local p=args(proposed)
        campaign.prepare_solid_route(p);campaign.build_solid_route(p)
        local cell=storage.solid_routes.cells[proposed.route]
        assert(not cell.parts.send and not cell.pending)
        expected_transfer_receipt="manual-extract";expected_extracting=true
        campaign.transfer(cell.source.role,cell.item,1,"manual-extract",true)
        expected_transfer_receipt="manual-insert";expected_extracting=false
        campaign.transfer(cell.target.role,cell.item,1,"manual-insert",false)
        assert(transfers==2 and paid_calls==1)
        assert(not pcall(campaign.configure,cell.target.role,"iron-gear-wheel"))
    ''')


def test_native_pending_cannot_interleave_manual_service():
    runtime().execute('''
        local proposed=offer();local p=args(proposed)
        campaign.prepare_solid_route(p)
        assert(not pcall(campaign.transfer,proposed.source.role,proposed.item,1,"manual-pending",true))
        assert(transfers==0)
    ''')


def test_native_old_implementation_revision_requires_reconciliation():
    from test_solid_routes_lua import LUA
    lua = runtime()
    lua.execute("storage.solid_routes.implementation_revision=nil")
    with pytest.raises(Exception, match="requires reconciliation"):
        lua.execute(LUA.read_text())
