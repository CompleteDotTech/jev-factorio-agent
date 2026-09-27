"""Paid corridor footprint isolation. Lua tests use API doubles, not Factorio."""
from copy import deepcopy
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
import json

import pytest

from jev_factorio import solid_routes as routes
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning.solid_routes import candidates
from jev_factorio.solid_controller import solid_loop_type
from solid_routes_fixtures import fixture, row, ROUTE, INTENTS, parameters


def pair(*, horizontal=False, offset=0):
    """Two distinct owned endpoints/identities; optionally crossing proposed paths."""
    state = fixture()
    other = deepcopy(row(state))
    other['route'] = 'solid:15001:15002:iron-gear-wheel:input'
    other['layout'] = 'solid-layout:2'

    def point(p):
        return ({'x': p['x'], 'y': p['y'] + offset} if horizontal
                else {'x': 4.5 - (p['y'] - .5) + offset, 'y': p['x'] - 3})

    for endpoint in (other['source'], other['target']):
        old_role = endpoint['role']
        entity = deepcopy(state.factory['entities'][old_role])
        endpoint['role'] = 'secondary:' + old_role
        endpoint['unit_number'] += 10000
        endpoint['position'] = point(endpoint['position'])
        a, b = (point(endpoint['bounds'][k]) for k in ('left_top', 'right_bottom'))
        endpoint['bounds'] = {'left_top': {k: min(a[k], b[k]) for k in ('x', 'y')},
                              'right_bottom': {k: max(a[k], b[k]) for k in ('x', 'y')}}
        entity.update(unit_number=endpoint['unit_number'], position=deepcopy(endpoint['position']))
        state.factory['entities'][endpoint['role']] = entity
    for step in other['steps']:
        step['position'] = point(step['position'])
        if not horizontal:
            step['direction'] = (step['direction'] + 4) % 16
    state.factory['solid_routes']['routes'][other['route']] = other
    state.factory['solid_routes']['diagnostics'].append(
        {'intent_index': 2, 'state': 'proposed', 'reason': 'ready_layout'})
    state.inventory.update(inserter=4, **{'transport-belt': 4})
    return state, other


def reserve(value, phase='prepared'):
    value['state'] = 'fault' if phase == 'fault' else 'building'
    if phase in {'prepared', 'dispatching', 'placed'}:
        value['pending'] = {'part': 'receive', 'receipt': 'reserved-receipt', 'phase': phase}
    elif phase == 'paid':
        value['parts']['receive'] = {'role': value['route'] + ':receive',
                                    'receipt': 'paid-receipt', 'unit_number': 25001, 'paid': 1}


@pytest.mark.parametrize('horizontal,offset', [(False, 0), (True, 0), (True, 1)])
def test_overlapping_proposals_are_alternatives_not_paid_reservations(horizontal, offset):
    state, _ = pair(horizontal=horizontal, offset=offset)
    assert len(routes.routes(state)) == 2
    assert len(candidates(state, 'rocket_launch')) == 2


@pytest.mark.parametrize('phase', ['prepared', 'dispatching', 'placed', 'paid', 'fault'])
@pytest.mark.parametrize('horizontal,offset', [(False, 0), (True, 0), (True, 1)])
def test_committed_corridor_cannot_share_planned_cells_or_join_clearance(phase, horizontal, offset):
    state, _ = pair(horizontal=horizontal, offset=offset)
    reserve(row(state), phase)
    with pytest.raises(ValueError, match='corridor'):
        routes.routes(state)
    assert not routes.allowed(parameters(state), state)
    assert not routes.permits('factory_wait', {}, state)


@pytest.mark.parametrize('offset', [2, -2, 10])
def test_spatially_separate_reservation_does_not_block_other_route(offset):
    state, _ = pair(horizontal=True, offset=offset)
    reserve(row(state))
    assert len(routes.routes(state)) == 2
    assert len(candidates(state, 'rocket_launch')) == 2


@pytest.mark.parametrize('reverse', [False, True])
def test_reservation_validation_is_order_independent(reverse):
    state, other = pair()
    reserve(other)
    if reverse:
        state.factory['solid_routes']['routes'] = dict(reversed(list(state.factory['solid_routes']['routes'].items())))
    with pytest.raises(ValueError, match='corridor'):
        routes.routes(state)


