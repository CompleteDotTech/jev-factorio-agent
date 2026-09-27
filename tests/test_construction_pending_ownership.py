"""Native journals cannot become fresh plans without their durable controller owner.

The ordinary fixtures exercise actual controller/planner code with backend doubles;
the Lua-only case produces the orphan journal through the real native extension.
None of these cases is a Factorio-engine or production-campaign result.
"""
from copy import deepcopy

import pytest

from jev_factorio import coal_supply as coal, solid_routes as solid
from jev_factorio.planning.coal_supply import candidates as coal_candidates
from jev_factorio.planning.solid_routes import candidates as solid_candidates
from coal_supply_fixtures import snapshot as coal_snapshot
from test_coal_supply_lua import runtime as coal_runtime
from test_coal_supply_integration import Backend as CoalBackend, controller as coal_controller
from test_solid_route_integration import Backend as SolidBackend, controller as solid_controller
from solid_routes_fixtures import row as solid_row


def setup_case(family, tmp_path):
    backend, maker = ((CoalBackend(), coal_controller) if family == 'coal'
                      else (SolidBackend(), solid_controller))
    loop = maker(backend, tmp_path)
    assert loop.step()['verified']
    if family == 'coal':
        row = coal.sources(backend.state)['beta']
        part = 'chest'
    else:
        row = solid_row(backend.state)
        part = next(p['part'] for p in row['steps'] if p['part'] not in row['parts'])
    return backend, loop, maker, row, part


@pytest.mark.parametrize('family', ['coal', 'solid'])
@pytest.mark.parametrize('resume', [False, True])
def test_orphan_prepared_journal_after_paid_prefix_never_becomes_a_new_attempt(family, resume, tmp_path):
    backend, loop, maker, row, part = setup_case(family, tmp_path)
    loop.memory.failures['retained-failure'] = 2
    loop._save()
    row['pending'] = {'part': part, 'receipt': 'foreign-prepared-journal', 'phase': 'prepared'}
    before = deepcopy(backend.state)
    calls = deepcopy(backend.calls)
    if resume:
        loop = maker(backend, tmp_path, resume=True)
    record = loop.step()
    assert record['status'] == 'uncertain', record
    assert backend.calls == calls
    assert backend.state == before
    assert loop.memory.pending is None
    assert loop.memory.failures == {'retained-failure': 2}
    loop.step()
    assert backend.calls == calls


@pytest.mark.parametrize('family', ['coal', 'solid'])
def test_orphan_journal_arriving_at_fresh_preconditions_never_pays(family, tmp_path):
    backend, loop, _, row, part = setup_case(family, tmp_path)
    calls = deepcopy(backend.calls)
    trigger = backend.observations + 2
    def inject():
        if backend.observations == trigger:
            # Even identical future plan parameters are not a prepared owner:
            # the controller has not persisted its pending action at this point.
            active = loop.memory.active_plan
            assert active and loop.memory.pending is None
            params = active['steps'][loop.memory.step_index]['parameters']
            row['pending'] = {'part': part, 'receipt': params['receipt'], 'phase': 'prepared'}
    backend.before_observe = inject
    record = loop.step()
    assert record['status'] == 'uncertain', record
    assert backend.calls == calls
    assert row['pending']['phase'] == 'prepared'
    assert not loop.memory.failures


@pytest.mark.parametrize('family', ['coal', 'solid'])
@pytest.mark.parametrize('change', ['receipt', 'pending_action'])
def test_exact_owner_mismatch_keeps_the_original_pending_identity(family, change, tmp_path):
    backend, loop, _, _, _ = setup_case(family, tmp_path)
    backend.prepared_once = True
    assert not loop.step()['verified']
    pending = deepcopy(loop.memory.pending)
    attempt = deepcopy(loop.memory.attempt)
    if family == 'coal':
        journal = coal.sources(backend.state)['beta']['pending']
    else:
        journal = solid_row(backend.state)['pending']
    if change == 'receipt':
        journal['receipt'] = 'different-owner-receipt'
    else:
        loop.memory.pending['action'] = 'factory_wait'
        pending = deepcopy(loop.memory.pending)
    calls = deepcopy(backend.calls)
    record = loop.step()
    assert record['status'] == 'uncertain', record
    assert backend.calls == calls
    assert loop.memory.pending == pending
    assert loop.memory.attempt == attempt


