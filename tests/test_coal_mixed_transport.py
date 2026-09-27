"""Mixed coal/downstream ownership: deterministic Python and Lua API doubles only."""
from collections import Counter
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path

import pytest
from lupa.lua52 import LuaRuntime, LuaError

from jev_factorio import coal_supply as coal, solid_routes as solid
from jev_factorio.backends.coal_supply import CoalSupplyFactory
from jev_factorio.backends.solid_routes import SolidRouteFactory
from jev_factorio.coal_controller import coal_loop_type
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.memory import load_checkpoint
from jev_factorio.planning.solid_routes import candidates as solid_candidates
from jev_factorio.skills import Step
from coal_supply_fixtures import TARGETS, INTENTS as COAL_INTENTS
from solid_routes_fixtures import INTENTS as DOWNSTREAM_INTENTS, ROUTE, fixture as downstream, build
from test_coal_supply_integration import Backend as CoalBackend
from test_solid_route_integration import FoundationScenario

ROOT = Path(__file__).resolve().parents[1]
MIXED = COAL_INTENTS + DOWNSTREAM_INTENTS
Loop = coal_loop_type(solid_loop_type(FoundationScenario))


class Backend(CoalBackend):
    def __init__(self):
        super().__init__()
        extra = downstream()
        # Keep synthetic endpoint ownership and geometry disjoint from the coal rows.
        row = extra.factory['solid_routes']['routes'][ROUTE]
        for endpoint in (row['source'], row['target']):
            endpoint['position']['y'] += 100
            for p in endpoint['bounds'].values():
                p['y'] += 100
        for step in row['steps']:
            step['position']['y'] += 100
        for entity in extra.factory['entities'].values():
            entity['position']['y'] += 100
        self.state.factory['entities'].update(extra.factory['entities'])
        self.state.factory['solid_routes']['routes'].update(extra.factory['solid_routes']['routes'])

    def execute(self, action, parameters):
        if action != solid.COMMAND or parameters['route'] != ROUTE:
            return super().execute(action, parameters)
        saved = json.loads(self.checkpoint.read_text())
        assert saved['pending']['dispatch'] in {'prepared', 'ambiguous'}
        assert saved['active_plan']['steps'][0]['parameters'] == parameters
        self.calls.append((action, deepcopy(parameters)))
        build(self.state, parameters)
        self.state.factory['coal_supply']['tick'] = self.state.tick
        self.post_dispatch()
        if self.lost_ack:
            raise TimeoutError('Modeled downstream acknowledgement loss after payment')
        return 'modeled paid downstream component'


def controller(backend, path, *, intents=MIXED, resume=False, kind=Loop, **options):
    backend.checkpoint = path / 'mixed.json'
    loop = kind(backend, target='rocket_launch', policy='deterministic',
                factory_scheduling='ready-work', tick_seconds=0,
                checkpoint=str(backend.checkpoint), resume_controller=resume,
                solid_intents=deepcopy(intents), coal_targets=TARGETS, **options)
    if not resume:
        loop.memory = loop.memory_type(backend.state.session_id, 'rocket_launch', active_goal='iron_smelting',
            completed_goals={'stockpile_fuel': 0, 'bootstrap_mining': 0}, last_tick=backend.state.tick)
    return loop


