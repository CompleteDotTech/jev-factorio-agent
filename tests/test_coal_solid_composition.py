"""Mixed treatment regressions. API doubles/paid-state fixtures, NOT engine flow."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import pytest
from lupa.lua52 import LuaRuntime, LuaError

from jev_factorio import coal_supply as coal, solid_routes as solid
from jev_factorio.coal_controller import coal_loop_type
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.planning import solid_investment
from jev_factorio.planning.coal_supply import candidates as coal_candidates
from jev_factorio.planning.solid_routes import candidates as solid_candidates
from jev_factorio.skills import Plan, Step
from coal_supply_fixtures import TARGETS, INTENTS as COAL_INTENTS, fixture, paid_source
from solid_routes_fixtures import INTENTS as DOWNSTREAM_INTENTS, ROUTE, SOURCE, TARGET
from test_coal_supply_integration import Backend as CoalBackend
from test_solid_route_integration import FoundationScenario
from test_solid_investment import scenario, service_history

INTENTS = COAL_INTENTS + DOWNSTREAM_INTENTS
ROOT = Path(__file__).resolve().parents[1]
Loop = coal_loop_type(solid_loop_type(FoundationScenario))


def shifted(value):
    if isinstance(value, dict):
        if set(value) == {'x', 'y'}:
            return {'x': value['x'], 'y': value['y'] + 100}
        return {k: shifted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shifted(v) for v in value]
    return deepcopy(value)


def mixed_state():
    state = fixture()
    downstream, data = scenario()
    state.factory['entities'].update(shifted(downstream.factory['entities']))
    state.factory.update(research='study', research_progress=0)
    state.factory['solid_routes']['routes'][ROUTE] = shifted(downstream.factory['solid_routes']['routes'][ROUTE])
    state.factory['solid_routes']['diagnostics'].append(
        {'intent_index': 3, 'state': 'proposed', 'reason': 'ready_layout'})
    history = service_history(state)
    state.factory['coal_supply']['tick'] = state.tick
    kit = Counter(coal.remaining_kit(coal.sources(state), state))
    kit.update(solid.remaining(solid.routes(state)[ROUTE]))
    state.inventory = {**dict(kit), 'coal': 20}
    return state, data, history


def paid_solid(state, parameters):
    """Exact one-item debit and unique identity; no production/flow is invented."""
    assert solid.allowed(parameters, state)
    row = solid.routes(state)[parameters['route']]
    spec = next(s for s in row['steps'] if s['part'] == parameters['part'])
    unit = max(e['unit_number'] for e in state.factory['entities'].values()) + 1
    role = row['route'] + ':' + spec['part']
    row['parts'][spec['part']] = {'role': role, 'unit_number': unit,
                                'receipt': parameters['receipt'], 'paid': 1}
    state.factory['entities'][role] = {'unit_number': unit, 'name': spec['name'],
                                     'position': deepcopy(spec['position'])}
    state.inventory[spec['name']] -= 1
    row['pending'] = {}
    row['state'] = 'ready' if len(row['parts']) == len(row['steps']) else 'building'
    row['topology'] = row['state'] == 'ready'
    if row['target']['inventory'] == 'fuel':
        state.factory['coal_supply']['sources'][row['target']['role']]['route'] = row['route']


class Backend(CoalBackend):
    def __init__(self):
        super().__init__()
        self.state, self.data, self.history = mixed_state()

    def enable_factory(self):
        self.enabled += 1
        return self.data

    def execute(self, action, parameters):
        if action != solid.COMMAND or parameters['route'] != ROUTE:
            return super().execute(action, parameters)
        saved = json.loads(self.checkpoint.read_text())
        assert saved['pending']['action'] == action
        assert saved['pending']['dispatch'] in {'prepared', 'ambiguous'}
        assert saved['active_plan']['steps'][0]['parameters'] == parameters
        self.calls.append((action, deepcopy(parameters)))
        paid_solid(self.state, parameters)
        self.post_dispatch()
        if self.lost_ack:
            raise TimeoutError('fixture acknowledgement loss after paid downstream receipt')
        return 'fixture paid component, not native flow'


def controller(backend, path, *, resume=False, intents=INTENTS, kind=Loop):
    backend.checkpoint = path / 'mixed-checkpoint.json'
    loop = kind(backend, target='rocket_launch', policy='deterministic',
                factory_scheduling='ready-work', tick_seconds=0,
                checkpoint=str(backend.checkpoint), resume_controller=resume,
                solid_intents=intents, coal_targets=TARGETS, solid_science_policy=True)
    if not resume:
        loop.memory = loop.memory_type(backend.state.session_id, 'rocket_launch',
            active_goal='rocket_launch', completed_goals={'stockpile_fuel': 0, 'bootstrap_mining': 0},
            last_tick=backend.state.tick)
        loop.memory.attempt_outcomes = deepcopy(backend.history)
    return loop


def next_downstream(state):
    return next(p for p in solid_candidates(state, 'rocket_launch')
                if p.steps[0].parameters['route'] == ROUTE)


def commit_coal(state, memory):
    plan = coal_candidates(state, 'rocket_launch')[0]
    paid_source(state, plan.steps[0].parameters)
    memory.coal_commitments = {key: coal.commitment(row) for key, row in coal.sources(state).items()}
    return plan


def choose_downstream(plans):
    return next(p for p in plans if p.steps[0].parameters.get('route') == ROUTE)


def test_mixed_constructor_preserves_both_immutable_intent_sets(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    state = loop._observe()
    assert not loop._execution_barrier(state)
    assert loop.memory.solid_intents == INTENTS
    assert loop.memory.coal_targets == TARGETS
    assert set(coal.sources(state)) == set(TARGETS)
    assert ROUTE in solid.routes(state)


@pytest.mark.parametrize('bad', [
    COAL_INTENTS[:1] + DOWNSTREAM_INTENTS,
    COAL_INTENTS + [dict(source='extra', target='consumer', item='coal', destination='fuel')],
    COAL_INTENTS + [dict(source='coal:other:chest', target='recipe:other', item='coal', destination='input')],
    COAL_INTENTS + [dict(source=SOURCE, target='alpha', item='iron-gear-wheel', destination='input')],
    INTENTS + [dict(source=f's{i}', target=f't{i}', item='iron-plate', destination='input') for i in range(2)],
])
def test_invalid_mixed_configuration_fails_before_native_attachment(tmp_path, bad):
    backend = Backend()
    with pytest.raises(ValueError):
        controller(backend, tmp_path, intents=bad)
    assert backend.enabled == 0 and backend.calls == []
    assert not (tmp_path / 'mixed-checkpoint.json').exists()


def test_paid_coal_corridor_is_not_the_downstream_policy_project():
    state, data, history = mixed_state()
    plan = coal_candidates(state, 'rocket_launch')[0]
    paid_source(state, plan.steps[0].parameters)
    corridor = next(p for p in solid_candidates(state, 'rocket_launch')
                    if p.steps[0].parameters['route'] != ROUTE)
    paid_solid(state, corridor.steps[0].parameters)
    offers, diagnostics = solid_investment.candidates(state, data, 'rocket_launch', outcomes=history)
    assert ROUTE in {p.steps[0].parameters['route'] for p in offers}, diagnostics
    assert all(p.steps[0].parameters['route'] == ROUTE for p in offers)


def test_committed_coal_kit_is_locked_in_downstream_valuation(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    state = loop._observe(); commit_coal(state, loop.memory)
    held = Counter(loop._solid_reservations())
    expected = Counter(coal.remaining_kit(coal.sources(state), state))
    assert held == expected


def test_exact_combined_kit_does_not_double_reserve_active_downstream(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    state = loop._observe(); commit_coal(state, loop.memory)
    plan = next_downstream(state); paid_solid(state, plan.steps[0].parameters)
    loop.memory.solid_commitments[ROUTE] = solid.commitment(solid.routes(state)[ROUTE])
    step = next_downstream(state).steps[0]
    assert loop._step_allowed(step, state)
    state.inventory['inserter'] -= 1
    assert not loop._step_allowed(step, state)


def test_ordinary_actions_cannot_spend_committed_coal_drill(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    state = loop._observe(); commit_coal(state, loop.memory)
    step = Step('factory_craft', 'inventory', item='electric-mining-drill', threshold=1,
                costs={'electric-mining-drill': 1}, parameters={'recipe':'electric-mining-drill','batches':1})
    assert not loop._step_allowed(step, state)


def test_sequential_mixed_receipts_resume_and_zero_duplicate_payment(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    # Deterministic selection is controlled only among the fully admitted frontier.
    loop._fallback_plan = lambda plans: next(p for p in plans if p.steps[0].action == coal.COMMAND)
    first = loop.step(); assert first['verified'], first
    assert loop.memory.coal_commitments
    loop._fallback_plan = choose_downstream
    backend.lost_ack = True
    second = loop.step(); assert not second['verified'], second
    assert len(backend.calls) == 2
    before = deepcopy(backend.state.inventory)
    backend.lost_ack = False
    resumed = controller(backend, tmp_path, resume=True)
    record = resumed.step(); assert record['verified'], record
    assert len(backend.calls) == 2 and backend.state.inventory == before
    resumed._fallback_plan = choose_downstream
    for _ in range(3):
        record = resumed.step(); assert record['verified'], record
    assert len(backend.calls) == 5
    assert solid.routes(backend.state)[ROUTE]['state'] == 'ready'
    assert not solid.flow_complete(ROUTE, solid.routes(backend.state)[ROUTE]['layout'], backend.state)
    assert not coal.flow_complete(backend.state)
    assert all(value >= 0 for value in backend.state.inventory.values())
    restored = Loop.memory_type._from_data(json.loads(backend.checkpoint.read_text()), backend.state.session_id, 'rocket_launch')
    assert restored.solid_commitments == resumed.memory.solid_commitments
    assert restored.coal_commitments == resumed.memory.coal_commitments


def test_coal_only_checkpoint_cannot_gain_downstream_on_resume(tmp_path):
    # Treatment identity is not a migration or permission to extend a campaign.
    backend = CoalBackend(); backend.checkpoint = tmp_path / 'mixed-checkpoint.json'
    loop = Loop(backend, target='rocket_launch', policy='deterministic', tick_seconds=0,
                factory_scheduling='ready-work',
                checkpoint=str(backend.checkpoint), solid_intents=COAL_INTENTS,
                coal_targets=TARGETS, solid_science_policy=True)
    loop._observe()
    before = backend.checkpoint.read_bytes()
    enabled = backend.enabled
    with pytest.raises(ValueError):
        controller(backend, tmp_path, resume=True)
    assert backend.checkpoint.read_bytes() == before and backend.enabled == enabled


def native_runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for file in ('tests/fixtures/solid_routes_runtime.lua', 'tests/fixtures/coal_supply_runtime.lua',
                 'src/jev_factorio/lua/solid_routes.lua', 'src/jev_factorio/lua/coal_supply.lua'):
        lua.execute((ROOT / file).read_text())
    lua.execute('''mixed_intents={
      {source="coal:alpha:chest",target="alpha",item="coal",destination="fuel"},
      {source="coal:beta:chest",target="beta",item="coal",destination="fuel"},
      {source="recipe:gears",target="recipe:science",item="iron-gear-wheel",destination="input"}}
      campaign.set_solid_intents(mixed_intents)''')
    return lua


def test_lua_accepts_bounded_mixed_treatment_and_rejects_live_migration():
    lua = native_runtime()
    lua.execute('campaign.set_coal_targets({"alpha","beta"});coal_build("alpha","chest");assert(paid_calls==1)')
    for file in ('solid_routes.lua', 'coal_supply.lua'):
        lua.execute((ROOT / 'src/jev_factorio/lua' / file).read_text())
    lua.execute('campaign.set_solid_intents(mixed_intents);campaign.set_coal_targets({"alpha","beta"});assert(paid_calls==1)')
    with pytest.raises(LuaError):
        lua.execute('table.remove(mixed_intents);campaign.set_solid_intents(mixed_intents)')
    assert lua.eval('paid_calls') == 1


@pytest.mark.parametrize('coal_count,input_count', [(2, 0), (2, 1), (2, 2), (3, 1), (4, 0)])
def test_total_route_bound_and_detached_configuration(coal_count, input_count):
    targets = [f'consumer{i}' for i in range(coal_count)]
    configured = coal.intents(targets) + [dict(source=f'source{i}', target=f'target{i}',
        item='iron-plate', destination='input') for i in range(input_count)]
    validated = coal.validate_transport_intents(targets, configured)
    assert validated == configured and len(validated) <= solid.MAX_ROUTES
    validated[0]['target'] = 'changed-copy'
    assert configured[0]['target'] == targets[0]


@pytest.mark.parametrize('coal_count,input_count', [(2, 3), (3, 2), (4, 1)])
def test_total_route_bound_not_per_capability(coal_count, input_count):
    targets = [f'consumer{i}' for i in range(coal_count)]
    configured = coal.intents(targets) + [dict(source=f's{i}', target=f't{i}',
        item='iron-plate', destination='input') for i in range(input_count)]
    with pytest.raises(ValueError):
        coal.validate_transport_intents(targets, configured)


@pytest.mark.parametrize('paid_corridor_parts', [0, 1, 2, 3])
def test_coal_reservations_count_each_remaining_component_once(tmp_path, paid_corridor_parts):
    backend = Backend(); loop = controller(backend, tmp_path)
    state = loop._observe(); commit_coal(state, loop.memory)
    key = next(key for key in solid.routes(state) if key != ROUTE)
    for _ in range(paid_corridor_parts):
        plan = next(p for p in solid_candidates(state, 'rocket_launch') if p.steps[0].parameters['route'] == key)
        paid_solid(state, plan.steps[0].parameters)
        loop.memory.solid_commitments[key] = solid.commitment(solid.routes(state)[key])
    assert Counter(loop._solid_reservations()) == Counter(coal.remaining_kit(coal.sources(state), state))


def test_useful_frontier_is_compiled_once_and_survives_both_project_types(tmp_path):
    class Production(FoundationScenario):
        compiled = 0
        def _compile_candidates(self, snapshot):
            self.compiled += 1
            return [Plan('science:ready', 'rocket_launch', 'Collect ready science',
                (Step('factory_extract', 'transfer', parameters={'role':'science:producer',
                    'item':'automation-science-pack', 'quantity':1, 'receipt':'science:ready:1'}),))], ''
    kind = coal_loop_type(solid_loop_type(Production))
    backend = Backend()
    backend.state.factory['entities']['science:producer'] = dict(unit_number=9000,
        name='assembling-machine-1', position={'x': 200, 'y': 200}, output={'automation-science-pack':4})
    loop = controller(backend, tmp_path, kind=kind); state = loop._observe()
    plans, _ = loop._compile_candidates(state)
    assert loop.compiled == 1
    assert len([p for p in plans if p.id == 'science:ready']) == 1
    assert any(p.steps[0].action == coal.COMMAND for p in plans)
    assert any(p.steps[0].parameters.get('route') == ROUTE for p in plans)
    assert len({p.id for p in plans}) == len(plans)


def test_fresh_kit_loss_refuses_selected_downstream_before_payment(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop._fallback_plan = choose_downstream
    def change():
        if backend.observations == 2:
            backend.state.inventory['transport-belt'] = 0
    backend.before_observe = change
    record = loop.step()
    assert not record['verified'] and backend.calls == []
    assert solid.routes(backend.state)[ROUTE]['parts'] == {}


@pytest.mark.parametrize('change', [
    lambda d: d['solid_intents'].pop(),
    lambda d: d['solid_intents'].reverse(),
    lambda d: d['solid_intents'][2].update(target='another-owned-role'),
    lambda d: d['coal_targets'].reverse(),
    lambda d: d['coal_commitments'].pop('beta'),
])
def test_mixed_resume_rejects_altered_treatment_or_lost_ownership_without_publication(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop._fallback_plan = lambda plans: next(p for p in plans if p.steps[0].action == coal.COMMAND)
    assert loop.step()['verified']
    saved = json.loads(backend.checkpoint.read_text()); change(saved)
    backend.checkpoint.write_text(json.dumps(saved))
    captured, enabled, calls = backend.checkpoint.read_bytes(), backend.enabled, len(backend.calls)
    with pytest.raises(ValueError):
        controller(backend, tmp_path, resume=True)
    assert backend.checkpoint.read_bytes() == captured
    assert backend.enabled == enabled and len(backend.calls) == calls


def test_changed_paid_identity_blocks_both_frontiers_and_preserves_history(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    loop._fallback_plan = lambda plans: next(p for p in plans if p.steps[0].action == coal.COMMAND)
    assert loop.step()['verified']
    old = deepcopy(loop.memory.coal_commitments)
    loop.memory.failures['retained-failure-id'] = 2
    backend.state.factory['entities']['coal:alpha:chest']['unit_number'] += 100
    loop.step()
    assert loop.memory.status == 'uncertain' and len(backend.calls) == 1
    assert loop.memory.coal_commitments == old
    assert loop.memory.failures['retained-failure-id'] == 2


@pytest.mark.parametrize('change', [
    'mixed_intents[3].destination="fuel";mixed_intents[3].item="coal"',
    'mixed_intents[3].source="coal:extra:chest"',
    'mixed_intents[3].target="coal:extra:drill"',
    'mixed_intents[3].item="coal"',
])
def test_lua_mixed_suffix_rejected_before_coal_binding_or_payment(change):
    lua = LuaRuntime(unpack_returned_tuples=True)
    for file in ('tests/fixtures/solid_routes_runtime.lua', 'tests/fixtures/coal_supply_runtime.lua',
                 'src/jev_factorio/lua/solid_routes.lua', 'src/jev_factorio/lua/coal_supply.lua'):
        lua.execute((ROOT / file).read_text())
    lua.execute('''mixed_intents={
      {source="coal:alpha:chest",target="alpha",item="coal",destination="fuel"},
      {source="coal:beta:chest",target="beta",item="coal",destination="fuel"},
      {source="recipe:gears",target="recipe:science",item="iron-gear-wheel",destination="input"}}''')
    lua.execute(change + ';campaign.set_solid_intents(mixed_intents)')
    with pytest.raises(LuaError):
        lua.execute('campaign.set_coal_targets({"alpha","beta"})')
    assert lua.eval('storage.coal_supply.binding') is None
    assert lua.eval('paid_calls') == 0


def test_real_extension_lua_builds_both_coal_branches_and_disjoint_downstream():
    lua = native_runtime()
    lua.execute('''
      gear=entity("assembling-machine-1","assembling-machine",.5,100.5,1.4)
      science=entity("assembling-machine-1","assembling-machine",7.5,100.5,1.4)
      gear.recipe=source.recipe;science.recipe=target.recipe
      gear.output.values["iron-gear-wheel"]=120;science.input.values["copper-plate"]=120
      power=entity("small-electric-pole","electric-pole",3.5,103.5,.2)
      campaign.entities["recipe:gears"]=gear;campaign.entities["recipe:science"]=science
      campaign.entities["mixed:power"]=power
      campaign.set_coal_targets({"alpha","beta"})
      coal_all()
      local values=campaign.observe().solid_routes.routes
      for _,row in pairs(values) do if row.target.role=="recipe:science" then downstream=row end end
      assert(downstream and downstream.state=="proposed", "Missing disjoint downstream offer")
      original_calls=paid_calls
      for _,s in ipairs(downstream.steps) do
        local p={route=downstream.route,layout=downstream.layout,part=s.part,receipt="mixed:"..s.part}
        campaign.prepare_solid_route(p);campaign.build_solid_route(p)
      end
      result=campaign.observe()
      assert(paid_calls==original_calls+#downstream.steps)
      assert(result.solid_routes.routes[downstream.route].state=="ready")
      assert(#result.solid_routes.diagnostics==3)
      assert(result.coal_supply.sources.alpha.parts.drill.paid==1)
      assert(result.coal_supply.sources.beta.parts.drill.paid==1)
      for _,n in pairs(stock) do assert(n>=0) end
    ''')
    # Native Lua control flow in a modeled API is not a real engine/network run.
    assert lua.eval('result.solid_routes.routes[downstream.route].flow.received') == 0