@pytest.mark.parametrize('family', ['coal', 'solid'])
def test_matching_owned_prepare_still_replays_once_with_same_attempt(family, tmp_path):
    backend, loop, maker, _, _ = setup_case(family, tmp_path)
    backend.prepared_once = True
    assert not loop.step()['verified']
    pending = deepcopy(loop.memory.pending)
    attempt = deepcopy(loop.memory.attempt)
    calls = deepcopy(backend.calls)
    resumed = maker(backend, tmp_path, resume=True)
    record = resumed.step()
    assert record['verified'], record
    assert len(backend.calls) == len(calls) + 1
    assert backend.calls[-1] == calls[-1]
    assert resumed.memory.attempt_outcomes[-1]['id'] == attempt['id']
    assert pending['action'] == calls[-1][0]


def test_native_lua_preparation_is_not_a_fresh_coal_candidate():
    lua = coal_runtime()
    lua.execute('coal_build("alpha","chest");p=coal_args("beta","chest");'
                'campaign.prepare_coal_source(p);before=paid_calls')
    state = coal_snapshot(lua)
    assert coal.sources(state)['beta']['pending']['phase'] == 'prepared'
    assert coal_candidates(state, 'iron_smelting') == []
    lua.execute('assert(paid_calls==before and storage.coal_supply.rows.beta.pending.receipt==p.receipt)')


def test_solid_preparation_is_not_a_fresh_component_candidate(tmp_path):
    backend, _, _, row, part = setup_case('solid', tmp_path)
    row['pending'] = {'part': part, 'receipt': 'foreign-prepared-journal', 'phase': 'prepared'}
    assert solid_candidates(backend.state, 'iron_smelting') == []


@pytest.mark.parametrize('phase', ['prepared', 'dispatching', 'placed'])
def test_second_mixed_journal_cannot_share_one_owned_controller_attempt(phase, tmp_path):
    backend, loop, _, _, _ = setup_case('coal', tmp_path)
    backend.prepared_once = True
    assert not loop.step()['verified']
    pending, attempt = deepcopy(loop.memory.pending), deepcopy(loop.memory.attempt)
    # The retained action owns beta's source, not alpha's receiving corridor.
    route = coal.route_for(coal.sources(backend.state)['alpha'], backend.state)
    route['state'] = 'building'
    route['pending'] = {'part': route['steps'][0]['part'],
                        'receipt': 'other-mixed-journal', 'phase': phase}
    calls, snapshot = deepcopy(backend.calls), deepcopy(backend.state)
    record = loop.step()
    assert record['status'] == 'uncertain', record
    assert backend.calls == calls and backend.state == snapshot
    assert loop.memory.pending == pending and loop.memory.attempt == attempt


@pytest.mark.parametrize('family', ['coal', 'solid'])
@pytest.mark.parametrize('phase', ['dispatching', 'placed'])
def test_unowned_nonprepared_journal_is_preserved_without_replay(family, phase, tmp_path):
    backend, loop, _, row, part = setup_case(family, tmp_path)
    row['pending'] = {'part': part, 'receipt': 'orphan-in-flight', 'phase': phase}
    calls, snapshot = deepcopy(backend.calls), deepcopy(backend.state)
    record = loop.step()
    assert record['status'] == 'uncertain', record
    assert backend.calls == calls and backend.state == snapshot
    assert loop.memory.pending is None and loop.memory.attempt is None


@pytest.mark.parametrize('family', ['coal', 'solid'])
def test_foreign_journal_fault_stays_closed_even_if_journal_disappears(family, tmp_path):
    backend, loop, _, row, part = setup_case(family, tmp_path)
    row['pending'] = {'part': part, 'receipt': 'unreconciled-foreign', 'phase': 'prepared'}
    calls = deepcopy(backend.calls)
    assert loop.step()['status'] == 'uncertain'
    row['pending'] = {}  # A later read cannot silently clear the recorded fault.
    assert loop.step()['status'] == 'uncertain'
    assert backend.calls == calls