def native(*, configure=True, intents=MIXED):
    lua = LuaRuntime(unpack_returned_tuples=True)
    for name in ('tests/fixtures/solid_routes_runtime.lua', 'tests/fixtures/coal_supply_runtime.lua',
                 'src/jev_factorio/lua/solid_routes.lua', 'src/jev_factorio/lua/coal_supply.lua'):
        lua.execute((ROOT / name).read_text())
    lua.execute('''
        local a,b=source.recipe,target.recipe
        source=entity("assembling-machine-1","assembling-machine",50.5,.5,1.4);source.recipe=a
        target=entity("assembling-machine-1","assembling-machine",57.5,.5,1.4);target.recipe=b
        source.output.values["iron-gear-wheel"]=20
        pole=entity("small-electric-pole","electric-pole",53.5,3.5,.2)
        campaign.entities["recipe:iron-gear-wheel"]=source
        campaign.entities["recipe:automation-science-pack"]=target
        campaign.entities["utility:downstream-power"]=pole
        function downstream_offer()
            for _,row in pairs(campaign.observe().solid_routes.routes) do
                if row.item=="iron-gear-wheel" then return row end
            end
            error("Missing modeled downstream offer")
        end
        function downstream_args(part)
            local row=downstream_offer();part=part or row.steps[1].part
            return {route=row.route,layout=row.layout,part=part,receipt="mixed:"..part}
        end
        function downstream_build()
            local row=downstream_offer()
            for _,spec in ipairs(row.steps) do
                local p=downstream_args(spec.part)
                campaign.prepare_solid_route(p);campaign.build_solid_route(p)
            end
            return campaign.observe()
        end
    ''')
    lua.globals().mixed_intents = lua.table_from(deepcopy(intents), recursive=True)
    if configure:
        lua.execute('campaign.set_solid_intents(mixed_intents);campaign.set_coal_targets({"alpha","beta"})')
    return lua


@pytest.mark.parametrize('order', list(permutations(range(3))))
def test_python_and_native_accept_disjoint_mixed_intents_in_any_fixed_order(tmp_path, order):
    intents = [MIXED[i] for i in order]
    backend = Backend(); loop = controller(backend, tmp_path, intents=intents)
    snapshot = loop._observe()
    assert not loop._execution_barrier(snapshot)
    plans, _ = loop._compile_candidates(snapshot)
    assert any(p.steps[0].action == coal.COMMAND for p in plans)
    assert any(p.steps[0].parameters.get('route') == ROUTE for p in plans)
    lua = native(intents=intents)
    assert lua.eval('#campaign.observe().coal_supply.targets') == 2
    assert lua.eval('paid_calls') == 0
    assert lua.eval('downstream_offer().item') == 'iron-gear-wheel'


def test_adapter_validates_mixed_attachment_before_mutating_native_configuration():
    class Native:
        def __init__(self): self.commands=[]; self.calls=[]
        def command(self, text): self.commands.append(text)
        def call(self, name, *args): self.calls.append((name, args))
    base = Native(); transport = SolidRouteFactory(base, MIXED)
    adapter = CoalSupplyFactory(transport, TARGETS)
    assert adapter.targets == TARGETS
    assert [name for name, _ in base.calls] == ['set_solid_intents', 'set_coal_targets']


