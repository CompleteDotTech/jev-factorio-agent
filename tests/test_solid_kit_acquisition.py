"""Bounded observed-stock funding, real composed dispatch, and durability fixtures."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from jev_factorio import solid_routes as contract
from jev_factorio.planning import solid_investment as policy
from jev_factorio.skills import Plan, Step
from test_solid_investment import scenario, service_history, make_loop, offers
from solid_routes_fixtures import SOURCE, TARGET, ROUTE, row


def missing_kit(state, kind='both'):
    state.inventory.update({'iron-plate': 200, 'copper-plate': 100})
    if kind in ('inserter', 'both'): state.inventory['inserter'] = 0
    if kind in ('belt', 'both'): state.inventory['transport-belt'] = 0


@pytest.mark.parametrize('kind', ['inserter', 'belt', 'both'])
def test_profitable_missing_kit_has_real_paid_acquisition(kind):
    state, data = scenario(); history = service_history(state); missing_kit(state, kind)
    plans, evidence = offers(state, data, history)
    assert plans, evidence
    step = plans[0].steps[0]
    assert step.action in {'factory_extract', 'factory_craft'}
    assert step.allowed(state)
    assert plans[0].materials[policy.MARKER]['stage'] == 'kit'
    assert plans[0].id.endswith(':kit')
    assert row(state)['parts'] == {} and not contract.flow_complete(ROUTE, row(state)['layout'], state)


def kit_loop(tmp_path, kind='both'):
    loop, backend = make_loop(tmp_path)
    missing_kit(backend.state, kind)
    backend.kit_after = lambda: None
    backend.lose_kit_ack = False
    original = backend.execute
    def execute(action, p):
        if action == contract.COMMAND:
            return original(action, p)
        assert action in {'factory_extract', 'factory_craft'}
        saved = json.loads(backend.checkpoint.read_text())
        assert saved['pending']['dispatch'] == 'prepared'
        assert saved['solid_funding']['actions'] >= 1
        assert saved['active_plan']['steps'][0]['parameters'] == p
        backend.calls.append((action, deepcopy(p)))
        if action == 'factory_craft':
            recipe = loop.catalog.recipes[p['recipe']]
            for i in recipe['ingredients']:
                cost = i['amount'] * p['batches']
                assert backend.state.inventory.get(i['name'], 0) >= cost
                backend.state.inventory[i['name']] -= cost
            for out in recipe['products']:
                backend.state.inventory[out['name']] = (backend.state.inventory.get(out['name'], 0)
                                                         + out['amount'] * p['batches'])
        else:
            entity = backend.state.factory['entities'][p['role']]
            assert entity['output'][p['item']] >= p['quantity']
            entity['output'][p['item']] -= p['quantity']
            backend.state.inventory[p['item']] = backend.state.inventory.get(p['item'], 0) + p['quantity']
            backend.state.factory['receipts'][p['receipt']] = {
                'role': p['role'], 'item': p['item'], 'quantity': p['quantity'],
                'extracting': True, 'unit_number': entity['unit_number']}
        backend.state.tick += 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
        backend.kit_after()
        if backend.lose_kit_ack:
            raise TimeoutError('Deliberately lost fixture acknowledgement')
        return 'fixture paid kit prerequisite'
    backend.execute = execute
    return loop, backend


def test_actual_controller_acquires_then_pays_entire_corridor(tmp_path):
    loop, backend = kit_loop(tmp_path)
    for _ in range(20):
        result = loop.step()
        assert result['verified'], result
        assert min(backend.state.inventory.values()) >= 0
        if row(backend.state)['state'] == 'ready':
            break
    assert row(backend.state)['state'] == 'ready'
    assert len(row(backend.state)['parts']) == 4
    assert any(a == 'factory_craft' for a, _ in backend.calls)
    first_build = next(i for i, (a, _) in enumerate(backend.calls) if a == contract.COMMAND)
    assert first_build > 0
    assert len(backend.calls[first_build:]) == 4
    assert not contract.flow_complete(ROUTE, row(backend.state)['layout'], backend.state)
    loop._observe()
    assert loop.memory.solid_funding is None and ROUTE in loop.memory.solid_commitments
    assert loop.memory.failures == {}


@pytest.mark.parametrize('change', ['kit', 'raw', 'recipe', 'catalog', 'research', 'power', 'stock', 'receipt', 'id', 'cost'])
def test_fresh_funding_permission_rejects_changed_or_forged_action(change):
    state, data = scenario(); history = service_history(state); missing_kit(state)
    state.player_position = {'x': 0, 'y': 0}
    if change == 'receipt':
        state.inventory['copper-plate'] = 0
        state.factory['entities']['copper-source'] = dict(unit_number=7890, name='wooden-chest',
            output={'copper-plate': 100}, position={'x': 1, 'y': 1})
    plan = offers(state, data, history)[0][0]
    assert policy.fresh_permission(plan, plan.steps[0], state, data, outcomes=history)
    if change == 'kit': state.inventory.update(inserter=2, **{'transport-belt': 2})
    elif change == 'raw': state.inventory['iron-plate'] = 0; state.inventory['copper-plate'] = 0
    elif change == 'recipe': data.recipes['inserter']['enabled'] = False
    elif change == 'catalog': data.recipes['inserter']['ingredients'][0]['amount'] += 1
    elif change == 'research': state.factory['research_progress'] = 1
    elif change == 'power': state.factory['entities'][TARGET]['energy'] = 0
    elif change == 'stock': state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 120
    elif change == 'receipt': plan.steps[0].parameters['receipt'] += 'tampered'
    elif change == 'id': plan = replace(plan, id=plan.id + ':retry')
    else: plan = replace(plan, steps=(replace(plan.steps[0], costs={'coal': 1}),))
    assert not policy.fresh_permission(plan, plan.steps[0], state, data, outcomes=history)


@pytest.mark.parametrize('change', ['reserved', 'future_output', 'smelting', 'locked_recipe', 'cycle', 'missing_actor_position'])
def test_missing_or_unsupported_stock_never_grants_acquisition(change):
    state, data = scenario(); history = service_history(state)
    state.inventory['inserter'] = 0
    options = {}
    if change == 'reserved':
        state.inventory.update({'iron-plate': 20, 'copper-plate': 20})
        options['reserved'] = {'iron-plate': 20}
    elif change == 'future_output':
        state.factory['entities'][SOURCE]['input']['iron-plate'] = 200
    elif change == 'smelting': state.inventory.update({'iron-ore': 200, 'copper-ore': 200})
    elif change == 'locked_recipe':
        state.inventory.update({'iron-plate': 20, 'copper-plate': 20})
        data.recipes['inserter']['enabled'] = False
    elif change == 'cycle':
        state.inventory.update({'iron-plate': 20, 'copper-plate': 20})
        data.recipes['inserter']['ingredients'][0]['name'] = 'inserter'
    else:
        state.factory['entities']['plate-source'] = dict(unit_number=7800, name='wooden-chest',
            output={'iron-plate': 200, 'copper-plate': 200}, position={'x': 1, 'y': 1})
        state.player_position = None
    assert not offers(state, data, history, **options)[0]


def test_owned_output_collection_is_funded_and_aliases_are_rejected():
    state, data = scenario(); history = service_history(state)
    state.inventory.update(inserter=0, **{'transport-belt': 0})
    state.player_position = {'x': 0, 'y': 0}
    state.factory['entities']['plate-source'] = dict(unit_number=7800, name='wooden-chest',
        output={'iron-plate': 200, 'copper-plate': 200}, position={'x': 1, 'y': 1})
    plans, evidence = offers(state, data, history)
    assert plans, evidence
    assert plans[0].steps[0].action == 'factory_extract'
    assert plans[0].materials[policy.MARKER]['source_draw_for_kit'] > 0
    state.factory['entities']['alias'] = deepcopy(state.factory['entities']['plate-source'])
    assert not offers(state, data, history)[0]


def test_same_kit_identity_survives_changed_quantities_and_ticks():
    state, data = scenario(); history = service_history(state); missing_kit(state)
    first = offers(state, data, history)[0][0]
    state.tick += 2; state.factory['solid_routes']['tick'] = state.tick
    state.inventory['inserter'] = 1
    second = offers(state, data, history)[0][0]
    assert first.id == second.id
    assert not offers(state, data, history, failures={first.id: 2})[0]


@pytest.mark.parametrize('change', ['deadline', 'catalog', 'demand'])
def test_selected_kit_revoked_without_native_mutation(change, tmp_path):
    loop, backend = kit_loop(tmp_path)
    def update():
        if backend.observations == 2:
            if change == 'deadline':
                backend.state.tick += 216001
                backend.state.factory['solid_routes']['tick'] = backend.state.tick
            elif change == 'catalog': loop.catalog.recipes['inserter']['ingredients'][0]['amount'] += 1
            else: backend.state.factory['research_progress'] = 1
    backend.before_observe = update
    result = loop.step()
    assert not result['verified'] and backend.calls == []
    assert loop.memory.solid_funding is None and loop.memory.pending is None
    assert any(v >= 2 for k,v in loop.memory.failures.items() if k.endswith(':kit'))


def test_candidate_inspection_has_no_checkpoint_commit(tmp_path):
    loop, backend = kit_loop(tmp_path)
    state = loop._observe()
    plans, _ = loop._compile_candidates(state)
    assert plans and loop.memory.solid_funding is None
    assert json.loads(backend.checkpoint.read_text())['solid_funding'] is None


def test_funding_survives_save_and_uses_original_deadline(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    state = deepcopy(loop.memory.solid_funding)
    restored = loop.memory_type.load(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert restored.solid_funding == state
    assert loop.step()['verified']
    assert loop.memory.solid_funding['deadline_tick'] == state['deadline_tick']
    assert loop.memory.solid_funding['actions'] == state['actions'] + 1


@pytest.mark.parametrize('field,value', [('actions', True), ('actions', 33), ('schema', 2),
    ('deadline_tick', 999999999), ('key', 'solid-project:' + '0'*64), ('layout', ''),
    ('source_unit', -1), ('catalog_sha256', 'z'*64)])
def test_funding_checkpoint_rejects_corruption(field, value, tmp_path):
    loop, backend = kit_loop(tmp_path); loop.step()
    saved = json.loads(backend.checkpoint.read_text()); saved['solid_funding'][field] = value
    with pytest.raises(ValueError):
        loop.memory_type.from_bytes(json.dumps(saved).encode(), backend.state.session_id, 'rocket_launch')


def test_inventory_headroom_and_urgent_boiler_revoke_optional_funding():
    state, data = scenario(); history = service_history(state); missing_kit(state)
    plan = offers(state, data, history)[0][0]
    state.factory['inventory_insertable'] = {plan.steps[0].item: 0}
    assert not offers(state, data, history)[0]
    state.factory.pop('inventory_insertable')
    state.factory['entities']['utility:boiler'] = dict(unit_number=7900, fuel={'coal': 1})
    assert not offers(state, data, history)[0]
    assert not policy.fresh_permission(plan, plan.steps[0], state, data, outcomes=history)


def test_action_budget_does_not_reset_after_material_changes(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    key = loop.memory.solid_funding['key'] + ':kit'
    loop.memory.solid_funding['actions'] = 32
    state = loop._observe()
    assert loop.memory.solid_funding is None and loop.memory.failures[key] == 2
    missing_kit(backend.state, 'inserter')
    later = loop._observe()
    plans, _ = loop._compile_candidates(later)
    assert not any(p.id == key for p in plans)


def test_thirty_second_acquisition_may_dispatch_but_not_thirty_third(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    loop.memory.solid_funding['actions'] = 31
    assert loop.step()['verified']
    assert loop.memory.solid_funding['actions'] == 32
    calls = len(backend.calls)
    loop.step()
    assert len(backend.calls) == calls
    assert loop.memory.solid_funding is None


def test_paid_prefix_keeps_normal_whole_kit_hold(tmp_path):
    loop, backend = kit_loop(tmp_path)
    for _ in range(20):
        loop.step()
        if row(backend.state)['parts']:
            break
    assert row(backend.state)['state'] == 'building'
    state = loop._observe()
    assert loop.memory.solid_funding is None
    use = Step('factory_craft', 'inventory', 'logistic-science-pack', 1,
               parameters={'recipe': 'inserter', 'batches': 1}, costs={'inserter': 1})
    assert not loop._step_allowed(use, state)


def test_same_actor_remains_single_pending_on_ambiguous_response(tmp_path):
    loop, backend = kit_loop(tmp_path)
    backend.lose_kit_ack = True
    first = loop.step()
    calls = len(backend.calls)
    assert calls == 1
    # The existing generic verifier may prove the observed completed inventory
    # immediately; it must never replay that paid craft as the same pending step.
    if loop.memory.pending:
        loop.step()
        assert len(backend.calls) == calls
    assert min(backend.state.inventory.values()) >= 0


def test_prepared_funding_is_durable_before_act_and_resume_does_not_duplicate(tmp_path):
    loop, backend = kit_loop(tmp_path)
    saved_prepared = []
    original = backend.execute
    def capture(action, p):
        saved_prepared.append(backend.checkpoint.read_bytes())
        return original(action, p)
    backend.execute = capture
    assert loop.step()['verified']
    prepared = loop.memory_type.from_bytes(saved_prepared[0], backend.state.session_id, 'rocket_launch')
    assert prepared.pending['dispatch'] == 'prepared' and prepared.solid_funding['actions'] == 1
    calls = len(backend.calls)
    loop.memory = prepared
    result = loop.step()
    assert result['verified'] and len(backend.calls) == calls
    assert loop.memory.pending is None


@pytest.mark.parametrize('mutation', ['remove_funding', 'remove_marker', 'remove_both', 'change_marker', 'disable_policy'])
def test_prepared_kit_checkpoint_cannot_shed_its_binding(mutation, tmp_path):
    loop, backend = kit_loop(tmp_path)
    captured = []
    original = backend.execute
    def keep(action, p):
        captured.append(json.loads(backend.checkpoint.read_text()))
        return original(action, p)
    backend.execute = keep
    loop.step(); saved = captured[0]
    if mutation == 'remove_funding': saved.pop('solid_funding')
    elif mutation == 'remove_marker': saved['active_plan']['materials'].pop(policy.MARKER)
    elif mutation == 'remove_both':
        saved.pop('solid_funding'); saved['active_plan']['materials'].pop(policy.MARKER)
    elif mutation == 'change_marker': saved['active_plan']['materials'][policy.MARKER]['layout'] += ':other'
    else: saved['solid_science_policy'] = False
    with pytest.raises(ValueError):
        loop.memory_type.from_bytes(json.dumps(saved).encode(), backend.state.session_id, 'rocket_launch')


def test_checkpoint_failure_prevents_kit_mutation_and_next_dispatch(tmp_path, monkeypatch):
    loop, backend = kit_loop(tmp_path)
    original = loop.memory_type.save
    def fail(memory, path):
        if memory.pending:
            raise OSError('Fixture prepared write failure')
        return original(memory, path)
    monkeypatch.setattr(loop.memory_type, 'save', fail)
    with pytest.raises(OSError): loop.step()
    assert backend.calls == []
    # Do not erase the in-memory pending or pretend a failed write succeeded.
    assert loop.memory.pending and loop._persistence_failed
    with pytest.raises(RuntimeError, match='persistence failed'): loop.step()
    assert backend.calls == []


def test_ready_science_survives_existing_funding(tmp_path, monkeypatch):
    from test_solid_route_integration import FoundationScenario
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    funding = deepcopy(loop.memory.solid_funding)
    backend.state.inventory['automation-science-pack'] = 1
    delivery = Plan('kit-fixture-science', 'rocket_launch', 'Deliver ready science',
        (Step('factory_insert', 'transfer', costs={'automation-science-pack': 1},
              parameters={'role': 'utility:lab', 'item': 'automation-science-pack',
                          'quantity': 1, 'receipt': 'ready-science'}),))
    monkeypatch.setattr(FoundationScenario, '_compile_candidates', lambda self, snapshot: ([delivery], ''))
    plans, _ = loop._work_candidates(loop._observe())
    assert delivery in plans
    assert loop.memory.solid_funding == funding and loop.memory.capital_investment is None

@pytest.mark.parametrize('change', ['deadline', 'catalog', 'demand'])
def test_abandoned_selected_kit_never_persists_unreloadable_memory(change, tmp_path, monkeypatch):
    loop, backend = kit_loop(tmp_path)
    original = loop.memory_type.save
    saved_count = 0
    def checked_save(memory, path):
        nonlocal saved_count
        result = original(memory, path)
        if path is not None:
            loop.memory_type.from_bytes(path.read_bytes(), memory.session_id, 'rocket_launch')
            saved_count += 1
        return result
    monkeypatch.setattr(loop.memory_type, 'save', checked_save)
    def update():
        if backend.observations == 2:
            if change == 'deadline':
                backend.state.tick += 216001
                backend.state.factory['solid_routes']['tick'] = backend.state.tick
            elif change == 'catalog':
                loop.catalog.recipes['inserter']['ingredients'][0]['amount'] += 1
            else:
                backend.state.factory['research_progress'] = 1
    backend.before_observe = update
    loop.step()
    assert saved_count > 0 and backend.calls == []


def test_entire_kit_acquisition_time_is_bounded_not_each_craft_individually():
    from jev_factorio.planning.solid_funding import acquire
    state, data = scenario(); missing_kit(state)
    # Every single craft fits the bound, but their sum does not.
    for name in ('inserter', 'iron-gear-wheel', 'electronic-circuit', 'copper-cable', 'transport-belt'):
        data.recipes[name]['energy'] = 600
    with pytest.raises(ValueError, match='horizon'):
        acquire(row(state), state, data)


def test_fresh_kit_permission_stays_on_committed_intent_when_another_offer_appears():
    from jev_factorio.planning import solid_funding
    state, data = scenario(); history = service_history(state); missing_kit(state)
    plan = offers(state, data, history)[0][0]
    funding = solid_funding.start(row(state), plan.materials[policy.MARKER], state.tick)
    extra = deepcopy(row(state))
    extra['route'] = 'aa:second-route'; extra['layout'] = 'second-layout'
    for index, side in enumerate(('source', 'target')):
        endpoint = extra[side]; old_role = endpoint['role']
        endpoint['role'] = 'additional:' + old_role; endpoint['unit_number'] = 8001 + index
        for pos in [endpoint['position'], *endpoint['bounds'].values()]: pos['y'] += 10
        machine = deepcopy(state.factory['entities'][old_role])
        machine['unit_number'] = endpoint['unit_number']; machine['position']['y'] += 10
        state.factory['entities'][endpoint['role']] = machine
    for step in extra['steps']: step['position']['y'] += 10
    original = row(state)
    state.factory['solid_routes']['routes'][ROUTE] = extra
    history += service_history(state)
    state.factory['solid_routes']['routes'][ROUTE] = original
    state.factory['solid_routes']['routes'][extra['route']] = extra
    assert contract.routes(state)
    default = offers(state, data, history)[0][0]
    assert default.id != plan.id
    constrained = offers(state, data, history, funding=funding)[0][0]
    assert constrained.id == plan.id
    assert policy.fresh_permission(plan, plan.steps[0], state, data, outcomes=history, funding=funding)


def test_normal_composed_planner_offers_and_selects_paid_kit_without_a_frontier_override(tmp_path):
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.solid_controller import solid_loop_type
    from solid_routes_fixtures import INTENTS
    original, backend = kit_loop(tmp_path)
    original._observe()
    loop = solid_loop_type(HierarchicalLoop)(backend, target='rocket_launch', policy='deterministic',
        factory_scheduling='ready-work', tick_seconds=0, checkpoint=str(backend.checkpoint),
        solid_intents=INTENTS, solid_science_policy=True, resume_controller=True)
    plans, _ = loop._work_candidates(loop._observe())
    assert any(p.id.endswith(':kit') for p in plans)
    assert any(not p.id.endswith(':kit') for p in plans)
    result = loop.step()
    assert result['verified'] and result['action'] in {'factory_craft', 'factory_extract'}
    assert loop.memory.solid_funding is not None
    assert backend.calls[0][0] != contract.COMMAND


def test_legacy_nonfunded_solid_checkpoint_loads_without_the_new_optional_field(tmp_path):
    loop, backend = kit_loop(tmp_path); loop._observe()
    saved = json.loads(backend.checkpoint.read_text()); saved.pop('solid_funding')
    restored = loop.memory_type.from_bytes(json.dumps(saved).encode(), backend.state.session_id, 'rocket_launch')
    assert restored.solid_funding is None and restored.solid_intents == loop._solid_intents


@pytest.mark.parametrize('kind', ['first_recipe', 'later_recipe', 'component', 'mixed_prior_failures'])
def test_kit_does_not_buy_fresh_budgets_for_exhausted_existing_work(kind):
    from jev_factorio.planning import solid_funding
    state, data = scenario(); history = service_history(state); missing_kit(state)
    key = solid_funding.project_key(row(state))
    counts = ({'factory:factory_craft:copper-cable': 2} if kind == 'first_recipe'
        else {'factory:factory_craft:inserter': 2} if kind == 'later_recipe'
        else {key + ':receive': 2} if kind == 'component'
        else {key + ':kit': 1, 'factory:factory_craft:copper-cable': 1})
    assert not offers(state, data, history, failures=counts)[0]


def test_fresh_ordinary_acquisition_failure_budget_stops_dispatch(tmp_path):
    loop, backend = kit_loop(tmp_path)
    def update():
        if backend.observations == 2:
            loop.memory.failures['factory:factory_craft:copper-cable'] = 2
    backend.before_observe = update
    record = loop.step()
    assert not record['verified'] and backend.calls == []
    assert loop.memory.failures['factory:factory_craft:copper-cable'] == 2