@pytest.mark.parametrize('offset,accept', [(0, False), (1, False), (2, True)])
def test_checkpoint_rejects_conflicting_full_footprints_before_native_enable(offset, accept):
    state, other = pair(horizontal=True, offset=offset)
    intents = [*deepcopy(INTENTS), {'source': other['source']['role'], 'target': other['target']['role'],
                                  'item': other['item'], 'destination': 'input'}]
    cls = solid_loop_type(HierarchicalLoop).memory_type
    memory = cls(state.session_id, 'rocket_launch', solid_intents=intents,
                 solid_epoch={'actor_index': 1, 'surface_index': 1, 'force_index': 1},
                 solid_commitments={key: routes.commitment(value)
                                    for key, value in state.factory['solid_routes']['routes'].items()})
    captured = deepcopy(asdict(memory))
    if accept:
        assert cls.from_bytes(json.dumps(captured).encode(), state.session_id, 'rocket_launch').solid_commitments == memory.solid_commitments
    else:
        with pytest.raises(ValueError, match='corridor'):
            cls.from_bytes(json.dumps(captured).encode(), state.session_id, 'rocket_launch')
    assert captured == asdict(memory)


SETUP = '''
    -- Two disjoint endpoint pairs whose unbuilt paths necessarily cross.
    pole.position={x=10.5,y=0.5}
    pole.bounding_box={left_top={x=10.3,y=0.3},right_bottom={x=10.7,y=0.7}}
    second_source=entity("assembling-machine-1","assembling-machine",3.5,-6.5,1.4)
    second_target=entity("assembling-machine-1","assembling-machine",3.5,6.5,1.4)
    second_source.recipe=source.recipe; second_target.recipe=target.recipe
    second_source.output.values["iron-gear-wheel"]=20
    campaign.entities["second-source"]=second_source
    campaign.entities["second-target"]=second_target
    campaign.set_solid_intents({
        {source="recipe:iron-gear-wheel",target="recipe:automation-science-pack",item="iron-gear-wheel",destination="input"},
        {source="second-source",target="second-target",item="iron-gear-wheel",destination="input"}
    })
    function both()
        local rows=campaign.observe().solid_routes.routes
        local a,b
        for _,v in pairs(rows) do
            if v.source.role=="second-source" then b=v else a=v end
        end
        assert(a and b, "Fixture needs two valid initial offers")
        local pa,pb=args(a),args(b)
        pa.receipt="first-project:receive";pb.receipt="second-project:receive"
        return a,b,pa,pb
    end
'''


def runtime():
    lua = pytest.importorskip('lupa.lua52').LuaRuntime()
    lua.execute((Path(__file__).parent / 'fixtures/solid_routes_runtime.lua').read_text())
    lua.execute(files('jev_factorio').joinpath('lua/solid_routes.lua').read_text())
    lua.execute(SETUP)
    return lua


@pytest.mark.parametrize('stage', ['prepared', 'paid', 'fault', 'dispatching'])
@pytest.mark.parametrize('reverse', [False, True])
def test_native_first_reservation_blocks_crossing_before_second_spend(stage, reverse):
    lua = runtime()
    lua.globals().reverse = reverse
    lua.globals().stage = stage
    lua.execute('''
        local a,b,pa,pb=both()
        if reverse then a,b,pa,pb=b,a,pb,pa end
        campaign.prepare_solid_route(pa)
        local cell=storage.solid_routes.cells[a.route]
        if stage=="paid" then campaign.build_solid_route(pa)
        elseif stage=="fault" then cell.fault="fixture-retained-fault"
        elseif stage=="dispatching" then cell.pending.phase="dispatching" end
        local spent=paid_calls
        assert(not pcall(campaign.prepare_solid_route,pb), "Crossing prepare must fail before a second commitment")
        assert(paid_calls==spent and storage.solid_routes.cells[b.route]==nil)
        assert(storage.solid_routes.cells[a.route]==cell)
    ''')


