"""Queue/funding/audit composition; fixtures are not native flow evidence."""
from copy import deepcopy
import json
import math

import pytest

from jev_factorio import solid_routes as contract
from jev_factorio.planning import solid_investment as policy
from test_solid_investment import scenario, service_history, offers, make_loop
from test_solid_kit_acquisition import missing_kit, kit_loop
from solid_routes_fixtures import row, SOURCE, TARGET, ROUTE


@pytest.mark.parametrize('gears,copper,expected', [
    (60, 120, 60), (30, 120, 90), (90, 30, 30), (1, 120, 119),
    (119, 120, 1), (60, 60, 60), (120, 30, 0), (60, 0, 60),
    (0, 120, 120), (120, 120, 0),
])
def test_queued_products_and_remaining_ingredients_receive_only_one_credit(gears, copper, expected):
    state, catalog = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input'] = {
        'iron-gear-wheel': gears, 'copper-plate': copper}
    demand, _ = policy.requirements(state, catalog)
    value = policy.offer_value(row(state), state, catalog, demand, history)
    assert value.get('demand_units', 0) == expected, value


@pytest.mark.parametrize('carried,reserved,expected', [(0, 0, 60), (20, 0, 40), (60, 0, 0), (60, 20, 20)])
def test_queue_carried_and_reserved_inputs_conserve_units(carried, reserved, expected):
    state, catalog = scenario(); history = service_history(state)
    state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    state.inventory['iron-gear-wheel'] = carried
    demand, _ = policy.requirements(state, catalog, reserved={'iron-gear-wheel': reserved})
    value = policy.offer_value(row(state), state, catalog, demand, history)
    assert value.get('demand_units', 0) == expected, value


@pytest.mark.parametrize('ready,crafting,expected', [(0, True, 59), (20, False, 40), (20, True, 39)])
def test_inflight_batch_and_ready_output_do_not_consume_queue_inputs_twice(ready, crafting, expected):
    state, catalog = scenario(); history = service_history(state)
    machine = state.factory['entities'][TARGET]
    machine['input']['iron-gear-wheel'] = 60
    machine['output']['automation-science-pack'] = ready
    machine['crafting'] = crafting
    demand, _ = policy.requirements(state, catalog)
    value = policy.offer_value(row(state), state, catalog, demand, history)
    assert value.get('demand_units', 0) == expected, value


@pytest.mark.parametrize('kind', ['inserter', 'belt', 'both'])
def test_missing_kit_is_funded_when_queue_covers_only_part_of_science(kind):
    state, catalog = scenario(); history = service_history(state); missing_kit(state, kind)
    state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    before = deepcopy(state)
    plans, diagnostics = offers(state, catalog, history)
    assert plans, diagnostics
    marker = plans[0].materials[policy.MARKER]
    assert marker['stage'] == 'kit' and marker['demand_units'] == 60
    assert marker['queued_input_units'] == 60
    assert marker['uncommitted_input_units'] == 0
    assert state == before and not row(state)['parts']


def test_queued_science_does_not_block_paid_kit_then_full_corridor(tmp_path):
    loop, backend = kit_loop(tmp_path)
    target = backend.state.factory['entities'][TARGET]
    target['input']['iron-gear-wheel'] = 60
    initial_inputs = deepcopy(target['input'])
    for _ in range(20):
        result = loop.step()
        assert result['verified'], result
        if row(backend.state)['state'] == 'ready':
            break
    assert row(backend.state)['state'] == 'ready'
    assert len(row(backend.state)['parts']) == 4
    first_build = next(i for i, (action, _) in enumerate(backend.calls) if action == contract.COMMAND)
    assert first_build > 0 and len(backend.calls[first_build:]) == 4
    assert target['input'] == initial_inputs
    assert min(backend.state.inventory.values()) >= 0
    assert not contract.flow_complete(ROUTE, row(backend.state)['layout'], backend.state)
    loop._observe()
    assert loop.memory.solid_funding is None and loop.memory.pending is None
    assert loop.memory.failures == {}


def test_queue_coverage_change_revokes_fresh_kit_dispatch(tmp_path):
    loop, backend = kit_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    def change():
        if backend.observations == 2:
            backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 120
    backend.before_observe = change
    result = loop.step()
    assert not result['verified']
    assert backend.calls == [] and loop.memory.pending is None
    assert loop.memory.solid_funding is None


def test_prepared_funded_action_reconciles_once_after_queue_coverage_changes(tmp_path):
    loop, backend = kit_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    captured = []
    original = backend.execute
    def retain(action, parameters):
        captured.append(backend.checkpoint.read_bytes())
        return original(action, parameters)
    backend.execute = retain
    assert loop.step()['verified']
    prepared = loop.memory_type.from_bytes(captured[0], backend.state.session_id, 'rocket_launch')
    assert prepared.pending['dispatch'] == 'prepared'
    initial_action = deepcopy(prepared.attempt)
    calls = len(backend.calls)
    backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 120
    loop.memory = prepared
    result = loop.step()
    assert result['verified'] and len(backend.calls) == calls
    assert loop.memory.pending is None
    assert any(outcome['id'] == initial_action['id'] and outcome['outcome'] == 'verified'
               for outcome in loop.memory.attempt_outcomes)


