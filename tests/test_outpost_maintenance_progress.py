"""Demand-aware outpost regressions on the actual composed planner/controller."""
from copy import deepcopy

import pytest

from jev_factorio import mining_outposts as contract
from jev_factorio.outpost_controller import outpost_loop_type
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_factory import machine
from test_input_route_integration import RouteLoop, controller
from test_maintenance_progress import progress_scenario
from test_mining_outposts import state_fixture, full, commission, row


def completed_source(fuel=1, stored=75):
    state, data = state_fixture(); full(state); commission(state)
    state.factory['entities'][contract.role('iron-ore', 'drill')]['fuel']['coal'] = fuel
    state.factory['entities'][contract.role('iron-ore', 'chest')]['output']['iron-ore'] = stored
    return state, data


def composed(tmp_path, *, coal=50, packs=20, fuel=1):
    backend, data = progress_scenario(coal=coal, science=packs)
    state, _ = completed_source(fuel)
    # A copper outpost supplies a separate manual producer; it is not a second
    # owner of the existing iron input-belt route.
    old = row(state); copied = deepcopy(old)
    copied.update(resource='copper-ore', layout='outpost:copper-ore:1')
    for part in copied['parts'].values():
        old_role = part['role']
        part['role'] = old_role.replace('iron-ore', 'copper-ore')
        entity = deepcopy(state.factory['entities'][old_role])
        entity['output'] = {'copper-ore': 75} if old_role.endswith('chest') else {}
        backend.state.factory['entities'][part['role']] = entity
    copied['flow']['layout'] = copied['layout']
    backend.state.factory['entities']['recipe:copper-plate'] = machine(
        recipe='copper-plate', unit_number=920, fuel={'coal':50}, products_finished=100)
    backend.state.factory['mining_outposts'] = dict(protocol=1,
        session_id=backend.state.session_id, tick=backend.state.tick,
        sources={'copper-ore':copied})
    backend.mining_outposts_supported = True
    loop = controller(backend, tmp_path, kind=outpost_loop_type(RouteLoop))
    return loop, backend


@pytest.mark.parametrize('coal', [0, 50])
@pytest.mark.parametrize('fuel', [0, 1, 2])
def test_stocked_outpost_cannot_replace_ready_science_delivery(tmp_path, coal, fuel):
    loop, backend = composed(tmp_path, coal=coal, fuel=fuel)
    before = deepcopy(backend.state)
    plans, blocker = loop._compile_candidates(backend.state)
    assert any(p.steps[0].action == 'factory_insert'
               and p.steps[0].parameters['role'] == 'utility:lab'
               and p.steps[0].parameters['item'] == 'logistic-science-pack'
               for p in plans), blocker
    assert backend.state == before and not backend.calls


@pytest.mark.parametrize('fuel', [0, 1, 2])
def test_outpost_does_not_replace_ready_science_crafting(tmp_path, fuel):
    loop, backend = composed(tmp_path, packs=0, fuel=fuel)
    plans, _ = loop._compile_candidates(backend.state)
    assert any(p.steps[0].action == 'factory_craft'
               and p.steps[0].parameters['recipe'] == 'logistic-science-pack' for p in plans)


@pytest.mark.parametrize('fuel', [0, 1, 2])
def test_commissioned_ready_ore_is_collected_before_refueling(fuel):
    state, data = completed_source(fuel)
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need('iron-ore', 10).steps[0]
    assert step.action == 'factory_extract' and step.parameters['quantity'] == 10
    assert step.parameters['role'] == contract.role('iron-ore', 'chest')
    assert step.allowed(state)


def test_outpost_controller_does_not_construct_a_discarded_second_planner(tmp_path, monkeypatch):
    loop, backend = composed(tmp_path)
    made=[]; init=ReadyWorkPlanner.__init__
    def counted(self, *args, **kwargs):
        made.append(type(self).__name__)
        return init(self, *args, **kwargs)
    monkeypatch.setattr(ReadyWorkPlanner, '__init__', counted)
    plans, _ = loop._compile_candidates(backend.state)
    assert plans
    assert made == ['MiningOutpostPlanner']


