"""Coal acquisition controller/payment fixtures; not Factorio-engine evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from jev_factorio import coal_supply as coal, solid_routes as solid
from jev_factorio.planning import coal_funding as funding
from jev_factorio.memory import load_checkpoint
from jev_factorio.skills import Plan, Step
from jev_factorio.research_log import RunConfiguration, ResearchLogError, _configuration
from coal_supply_fixtures import TARGETS, INTENTS, fixture
from test_coal_supply_integration import Backend as BaseBackend, Loop
from test_solid_investment import catalog
from test_factory import recipe


def data():
    result = catalog()
    # Synthetic recipes: the live negotiated catalog, not these quantities,
    # defines engine construction costs.
    result.recipes.update({
        'wooden-chest': recipe('wooden-chest', {'wood': 2}),
        'electric-mining-drill': recipe('electric-mining-drill',
                                      {'iron-plate': 10, 'iron-gear-wheel': 5, 'electronic-circuit': 3}),
    })
    return result


class Backend(BaseBackend):
    def __init__(self, mode='output'):
        super().__init__()
        self.data = data()
        self.state.factory.setdefault("receipts", {})
        self.lose_kit_ack = False
        self.kit_after = lambda: None
        self.state.inventory = {**coal.remaining_kit(coal.sources(self.state), self.state), 'coal': 20}
        self.state.inventory['electric-mining-drill'] = 0
        if mode == 'output':
            self.state.factory['entities']['kit:storage'] = {
                'unit_number': 6000, 'name': 'wooden-chest', 'position': {'x': 2, 'y': 2},
                'output': {'electric-mining-drill': 2}}
        if mode in {'craft', 'all'}:
            self.state.inventory.update({'iron-plate': 180, 'copper-plate': 100, 'wood': 20})
        if mode == 'all':
            for k in coal.remaining_kit(coal.sources(self.state), self.state):
                self.state.inventory[k] = 0

    def enable_factory(self):
        self.enabled += 1
        return self.data

    def advance(self):
        self.state.tick += 1
        self.state.factory['coal_supply']['tick'] = self.state.tick
        self.state.factory['solid_routes']['tick'] = self.state.tick

    def execute(self, action, p):
        if action not in {'factory_craft', 'factory_extract'}:
            return super().execute(action, p)
        saved = json.loads(self.checkpoint.read_text())
        assert saved['pending']['dispatch'] == 'prepared'
        assert saved['active_plan']['steps'][0]['parameters'] == p
        assert saved['coal_funding']['actions'] >= 1 and saved['coal_kit_policy'] is True
        self.calls.append((action, deepcopy(p)))
        if action == 'factory_craft':
            r = self.data.recipes[p['recipe']]
            for item in r['ingredients']:
                qty = item['amount'] * p['batches']
                assert self.state.inventory.get(item['name'], 0) >= qty
                self.state.inventory[item['name']] -= qty
            for item in r['products']:
                self.state.inventory[item['name']] = self.state.inventory.get(item['name'], 0) + item['amount'] * p['batches']
        else:
            entity = self.state.factory['entities'][p['role']]
            assert p['receipt'] not in self.state.factory['receipts']
            assert entity['output'][p['item']] >= p['quantity']
            entity['output'][p['item']] -= p['quantity']
            self.state.inventory[p['item']] = self.state.inventory.get(p['item'], 0) + p['quantity']
            self.state.factory['receipts'][p['receipt']] = {
                'role': p['role'], 'item': p['item'], 'quantity': p['quantity'],
                'extracting': True, 'unit_number': entity['unit_number']}
        self.advance()
        self.kit_after()
        if self.lose_kit_ack:
            raise TimeoutError('Fixture acknowledgement lost after paid kit action')
        return 'fixture paid kit action'


def controller(backend, path, *, resume=False, policy=True, kind=Loop, target="rocket_launch", **options):
    backend.checkpoint = path / 'coal-kit.json'
    loop = kind(backend, target=target, policy='deterministic',
                factory_scheduling='ready-work', tick_seconds=0,
                checkpoint=str(backend.checkpoint), resume_controller=resume,
                solid_intents=INTENTS, coal_targets=TARGETS, coal_kit_policy=policy, **options)
    if not resume:
        loop.memory = loop.memory_type(backend.state.session_id, target,
            active_goal=target, completed_goals={'stockpile_fuel': 0, 'bootstrap_mining': 0},
            last_tick=backend.state.tick)
    return loop


def offers(loop):
    snapshot = loop._observe()
    return snapshot, loop._compile_candidates(snapshot)[0]


@pytest.mark.parametrize('mode', ['output', 'craft', 'all'])
def test_funds_entire_bundle_then_builds_using_only_paid_existing_commands(tmp_path, mode):
    backend = Backend(mode); loop = controller(backend, tmp_path)
    first_action = None
    for _ in range(60):
        record = loop.step()
        assert record['verified'], record
        first_action = first_action or record['action']
        assert all(n >= 0 for n in backend.state.inventory.values())
        saved = load_checkpoint(backend.checkpoint, backend.state.session_id, 'rocket_launch')
        assert saved.coal_funding == loop.memory.coal_funding
        if all(len(row['parts']) == 2 for row in coal.sources(backend.state).values()):
            break
    assert first_action in {'factory_craft', 'factory_extract'}
    assert all(len(row['parts']) == 2 for row in coal.sources(backend.state).values())
    assert loop.memory.coal_funding is None
    assert not coal.flow_complete(backend.state)
    assert not loop.memory.failures
    assert all(action in {'factory_craft', 'factory_extract', coal.COMMAND, solid.COMMAND}
               for action, _ in backend.calls)


def test_default_carried_only_treatment_has_unchanged_frontier(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path, policy=False)
    _, plans = offers(loop)
    assert not plans and not backend.calls
    assert loop.memory.coal_funding is None


@pytest.mark.parametrize('value', [None, 0, 1, 'true', [], {}])
def test_policy_must_be_boolean_before_any_attachment(tmp_path, value):
    backend = Backend()
    with pytest.raises(ValueError): controller(backend, tmp_path, policy=value)
    assert backend.enabled == 0 and not backend.calls


def test_actual_carried_holds_survive_plan_completion_and_resume(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    record = loop.step(); assert record['verified'], record
    state = deepcopy(loop.memory.coal_funding)
    assert state and state['held'] == state['kit']
    assert loop.memory.active_plan is None and loop.memory.pending is None
    resumed = controller(backend, tmp_path, resume=True)
    resumed._observe()
    assert resumed.memory.coal_funding == state
    assert resumed.step()['verified']
    assert resumed.memory.coal_funding is None
    assert len(backend.calls) == 2


@pytest.mark.parametrize('mode', ['output', 'craft'])
def test_lost_kit_reply_reconciles_without_duplicate_mutation(tmp_path, mode):
    backend = Backend(mode); backend.lose_kit_ack = True
    loop = controller(backend, tmp_path)
    first = loop.step(); assert not first['verified']
    pending = deepcopy(loop.memory.pending)
    attempt = loop.memory.attempt['id']; assert pending
    backend.lose_kit_ack = False
    resumed = controller(backend, tmp_path, resume=True)
    second = resumed.step(); assert second['verified'], second
    assert len(backend.calls) == 1
    assert resumed.memory.attempt_outcomes[-1]['id'] == attempt
    assert resumed.memory.coal_funding is not None


def test_held_components_cannot_be_spent_by_unrelated_actions(tmp_path):
    backend = Backend('craft'); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    snapshot = loop._observe()
    held = loop.memory.coal_funding['held']
    assert held['wooden-chest'] == 2
    step = Step('factory_place', 'machine', costs={'wooden-chest': 1},
                parameters={'role': 'other:chest', 'name': 'wooden-chest', 'anchor': 'iron-ore'})
    assert step.allowed(snapshot) and not loop._step_allowed(step, snapshot)
    snapshot.inventory['wooden-chest'] += 1
    assert loop._step_allowed(step, snapshot)


def test_unexplained_lost_hold_fails_closed_without_erasing_funding(tmp_path):
    backend = Backend('craft'); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    held = deepcopy(loop.memory.coal_funding)
    backend.state.inventory['wooden-chest'] -= 1
    loop.step()
    assert loop.memory.status == 'uncertain'
    assert loop.memory.coal_funding == held
    assert len(backend.calls) == 1


@pytest.mark.parametrize('change', ['power', 'ore', 'queue', 'disconnected', 'unbound', 'output',
                                  'alias', 'position', 'headroom', 'pending', 'boiler'])
def test_unqualified_or_unfundable_proposals_make_no_mutation(tmp_path, change):
    backend = Backend(); snapshot = backend.state
    row = coal.sources(snapshot)['alpha']
    if change == 'power': row['power']['energized'] = False
    elif change == 'ore': row['remaining'] = 0
    elif change == 'queue': snapshot.factory['crafting_queue'] = 1
    elif change == 'disconnected': snapshot.factory['player_connected'] = False
    elif change == 'unbound': snapshot.factory['player_bound'] = False
    elif change == 'output': snapshot.factory['entities']['kit:storage']['output'].clear()
    elif change == 'alias': snapshot.factory['entities']['kit:alias'] = deepcopy(snapshot.factory['entities']['kit:storage'])
    elif change == 'position': snapshot.factory['entities']['kit:storage'].pop('position')
    elif change == 'headroom': snapshot.factory['inventory_insertable'] = {'electric-mining-drill': 0}
    elif change == 'pending': row['manual_pending'] = {'phase': 'unrecognized'}
    elif change == 'boiler': snapshot.factory['entities']['utility:boiler'] = {
        'unit_number': 6100, 'name': 'boiler', 'fuel': {'coal': 1}}
    loop = controller(backend, tmp_path)
    _, plans = offers(loop)
    assert not any(funding.MARKER in (p.materials or {}) for p in plans)
    assert not backend.calls


@pytest.mark.parametrize('field,value', [('schema', True), ('key', 'coal-kit:' + '0' * 64),
    ('held', {'wooden-chest': 300}), ('kit', {'wooden-chest': 1}), ('actions', 0),
    ('actions', 33), ('deadline_tick', 0), ('catalog_sha256', 'x' * 64)])
def test_corrupt_funding_checkpoint_rejected_before_attachment(tmp_path, field, value):
    backend = Backend('craft'); loop = controller(backend, tmp_path); assert loop.step()['verified']
    raw = json.loads(backend.checkpoint.read_text()); raw['coal_funding'][field] = value
    backend.checkpoint.write_text(json.dumps(raw)); before = backend.enabled
    with pytest.raises(ValueError): controller(backend, tmp_path, resume=True)
    assert backend.enabled == before and len(backend.calls) == 1


@pytest.mark.parametrize('change', ['flag', 'state', 'marker', 'id', 'digest', 'step'])
def test_pending_funding_cannot_be_reinterpreted_on_resume(tmp_path, change):
    backend = Backend(); backend.lose_kit_ack = True
    loop = controller(backend, tmp_path); loop.step()
    raw = json.loads(backend.checkpoint.read_text())
    if change == 'flag': raw['coal_kit_policy'] = False
    elif change == 'state': raw['coal_funding'] = None
    elif change == 'marker': raw['active_plan']['materials'].pop(funding.MARKER)
    elif change == 'id': raw['active_plan']['id'] = 'coal-kit:' + '0' * 64
    elif change == 'digest': raw['active_plan']['materials'][funding.MARKER]['bundle_sha256'] = '0' * 64
    elif change == 'step': raw['active_plan']['steps'].append(deepcopy(raw['active_plan']['steps'][0]))
    backend.checkpoint.write_text(json.dumps(raw)); before = backend.enabled
    with pytest.raises(ValueError): controller(backend, tmp_path, resume=True)
    assert backend.enabled == before


@pytest.mark.parametrize('change', ['cost', 'receipt', 'key', 'tick', 'bundle', 'quantity', 'evidence'])
def test_canonical_fresh_permission_rejects_modified_selection(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path)
    snapshot, plans = offers(loop); plan = next(p for p in plans if funding.MARKER in (p.materials or {}))
    loop._commit_solid(plan, snapshot)
    altered = deepcopy(plan.to_dict())
    if change == 'evidence': altered['materials']['coal_kit_cost']['acquisition_actions_estimate'] += 1
    elif change == 'cost': altered['steps'][0]['costs'] = {'coal': 1}
    elif change == 'receipt': altered['steps'][0]['parameters']['receipt'] = 'other'
    elif change == 'key': altered['id'] = 'coal-kit:' + '0' * 64
    elif change == 'tick': altered['materials'][funding.MARKER]['observed_tick'] += 1
    elif change == 'bundle': altered['materials'][funding.MARKER]['bundle_sha256'] = '0' * 64
    elif change == 'quantity': altered['steps'][0]['parameters']['quantity'] = 1
    other = Plan.from_dict(altered)
    assert not loop._investment_step_allowed(other, other.steps[0], snapshot)
    assert not backend.calls


def test_manifest_binds_kit_treatment_and_requires_coal_treatment():
    good = RunConfiguration('mock', 'hierarchical', 'deterministic', solid_routes=True,
                            coal_supply=True, coal_kit_policy=True)
    _configuration(asdict(good))
    with pytest.raises(ResearchLogError): _configuration(asdict(replace(good, coal_supply=False)))


def test_project_budget_identity_ignores_order_not_targets():
    assert funding.project_key(TARGETS) == funding.project_key(list(reversed(TARGETS)))
    assert funding.project_key(TARGETS) != funding.project_key(['alpha', 'gamma'])


def test_expired_unresolved_action_preserves_pending_and_holds(tmp_path):
    backend = Backend(); backend.lose_kit_ack = True
    loop = controller(backend, tmp_path); loop.step()
    state = deepcopy(loop.memory.coal_funding)
    backend.state.tick = state['deadline_tick'] + 1
    backend.state.factory['coal_supply']['tick'] = backend.state.tick
    backend.state.factory['solid_routes']['tick'] = backend.state.tick
    assert loop.memory.pending is not None
    assert loop.step()['verified']  # Receipt reconciliation, not a new mutation.
    assert len(backend.calls) == 1
    loop.step()
    assert len(backend.calls) == 1 and loop.memory.failures[state['key']] >= 2
    assert loop.memory.coal_funding is None


@pytest.mark.parametrize('kind', ['source', 'corridor', 'ordinary', 'project'])
def test_existing_failure_budgets_cannot_be_bypassed_with_coal_kit_identity(tmp_path, kind):
    from jev_factorio.planning.coal_supply import source_project
    from jev_factorio.planning.solid_funding import project_key
    backend = Backend(); loop = controller(backend, tmp_path)
    snapshot = loop._observe(); row = coal.sources(snapshot)['alpha']
    key = funding.project_key(TARGETS)
    if kind == 'source': key = source_project('alpha', 'chest')
    elif kind == 'corridor':
        stub = {'source': {'role': coal.role('alpha', 'chest')}, 'target': row['target'], 'item': 'coal'}
        key = project_key(stub) + ':send'
    elif kind == 'ordinary': key = 'factory:factory_extract:kit:storage'
    loop.memory.failures[key] = 2
    plans = loop._compile_candidates(snapshot)[0]
    assert not any(funding.MARKER in (p.materials or {}) for p in plans)
    assert loop.memory.failures == {key: 2} and not backend.calls


def test_old_receipt_cannot_authorize_new_kit_pickup(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    snapshot, plans = offers(loop)
    plan = next(p for p in plans if funding.MARKER in (p.materials or {})); p = plan.steps[0].parameters
    backend.state.factory['receipts'][p['receipt']] = {
        'role': p['role'], 'item': p['item'], 'quantity': p['quantity'],
        'extracting': True, 'unit_number': 6000}
    _, plans = offers(loop)
    assert not any(funding.MARKER in (p.materials or {}) for p in plans)
    assert not backend.calls


@pytest.mark.parametrize('change', ['power', 'stock', 'queue', 'catalog'])
def test_change_after_selection_prevents_actual_dispatch(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path)
    original = loop._compile_candidates
    def select(snapshot):
        result = original(snapshot)
        if change == 'power': coal.sources(backend.state)['beta']['power']['energized'] = False
        elif change == 'stock': backend.state.factory['entities']['kit:storage']['output'].clear()
        elif change == 'queue': backend.state.factory['crafting_queue'] = 1
        elif change == 'catalog': backend.data.recipes['electric-mining-drill']['energy'] *= 2
        return result
    loop._compile_candidates = select
    # A catalog mutated inside selection is rejected at plan commitment; fresh
    # native-world changes are rejected at the pre-dispatch observation.
    if change == 'catalog':
        with pytest.raises(ValueError): loop.step()
    else:
        record = loop.step(); assert not record['verified']
    assert not backend.calls


def test_action_cap_cannot_be_reset_by_delivering_a_complete_kit_later(tmp_path):
    backend = Backend('craft'); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    key = loop.memory.coal_funding['key']
    loop.memory.coal_funding['actions'] = funding.MAX_ACTIONS
    loop._observe()
    assert loop.memory.coal_funding is None and loop.memory.failures[key] >= 2
    backend.state.inventory.update(coal.remaining_kit(coal.sources(backend.state), backend.state))
    _, plans = offers(loop)
    assert not plans and len(backend.calls) == 1


def test_changed_unpaid_target_keeps_stable_project_failure_history(tmp_path):
    backend = Backend('craft'); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    key = loop.memory.coal_funding['key']
    source = coal.sources(backend.state)['alpha']
    source['target']['unit_number'] += 1000
    backend.state.factory['entities']['alpha']['unit_number'] = source['target']['unit_number']
    loop._observe()
    assert loop.memory.coal_funding is None and loop.memory.failures[key] >= 2
    assert len(backend.calls) == 1


@pytest.mark.parametrize('change', ['remove_new_fields', 'turn_on'])
def test_legacy_carried_only_checkpoint_cannot_silently_enable_funding(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path, policy=False)
    loop._observe(); saved = json.loads(backend.checkpoint.read_text())
    if change == 'remove_new_fields':
        saved.pop('coal_kit_policy'); saved.pop('coal_funding')
        backend.checkpoint.write_text(json.dumps(saved))
        resumed = controller(backend, tmp_path, resume=True, policy=False)
        resumed._observe(); assert resumed.memory.coal_kit_policy is False
    before = backend.enabled
    with pytest.raises(ValueError): controller(backend, tmp_path, resume=True, policy=True)
    assert backend.enabled == before and not backend.calls


def test_internal_bill_solver_never_consumes_already_carried_final_components():
    from jev_factorio.planning.solid_funding import _acquire_bill
    state = fixture(); state.factory.setdefault('receipts', {})
    state.inventory = {'transport-belt': 2, 'inserter': 0, 'iron-plate': 4}
    c = data()
    c.recipes['inserter'] = recipe('inserter', {'transport-belt': 1})
    c.recipes['transport-belt'] = recipe('transport-belt', {'iron-plate': 2})
    plan, _, _ = _acquire_bill({'transport-belt': 2, 'inserter': 1}, 'probe:paid', state, c,
                               protect_final_stock=True)
    assert plan.steps[0].parameters == {'recipe': 'transport-belt', 'batches': 1}
    assert plan.steps[0].costs == {'iron-plate': 2}


def test_competing_project_reservations_cannot_fund_missing_raw_inputs(tmp_path):
    backend = Backend('craft'); loop = controller(backend, tmp_path)
    snapshot = loop._observe()
    with pytest.raises(ValueError):
        funding.candidate(snapshot, loop.catalog,
                          reserved={'iron-plate': snapshot.inventory['iron-plate']})
    assert not backend.calls


def test_ready_science_frontier_remains_and_parent_compiles_once(tmp_path):
    from jev_factorio.coal_controller import coal_loop_type
    from jev_factorio.solid_controller import solid_loop_type
    from test_solid_route_integration import FoundationScenario
    class Production(FoundationScenario):
        compiled = 0
        def _compile_candidates(self, snapshot):
            self.compiled += 1
            return [Plan('science:ready', 'rocket_launch', 'Collect ready science',
                (Step('factory_extract', 'transfer', parameters={'role': 'science:ready',
                 'item': 'automation-science-pack', 'quantity': 1, 'receipt': 'science:1'}),))], ''
    backend = Backend()
    backend.state.factory['entities']['science:ready'] = {
        'unit_number': 8000, 'name': 'assembling-machine-1', 'position': {'x': 4, 'y': 4},
        'output': {'automation-science-pack': 1}}
    loop = controller(backend, tmp_path, kind=coal_loop_type(solid_loop_type(Production)))
    _, plans = offers(loop)
    assert loop.compiled == 1
    assert any(p.id == 'science:ready' for p in plans)
    assert any(funding.MARKER in (p.materials or {}) for p in plans)


@pytest.mark.parametrize('mode', ['output', 'craft'])
def test_nonrocket_goal_survives_paid_kit_lost_reply_and_resume(tmp_path, mode):
    backend = Backend(mode); backend.lose_kit_ack = True
    loop = controller(backend, tmp_path, target='iron_smelting')
    assert not loop.step()['verified']
    assert loop.memory.active_plan['goal'] == loop.memory.active_goal == 'iron_smelting'
    pending, attempt = deepcopy(loop.memory.pending), deepcopy(loop.memory.attempt)
    backend.lose_kit_ack = False
    resumed = controller(backend, tmp_path, resume=True, target='iron_smelting')
    assert resumed.step()['verified']
    assert len(backend.calls) == 1
    assert resumed.memory.attempt_outcomes[-1]['id'] == attempt['id']
    assert pending['action'] == backend.calls[0][0]


@pytest.mark.parametrize('status', ['active', 'paused'])
def test_successor_ownership_excludes_coal_offer_and_commit(tmp_path, status):
    backend = Backend(); loop = controller(backend, tmp_path)
    snapshot, plans = offers(loop)
    plan = next(p for p in plans if funding.MARKER in (p.materials or {}))
    loop.memory.successor_projects = {'growth:iron-plate': {'status': status}}
    plans, _ = loop._compile_candidates(snapshot)
    assert not any(funding.MARKER in (p.materials or {}) for p in plans)
    with pytest.raises(ValueError, match='unqualified coal kit'):
        loop._commit_solid(plan, snapshot)
    assert loop.memory.coal_funding is None and not backend.calls


def test_changed_later_kit_stock_invalidates_cost_evidence_with_same_first_action(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    backend.state.inventory['wooden-chest'] = 0
    backend.state.factory['entities']['kit:storage']['output']['wooden-chest'] = 2
    snapshot, plans = offers(loop)
    plan = next(p for p in plans if funding.MARKER in (p.materials or {}))
    loop._commit_solid(plan, snapshot)
    fresh = deepcopy(snapshot)
    fresh.inventory['wooden-chest'] = 2
    current, _ = funding.candidate(fresh, loop.catalog, **loop._coal_funding_options(plan))
    assert current.steps == plan.steps
    assert current.materials['coal_kit_cost'] != plan.materials['coal_kit_cost']
    assert not loop._investment_step_allowed(plan, plan.steps[0], fresh)
    assert not backend.calls


def test_qualified_successor_does_not_block_coal_funding(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop.memory.successor_projects = {'growth:iron-plate': {'status': 'qualified'}}
    snapshot, plans = offers(loop)
    plan = next(p for p in plans if funding.MARKER in (p.materials or {}))
    loop._commit_solid(plan, snapshot)
    assert loop.memory.coal_funding is not None
    assert not backend.calls
