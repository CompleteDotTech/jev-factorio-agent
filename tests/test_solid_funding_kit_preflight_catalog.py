"""Local kit rejection retains ownership; catalog diagnostics stay reloadable."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from jev_factorio.operational_safety import StoragePressure, MaintenanceAdmissionClosed
from jev_factorio.planning import solid_funding
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('error', [StoragePressure, MaintenanceAdmissionClosed])
@pytest.mark.parametrize('retained', [False, True])
def test_kit_preflight_outcome_retains_exact_funding_and_active_plan(tmp_path, error, retained):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    def reject(): raise error()
    loop._trace.admission_check = reject
    if retained:
        loop.step()
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['action'] == 'observe' and not record['verified'] and not backend.calls
    assert final['active_plan'] and final['solid_funding']['actions'] == 1 and not final['failures']
    assert final['pending'] is None and final['attempt'] is None
    loop.memory_type.from_bytes(json.dumps(final).encode(), loop.memory.session_id, loop.target)
    assert not funding_history_issues(initial, [record], final)
    record['history'] = [event for event in record['history'] if not event['kind'].endswith('preflight_rejected')]
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('suffix', ['', 'x', 'a' * 63, 'a' * 65, 'G' * 64, 'A' * 64, '0' * 63 + ':'])
def test_checkpoint_rejects_malformed_catalog_project_identity(tmp_path, suffix):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    saved = asdict(loop.memory)
    declaration = deepcopy(next(iter(saved['solid_funding_catalogs'].values())))
    saved['solid_funding_catalogs'] = {'solid-project:' + suffix: declaration}
    with pytest.raises(ValueError, match='catalog declaration'):
        loop.memory_type.from_bytes(json.dumps(saved).encode(), loop.memory.session_id, loop.target)


def test_full_valid_catalog_cache_does_not_save_an_unreloadable_fifth_entry(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    declaration = deepcopy(next(iter(loop.memory.solid_funding_catalogs.values())))
    retained = {'solid-project:' + str(index) * 64: deepcopy(declaration) for index in range(4)}
    loop.memory.solid_funding_catalogs = deepcopy(retained)
    solid_funding.validate_catalog_declarations(retained, loop.memory.last_tick)
    loop._observe()
    assert loop.memory.solid_funding_catalogs == retained
    restored = loop.memory_type.from_bytes(loop.checkpoint.read_bytes(), loop.memory.session_id, loop.target)
    assert restored.solid_funding_catalogs == retained


def test_hybrid_request_rejection_falls_back_without_claiming_a_model_call(tmp_path):
    from jev_factorio.skills import Plan, Step
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    class NoCall:
        model = 'fixture-no-call'
        is_mock = False
        def evaluate(self, *args): raise AssertionError('Oversized request reached the provider')
    loop.jev = NoCall()
    loop.policy = 'hybrid'
    loop.max_request_bytes = 1
    plans = [Plan('ordinary-' + name, 'rocket_launch', 'Ordinary work', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + batch,
             costs={'iron-plate': 2 * batch}, parameters={'recipe': 'iron-gear-wheel', 'batches': batch}),))
        for name, batch in [('one', 1), ('two', 2)]]
    loop._compile_candidates = lambda snapshot: (plans, '')
    initial = asdict(loop.memory); record = loop.step(); final = asdict(loop.memory)
    assert record['verified'] and record['decision']['source'] == 'deterministic-fallback'
    assert record['decision']['diagnostics']['outcome'] == 'request_rejected'
    assert record['decision']['model_called'] is record['model_call'] is False
    assert not funding_history_issues(initial, [record], final)