def test_uncovered_required_outpost_is_still_refueled():
    state, data = completed_source(stored=0)
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need('iron-ore', 10).steps[0]
    assert step.action == 'factory_insert' and step.parameters['item'] == 'coal'
    assert step.parameters['role'] == contract.role('iron-ore', 'drill')
    assert step.parameters['quantity'] == 4 and step.allowed(state)


def test_uncommissioned_stock_is_not_collected_instead_of_necessary_bootstrap_fuel():
    state, data = completed_source(stored=75); row(state)['flow']={}
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need('iron-ore', 10).steps[0]
    assert step.action == 'factory_insert' and step.parameters['item'] == 'coal'


def two_due_outposts():
    state, data = completed_source(stored=0)
    old = row(state); other=deepcopy(old)
    other.update(resource='copper-ore', layout='outpost:copper-ore:1')
    other['flow'].update(layout=other['layout'], drill_unit=81, chest_unit=80)
    for spec in other['steps']:
        spec['position']['x'] += 10
        part = other['parts'][spec['part']]
        original=deepcopy(state.factory['entities'][part['role']])
        part.update(role=part['role'].replace('iron-ore','copper-ore'), unit_number=part['unit_number']+10,
                    receipt='copper:'+spec['part'])
        original.update(unit_number=part['unit_number'], position=deepcopy(spec['position']), output={})
        state.factory['entities'][part['role']]=original
    state.factory['mining_outposts']['sources']['copper-ore']=other
    state.inventory['coal']=0
    planner=MiningOutpostPlanner(data,state,'rocket_launch')
    planner._set_focus('iron-ore',20)
    planner.demands['copper-ore']=20
    return state,data,planner


def test_two_demanded_outposts_share_one_bounded_fuel_acquisition():
    state, data, planner=two_due_outposts()
    plan=planner._need('iron-ore',20)
    assert plan.steps[0].action=='factory_gather'
    assert plan.steps[0].parameters['quantity']>=8
    evidence=plan.materials['fuel_service']
    assert evidence['consumer_count']==2 and evidence['combined_deficit']==8
    assert evidence['acquisition_target']<=50


@pytest.mark.parametrize('reason', ['idle', 'covered', 'full', 'depleted', 'distant'])
def test_optional_outpost_does_not_inflate_due_demand(reason):
    state, data, planner=two_due_outposts()
    other=state.factory['mining_outposts']['sources']['copper-ore']
    drill=state.factory['entities'][contract.role('copper-ore','drill')]
    if reason=='idle': planner.demands.pop('copper-ore')
    if reason=='covered': state.factory['entities'][contract.role('copper-ore','chest')]['output']['copper-ore']=75
    if reason=='full': drill['fuel_insertable']={'coal':0}
    if reason=='depleted': other.update(remaining=0,state='depleted')
    if reason=='distant':
        for spec in other['steps']:
            spec['position']['x']+=500
            state.factory['entities'][other['parts'][spec['part']]['role']]['position']=deepcopy(spec['position'])
    plan=planner._need('iron-ore',20)
    assert plan.steps[0].action=='factory_gather'
    assert plan.materials['fuel_service']['consumer_count']==1
    assert plan.materials['fuel_service']['combined_deficit']==4


@pytest.mark.parametrize('reason', ['carried', 'uncommissioned', 'replaced', 'failed'])
def test_additional_optional_outpost_exclusions(reason):
    from jev_factorio.planning.demand import SupplyLedger

    state, data, planner = two_due_outposts()
    other = state.factory['mining_outposts']['sources']['copper-ore']
    drill_role = contract.role('copper-ore', 'drill')
    if reason == 'carried':
        state.inventory['copper-ore'] = 20
        planner.ledger = SupplyLedger.capture(state, data)
    elif reason == 'uncommissioned':
        other['flow'] = {}
    elif reason == 'replaced':
        state.factory['entities'][drill_role]['unit_number'] = 999
    else:
        state._planner_failure_budgets = {planner._transfer(drill_role, 'coal', 4).id: 2}
    plan = planner._need('iron-ore', 20)
    evidence = plan.materials['fuel_service']
    assert evidence['consumer_count'] == 1 and evidence['combined_deficit'] == 4
    # A replaced endpoint is excluded from the quantity calculation but cannot
    # thereby authorize an action in an incoherent actual composed observation.
    if reason == 'replaced':
        assert not plan.steps[0].allowed(state)