@pytest.mark.parametrize('reverse', [False, True])
def test_native_observation_prunes_conflicting_offer_and_retains_reserved_identity(reverse):
    lua = runtime(); lua.globals().reverse = reverse
    lua.execute('''
        local a,b,pa,pb=both()
        if reverse then a,b,pa,pb=b,a,pb,pa end
        campaign.prepare_solid_route(pa)
        for _=1,3 do
            local snapshot=campaign.observe().solid_routes
            assert(snapshot.routes[a.route] and snapshot.routes[a.route].layout==a.layout)
            assert(not snapshot.routes[b.route], "Conflicting unpaid offer must be withdrawn")
            local diagnostic=snapshot.diagnostics[reverse and 1 or 2]
            assert(diagnostic.state=="unavailable" and diagnostic.reason=="reserved_corridor")
            assert(storage.solid_routes.cells[a.route].pending.receipt==pa.receipt)
            assert(paid_calls==0)
        end
        assert(not pcall(campaign.prepare_solid_route,pb) and paid_calls==0)
        campaign.build_solid_route(pa);assert(paid_calls==1)
    ''')


def test_native_failed_affordability_does_not_reserve_unpaid_offer():
    runtime().execute('''
        local a,b,pa,pb=both()
        stock.inserter=1
        assert(not pcall(campaign.prepare_solid_route,pa))
        assert(next(storage.solid_routes.cells)==nil and paid_calls==0)
        stock.inserter=10
        campaign.prepare_solid_route(pb)
        assert(storage.solid_routes.cells[b.route] and not storage.solid_routes.cells[a.route])
    ''')


def test_native_reattach_retains_reserved_footprint_and_pending_receipt():
    lua = runtime()
    lua.execute('a,b,pa,pb=both();campaign.prepare_solid_route(pa);retained=storage.solid_routes.cells[a.route]')
    lua.execute(files('jev_factorio').joinpath('lua/solid_routes.lua').read_text())
    lua.execute('''
        assert(storage.solid_routes.cells[a.route]==retained and retained.pending.receipt==pa.receipt)
        assert(not pcall(campaign.prepare_solid_route,pb) and paid_calls==0)
    ''')


def test_older_runtime_requires_explicit_handoff_not_in_place_upgrade():
    lua = runtime()
    lua.execute('a,b,pa,pb=both();campaign.prepare_solid_route(pa);storage.solid_routes.implementation_revision=2;retained=storage.solid_routes')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute(files('jev_factorio').joinpath('lua/solid_routes.lua').read_text())
    lua.execute('assert(storage.solid_routes==retained and paid_calls==0 and retained.cells[a.route].pending.receipt==pa.receipt)')


def test_native_separate_corridors_can_build_interleaved_and_replay_without_extra_payment():
    runtime().execute('''
        local function move(e,x,y)
            e.position={x=x,y=y}
            e.bounding_box={left_top={x=x-1.4,y=y-1.4},right_bottom={x=x+1.4,y=y+1.4}}
        end
        move(second_source,0.5,6.5);move(second_target,7.5,6.5)
        local a,b,pa,pb=both()
        campaign.prepare_solid_route(pa);campaign.build_solid_route(pa)
        campaign.prepare_solid_route(pb);campaign.build_solid_route(pb)
        for i=2,#a.steps do
            for j,current in ipairs({a,b}) do
                local p=args(current,current.steps[i].part)
                p.receipt="separate:"..j..":"..p.part
                campaign.prepare_solid_route(p);campaign.build_solid_route(p)
            end
        end
        local snapshot=campaign.observe().solid_routes
        assert(snapshot.routes[a.route].state=="ready" and snapshot.routes[b.route].state=="ready")
        assert(paid_calls==8 and stock.inserter==6 and stock["transport-belt"]==96)
        campaign.prepare_solid_route(pa);campaign.build_solid_route(pa)
        campaign.prepare_solid_route(pb);campaign.build_solid_route(pb)
        assert(paid_calls==8)
        assert(snapshot.routes[a.route].flow.received==0 and snapshot.routes[b.route].flow.received==0)
    ''')