def test_both_paid_networks_finish_and_resume_without_duplicate_payment(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    total = 4 + sum(2 + len(r['corridor']) for r in coal.sources(backend.state).values())
    for _ in range(total):
        record = loop.step()
        assert record['verified'], record
    assert len(backend.calls) == total
    assert len(loop.memory.solid_commitments) == 3
    assert all(len(row['parts']) == 2 for row in loop.memory.coal_commitments.values())
    saved = load_checkpoint(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert saved.solid_intents == MIXED
    resumed = controller(backend, tmp_path, resume=True); resumed.step()
    assert len(backend.calls) == total
    assert not coal.flow_complete(backend.state)  # Building is not mined/consumed flow.


def test_exact_combined_remaining_kit_does_not_double_reserve_own_downstream_project(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    # Deterministic foundation candidates start the explicit downstream route first.
    record = loop.step()
    assert record['verified'] and backend.calls[0][1]['route'] == ROUTE
    state = loop._observe()
    plan = next(p for p in solid_candidates(state, 'iron_smelting') if p.steps[0].parameters['route'] == ROUTE)
    own = solid.remaining(solid.routes(state)[ROUTE])
    state.inventory.update(own)
    assert loop._step_allowed(plan.steps[0], state)
    for item in own:
        broken = deepcopy(state); broken.inventory[item] -= 1
        assert not loop._step_allowed(plan.steps[0], broken)


def test_paid_coal_bundle_is_in_downstream_reservations_and_blocks_shared_kit_spending(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    # Suppress only the optional downstream offer so the next authoritative step is coal.
    extra = backend.state.factory['solid_routes']['routes'].pop(ROUTE)
    loop.step()
    backend.state.factory['solid_routes']['routes'][ROUTE] = extra
    state = loop._observe(); locked = coal.remaining_kit(coal.sources(state), state)
    assert all(loop._solid_reservations().get(item, 0) >= count for item, count in locked.items())
    offer = next(p for p in solid_candidates(state, 'iron_smelting') if p.steps[0].parameters['route'] == ROUTE)
    state.inventory.update(locked)
    assert not loop._step_allowed(offer.steps[0], state)
    state.inventory.update(dict(Counter(locked) + Counter(solid.remaining(extra))))
    assert loop._step_allowed(offer.steps[0], state)


@pytest.mark.parametrize('change', ['remove', 'reorder', 'target'])
def test_resume_cannot_change_mixed_treatment_before_backend_attachment(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path); loop.step()
    saved = backend.checkpoint.read_bytes(); before = backend.enabled
    intents = deepcopy(MIXED)
    if change == 'remove': intents.pop()
    elif change == 'reorder': intents.reverse()
    else: intents[-1]['target'] = 'new:target'
    with pytest.raises(ValueError): controller(backend, tmp_path, intents=intents, resume=True)
    assert backend.enabled == before and backend.checkpoint.read_bytes() == saved


def test_lua_builds_and_observes_both_treatments_without_crediting_construction_as_flow():
    lua = native(); lua.execute('downstream_build();coal_all();result=campaign.observe()')
    assert lua.eval('result.coal_supply.sources.alpha.flow.delivered_lower') == 0
    assert lua.eval('result.coal_supply.sources.beta.flow.delivered_lower') == 0
    lua.execute('coal_pulse();pulse();coal_pulse();pulse();coal_pulse();pulse();result=campaign.observe()')
    assert lua.eval('result.coal_supply.sources.alpha.flow.delivered_lower') > 0
    assert lua.eval('downstream_offer().flow.received') == 3


def test_lua_downstream_cannot_spend_the_committed_coal_kit():
    lua = native(); lua.execute('coal_build("alpha","chest");p=downstream_args();stock.inserter=4;before=paid_calls')
    with pytest.raises(LuaError): lua.execute('campaign.prepare_solid_route(p)')
    assert lua.eval('paid_calls') == lua.eval('before')
    lua.execute('stock.inserter=6;campaign.prepare_solid_route(p);campaign.build_solid_route(p)')
    assert lua.eval('paid_calls') == lua.eval('before') + 1


def test_lua_coal_cannot_spend_a_paid_downstream_prefix_kit():
    lua = native(); lua.execute('p=downstream_args();campaign.prepare_solid_route(p);campaign.build_solid_route(p);'
        'stock.inserter=4;before=paid_calls')
    with pytest.raises(LuaError): lua.execute('coal_build("alpha","chest")')
    assert lua.eval('paid_calls') == lua.eval('before')
    lua.execute('stock.inserter=5;coal_build("alpha","chest")')
    assert lua.eval('paid_calls') == lua.eval('before') + 1


def test_lua_pending_coal_action_blocks_unrelated_downstream_placement():
    lua = native(); lua.execute('p=coal_args("alpha","chest");campaign.prepare_coal_source(p);d=downstream_args()')
    with pytest.raises(LuaError): lua.execute('campaign.prepare_solid_route(d)')
    assert lua.eval('paid_calls') == 0


def test_lua_old_implementation_cannot_be_silently_reattached():
    lua = native(); lua.execute('storage.coal_supply.revision=1')
    with pytest.raises(LuaError): lua.execute((ROOT/'src/jev_factorio/lua/coal_supply.lua').read_text())


@pytest.mark.parametrize('change', ['missing', 'missing_all', 'reused_role', 'extra_fuel',
    'coal_source_name', 'coal_target_name', 'too_many', 'wrong_consumer_item'])
def test_invalid_mixed_bindings_fail_before_payment_in_python_and_lua(tmp_path, change):
    intents = deepcopy(MIXED)
    if change == 'missing': intents.pop(0)
    elif change == 'missing_all': intents = intents[2:]
    elif change == 'reused_role': intents[-1]['source'] = intents[0]['target']
    elif change == 'extra_fuel': intents[-1].update(item='coal', destination='fuel')
    elif change == 'coal_source_name': intents[-1]['source'] = 'coal:invented:chest'
    elif change == 'coal_target_name': intents[-1]['target'] = 'coal:invented:drill'
    elif change == 'too_many':
        intents += [dict(source=f'extra:{i}:source', target=f'extra:{i}:target',
                         item='iron-plate', destination='input') for i in (1, 2)]
    else: intents[0]['item'] = 'iron-plate'
    backend = Backend()
    with pytest.raises(ValueError): controller(backend, tmp_path, intents=intents)
    assert backend.enabled == 0 and not backend.calls
    lua = native(configure=False, intents=intents)
    with pytest.raises(LuaError):
        lua.execute('campaign.set_solid_intents(mixed_intents);campaign.set_coal_targets({"alpha","beta"})')
    assert lua.eval('paid_calls') == 0


@pytest.mark.parametrize('coal_first', [False, True])
def test_mixed_paid_ack_loss_resumes_without_repaying_either_project(tmp_path, coal_first):
    backend = Backend(); loop = controller(backend, tmp_path)
    extra = backend.state.factory['solid_routes']['routes'].pop(ROUTE) if coal_first else None
    backend.lost_ack = True
    result = loop.step()
    assert not result['verified'] and len(backend.calls) == 1
    if extra is not None: backend.state.factory['solid_routes']['routes'][ROUTE] = extra
    backend.lost_ack = False
    resumed = controller(backend, tmp_path, resume=True)
    result = resumed.step()
    assert result['verified'], result
    assert len(backend.calls) == 1 and resumed.memory.pending is None
    assert resumed.memory.attempt_outcomes[-1]['outcome'] == 'verified'


def test_mixed_frontier_keeps_ready_science_and_compiles_parent_once(tmp_path):
    from jev_factorio.skills import Plan
    class Production(FoundationScenario):
        compiled = 0
        def _compile_candidates(self, snapshot):
            self.compiled += 1
            return [Plan('science:ready', 'iron_smelting', 'Collect ready science',
                (Step('factory_extract', 'transfer', parameters={'role':'science:producer',
                 'item':'automation-science-pack','quantity':1,'receipt':'science:ready:1'}),))], ''
    backend = Backend()
    backend.state.factory['entities']['science:producer'] = dict(unit_number=9999,
        name='assembling-machine-1', position={'x':200,'y':200}, output={'automation-science-pack':4})
    loop = controller(backend, tmp_path, kind=coal_loop_type(solid_loop_type(Production)))
    plans, _ = loop._compile_candidates(loop._observe())
    assert loop.compiled == 1 and any(p.id == 'science:ready' for p in plans)
    assert any(p.steps[0].action == coal.COMMAND for p in plans)
    assert any(p.steps[0].parameters.get('route') == ROUTE for p in plans)


@pytest.mark.parametrize('change', ['replace', 'delete', 'raise', 'interrupt'])
@pytest.mark.parametrize('pending', [False, True])
def test_mixed_resume_retains_the_complete_observation_publication_barrier(tmp_path, pending, change):
    # Exercise the same boundary as #121 with the actual mixed checkpoint schema.
    from test_coal_resume_transaction import ObservedCoalLoop
    backend = Backend()
    loop = controller(backend, tmp_path, kind=ObservedCoalLoop)
    if pending:
        backend.lost_ack = True; loop.step(); backend.lost_ack = False
    else:
        loop.memory.active_goal = "rocket_launch"
        loop._observe()
    original = backend.checkpoint.read_bytes(); calls = deepcopy(backend.calls)
    external = json.loads(original); external['failures']['external-writer'] = 3
    external_bytes = json.dumps(external).encode()
    resumed = controller(backend, tmp_path, resume=True, kind=ObservedCoalLoop)
    def failed(self):
        self.memory.failures['provisional-mixed'] = 1
        if change == 'replace': backend.checkpoint.write_bytes(external_bytes)
        elif change == 'delete': backend.checkpoint.unlink()
        self._save()
        if change == 'raise': raise RuntimeError('modeled mixed validation failure')
        if change == 'interrupt': raise KeyboardInterrupt('modeled mixed interruption')
    resumed.after_coal = failed
    with pytest.raises((ValueError, OSError, RuntimeError, KeyboardInterrupt)): resumed._observe()
    if change == 'delete': assert not backend.checkpoint.exists()
    else: assert backend.checkpoint.read_bytes() == (external_bytes if change == 'replace' else original)
    assert backend.calls == calls and resumed.memory is None and resumed._persistence_failed


@pytest.mark.parametrize('consumer_count', [2, 3])
def test_four_route_bound_supports_two_plus_two_and_three_plus_one(consumer_count):
    targets = ['alpha', 'beta', 'gamma'][:consumer_count]
    intents = coal.intents(targets) + deepcopy(DOWNSTREAM_INTENTS)
    if consumer_count == 2:
        intents.append(dict(source='downstream:gears', target='downstream:science',
                            item='iron-gear-wheel', destination='input'))
    assert len(coal.validate_transport_intents(targets, intents)) == 4
    lua = native(configure=False, intents=intents)
    lua.globals().target_names = lua.table_from(targets)
    lua.globals().consumer_count = consumer_count
    lua.execute('''
        if consumer_count==3 then
            campaign.entities.gamma=coal_burner(10,24);coal_resource(3.5,24.5)
            campaign.entities["extra-power-3"]=entity("small-electric-pole","electric-pole",7.5,28.5,.2)
            campaign.entities["extra-witness-3"]=entity("assembling-machine-1","assembling-machine",6.5,30.5,1.4)
        else
            local a=entity("assembling-machine-1","assembling-machine",50.5,20.5,1.4)
            local b=entity("assembling-machine-1","assembling-machine",57.5,20.5,1.4)
            a.recipe=source.recipe;b.recipe=target.recipe;a.output.values["iron-gear-wheel"]=20
            campaign.entities["downstream:gears"]=a;campaign.entities["downstream:science"]=b
            campaign.entities["downstream:power"]=entity("small-electric-pole","electric-pole",53.5,23.5,.2)
        end
        campaign.set_solid_intents(mixed_intents);campaign.set_coal_targets(target_names)
        for _,row in pairs(campaign.observe().solid_routes.routes) do
            for _,spec in ipairs(row.steps) do
                local p={route=row.route,layout=row.layout,part=spec.part,receipt=row.route..":"..spec.part}
                campaign.prepare_solid_route(p);campaign.build_solid_route(p)
            end
        end
        for _,name in ipairs(target_names) do coal_build(name,"chest") end
        for _,name in ipairs(target_names) do coal_build_corridor(name) end
        for _,name in ipairs(target_names) do coal_build(name,"drill") end
        result=campaign.observe();routes=0
        for _,row in pairs(result.solid_routes.routes) do assert(row.state=="ready");routes=routes+1 end
        assert(routes==4)
        for _,row in pairs(result.coal_supply.sources) do assert(row.flow.delivered_lower==0) end
    ''')