@pytest.mark.parametrize('held,spendable', [(10, 0), (10, 2), (0, 2)])
def test_outpost_fuel_never_spends_held_coal_and_uses_a_partial_reserve(held, spendable):
    from jev_factorio.planning.demand import SupplyLedger

    state, data, planner = two_due_outposts()
    state.inventory['coal'] = held + spendable
    planner.ledger = SupplyLedger.capture(state, data, reserved={'coal': held})
    plan = planner._need('iron-ore', 20)
    evidence, step = plan.materials['fuel_service'], plan.steps[0]
    assert evidence['carried_held'] == held and evidence['carried_spendable'] == spendable
    if spendable:
        assert step.action == 'factory_insert' and step.parameters['quantity'] == spendable
        assert step.costs == {'coal': spendable}
    else:
        assert step.action == 'factory_gather' and step.parameters['quantity'] == 8
        assert step.threshold == held + 8


@pytest.mark.parametrize('capacity', [0, 1, 3, 7])
def test_outpost_acquisition_respects_known_actor_capacity(capacity):
    state, data, planner = two_due_outposts()
    state.factory['inventory_insertable'] = {'coal': capacity}
    if not capacity:
        with pytest.raises(ValueError, match='capacity'):
            planner._need('iron-ore', 20)
    else:
        plan = planner._need('iron-ore', 20)
        assert plan.steps[0].parameters['quantity'] == capacity
        assert plan.materials['fuel_service']['consumer_count'] == 2


def test_failed_primary_fuel_transfer_cannot_trigger_another_acquisition():
    state, data, planner = two_due_outposts()
    failure = planner._transfer(contract.role('iron-ore', 'drill'), 'coal', 4).id
    state._planner_failure_budgets = {failure: 2}
    before = deepcopy(state)
    with pytest.raises(ValueError, match='primary transfer failure'):
        planner._need('iron-ore', 20)
    assert state == before and state._planner_failure_budgets[failure] == 2


def test_outpost_group_recomputes_after_a_partial_receipt_without_more_gathering():
    """Planner simulation only: insert receipts are synthetic, not native flow."""
    from jev_factorio.planning.demand import SupplyLedger

    state, data, _ = two_due_outposts()
    state.factory['entities'][contract.role('iron-ore', 'drill')]['fuel']['coal'] = 0
    actions = []
    # One combined acquisition, a partial first insertion, then fresh planning
    # for both still-due consumers. No plan is reused as future authorization.
    for item, transferred in [('iron-ore', None), ('iron-ore', 1),
                              ('iron-ore', None), ('copper-ore', None)]:
        planner = MiningOutpostPlanner(data, state, 'rocket_launch')
        planner._set_focus(item, 20)
        planner.demands.update({'iron-ore': 20, 'copper-ore': 20})
        planner.ledger = SupplyLedger.capture(state, data)
        plan = planner._need(item, 20)
        step = plan.steps[0]
        actions.append(step.action)
        if step.action == 'factory_gather':
            state.inventory['coal'] += step.parameters['quantity']
        else:
            amount = step.parameters['quantity'] if transferred is None else transferred
            state.inventory['coal'] -= amount
            entity = state.factory['entities'][step.parameters['role']]
            entity['fuel']['coal'] += amount
            state.factory.setdefault('receipts', {})[step.parameters['receipt']] = {
                'role': step.parameters['role'], 'unit_number': entity['unit_number'],
                'item': 'coal', 'quantity': amount, 'extracting': False}
            assert step.satisfied(state) is (amount == step.parameters['quantity'])
        state.tick += 1
        for name in ('input_routes', 'output_buffers', 'mining_outposts'):
            state.factory[name]['tick'] = state.tick
    assert actions == ['factory_gather', 'factory_insert', 'factory_insert', 'factory_insert']
    assert state.inventory['coal'] == 0
    assert all(state.factory['entities'][contract.role(item, 'drill')]['fuel']['coal'] == 5
               for item in ('iron-ore', 'copper-ore'))


