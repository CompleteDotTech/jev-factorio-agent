"""Additional current-main outpost checks, not a second maintenance policy.

PR #120 supplies the correction. These cases retain its five-coal target and
exercise extra rejection, receipt and composition boundaries using fixtures.
They never contact a native game or a provider.
"""
from copy import deepcopy

import pytest

from jev_factorio import mining_outposts as outposts
from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.input_controller import input_loop_type
from jev_factorio.outpost_controller import outpost_loop_type
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.fuel_failure_budget import acquisition_failures
from jev_factorio.planning.fuel_service import service_plan
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.skills import Plan
from test_input_route_integration import controller
from test_outpost_maintenance_progress import (
    completed_source, composed, two_due_outposts, _retain_outposts,
)


def advance(state):
    """Advance only a synthetic observation, never the real campaign clock."""
    state.tick += 1
    for name in ('input_routes', 'output_buffers', 'mining_outposts'):
        if name in state.factory:
            state.factory[name]['tick'] = state.tick


def replanner(state, data):
    planner = MiningOutpostPlanner(data, state, 'rocket_launch')
    planner._set_focus('iron-ore', 20)
    planner.demands['copper-ore'] = 20
    return planner


@pytest.mark.parametrize('fuel', [0, 1, 2])
@pytest.mark.parametrize('coal', [0, 50])
@pytest.mark.parametrize('packs', [0, 20])
def test_background_outpost_preserves_ready_science(tmp_path, fuel, coal, packs):
    _, backend = composed(tmp_path, coal=coal, packs=packs, fuel=fuel)
    kind = outpost_loop_type(input_loop_type(buffered_loop_type(BackgroundWorkLoop)))
    loop = controller(backend, tmp_path, kind=kind)
    backend.state.factory['craft_jobs_protocol'] = 1
    before = deepcopy(backend.state)
    plans, reason = loop._compile_candidates(backend.state)
    action = 'factory_insert' if packs else 'factory_craft_job'
    key = 'item' if packs else 'recipe'
    assert any(plan.steps[0].action == action
               and plan.steps[0].parameters.get(key) == 'logistic-science-pack'
               for plan in plans), reason
    assert backend.state == before and backend.calls == []


@pytest.mark.parametrize('field', ['inventory_insertable', 'fuel_insertable'])
@pytest.mark.parametrize('value', [True, -1, 1.5, float('inf'), float('nan'), '3'])
def test_outpost_rejects_malformed_capacity_hints(field, value):
    state, _, planner = two_due_outposts()
    target = state.factory if field == 'inventory_insertable' else state.factory['entities'][outposts.role('iron-ore', 'drill')]
    target[field] = {'coal': value}
    with pytest.raises(ValueError, match='capacity'):
        planner._need('iron-ore', 20)


@pytest.mark.parametrize('capacity', [0, 1, 3, 4, 100])
def test_optional_headroom_caps_only_the_optional_deficit(capacity):
    state, _, planner = two_due_outposts()
    state.factory['entities'][outposts.role('copper-ore', 'drill')]['fuel_insertable'] = {'coal': capacity}
    plan = planner._need('iron-ore', 20)
    assert plan.steps[0].parameters['quantity'] == 4 + min(4, capacity)
    assert plan.materials['fuel_service']['combined_deficit'] == 4 + min(4, capacity)


def test_consumption_and_capacity_changes_invalidate_prior_service_quantities():
    state, data, planner = two_due_outposts()
    initial = planner._need('iron-ore', 20)
    assert initial.steps[0].parameters['quantity'] == 8
    state.factory['entities'][outposts.role('copper-ore', 'drill')]['fuel']['coal'] = 2
    state.factory['inventory_insertable'] = {'coal': 3}
    advance(state)
    changed = replanner(state, data)._need('iron-ore', 20)
    assert changed.steps[0].parameters['quantity'] == 3
    assert changed.materials['fuel_service']['combined_deficit'] == 4
    assert changed.materials['fuel_service']['observed_tick'] == state.tick


def test_changed_service_quantity_cannot_erase_coal_site_failures():
    state, data, planner = two_due_outposts()
    initial = planner._need('iron-ore', 20)
    failures = {initial.id: 2}
    state.factory['entities'][outposts.role('copper-ore', 'drill')]['fuel']['coal'] = 2
    advance(state)
    changed = replanner(state, data)._need('iron-ore', 20)
    assert changed.id != initial.id
    assert acquisition_failures(changed, failures) == 2
    assert failures == {initial.id: 2}


