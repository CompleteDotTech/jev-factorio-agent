"""Interrupted transfers and asynchronous goal completion preserve causal ownership."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.skills import Plan, Step
from jev_factorio.planning import solid_funding
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop
from solid_routes_fixtures import row


@pytest.mark.parametrize('quantity', [0, 1])
@pytest.mark.parametrize('kit', [False, True])
@pytest.mark.parametrize('failures', [0, 1])
def test_partial_or_zero_transfer_reconciles_owned_plan(tmp_path, quantity, kit, failures):
    loop, backend = kit_loop(tmp_path)
    backend.state.world_kind = 'fle'  # Synthetic receipt fixture exercises the native-only guard.
    backend.state.player_position = (0, 0)
    backend.state.inventory['copper-plate'] = 0
    backend.state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
        output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    if not kit:
        assert loop.step()['verified']
        plan = Plan('ordinary-partial', 'rocket_launch', 'Ordinary extraction', (
            Step('factory_extract', 'transfer', parameters={'role': 'copper-source', 'item': 'copper-plate',
                'quantity': 10, 'receipt': f'{backend.state.tick}:factory_extract:copper-source:copper-plate'}),))
        loop._compile_candidates = lambda snapshot: ([plan], '')
        key = plan.id
    else: key = solid_funding.project_key(row(backend.state)) + ':kit'
    loop.memory.failures[key] = failures
    original = backend.execute
    def partial(action, parameters):
        assert action == 'factory_extract'
        result = original(action, parameters)
        difference = parameters['quantity'] - quantity
        backend.state.inventory[parameters['item']] -= difference
        backend.state.factory['entities'][parameters['role']]['output'][parameters['item']] += difference
        receipt = backend.state.factory['receipts'][parameters['receipt']]
        receipt.update(quantity=quantity, tick=backend.state.tick)
        raise RuntimeError('Synthetic partial native transfer response')
    backend.execute = partial
    loop._observe(); initial = asdict(loop.memory)
    records = [loop.step()]
    assert records[0]['pending']['dispatch'] == 'ambiguous'
    records.append(loop.step()); final = asdict(loop.memory)
    expected = 'partial_transfer_reconciled' if quantity else 'zero_effect_transfer_reconciled'
    assert records[-1]['attempt_outcomes'][-1]['outcome'] == expected
    assert final['active_plan'] is None and final['failures'][key] == failures + 1
    assert not funding_history_issues(initial, records, final)
    records[-1]['history'] = [event for event in records[-1]['history'] if event['kind'] != expected]
    final['history'] = deepcopy(records[-1]['history'])
    assert funding_history_issues(initial, records, final)


@pytest.mark.parametrize('kind', ['ordinary_before', 'ordinary_after', 'kit_before'])
def test_observed_native_goal_completion_clears_retained_plan(tmp_path, kind):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    if kind == 'kit_before':
        from jev_factorio.operational_safety import StoragePressure
        def reject(): raise StoragePressure()
        loop._trace.admission_check = reject
        loop.step(); loop._trace.admission_check = None
    else:
        step = Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
                    costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1})
        loop.memory.active_plan = Plan('ordinary-retained', 'rocket_launch', 'Retained work', (step, step)).to_dict()
        loop.memory.step_index = 0
    initial = asdict(loop.memory)
    def victory():
        backend.state.victory = True
        backend.state.victory_source = 'native:base-game-rocket-launch'
    if kind == 'ordinary_after':
        # The fixture craft backend requires a funding lock; preserve a real one.
        loop.memory.active_plan = None
        loop.step()
        loop.memory.active_plan = Plan('ordinary-retained', 'rocket_launch', 'Retained work', (step, step)).to_dict()
        initial = asdict(loop.memory)
        backend.kit_after = victory
    else: victory()
    record = loop.step(); final = asdict(loop.memory)
    assert final['status'] == 'completed' and final['active_plan'] is None
    assert not funding_history_issues(initial, [record], final)
    record['history'] = [event for event in record['history'] if event['kind'] != 'goal_completed']
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('change', ['reference', 'sequence', 'missing'])
def test_rejected_transfer_uses_the_retained_sealed_recovery_reference(tmp_path, change):
    from jev_factorio.controller import HierarchicalLoop
    from test_transfer_recovery import _recovery_case, _NoActBackend
    memory, _, current, _, _ = _recovery_case(tmp_path, initial_failures=1)
    backend = _NoActBackend(current)
    loop = HierarchicalLoop(backend, policy='deterministic', target='iron_smelting',
        checkpoint=str(tmp_path / 'recovery.json'), tick_seconds=0)
    loop.memory = memory
    initial = asdict(memory)
    record = loop.step(); final = asdict(memory)
    assert record['attempt_outcomes'][-1]['outcome'] == 'rejected_transfer_reconciled' and not backend.act_calls
    # Replay the actual core recovery trace under an empty funding contract.
    # The core has already verified the sealed fixture log; the reader binds its
    # retained reference and never opens a path supplied by the capture.
    for checkpoint in (initial, final): checkpoint.update(solid_science_policy=True, solid_funding=None)
    record.update(solid_funding_schema=1, solid_funding=None)
    assert not funding_history_issues(initial, [record], final)
    if change == 'reference': initial['transfer_recovery']['events_sha256'] = 'f' * 64
    elif change == 'sequence': initial['transfer_recovery']['sequences'][0] += 1
    else: initial['transfer_recovery'] = None
    assert funding_history_issues(initial, [record], final)


def test_goal_activation_before_selection_and_victory_after_dispatch(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop.step()
    step = Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
                costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1})
    loop.memory.active_plan = Plan('old-goal-plan', 'stockpile_fuel', 'Old work', (step,)).to_dict()
    loop.memory.active_goal = 'stockpile_fuel'
    loop.memory.completed_goals = {}
    backend.state.drill_output_connected = True
    backend.state.drill_status = 'working'
    backend.state.placed_entities = ['burner-mining-drill:fixture']
    backend.state.iron_ore_collected = 5
    next_plan = Plan('new-goal-plan', 'rocket_launch', 'New work', (step, step))
    loop._compile_candidates = lambda snapshot: ([next_plan], '')
    def victory():
        backend.state.victory = True
        backend.state.victory_source = 'native:base-game-rocket-launch'
    backend.kit_after = victory
    initial = asdict(loop.memory); record = loop.step(); final = asdict(loop.memory)
    assert final['status'] == 'completed' and final['active_plan'] is None
    assert any(event['kind'] == 'goal_activated' for event in record['history'])
    assert not funding_history_issues(initial, [record], final)