def test_native_inconsistent_retained_commitments_fail_closed_without_dropping_pending():
    runtime().execute('''
        local a,b,pa,pb=both()
        campaign.prepare_solid_route(pa)
        -- Corrupted retained state, not a second legal actor operation.
        storage.solid_routes.cells[b.route]=storage.solid_routes.offers[b.route]
        storage.solid_routes.offers[b.route]=nil
        local snapshot=campaign.observe().solid_routes
        assert(snapshot.routes[a.route].state=="fault" and snapshot.routes[b.route].state=="fault")
        assert(snapshot.routes[a.route].pending.receipt==pa.receipt and paid_calls==0)
        assert(not pcall(campaign.build_solid_route,pa))
        assert(not pcall(campaign.prepare_solid_route,pb))
        assert(paid_calls==0)
    ''')


@pytest.mark.parametrize('offset', [0, 1])
def test_invalid_checkpoint_is_rejected_before_backend_enable(tmp_path, offset):
    state, other = pair(horizontal=True, offset=offset)
    intents = [*deepcopy(INTENTS), {'source': other['source']['role'], 'target': other['target']['role'],
                                  'item': other['item'], 'destination': 'input'}]
    loop_type = solid_loop_type(HierarchicalLoop)
    memory = loop_type.memory_type(state.session_id, 'rocket_launch', solid_intents=intents,
        solid_epoch={'actor_index': 1, 'surface_index': 1, 'force_index': 1},
        solid_commitments={key: routes.commitment(value)
                           for key, value in state.factory['solid_routes']['routes'].items()})
    path = tmp_path / 'checkpoint.json'
    captured = json.dumps(asdict(memory)).encode()
    path.write_bytes(captured)

    class Backend:
        calls = 0

        def enable_factory(self):
            self.calls += 1
            raise AssertionError('Invalid checkpoint reached native installation')

    backend = Backend()
    with pytest.raises(ValueError, match='corridor'):
        loop_type(backend, solid_intents=intents, factory_scheduling='ready-work',
                  checkpoint=path, resume_controller=True, target='rocket_launch', policy='deterministic')
    assert backend.calls == 0 and path.read_bytes() == captured


def test_composed_observer_rejects_overlapping_commitment_without_erasing_failure_history(tmp_path):
    from test_solid_route_integration import Backend
    backend = Backend()
    backend.state, other = pair()
    reserve(row(backend.state))
    intents = [*deepcopy(INTENTS), {'source': other['source']['role'], 'target': other['target']['role'],
                                  'item': other['item'], 'destination': 'input'}]
    path = tmp_path / 'checkpoint.json'
    loop_type = solid_loop_type(HierarchicalLoop)
    loop = loop_type(backend, solid_intents=intents, factory_scheduling='ready-work',
                     checkpoint=path, target='rocket_launch', tick_seconds=0, policy='deterministic')
    loop.memory = loop.memory_type(backend.state.session_id, 'rocket_launch',
                                  failures={'previous-project': 2}, last_tick=backend.state.tick)
    snapshot = loop._observe()
    assert loop._execution_barrier(snapshot)
    assert loop.memory.status == 'uncertain'
    assert loop.memory.failures == {'previous-project': 2} and backend.calls == []
    assert row(backend.state)['pending']['receipt'] == 'reserved-receipt'
    saved = json.loads(path.read_bytes())
    assert saved['status'] == 'uncertain' and saved['failures'] == {'previous-project': 2}


@pytest.mark.parametrize('marker', [None, 'unrelated-reservation-contract'])
def test_matching_revision_without_matching_reservation_contract_is_not_reattached(marker):
    lua = runtime()
    lua.globals().replacement_marker = marker
    lua.execute('a,b,pa,pb=both();campaign.prepare_solid_route(pa);storage.solid_routes.implementation_revision=3;storage.solid_routes.reservation_contract=replacement_marker;retained=storage.solid_routes')
    with pytest.raises(Exception, match='reconciliation'):
        lua.execute(files('jev_factorio').joinpath('lua/solid_routes.lua').read_text())
    lua.execute('assert(storage.solid_routes==retained and paid_calls==0 and retained.cells[a.route].pending.receipt==pa.receipt)')