def test_composed_outpost_still_defers_to_urgent_boiler(tmp_path):
    loop, backend = composed(tmp_path)
    backend.state.factory['entities']['utility:boiler'] = machine(
        'boiler', unit_number=901, fuel={'coal': 1})
    plans, _ = loop._compile_candidates(backend.state)
    assert plans and plans[0].steps[0].parameters['role'] == 'utility:boiler'
    assert plans[0].steps[0].parameters['item'] == 'coal'


def _retain_outposts(loop, state):
    """Create a fixture checkpoint's existing paid ownership, never native state."""
    loop.memory.outpost_commitments = {
        resource: {key: deepcopy(value[key]) for key in
                   ('layout', 'surface_index', 'force_index', 'steps', 'parts', 'flow')}
        for resource, value in contract.sources(state).items()}


@pytest.mark.parametrize('fault', ['fault', 'missing', 'replaced'])
def test_outpost_fault_blocks_mutations_without_resetting_history(tmp_path, fault):
    loop, backend = composed(tmp_path)
    _retain_outposts(loop, backend.state)
    loop.memory.failures['retained-attempt'] = 2
    previous = deepcopy(loop.memory.outpost_commitments)
    if fault == 'fault':
        backend.state.factory['mining_outposts']['sources']['copper-ore']['state'] = 'fault'
    elif fault == 'missing':
        backend.state.factory['mining_outposts']['sources'] = {}
    else:
        backend.state.factory['entities'][contract.role('copper-ore', 'drill')]['unit_number'] = 999
    result = loop.step()
    assert result['status'] == 'uncertain' and backend.calls == []
    assert loop.memory.failures['retained-attempt'] == 2
    assert loop.memory.outpost_commitments == previous


def test_actual_composed_controller_delivers_science_with_receipts_repeatedly(tmp_path):
    loop, backend = composed(tmp_path, packs=100, coal=0, fuel=0)
    _retain_outposts(loop, backend.state)
    commitments = deepcopy(loop.memory.outpost_commitments)
    loop.memory.failures['retained-attempt'] = 2

    def transfer(action, args):
        assert action == 'factory_insert' and args['role'] == 'utility:lab'
        assert args['item'] == 'logistic-science-pack'
        backend.calls.append((action, deepcopy(args)))
        amount, item = args['quantity'], args['item']
        entity = backend.state.factory['entities'][args['role']]
        assert backend.state.inventory[item] >= amount
        backend.state.inventory[item] -= amount
        entity['input'][item] = entity['input'].get(item, 0) + amount
        backend.state.factory.setdefault('receipts', {})[args['receipt']] = {
            'role': args['role'], 'unit_number': entity['unit_number'],
            'item': item, 'quantity': amount, 'extracting': False}
        return 'synthetic receipt; verify the next observation'

    backend.execute = transfer
    delivered = 0
    for _ in range(3):
        record = loop.step()
        assert record['verified'] and loop.memory.pending is None, record
        lab = backend.state.factory['entities']['utility:lab']
        delivered += lab['input'].get('logistic-science-pack', 0)
        # Simulate background lab consumption solely inside this fixture. This
        # is not a native science/research or elapsed-throughput measurement.
        lab['input'].clear()
        backend.state.tick += 60
        for name in ('input_routes', 'output_buffers', 'mining_outposts'):
            backend.state.factory[name]['tick'] = backend.state.tick
    assert delivered > 0 and len(backend.calls) == 3
    assert backend.state.inventory['coal'] == 0
    assert loop.memory.failures['retained-attempt'] == 2
    assert loop.memory.outpost_commitments == commitments
