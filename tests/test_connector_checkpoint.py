"""Checkpoint authority for native connector payments is never inferred from topology."""
from types import SimpleNamespace
import json

import pytest

from jev_factorio.connector_checkpoint import (pending_owned, reconcile,
                                                validate_binding)
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.connection_identity import connection_key
from jev_factorio.skills import Step


PARAMS = {'source': 'utility:boiler', 'target': 'utility:engine',
          'kind': 'pipe', 'fluid': 'steam'}
RECEIPT = connection_key(PARAMS)


class Native:
    def __init__(self, row, cells):
        self.row, self.cells = row, cells
        self.calls = []

    def command(self, script):
        assert 'connector_page' in script
        self.calls.append(script)
        args = script.split('connector_page(', 1)[1].split(')', 1)[0]
        receipt, offset, limit = json.loads('[' + args + ']')
        assert receipt == RECEIPT
        return json.dumps({'id': receipt, 'valid': self.row['state'] != 'fault',
                           'cell_count': len(self.cells), 'offset': offset,
                           'cells': self.cells[offset - 1:offset - 1 + limit]})


def fixture(*, paid=True, state='complete', unit=100):
    cells = [{'index': 1, 'position': {'x': .5, 'y': 1.5},
              'unit_number': unit, 'paid': 1 if paid else 0,
              'external': not paid, 'tick': 20}]
    row = {'id': RECEIPT, **PARAMS, 'source_unit': 11, 'target_unit': 12,
           'actor_unit': 13, 'surface_index': 1, 'force_index': 1,
           'session_id': 'test-session', 'state': state, 'paid': int(paid),
           'external': int(not paid), 'owned': paid and state == 'complete',
           'cell_count': 1, 'tick': 20}
    native = Native(row, cells)
    snapshot = SimpleNamespace(session_id='test-session', factory={
        'connector_ownership': {'protocol': 1, 'session_id': 'test-session',
                                'active': None if state == 'complete' else RECEIPT,
                                'routes': {RECEIPT: row}}})
    memory = SimpleNamespace(connector_ownership={
        'protocol': 1, 'session_id': 'test-session', 'routes': {}},
        pending={'action': 'factory_connect', 'dispatch': 'ambiguous'},
        active_plan={'steps': [{'parameters': PARAMS}]}, step_index=0,
        status='running', reason='')
    return memory, snapshot, native


def test_lost_reply_binds_exact_paid_unit_without_repayment():
    memory, snapshot, native = fixture()
    reconcile(memory, snapshot, native, resume=True)
    row = memory.connector_ownership['routes'][RECEIPT]
    assert row['cells'][0]['unit_number'] == 100
    assert pending_owned(memory, SimpleNamespace(parameters=PARAMS))
    assert len(native.calls) == 1 and all('connector_page' in c for c in native.calls)
    validate_binding(json.loads(json.dumps(memory.connector_ownership)), 'test-session')
    reconcile(memory, snapshot, native, resume=True)
    assert row == memory.connector_ownership['routes'][RECEIPT]
    assert len(native.calls) == 2  # Every observation rechecks the exact paid cell.


def test_partial_route_retains_pending_and_blocks_another_payment():
    memory, snapshot, native = fixture(state='building')
    native.row['pending'] = 1
    native.row.pop('owned')  # Lua leaves ownership nil until finish.
    native.row['paid'] = 0
    native.cells[0]['unit_number'] = None
    native.cells[0]['paid'] = 0
    reconcile(memory, snapshot, native, resume=True)
    assert memory.status == 'uncertain'
    assert not pending_owned(memory, SimpleNamespace(parameters=PARAMS))


def test_old_checkpoint_cannot_adopt_native_route_or_empty_new_protocol():
    memory, snapshot, native = fixture()
    memory.connector_ownership = None
    with pytest.raises(ValueError, match='Old checkpoint'):
        reconcile(memory, snapshot, native, resume=True)
    snapshot.factory['connector_ownership']['routes'] = []
    with pytest.raises(ValueError, match='Old checkpoint'):
        reconcile(memory, snapshot, native, resume=True)


