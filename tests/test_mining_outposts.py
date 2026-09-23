"""Synthetic contracts and actual Lua builder tests; no native throughput claims."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from jev_factorio import mining_outposts as outposts
from jev_factorio.backends.mining_outposts import MiningOutpostFactory
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.input_controller import input_loop_type
from jev_factorio.memory import load_checkpoint
from jev_factorio.outpost_controller import outpost_loop_type
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.skills import Plan, Step
from test_factory import catalog, snapshot, machine, recipe

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'src/jev_factorio/lua'
RESOURCE = 'iron-ore'


def state_fixture():
    state = snapshot(tick=300, inventory={'burner-mining-drill': 1, 'wooden-chest': 1, 'coal': 50})
    state.factory['entities']['recipe:iron-plate'] = machine(
        recipe='iron-plate', fuel={'coal': 50}, products_finished=100, crafting=False)
    for key in ('input_routes', 'output_buffers', 'mining_outposts'):
        state.factory[key] = dict(protocol=1, session_id=state.session_id, tick=state.tick, sources={})
    state.factory['mining_outposts']['sources'][RESOURCE] = dict(resource=RESOURCE, layout='outpost:iron-ore:1',
        surface_index=1, force_index=1, state='proposed', topology=False, remaining=2000, parts={}, flow={}, steps=[
            dict(part='chest', name='wooden-chest', position={'x': 149.5, 'y': -1.5}, direction=0),
            dict(part='drill', name='burner-mining-drill', position={'x': 150, 'y': 0}, direction=0)])
    data = catalog()
    data.recipes['burner-mining-drill'] = recipe('burner-mining-drill', {'iron-plate': 5})
    data.recipes['wooden-chest'] = recipe('wooden-chest', {'wood': 2})
    return state, data


def row(state):
    return state.factory['mining_outposts']['sources'][RESOURCE]


def command(state, part='chest'):
    return dict(resource=RESOURCE, layout=row(state)['layout'], part=part, receipt='build:'+part)


def build(state, part, receipt=None):
    spec = next(s for s in row(state)['steps'] if s['part'] == part)
    unit = 70 if part == 'chest' else 71
    role = outposts.role(RESOURCE, part)
    state.inventory[spec['name']] -= 1
    row(state)['parts'][part] = dict(role=role, unit_number=unit, receipt=receipt or 'build:'+part, paid=1)
    row(state)['state'] = 'building' if part == 'chest' else 'ready'
    row(state)['topology'] = part == 'drill'
    state.factory['entities'][role] = machine(spec['name'], unit_number=unit,
        position=deepcopy(spec['position']), fuel={'coal': 5} if part == 'drill' else {})


def full(state):
    build(state, 'chest'); build(state, 'drill')
    return state


def commission(state):
    row(state)['flow'] = dict(layout=row(state)['layout'], drill_unit=71, chest_unit=70,
        first_tick=0, last_tick=180, positive_samples=3, received=3, mined=3, conservation=True)


def make_loop(backend, **kwargs):
    cls = outpost_loop_type(input_loop_type(buffered_loop_type(HierarchicalLoop)))
    return cls(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work', tick_seconds=0, **kwargs)


@pytest.mark.parametrize('change', [
    lambda p:p.update(resource='coal'), lambda p:p.update(part='belt'), lambda p:p.update(receipt=''),
    lambda p:p.update(layout=True), lambda p:p.update(extra='field'), lambda p:p.pop('part')])
def test_invalid_commands_rejected(change):
    state, _ = state_fixture(); params = command(state); change(params)
    with pytest.raises(ValueError):
        Step(outposts.COMMAND, 'outpost_component', parameters=params)


def test_paid_legacy_outpost_is_proposed_without_relocating_the_furnace():
    state, data = state_fixture(); before = deepcopy(state)
    plan = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 20)
    step = plan.steps[0]
    assert step.action == outposts.COMMAND and step.parameters['part'] == 'chest'
    assert step.allowed(state) and not step.satisfied(state)
    assert step.costs == {'wooden-chest': 1, 'burner-mining-drill': 1, 'coal': 5}
    assert state == before
    build(state, 'chest', step.parameters['receipt']); assert step.satisfied(state)
    next_step = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 1).steps[0]
    assert next_step.action == outposts.COMMAND and next_step.parameters['part'] == 'drill'
    assert state.factory['entities']['recipe:iron-plate'] == before.factory['entities']['recipe:iron-plate']


@pytest.mark.parametrize('change', [
    lambda s:s.inventory.update(coal=4), lambda s:s.inventory.update(**{'wooden-chest':0}),
    lambda s:s.inventory.update(**{'burner-mining-drill':0}), lambda s:s.factory.update(crafting_queue=1),
    lambda s:s.factory.update(player_bound=False), lambda s:s.factory.update(player_connected=False),
    lambda s:row(s).update(remaining=99), lambda s:s.factory['mining_outposts'].update(tick=s.tick-1)])
def test_build_requires_whole_remaining_paid_kit_and_fresh_idle_actor(change):
    state, _ = state_fixture(); change(state)
    assert not Step(outposts.COMMAND, 'outpost_component', parameters=command(state)).allowed(state)


@pytest.mark.parametrize('change', [
    lambda s:s.factory['mining_outposts'].update(protocol=True),
    lambda s:s.factory['mining_outposts'].update(session_id='other'),
    lambda s:row(s).update(remaining=-1), lambda s:row(s).update(force_index=True),
    lambda s:row(s)['steps'][0]['position'].update(x=150),
    lambda s:row(s)['steps'][1].update(direction=1),
    lambda s:row(s)['parts'].update(drill=dict(role='alien',unit_number=5,paid=1,receipt='x')),
    lambda s:row(s).update(state='ready'), lambda s:row(s)['steps'][1]['position'].update(x=float('nan'))])
def test_malformed_telemetry_fails_closed(change):
    state, _ = state_fixture(); change(state)
    with pytest.raises(ValueError): outposts.sources(state)


def test_kit_acquisition_does_not_recursively_build_another_outpost():
    state, data = state_fixture(); state.inventory = {'iron-plate': 5, 'coal': 5, 'wooden-chest': 1}
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 20).steps[0]
    assert step.action == 'factory_craft' and step.parameters['recipe'] == 'burner-mining-drill'
    state.inventory.pop('iron-plate'); state.factory['entities']['recipe:iron-plate']['input'] = {'iron-ore': 5}
    step = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 20).steps[0]
    assert step.action != outposts.COMMAND


@pytest.mark.parametrize('mode', ['small', 'bootstrap', 'other_goal', 'existing_ore', 'no_offer'])
def test_manual_fallback_and_existing_paid_supply_remain_available(mode):
    state, data = state_fixture(); amount, goal = 20, 'rocket_launch'
    if mode == 'small': amount = 2
    if mode == 'bootstrap': state.factory['entities']['recipe:iron-plate']['products_finished'] = 0
    if mode == 'other_goal': goal = 'iron_smelting'
    if mode == 'existing_ore': state.factory['entities']['legacy:chest'] = machine('wooden-chest', output={RESOURCE: 50})
    if mode == 'no_offer': state.factory['mining_outposts']['sources'] = {}
    step = MiningOutpostPlanner(data, state, goal)._need(RESOURCE, amount).steps[0]
    assert step.action in {'factory_gather', 'factory_extract'}


def test_flow_is_not_placement_or_time_and_precommissioning_cannot_be_seeded_or_drained():
    state, data = state_fixture(); full(state)
    state.factory['entities'][outposts.role(RESOURCE,'chest')]['output'][RESOURCE] = 10
    assert not outposts.flow_complete(RESOURCE, row(state)['layout'], state)
    for action in ('factory_insert','factory_extract'):
        assert not Step(action, 'transfer', parameters=dict(role=outposts.role(RESOURCE,'chest'),item=RESOURCE,
            quantity=1,receipt='test')).allowed(state)
    assert not Step('factory_gather','inventory', RESOURCE,20,
                    parameters={'resource':RESOURCE,'quantity':20}).allowed(state)
    wait=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).steps[0]
    assert wait.effect=='outpost_flow' and not wait.satisfied(state)
    state.tick+=100000;state.factory['mining_outposts']['tick']=state.tick
    assert not wait.satisfied(state)
    commission(state);assert wait.satisfied(state)


@pytest.mark.parametrize('key,value', [('received',4),('mined',2),('positive_samples',2),('drill_unit',99),
    ('chest_unit',99),('conservation',1),('last_tick',999),('first_tick',179),('layout','other')])
def test_certificate_requires_identity_conservation_samples_and_time(key,value):
    state,_=state_fixture();full(state);commission(state);row(state)['flow'][key]=value
    assert not outposts.flow_complete(RESOURCE,row(state)['layout'],state)


def test_collection_uses_real_buffer_and_bounded_hauling_not_more_mining():
    state,data=state_fixture();full(state);commission(state)
    chest=state.factory['entities'][outposts.role(RESOURCE,'chest')]
    chest['output'][RESOURCE]=75
    step=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,300).steps[0]
    assert step.action=='factory_extract' and step.parameters['quantity']==50 and step.allowed(state)
    chest['output'][RESOURCE]=2
    step=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,2).steps[0]
    assert step.action=='factory_extract' and step.parameters['quantity']==2
    chest['output'][RESOURCE]=0
    step=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).steps[0]
    assert step.action=='factory_wait' and step.effect=='machine_output' and step.threshold==20
    assert not step.satisfied(state)


def test_depleted_outpost_tail_then_observed_manual_fallback_without_rebuild():
    state,data=state_fixture();full(state);commission(state);row(state).update(state='depleted',remaining=0)
    chest=state.factory['entities'][outposts.role(RESOURCE,'chest')]
    chest['output'][RESOURCE]=2
    assert MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).steps[0].action=='factory_extract'
    chest['output'][RESOURCE]=0
    step=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).steps[0]
    assert step.action=='factory_gather' and step.allowed(state)


def test_fuel_service_uses_carried_coal_before_waiting_for_commissioning():
    state,data=state_fixture();full(state);state.inventory['coal']=5
    state.factory['entities'][outposts.role(RESOURCE,'drill')]['fuel']={}
    step=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).steps[0]
    assert step.action=='factory_insert' and step.parameters['item']=='coal' and step.parameters['quantity']==5
    assert step.allowed(state)


class Backend:
    input_routes_supported = output_buffers_supported = mining_outposts_supported = True
    def __init__(self,state,data): self.state,self.data,self.calls=state,data,[]
    def enable_factory(self): return self.data
    def observe(self): return deepcopy(self.state)
    def act(self, action):
        assert action == 'idle'
        return 'synthetic idle'
    def execute(self, action, parameters):
        self.calls.append((action,deepcopy(parameters)))
        if action==outposts.COMMAND:
            build(self.state,parameters['part'],parameters['receipt'])
        return 'synthetic result'


def controlled_loop(tmp_path, state=None, data=None):
    if state is None: state,data=state_fixture()
    backend=Backend(state,data)
    loop=make_loop(backend,checkpoint=str(tmp_path/'checkpoint.json'))
    loop.order=['rocket_launch']
    loop._compile_candidates=lambda s: ([MiningOutpostPlanner(data,s,'rocket_launch')._need(RESOURCE,20)],'')
    return loop,backend


def test_actual_controller_builds_each_paid_component_once_and_retains_ownership(tmp_path):
    loop,backend=controlled_loop(tmp_path)
    assert loop.step()['verified']
    assert loop.step()['verified']
    assert [p['part'] for a,p in backend.calls]==['chest','drill']
    assert set(loop.memory.outpost_commitments[RESOURCE]['parts'])=={'chest','drill'}
    saved=load_checkpoint(loop.checkpoint,backend.state.session_id,'rocket_launch')
    assert saved.outpost_commitments==loop.memory.outpost_commitments
    assert backend.state.inventory['burner-mining-drill']==0
    assert backend.state.factory['entities']['recipe:iron-plate']['unit_number']==17


def test_lost_acknowledgement_verifies_paid_component_without_replaying(tmp_path):
    loop,backend=controlled_loop(tmp_path)
    execute=backend.execute
    def lost(action,p):
        execute(action,p);raise TimeoutError('lost result')
    backend.execute=lost
    loop.step()
    assert loop.memory.pending and loop.memory.pending['dispatch']=='ambiguous'
    loop.step()
    assert len(backend.calls)==1 and loop.memory.pending is None
    assert len(loop.memory.outpost_commitments[RESOURCE]['parts'])==1


def test_uncertain_unpaid_build_remains_behind_pending_barrier(tmp_path):
    loop,backend=controlled_loop(tmp_path)
    def lost(action,p): backend.calls.append((action,p));raise TimeoutError('unknown')
    backend.execute=lost;loop.step()
    for _ in range(3):loop.step()
    assert len(backend.calls)==1 and loop.memory.pending
    assert not loop.memory.outpost_commitments


@pytest.mark.parametrize('change', ['missing', 'replace', 'move', 'lost_prefix', 'lost_proof'])
def test_resume_does_not_drop_owned_outpost_evidence(tmp_path,change):
    loop,backend=controlled_loop(tmp_path);loop.step();loop.step()
    state=backend.state;commission(state);loop._observe()
    if change=='missing': state.factory['mining_outposts']['sources']={}
    if change=='replace': state.factory['entities'][outposts.role(RESOURCE,'drill')]['unit_number']=909
    if change=='move': state.factory['entities'][outposts.role(RESOURCE,'drill')]['position']['x']+=1
    if change=='lost_prefix': row(state)['parts'].pop('drill');row(state).update(state='building',topology=False,flow={})
    if change=='lost_proof': row(state)['flow']={}
    loop._observe()
    assert loop.memory.status=='uncertain'
    assert loop.memory.outpost_commitments[RESOURCE]['flow']
    assert len(backend.calls)==2


def test_legacy_checkpoint_upgrade_is_explicit_and_idle_only(tmp_path):
    state,data=state_fixture()
    old_cls=input_loop_type(buffered_loop_type(HierarchicalLoop)).memory_type
    memory=old_cls(state.session_id,'rocket_launch',last_tick=state.tick,active_goal='rocket_launch')
    path=tmp_path/'legacy.json';memory.save(path);before=path.read_bytes()
    cls=outpost_loop_type(input_loop_type(buffered_loop_type(HierarchicalLoop))).memory_type
    upgraded=cls.load(path,state.session_id,'rocket_launch')
    assert upgraded.outpost_commitments=={} and path.read_bytes()==before
    assert upgraded.history[-1]['kind']=='mining_outposts_enabled'
    memory.active_plan=MiningOutpostPlanner(data,state,'rocket_launch')._need(RESOURCE,20).to_dict();memory.save(path)
    with pytest.raises(ValueError,match='idle'):cls.load(path,state.session_id,'rocket_launch')


def test_legacy_readers_reject_new_ownership_and_half_extensions(tmp_path):
    loop,backend=controlled_loop(tmp_path);loop.step()
    old_cls=input_loop_type(buffered_loop_type(HierarchicalLoop)).memory_type
    with pytest.raises(ValueError):old_cls.load(loop.checkpoint,backend.state.session_id,'rocket_launch')
    data=json.loads(loop.checkpoint.read_text());data.pop('outpost_commitments');loop.checkpoint.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Incomplete'):load_checkpoint(loop.checkpoint,backend.state.session_id,'rocket_launch')


def test_backend_adapter_prepares_walks_builds_and_never_retries():
    calls=[]
    def call(name,*args):
        calls.append(name)
        if name=='prepare_mining_outpost':return json.dumps({'name':'wooden-chest','position':{'x':149.5,'y':-1.5}})
        raise TimeoutError('lost placement result')
    native=SimpleNamespace(command=lambda code:None,call=call,backend=SimpleNamespace(
        _fair=SimpleNamespace(approach=lambda *args:calls.append('walk'))))
    adapter=MiningOutpostFactory(native);state,_=state_fixture()
    with pytest.raises(TimeoutError):adapter.execute(outposts.COMMAND,command(state))
    assert calls==['prepare_mining_outpost','walk','build_mining_outpost']


@pytest.fixture
def lua_runtime():
    lua=pytest.importorskip('lupa.lua54').LuaRuntime()
    lua.execute((ROOT/'tests/fixtures/input_routes_runtime.lua').read_text())
    lua.execute('''
        source.surface.index=1;force.index=1
        storage.input_routes={protocol=1,cells={},offers={}}
        storage.campaign.exploration_radius=8
        prototypes.entity['wooden-chest']={tile_width=1,tile_height=1}
        for _,ore in ipairs(resources) do
            ore.position.x=ore.position.x+150
            ore.prototype={mineable_properties={products={{name='iron-ore',type='item',amount=1}}}}
        end
        stock['wooden-chest']=1
        local oldcreate=create
        create=function(name,pos,dir,id)
            local e=oldcreate(name,{x=pos.x,y=pos.y},dir,id)
            local get=e.get_inventory
            e.get_inventory=function(kind)
                local inv=get(kind)
                inv.get_item_count=function(item)
                    if item then return inv.values[item] or 0 end
                    local sum=0;for _,n in pairs(inv.values) do sum=sum+n end;return sum
                end
                return inv
            end
            return e
        end
        function observed() return storage.campaign.observe_mining_outposts().sources['iron-ore'] end
        function build_outpost()
            local c=storage.campaign;local row=observed()
            assert(row,'No outpost offer: '..(storage.mining_outposts.reasons['iron-ore'] or 'unknown'))
            for _,s in ipairs(row.steps) do
                local p={resource='iron-ore',layout=row.layout,part=s.part,receipt='build:'..s.part}
                c.prepare_mining_outpost(p);player.position=s.position;c.build_mining_outpost(p)
            end
            local cell=storage.mining_outposts.cells['iron-ore']
            cell.parts.drill.entity.drop_target=cell.parts.chest.entity
            cell.parts.drill.entity.mining_target=cell.patch[1]
            return cell
        end
        function pulse_outpost(cell)
            game.tick=game.tick+60;cell.patch[1].amount=cell.patch[1].amount-1
            local inv=cell.parts.chest.entity.get_inventory(defines.inventory.chest).values
            inv['iron-ore']=(inv['iron-ore'] or 0)+1
            return observed()
        end
    ''')
    lua.execute((LUA/'mining_outposts.lua').read_text())
    return lua


def test_actual_lua_surveys_distant_ore_without_mutation(lua_runtime):
    lua_runtime.execute('''
        local row=observed();assert(row and row.state=='proposed')
        assert(row.steps[2].position.x>100 and row.remaining==2000)
        assert(observed().layout==row.layout and placements==0)
        assert(storage.campaign.entities['recipe:iron-plate']==source and stock['burner-mining-drill']==1)
        local p={resource='iron-ore',layout=row.layout,part='chest',receipt='chest'}
        storage.campaign.prepare_mining_outpost(p)
        assert(placements==0 and observed().state=='building')
    ''')


def test_actual_lua_paid_build_conservation_and_collection_gate(lua_runtime):
    lua_runtime.execute('''
        local original_source=source;local cell=build_outpost();local c=storage.campaign
        assert(placements==2 and stock['wooden-chest']==0 and stock['burner-mining-drill']==0)
        assert(c.entities['recipe:iron-plate']==original_source and source.unit_number==17)
        assert(observed().topology and not cell.flow)
        assert(not pcall(c.guard_mining_outpost_transfer,'outpost:iron-ore:chest','iron-ore',1,'x',true))
        assert(not pcall(c.guard_mining_outpost_transfer,'outpost:iron-ore:chest','iron-ore',1,'x',false))
        c.guard_mining_outpost_transfer('outpost:iron-ore:drill','coal',5,'fuel',false)
        c.transfer('outpost:iron-ore:drill','coal',5,'fuel',false)
        assert(stock.coal==95)
        pulse_outpost(cell);pulse_outpost(cell);assert(not cell.flow)
        local row=pulse_outpost(cell);assert(row.flow and row.flow.mined==3 and row.flow.received==3)
        c.guard_mining_outpost_transfer('outpost:iron-ore:chest','iron-ore',3,'collect',true)
        c.transfer('outpost:iron-ore:chest','iron-ore',3,'collect',true)
        assert(stock['iron-ore']==3 and observed().state=='ready')
    ''')


@pytest.mark.parametrize('change', [
    "player.connected=false", "game.speed=2", "player.cheat_mode=true", "player.crafting_queue_size=1",
    "stock['wooden-chest']=0", "stock['burner-mining-drill']=0", "stock.coal=4",
    "resources[1].amount=1;resources[2].amount=1", "obstacle=function(q)return true end",
    "force.mining_drill_productivity_bonus=1"])
def test_actual_lua_stale_preflight_never_places(lua_runtime,change):
    lua_runtime.execute("r=observed();p={resource='iron-ore',layout=r.layout,part='chest',receipt='a'}")
    lua_runtime.execute(change)
    lua_runtime.execute("assert(not pcall(storage.campaign.prepare_mining_outpost,p));assert(placements==0)")


def test_actual_lua_rechecks_after_walk_and_requires_normal_reach(lua_runtime):
    lua_runtime.execute('''
        local c=storage.campaign;local r=observed()
        local p={resource='iron-ore',layout=r.layout,part='chest',receipt='a'}
        c.prepare_mining_outpost(p);player.position={x=0,y=0}
        assert(not pcall(c.build_mining_outpost,p) and placements==0)
        player.position=r.steps[1].position;obstacle=function(q)return true end
        assert(not pcall(c.build_mining_outpost,p) and placements==0)
    ''')


def test_actual_lua_duplicate_receipt_and_duplicate_build_fail(lua_runtime):
    lua_runtime.execute('''
        local c=storage.campaign;local r=observed()
        local p={resource='iron-ore',layout=r.layout,part='chest',receipt='a'}
        c.prepare_mining_outpost(p);player.position=r.steps[1].position;c.build_mining_outpost(p)
        assert(not pcall(c.build_mining_outpost,p) and placements==1)
        p.part='drill';assert(not pcall(c.prepare_mining_outpost,p) and placements==1)
    ''')


@pytest.mark.parametrize('change', [
    "cell.parts.drill.entity.valid=false", "cell.parts.drill.entity.direction=4",
    "cell.parts.drill.entity.drop_target=source;game.tick=game.tick+121",
    "cell.parts.chest.entity.position.x=cell.parts.chest.entity.position.x+1",
    "storage.campaign.entities['outpost:iron-ore:drill']=source",
    "cell.parts.chest.entity.get_inventory(4).values['iron-ore']=50",
    "cell.parts.chest.entity.get_inventory(4).values['copper-ore']=1",
    "cell.patch[1].amount=cell.patch[1].amount+1", "cell.patch[1].amount=cell.patch[1].amount-10",
    "force.mining_drill_productivity_bonus=1"])
def test_actual_lua_identity_and_conservation_fail_closed(lua_runtime,change):
    lua_runtime.execute('cell=build_outpost();observed()');lua_runtime.execute(change)
    lua_runtime.execute("assert(observed().state=='fault');assert(placements==2)")


def test_actual_lua_reinstall_preserves_owner_and_never_wraps_observer_or_tick(lua_runtime):
    lua_runtime.execute('cell=build_outpost();observer=storage.campaign.observe;tick=handlers[1]')
    for _ in range(3):lua_runtime.execute((LUA/'mining_outposts.lua').read_text())
    lua_runtime.execute('''
        assert(storage.campaign.observe==observer and handlers[1]==tick)
        assert(storage.mining_outposts.cells['iron-ore']==cell and placements==2)
        pulse_outpost(cell);pulse_outpost(cell);pulse_outpost(cell);assert(cell.flow)
    ''')


@pytest.mark.parametrize('change', [
    "resources={}", "resources[2].name='copper-ore'", "force.mining_drill_productivity_bonus=1",
    "prototypes.entity['burner-mining-drill'].tile_width=3",
    "resources[1].prototype.mineable_properties.products[1].amount=2",
    "resources[1].prototype.mineable_properties.products[1].probability=0.5",
    "storage.input_routes.offers['recipe:iron-plate']={}",
    "storage.campaign.production_reserved=function()return true end"])
def test_actual_lua_unsupported_or_conflicting_sites_fall_back_without_build(lua_runtime,change):
    lua_runtime.execute(change)
    lua_runtime.execute("assert(not observed() and placements==0);assert(storage.mining_outposts.reasons['iron-ore'])")


@pytest.mark.parametrize('arguments', [
    [], ['--furnace-input-belts'],
    ['--furnace-input-belts', '--furnace-output-buffers'],
    ['--backend', 'fle', '--controller', 'hierarchical', '--factory-scheduling', 'ready-work',
     '--furnace-input-belts', '--furnace-output-buffers', '--target', 'iron_smelting'],
])
def test_invalid_outpost_cli_fails_before_world_initialization(monkeypatch, tmp_path, arguments):
    from jev_factorio import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['jev-factorio', '--mining-outposts', *arguments])
    monkeypatch.setattr(main, 'make_backend', lambda *a, **k: pytest.fail('World initialized'))
    with pytest.raises(SystemExit) as error:
        main.cli()
    assert error.value.code == 2


def test_cli_records_explicit_outpost_treatment_and_preserves_resume(monkeypatch, tmp_path):
    from jev_factorio import main
    from jev_factorio.research_log import verify_run
    from jev_factorio.outpost_controller import MiningOutpostMixin
    captured = {}
    monkeypatch.chdir(tmp_path)
    checkpoint = tmp_path / 'state.json'
    checkpoint.write_text('{}')  # Recording loop deliberately does not read a live checkpoint.
    monkeypatch.setattr('sys.argv', [
        'jev-factorio', '--backend', 'fle', '--controller', 'hierarchical',
        '--factory-scheduling', 'ready-work', '--furnace-output-buffers', '--furnace-input-belts',
        '--mining-outposts', '--policy', 'deterministic', '--tick-seconds', '2', '--steps', '0',
        '--resume', '--resume-controller', '--checkpoint', str(checkpoint), '--run-dir', str(tmp_path/'research')])
    def backend(name, **options):
        captured.update(backend=name, options=options)
        return object()
    def initialize(self, backend, jev=None, **options):
        captured['loop'] = type(self)
        captured['loop_options'] = options
    monkeypatch.setattr(main, 'make_backend', backend)
    monkeypatch.setattr(MiningOutpostMixin, '__init__', initialize)
    monkeypatch.setattr(MiningOutpostMixin, 'run', lambda self, steps: captured.update(steps=steps), raising=False)
    main.cli()
    manifest = json.loads((tmp_path/'research/manifest.json').read_text())
    assert manifest['configuration']['mining_outposts'] is True
    assert manifest['configuration']['resume'] and manifest['configuration']['resume_controller']
    assert captured['options'] == {'resume': True, 'adopt_session': False}
    assert captured['loop_options']['resume_controller'] and captured['steps'] == 0
    assert verify_run(tmp_path/'research')['complete']
    assert checkpoint.read_text() == '{}'


def test_outpost_capability_cannot_be_silently_dropped():
    state, data = state_fixture()
    backend = Backend(state, data)
    cls = input_loop_type(buffered_loop_type(HierarchicalLoop))
    loop = cls(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work')
    with pytest.raises(ValueError, match='[Oo]utpost'):
        loop._observe()
    assert not backend.calls


def test_readonly_manifest_validation_accepts_legacy_omission_and_rejects_nonboolean():
    from jev_factorio.research_log import RunConfiguration, _configuration
    configuration = asdict(RunConfiguration('fle', 'hierarchical', 'hybrid', mining_outposts=True))
    _configuration(configuration)
    configuration.pop('mining_outposts')
    _configuration(configuration)
    for invalid in (1, 'true', None):
        with pytest.raises(ValueError):
            _configuration(dict(configuration, mining_outposts=invalid))


def test_restart_command_preserves_outpost_flag_and_original_deadline(tmp_path, monkeypatch):
    from jev_factorio.supervisor import Supervisor, SupervisorConfig
    from test_supervisor import FakeClock, FakeProcess
    clock = FakeClock()
    config = SupervisorConfig(state_dir=tmp_path/'supervisor', checkpoint=tmp_path/'state.json',
        session_id='fresh', started_at=1000, repair_command=['unused'], cwd=tmp_path,
        factory_scheduling='ready-work', furnace_output_buffers=True, furnace_input_belts=True,
        mining_outposts=True)
    config.state_dir.mkdir()
    config.checkpoint.write_text(json.dumps({'session_id': 'fresh', 'target': 'rocket_launch',
        'status': 'running', 'pending': None}))
    instance = Supervisor(config, clock=clock, sleep=clock.sleep, popen=lambda *a, **k: FakeProcess())
    monkeypatch.setattr(instance, 'source_identity', lambda: ('head', 'source'))
    instance.initialize()
    cutoff = instance.state['cutoff']
    for _ in range(2):
        command = instance.gameplay_command()
        assert '--mining-outposts' in command and '--resume' in command and '--resume-controller' in command
        instance.initialize()
        assert instance.state['cutoff'] == cutoff
    config.mining_outposts = False
    with pytest.raises(ValueError, match='configuration cannot be changed'):
        instance.initialize()


def test_supervisor_requires_outpost_capability_dependencies(tmp_path):
    from jev_factorio.supervisor import SupervisorConfig
    config = SupervisorConfig(state_dir=tmp_path, checkpoint=tmp_path/'state', session_id='fresh',
        started_at=1, repair_command=['unused'], cwd=tmp_path, mining_outposts=True)
    with pytest.raises(ValueError, match='input belts'):
        config.validate()
    config.furnace_input_belts = True
    with pytest.raises(ValueError, match='ready-work'):
        config.validate()


def test_background_locks_still_reject_construction_and_locked_ore():
    from jev_factorio.craft_jobs import CraftJob
    state, data = state_fixture()
    plan = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 20)
    lock = SimpleNamespace(failed='', outputs={RESOURCE: 20})
    assert not CraftJob.permits(lock, plan.steps[0])
    full(state); commission(state)
    state.factory['entities'][outposts.role(RESOURCE, 'chest')]['output'][RESOURCE] = 50
    pickup = MiningOutpostPlanner(data, state, 'rocket_launch')._need(RESOURCE, 20).steps[0]
    assert pickup.allowed(state) and not CraftJob.permits(lock, pickup)
    lock.outputs = {'copper-cable': 20}
    assert CraftJob.permits(lock, pickup)


def test_persistence_failure_poisoning_stops_later_mutations(tmp_path, monkeypatch):
    loop, backend = controlled_loop(tmp_path)
    loop.step()
    before = len(backend.calls)
    def fail(self, path):
        raise OSError('Synthetic disk failure')
    monkeypatch.setattr(type(loop.memory), 'save', fail)
    with pytest.raises(OSError):
        loop._observe()
    with pytest.raises(RuntimeError, match='persistence failed'):
        loop.step()
    assert len(backend.calls) == before


def test_recreated_controller_reconciles_paid_lost_ack_without_build_replay(tmp_path):
    loop, backend = controlled_loop(tmp_path)
    execute = backend.execute
    def lost(action, parameters):
        execute(action, parameters)
        raise TimeoutError('lost acknowledgement')
    backend.execute = lost
    loop.step()
    resumed = make_loop(backend, checkpoint=str(loop.checkpoint), resume_controller=True)
    resumed.step()
    assert len(backend.calls) == 1 and resumed.memory.pending is None
    assert resumed.memory.outpost_commitments[RESOURCE]['parts']['chest']['paid'] == 1


def test_actual_lua_committed_outpost_rejects_direct_route_prepare(lua_runtime):
    lua_runtime.execute((LUA/'input_routes.lua').read_text())
    lua_runtime.execute('''
        local cell=build_outpost()
        local p={source='recipe:iron-plate',layout='stale',part='inserter',receipt='direct',reserve_belts=0}
        local ok,error=pcall(storage.campaign.prepare_input_route,p)
        assert(not ok and string.find(error,'ore outpost'))
        assert(not storage.input_routes.cells['recipe:iron-plate'] and placements==2)
    ''')


def test_actual_lua_exhaustion_keeps_paid_cell_and_verified_tail(lua_runtime):
    lua_runtime.execute('''
        local cell=build_outpost()
        pulse_outpost(cell);pulse_outpost(cell);pulse_outpost(cell)
        local proof=cell.flow
        for _,e in ipairs(cell.patch) do e.amount=0;e.valid=false end
        cell.parts.drill.entity.mining_target=nil
        local row=observed()
        assert(row.state=='depleted' and row.remaining==0 and row.flow==proof and placements==2)
        storage.campaign.guard_mining_outpost_transfer('outpost:iron-ore:chest','iron-ore',3,'tail',true)
        assert(observed().parts.drill.unit_number==cell.parts.drill.unit_number)
    ''')


def test_actual_base_transfer_calls_outpost_guard_before_any_inventory_access():
    lua = pytest.importorskip('lupa.lua54').LuaRuntime()
    lua.execute('storage={agent_characters={{force={rockets_launched=0}}}}')
    lua.execute((LUA/'factory.lua').read_text())
    lua.execute('''
        calls=0
        storage.campaign.guard_mining_outpost_transfer=function(role,item,quantity,receipt,extracting)
            calls=calls+1;assert(role=='outpost:iron-ore:chest' and extracting)
            error('Uncommissioned outpost guard')
        end
        local ok,error=pcall(storage.campaign.transfer,'outpost:iron-ore:chest','iron-ore',1,'x',true)
        assert(not ok and string.find(error,'Uncommissioned outpost guard') and calls==1)
        assert(not next(storage.campaign.receipts))
    ''')


def test_full_capability_stack_builds_and_reloads_with_background_memory(tmp_path):
    from jev_factorio.background import BackgroundWorkLoop
    state, data = state_fixture()
    backend = Backend(state, data)
    backend.craft_jobs_supported = True
    cls = outpost_loop_type(input_loop_type(buffered_loop_type(BackgroundWorkLoop)))
    loop = cls(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work',
               tick_seconds=0, checkpoint=str(tmp_path/'combined.json'))
    loop.order = ['rocket_launch']
    loop._compile_candidates = lambda s: ([MiningOutpostPlanner(data, s, 'rocket_launch')._need(RESOURCE, 20)], '')
    assert loop.step()['verified'] and loop.step()['verified']
    loaded = load_checkpoint(loop.checkpoint, state.session_id, 'rocket_launch')
    assert loaded.background_schema == 2 and loaded.background_job is None
    assert loaded.input_routes_schema == 1 and loaded.outposts_schema == 1
    assert loaded.outpost_commitments == loop.memory.outpost_commitments
    assert len(backend.calls) == 2