def test_service_diagnostics_survive_plan_round_trip_without_native_ids():
    _, _, planner = two_due_outposts()
    plan = planner._need('iron-ore', 20)
    assert Plan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()
    assert all('unit_number' not in row for row in plan.materials['fuel_service']['consumers'])


def test_depleted_commissioned_tail_is_collected_without_dead_drill_fuel():
    state, data = completed_source(fuel=0, stored=2)
    state.factory['mining_outposts']['sources']['iron-ore'].update(remaining=0, state='depleted')
    state.inventory['coal'] = 0
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need('iron-ore', 20).steps[0]
    assert step.action == 'factory_extract' and step.parameters['quantity'] == 2
    assert step.allowed(state)


def test_service_accounting_deduplicates_aliases_without_authorizing_fake_topology():
    state, _, planner = two_due_outposts()
    state.inventory['coal'] = 8
    planner.ledger = SupplyLedger.capture(state, planner.catalog)
    native = state.factory['entities'][outposts.role('iron-ore', 'drill')]
    state.factory['entities']['input:accounting-alias'] = deepcopy(native)
    # Isolate arithmetic. This intentionally incomplete extra route cannot
    # authorize an action; the final assertion also tests that distinction.
    state.factory['input_routes']['sources']['accounting-alias'] = {
        'state': 'ready', 'topology': True, 'steps': ['drill'],
        'parts': {'drill': {'role': 'input:accounting-alias'}}}
    plan = service_plan(planner, outposts.role('iron-ore', 'drill'),
                        'accounting-alias', (), planner._acquire_outpost)
    assert plan.materials['fuel_service']['combined_deficit'] == 8
    assert plan.materials['fuel_service']['consumer_count'] == 2
    assert plan.materials['fuel_service']['consumers'][0]['role'] == outposts.role('iron-ore', 'drill')
    assert not plan.steps[0].allowed(state)


@pytest.mark.parametrize('fuel', [0, 1])
@pytest.mark.parametrize('lost_ack', [False, True])
def test_science_receipt_after_lost_ack_is_not_reissued_after_reload(tmp_path, fuel, lost_ack):
    loop, backend = composed(tmp_path, fuel=fuel, coal=0)
    _retain_outposts(loop, backend.state)
    binding = deepcopy(loop.memory.outpost_commitments)
    loop.memory.failures['retained-attempt'] = 2
    observations = []

    def observe():
        advance(backend.state)
        observations.append(backend.state.tick)
        return deepcopy(backend.state)

    def execute(action, args):
        assert action == 'factory_insert' and args['role'] == 'utility:lab'
        assert args['item'] == 'logistic-science-pack'
        backend.calls.append((action, deepcopy(args)))
        entity = backend.state.factory['entities'][args['role']]
        backend.state.inventory[args['item']] -= args['quantity']
        entity['input'][args['item']] = entity['input'].get(args['item'], 0) + args['quantity']
        backend.state.factory['receipts'][args['receipt']] = {
            **args, 'unit_number': entity['unit_number'], 'extracting': False}
        if lost_ack:
            raise TimeoutError('Synthetic lost acknowledgement after persisted receipt')
        return 'Synthetic conserved transfer; verify the next observation'

    backend.observe, backend.execute = observe, execute
    record = loop.step()
    # Exercise actual reader/reconciler reconstruction, not just a second call
    # on the old in-memory loop. The published paid identities are unchanged.
    if not record.get('verified'):
        assert lost_ack
        loop = controller(backend, tmp_path, kind=type(loop), resume=True)
        record = loop.step()
    assert record.get('verified'), record
    assert len(backend.calls) == 1
    assert backend.state.factory['entities']['utility:lab']['input']['logistic-science-pack'] == 20
    assert backend.state.inventory['logistic-science-pack'] == 0
    assert backend.state.inventory['coal'] == 0
    assert loop.memory.pending is None and not loop.memory.reservations
    assert loop.memory.outpost_commitments == binding
    assert loop.memory.failures['retained-attempt'] == 2
    assert len(observations) >= 3 and observations == sorted(set(observations))
    restored = loop.memory_type.from_bytes(loop.checkpoint.read_bytes(), backend.state.session_id, 'rocket_launch')
    assert restored.pending is None and restored.failures['retained-attempt'] == 2
    assert restored.outpost_commitments == binding
