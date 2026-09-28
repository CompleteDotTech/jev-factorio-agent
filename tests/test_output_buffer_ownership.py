"""Durable paid output ownership over synthetic dispatcher/restart evidence."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from jev_factorio.acceptance_io import canonical
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.dev_preflight import checkpoint_read, checkpoint_type
from jev_factorio.memory import CampaignMemory, load_checkpoint
from jev_factorio.output_buffers import expected_commitments, validate_commitments
from test_output_buffer_integration import BufferBackend, loop_for, setup


def resume(backend, path):
    return buffered_loop_type(HierarchicalLoop)(backend, policy='deterministic',
        target='rocket_launch', factory_scheduling='ready-work', tick_seconds=0,
        checkpoint=str(path), resume_controller=True)


def owned(row):
    return {row['source']: {key: deepcopy(row[key]) for key in ('source_unit', 'layout', 'parts')}}


def test_new_paid_part_is_durable_before_success_and_both_readers_keep_output_only_schema(tmp_path):
    backend = BufferBackend(); loop = loop_for(backend, tmp_path)
    assert loop.step()['verified']
    path = tmp_path / 'state.json'
    saved = load_checkpoint(path, backend.state.session_id, 'rocket_launch')
    assert saved.output_commitments == owned(backend.row) == loop.memory.output_commitments
    value, _ = checkpoint_read(path)
    assert checkpoint_type(value).from_bytes(path.read_bytes(), saved.session_id, saved.target).output_commitments == owned(backend.row)
    with pytest.raises(ValueError):
        CampaignMemory.from_bytes(path.read_bytes(), saved.session_id, saved.target)


def test_unchanged_owner_observation_does_not_add_checkpoint_io(tmp_path, monkeypatch):
    backend = BufferBackend(); loop = loop_for(backend, tmp_path)
    assert loop.step()['verified']
    monkeypatch.setattr(loop.memory, 'save', lambda _: pytest.fail('unchanged owner caused extra save'))
    loop._observe()
    assert loop.memory.output_commitments == owned(backend.row)


def test_lost_reply_restart_observes_exact_pending_receipt_without_second_payment(tmp_path):
    backend = BufferBackend(); backend.mode = 'lost_ack'
    loop = loop_for(backend, tmp_path)
    assert not loop.step()['verified']
    pending = deepcopy(loop.memory.pending)
    rebuilt = resume(backend, tmp_path / 'state.json')
    assert rebuilt.step()['verified']
    assert len(backend.calls) == 1 and pending['dispatch'] == 'ambiguous'
    assert rebuilt.memory.output_commitments == owned(backend.row)


def test_idle_new_controller_never_adopts_existing_paid_buffer(tmp_path):
    backend = BufferBackend()
    backend.state, backend.catalog, backend.row = setup(True)
    loop = loop_for(backend, tmp_path)
    result = loop.step()
    assert result['status'] == 'uncertain' and not backend.calls
    assert loop.memory.output_commitments == {}


@pytest.mark.parametrize('mutation', ['receipt', 'unit', 'layout', 'removed', 'extra', 'alias', 'bool_paid'])
def test_retained_identity_regression_or_unowned_addition_preserves_checkpoint_owner(tmp_path, mutation):
    backend = BufferBackend(); loop = loop_for(backend, tmp_path)
    assert loop.step()['verified']
    before = deepcopy(loop.memory.output_commitments)
    part = backend.row['parts']['chest']
    if mutation == 'receipt': part['receipt'] = 'other'
    elif mutation == 'unit': backend.state.factory['entities'][part['role']]['unit_number'] = 999
    elif mutation == 'layout': backend.row['layout'] = 'other'
    elif mutation == 'removed': backend.row['parts'] = {}
    elif mutation == 'extra':
        backend.row['parts']['inserter'] = dict(part, role='extra', unit_number=999, receipt='untracked')
    elif mutation == 'alias': backend.row['parts']['inserter'] = dict(part)
    else: part['paid'] = True
    assert loop.step()['status'] == 'uncertain'
    assert loop.memory.output_commitments == before and len(backend.calls) == 1


def test_unrelated_receipt_cannot_clear_pending_or_create_owner(tmp_path):
    backend = BufferBackend(); backend.mode = 'lost_ack'
    loop = loop_for(backend, tmp_path); loop.step()
    original = deepcopy(loop.memory.pending), deepcopy(loop.memory.attempt), deepcopy(loop.memory.reservations)
    backend.row['parts']['chest']['receipt'] = 'unrelated'
    assert loop.step()['status'] == 'uncertain'
    assert loop.memory.output_commitments == {} and len(backend.calls) == 1
    assert (loop.memory.pending, loop.memory.attempt, loop.memory.reservations) == original


def test_later_invalid_row_does_not_partially_install_new_paid_prefix(tmp_path):
    backend = BufferBackend(); backend.mode = 'lost_ack'
    loop = loop_for(backend, tmp_path); loop.step()
    backend.state.factory['output_buffers']['sources']['recipe:copper-plate'] = {'state': 'fault'}
    assert loop.step()['status'] == 'uncertain'
    assert loop.memory.output_commitments == {} and len(backend.calls) == 1


def test_new_owner_save_failure_poison_blocks_observation_and_replay(tmp_path, monkeypatch):
    backend = BufferBackend(); loop = loop_for(backend, tmp_path)
    save = loop.memory.save
    def fail_on_new_owner(path):
        if loop.memory.output_commitments:
            raise OSError('fixture output ownership persistence failure')
        return save(path)
    monkeypatch.setattr(loop.memory, 'save', fail_on_new_owner)
    with pytest.raises(OSError, match='ownership persistence'):
        loop.step()
    assert len(backend.calls) == 1
    with pytest.raises(RuntimeError, match='persistence failed'):
        loop.step()
    saved = json.loads((tmp_path / 'state.json').read_bytes())
    assert saved['pending'] and saved['output_commitments'] == {}


@pytest.mark.parametrize('field', ['output_buffers_schema', 'output_commitments'])
def test_partial_extension_is_not_migrated(tmp_path, field):
    memory = buffered_loop_type(HierarchicalLoop).memory_type('test', 'rocket_launch', last_tick=1)
    data = asdict(memory); data.pop(field)
    path = tmp_path / 'checkpoint.json'; path.write_bytes(canonical(data))
    with pytest.raises(ValueError, match='Incomplete'):
        load_checkpoint(path, 'test', 'rocket_launch')
    with pytest.raises(ValueError, match='Incomplete'):
        checkpoint_read(path)


def test_legacy_inspection_does_not_enable_ownership_but_idle_explicit_upgrade_is_empty(tmp_path):
    legacy = CampaignMemory('test', 'rocket_launch', last_tick=1)
    path = tmp_path / 'checkpoint.json'; path.write_bytes(canonical(asdict(legacy)))
    before = path.read_bytes()
    assert not hasattr(load_checkpoint(path, 'test', 'rocket_launch'), 'output_commitments')
    assert 'output_commitments' not in checkpoint_read(path)[0]
    value = buffered_loop_type(HierarchicalLoop).memory_type.load(path, 'test', 'rocket_launch')
    assert value.output_commitments == {} and value.history[-1]['kind'] == 'output_ownership_enabled'
    assert path.read_bytes() == before


def test_active_legacy_upgrade_fails_before_backend_initialization(tmp_path):
    backend = BufferBackend(); backend.mode = 'lost_ack'
    loop = loop_for(backend, tmp_path); loop.step()
    path = tmp_path / 'state.json'
    data = json.loads(path.read_bytes())
    data.pop('output_buffers_schema'); data.pop('output_commitments')
    path.write_bytes(canonical(data)); before = path.read_bytes()
    backend.enable_factory = lambda: pytest.fail('backend initialized before legacy gate')
    with pytest.raises(ValueError, match='idle, reconciled'):
        resume(backend, path)
    assert path.read_bytes() == before
    assert load_checkpoint(path, backend.state.session_id, 'rocket_launch').pending == data['pending']


@pytest.mark.parametrize('mutation', ['paid_bool', 'unit_bool', 'shared_receipt', 'source_alias', 'missing_chest'])
def test_strict_loader_rejects_corrupt_or_aliased_paid_fields(mutation):
    _, _, row = setup(True)
    retained = owned(row)
    part = retained[row['source']]['parts']['chest']
    if mutation == 'paid_bool': part['paid'] = True
    elif mutation == 'unit_bool': part['unit_number'] = True
    elif mutation == 'shared_receipt': retained[row['source']]['parts']['inserter']['receipt'] = part['receipt']
    elif mutation == 'source_alias': part['unit_number'] = row['source_unit']
    else: retained[row['source']]['parts'].pop('chest')
    with pytest.raises(ValueError):
        validate_commitments(retained)


def successor_fixture(tmp_path):
    from test_successors import Backend, KIND, GROWTH, flowing_state
    from jev_factorio.successor_controller import _empty_receipts
    state, native = flowing_state()
    backend = Backend(state)
    loop = KIND(backend, policy='deterministic', target='rocket_launch',
                factory_scheduling='ready-work', checkpoint=str(tmp_path / 'successor.json'))
    memory = loop.memory_type(state.session_id, 'rocket_launch', last_tick=state.tick)
    memory.successor_projects[GROWTH] = dict(anchor=native['anchor'], predecessor_unit=500,
        source_unit=17, started_tick=0, deadline_tick=216000, status='active')
    receipt = _empty_receipts()
    for label, family in (('output', 'output_buffers'), ('input', 'input_routes')):
        row = state.factory[family]['sources'][GROWTH]
        receipt[label] = deepcopy(row['parts']); receipt[label + '_layout'] = row['layout']
    memory.successor_receipts[GROWTH] = receipt
    row = state.factory['input_routes']['sources'][GROWTH]
    memory.input_commitments[GROWTH] = {key: deepcopy(row[key]) for key in ('source_unit', 'layout', 'parts')}
    loop.memory = memory
    return loop, backend, GROWTH


def test_successor_receipts_remain_single_durable_owner_and_new_map_stays_ordinary(tmp_path):
    loop, backend, source = successor_fixture(tmp_path)
    before = deepcopy(loop.memory.successor_receipts)
    loop._observe()
    assert not loop._buffer_fault and not loop._successor_fault
    assert loop.memory.output_commitments == {} and loop.memory.successor_receipts == before
    assert expected_commitments(loop.memory)[source]['parts'] == before[source]['output']
    restored = load_checkpoint(tmp_path / 'successor.json', backend.state.session_id, 'rocket_launch')
    assert restored.output_commitments == {} and restored.successor_receipts == before


@pytest.mark.parametrize('mutation', ['changed_receipt', 'changed_unit', 'aliased_predecessor'])
def test_rejected_successor_output_never_advances_outer_owner(tmp_path, mutation):
    loop, backend, source = successor_fixture(tmp_path)
    before = deepcopy(loop.memory.successor_receipts)
    part = backend.state.factory['output_buffers']['sources'][source]['parts']['chest']
    if mutation == 'changed_receipt': part['receipt'] = 'untracked'
    elif mutation == 'changed_unit': part['unit_number'] = 999
    else: part['unit_number'] = 500
    loop._observe()
    assert loop.memory.status == 'uncertain' and loop.memory.output_commitments == {}
    assert loop.memory.successor_receipts == before and not backend.calls
    assert load_checkpoint(tmp_path / 'successor.json', backend.state.session_id,
                           'rocket_launch').successor_receipts == before


@pytest.mark.parametrize('mutation', ['duplicate_owner', 'shared_receipt', 'shared_unit'])
def test_ordinary_and_successor_durable_owners_cannot_alias(tmp_path, mutation):
    loop, _, source = successor_fixture(tmp_path)
    known = expected_commitments(loop.memory)[source]
    if mutation == 'duplicate_owner':
        loop.memory.output_commitments[source] = deepcopy(known)
    else:
        part = deepcopy(known['parts']['chest'])
        part.update(role='ordinary-chest', receipt='ordinary-paid', unit_number=700)
        if mutation == 'shared_receipt': part['receipt'] = known['parts']['chest']['receipt']
        else: part['unit_number'] = known['parts']['chest']['unit_number']
        loop.memory.output_commitments['recipe:copper-plate'] = {
            'source_unit': 701, 'layout': 'output:701', 'parts': {'chest': part}}
    with pytest.raises(ValueError):
        loop.memory_type.from_bytes(canonical(asdict(loop.memory)), loop.memory.session_id, loop.memory.target)


@pytest.mark.parametrize('failure', ['raise', 'replace'])
def test_real_buffer_observer_stays_inside_outer_solid_initial_resume_transaction(tmp_path, failure):
    from jev_factorio.solid_controller import solid_loop_type
    from test_solid_route_integration import Backend, FoundationScenario, controller
    class ObservedBuffer(buffered_loop_type(FoundationScenario)):
        after_buffer = None
        @property
        def _buffer_evidence(self):
            return getattr(self, '_evidence', {})
        @_buffer_evidence.setter
        def _buffer_evidence(self, value):
            self._evidence = value
            if value and self.after_buffer:
                self.after_buffer(self)
    kind = solid_loop_type(ObservedBuffer)
    backend = Backend(); backend.output_buffers_supported = True
    buffer_state, _, row = setup(True)
    backend.state.factory['entities'].update(deepcopy(buffer_state.factory['entities']))
    backend.state.factory['output_buffers'] = deepcopy(buffer_state.factory['output_buffers'])
    backend.state.factory['output_buffers'].update(session_id=backend.state.session_id, tick=backend.state.tick)
    origin = controller(backend, tmp_path, kind=kind)
    origin.memory.active_goal = 'rocket_launch'
    origin.memory.output_commitments = owned(row)
    origin._observe()
    path = backend.checkpoint; before = path.read_bytes()
    other = json.loads(before); other['failures']['other-writer'] = 3
    other_bytes = canonical(other)
    loop = controller(backend, tmp_path, kind=kind, resume=True)
    def rejected(self):
        self.memory.failures['tentative-buffer'] = 1
        if failure == 'replace': path.write_bytes(other_bytes)
        self._save()
        raise RuntimeError('fixture post-buffer failure')
    loop.after_buffer = rejected
    with pytest.raises((ValueError, RuntimeError)):
        loop._observe()
    assert path.read_bytes() == (other_bytes if failure == 'replace' else before)
    assert loop.memory is None and loop._persistence_failed and not backend.calls


def test_v2_preflight_accepts_retained_ordinary_output_and_rejects_missing_owner(tmp_path):
    from jev_factorio.dev_preflight_v2 import inspect_native
    from test_transport_ownership_preflight_v2 import checkpoint, prepared_runtime, projected, wire_native
    native = projected(prepared_runtime())
    source = 'recipe:iron-plate'
    native['entities'][source] = native['entities'].pop('alpha')
    unit = native['entities'][source]['unit_number']
    chest = deepcopy(native['entities'][source])
    chest.update(name='wooden-chest', unit_number=90001, recipe='')
    native['entities']['output-chest:' + str(unit)] = chest
    row = {'source_unit': unit, 'layout': 'output:fixture', 'parts': {'chest': {
        'role': 'output-chest:' + str(unit), 'unit_number': 90001, 'receipt': 'paid:output', 'paid': 1}}}
    native['output_buffers'] = {'present': True, 'protocol': 1, 'commitments': {source: row}}
    retained = checkpoint(native)
    retained.update(output_buffers_schema=1, output_commitments={source: deepcopy(row)})
    path = tmp_path / 'checkpoint.json'; path.write_bytes(canonical(retained))
    assert checkpoint_read(path)[0] == retained
    assert inspect_native(wire_native(native), retained, retained['session_id']) == []
    retained['output_commitments'] = {}
    assert inspect_native(native, retained, retained['session_id']) == ['ordinary_output_ownership_not_retained']


def test_complete_capture_rejects_output_owner_regression():
    from jev_factorio.complete_capture import checked_checkpoint_progress
    _, _, row = setup(True)
    initial = dict(last_tick=1, failures={}, coal_commitments={}, output_buffers_schema=1,
                   output_commitments=owned(row))
    final = deepcopy(initial)
    final['output_commitments'] = {}
    with pytest.raises(ValueError, match='paid output-buffer ownership regressed'):
        checked_checkpoint_progress(initial, final)