def test_existing_same_force_cell_is_external_not_owned():
    memory, snapshot, native = fixture(paid=False)
    reconcile(memory, snapshot, native, resume=True)
    assert not pending_owned(memory, SimpleNamespace(parameters=PARAMS))
    assert memory.connector_ownership['routes'][RECEIPT]['cells'][0]['external']


def test_changed_unit_and_disappeared_route_fail_closed():
    memory, snapshot, native = fixture()
    reconcile(memory, snapshot, native, resume=True)
    native.cells[0]['unit_number'] = 101
    native.row['state'] = 'fault'  # Native summary checks each retained unit.
    with pytest.raises(ValueError, match='ownership fault'):
        reconcile(memory, snapshot, native, resume=True)
    snapshot.factory['connector_ownership']['routes'] = {}
    with pytest.raises(ValueError, match='disappeared'):
        reconcile(memory, snapshot, native, resume=True)


def test_same_count_payment_reassignment_cannot_evade_detail_comparison():
    memory, snapshot, native = fixture()
    native.cells.append({'index': 2, 'position': {'x': 1.5, 'y': 1.5},
                         'unit_number': 101, 'paid': 0, 'external': True, 'tick': 20})
    native.row['cell_count'] = 2
    native.row['external'] = 1
    native.row['owned'] = False
    reconcile(memory, snapshot, native, resume=True)
    native.cells[0]['paid'] = 0
    native.cells[0]['external'] = True
    native.cells[1]['paid'] = 1
    native.cells[1]['external'] = False
    # Summary identity, totals and ownership have not changed.
    with pytest.raises(ValueError, match='Paid connector unit changed'):
        reconcile(memory, snapshot, native, resume=True)


def test_unrelated_route_cannot_appear_or_duplicate_receipt():
    memory, snapshot, native = fixture()
    memory.pending = None
    with pytest.raises(ValueError, match='Uncheckpointed'):
        reconcile(memory, snapshot, native, resume=True)
    memory.pending = {'action': 'factory_connect', 'dispatch': 'ambiguous'}
    memory.active_plan['steps'][0]['parameters'] = {**PARAMS, 'fluid': 'water'}
    with pytest.raises(ValueError, match='identity differs'):
        reconcile(memory, snapshot, native, resume=True)


def test_completed_receipt_cannot_be_dispatched_or_debited_again():
    memory, snapshot, native = fixture()
    reconcile(memory, snapshot, native, resume=True)
    loop = SimpleNamespace(memory=memory)
    step = Step('factory_connect', 'connection', parameters=PARAMS)
    snapshot.world_kind = 'fle'
    assert HierarchicalLoop._step_allowed(loop, step, snapshot) is False
    assert len(native.calls) == 1  # Only a read-only detail page was sent.


def test_old_checkpoint_rejected_before_native_factory_enable(tmp_path):
    path = tmp_path / 'controller.json'
    CampaignMemory('test-session', 'rocket_launch').save(path)

    class Backend:
        _native_attachment = {'modules': {'connector_ownership': True},
                              'session_id': 'test-session'}
        calls = 0

        def enable_factory(self):
            self.calls += 1

    backend = Backend()
    with pytest.raises(ValueError, match='Old checkpoint'):
        HierarchicalLoop(backend, policy='deterministic', checkpoint=str(path),
                         resume_controller=True)
    assert backend.calls == 0


def test_preflight_checkpoint_bytes_stay_pinned_until_first_observation(tmp_path):
    path = tmp_path / 'controller.json'
    binding = {'protocol': 1, 'session_id': 'test-session', 'routes': {}}
    CampaignMemory('test-session', 'rocket_launch', connector_ownership=binding).save(path)

    class Backend:
        _native_attachment = {'modules': {'connector_ownership': True},
                              'session_id': 'test-session'}
        calls = 0

        def enable_factory(self):
            self.calls += 1

    backend = Backend()
    loop = HierarchicalLoop(backend, policy='deterministic', checkpoint=str(path),
                            resume_controller=True)
    assert backend.calls == 1
    data = json.loads(path.read_text())
    data['last_tick'] = 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='changed after preflight'):
        loop._initial_memory(SimpleNamespace(session_id='test-session'))