@pytest.mark.parametrize('kind', ['duplicate', 'fluid', 'probabilistic', 'multiple_products'])
def test_unsupported_destination_forecast_cannot_authorize_kit(kind):
    state, catalog = scenario(); history = service_history(state); missing_kit(state)
    state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 30
    recipe = catalog.recipes['automation-science-pack']
    if kind == 'duplicate': recipe['ingredients'].append(deepcopy(recipe['ingredients'][0]))
    elif kind == 'fluid': recipe['ingredients'][0]['type'] = 'fluid'
    elif kind == 'probabilistic': recipe['products'][0]['probability'] = .5
    else: recipe['products'].append({'name': 'byproduct', 'type': 'item', 'amount': 1})
    assert offers(state, catalog, history)[0] == []


def test_duplicate_recipe_is_rejected_by_shared_forecast_not_only_route_policy():
    from jev_factorio.planning.demand import SupplyLedger
    state, catalog = scenario()
    recipe = catalog.recipes['automation-science-pack']
    recipe['ingredients'].append(deepcopy(recipe['ingredients'][0]))
    with pytest.raises(ValueError, match='Ambiguous queued recipe ingredients'):
        SupplyLedger.capture(state, catalog)


def test_192_quantity_yield_stock_cases_match_independent_remaining_bill():
    state, catalog = scenario(); history = service_history(state)
    recipe = catalog.recipes['automation-science-pack']
    gear = next(i for i in recipe['ingredients'] if i['name'] == 'iron-gear-wheel')
    cases = 0
    for amount in (1, 2):
        gear['amount'] = amount
        for output in (1, 2):
            recipe['products'][0]['amount'] = output
            for gears in (0, 1, 30, 60, 90, 120):
                for copper in (0, 30, 60, 120):
                    for carried in (0, 20):
                        state.inventory['iron-gear-wheel'] = carried
                        state.factory['entities'][TARGET]['input'] = {
                            'iron-gear-wheel': gears, 'copper-plate': copper}
                        queued = min(gears // amount, copper)
                        batches = math.ceil(max(0, 120 - queued * output) / output)
                        expected = max(0, batches * amount - carried - (gears - queued * amount))
                        demand, _ = policy.requirements(state, catalog)
                        value = policy.offer_value(row(state), state, catalog, demand, history)
                        assert value.get('demand_units', 0) == expected, (
                            amount, output, gears, copper, carried, value)
                        cases += 1
    assert cases == 192


@pytest.mark.parametrize('event,paid', [('action_prepared', 0), ('action_returned', 1)])
@pytest.mark.parametrize('exception_type', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interrupted_funded_action_keeps_pending_and_stops_all_later_work(tmp_path, event, paid, exception_type):
    from jev_factorio.causal_trace import CausalTrace
    from jev_factorio.research_log import ResearchLogError
    from test_causal_trace_interruption import OnceInterruptedSink
    loop, backend = kit_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    error = exception_type('private fixture interruption')
    sink = OnceInterruptedSink(event, error, after_append=True)
    loop._trace = CausalTrace(sink, 'queued-kit-fixture')
    with pytest.raises(exception_type) as caught:
        loop.step()
    assert caught.value is error and len(backend.calls) == paid
    saved = backend.checkpoint.read_bytes()
    checkpoint = json.loads(saved)
    assert checkpoint['pending']['dispatch'] == 'prepared'
    assert checkpoint['solid_funding']['actions'] == 1
    before = (deepcopy(backend.calls), backend.observations, deepcopy(loop.memory.failures))
    with pytest.raises(ResearchLogError):
        loop.step()
    assert (backend.calls, backend.observations, loop.memory.failures) == before
    assert backend.checkpoint.read_bytes() == saved


@pytest.mark.parametrize('stage', ['action_returned', 'trace_capture', 'trace_emit'])
@pytest.mark.parametrize('error_type', [RuntimeError, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_queued_funding_counter_failure_keeps_exact_prepared_bytes_and_budget(
        tmp_path, monkeypatch, stage, error_type):
    import jev_factorio.performance as performance
    from jev_factorio.causal_trace import CausalTrace
    from jev_factorio.performance import PerformanceCounters
    from jev_factorio.research_log import ResearchLogError
    from test_causal_metrics_interruption import RecordingSink
    loop, backend = kit_loop(tmp_path)
    backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 60
    original_error = error_type('private fixture counter error')
    raised, prepared = [], []
    original_execute = backend.execute
    def save_before_effect(action, parameters):
        prepared.append(backend.checkpoint.read_bytes())
        return original_execute(action, parameters)
    backend.execute = save_before_effect
    class Counters(PerformanceCounters):
        def call(self, name, duration_ns, failed=False, *, cpu_ns=None):
            super().call(name, duration_ns, failed, cpu_ns=cpu_ns)
            if name == stage and len(backend.calls) == 1 and not raised:
                raised.append(True)
                raise original_error
    monkeypatch.setattr(performance, 'PerformanceCounters', Counters)
    loop._trace = CausalTrace(RecordingSink(), 'queued-funded-fixture')
    with pytest.raises(BaseException) as caught:
        loop.step()
    assert raised == [True] and len(backend.calls) == 1
    if error_type is RuntimeError:
        assert isinstance(caught.value, ResearchLogError)
        assert 'private' not in str(caught.value)
    else:
        assert caught.value is original_error
    assert loop._trace._failed is True
    assert backend.checkpoint.read_bytes() == prepared[0]
    saved = json.loads(prepared[0])
    assert saved['pending']['dispatch'] == 'prepared'
    assert saved['solid_funding']['actions'] == 1
    before = (deepcopy(backend.calls), backend.observations, deepcopy(loop.memory.failures))
    # Disabling a failed diagnostic is not authority to reopen the action path.
    loop._trace.metrics = None
    with pytest.raises(ResearchLogError):
        loop.step()
    assert (backend.calls, backend.observations, loop.memory.failures) == before
    assert backend.checkpoint.read_bytes() == prepared[0]
