"""Fresh mixed-construction checks use real Lua with modeled native entities."""
import pytest
from lupa.lua52 import LuaError

from test_coal_mixed_transport import native, ROOT


@pytest.mark.parametrize('boundary', ['prepare', 'build'])
@pytest.mark.parametrize('target', ['alpha', 'beta'])
@pytest.mark.parametrize('part', ['chest', 'drill'])
@pytest.mark.parametrize('change', ['deleted', 'replaced'])
def test_downstream_revalidates_every_paid_coal_source_before_payment(boundary, target, part, change):
    lua = native()
    lua.execute('coal_all();p=downstream_args();before=paid_calls;'
                'before_inserters=stock.inserter;before_belts=stock["transport-belt"]')
    if boundary == 'build':
        lua.execute('campaign.prepare_solid_route(p);journal=storage.solid_routes.cells[p.route].pending')
    lua.globals().changed_role = f'coal:{target}:{part}'
    lua.globals().changed_target = target
    lua.globals().changed_part = part
    # Cache the offer/prepare first: there is deliberately no later observation
    # to set a fault flag on behalf of the final native construction gate.
    lua.execute('old=campaign.entities[changed_role];'
                'paid=storage.coal_supply.rows[changed_target].parts[changed_part];'
                'old.valid=false;game.tick=game.tick+1')
    if change == 'replaced':
        lua.execute('campaign.entities[changed_role]=entity(old.name,old.type,'
                    'old.position.x,old.position.y,(old.bounding_box.right_bottom.x-old.bounding_box.left_top.x)/2)')
    with pytest.raises(LuaError):
        lua.execute(f'campaign.{"prepare" if boundary == "prepare" else "build"}_solid_route(p)')
    lua.execute('assert(paid_calls==before and stock.inserter==before_inserters '
                'and stock["transport-belt"]==before_belts);'
                'assert(storage.coal_supply.committed and '
                'storage.coal_supply.rows[changed_target].parts[changed_part]==paid and paid.entity==old)')
    if boundary == 'prepare':
        lua.execute('assert(storage.solid_routes.cells[p.route]==nil)')
    else:
        lua.execute('assert(storage.solid_routes.cells[p.route].pending==journal '
                    'and journal.phase=="prepared" and journal.receipt==p.receipt)')


@pytest.mark.parametrize('boundary', ['prepare', 'build'])
@pytest.mark.parametrize('change', ['depleted_resource', 'lost_power'])
def test_downstream_revalidates_the_unbuilt_committed_coal_branch(boundary, change):
    lua = native()
    lua.execute('coal_build("alpha","chest");p=downstream_args();before=paid_calls;'
                'before_inserters=stock.inserter')
    if boundary == 'build':
        lua.execute('campaign.prepare_solid_route(p)')
    if change == 'depleted_resource':
        lua.execute('ore2.amount=0')
    else:
        lua.execute('pole1.electric_network_id=nil;pole2.electric_network_id=nil')
    with pytest.raises(LuaError):
        lua.execute(f'campaign.{"prepare" if boundary == "prepare" else "build"}_solid_route(p)')
    lua.execute('assert(paid_calls==before and stock.inserter==before_inserters '
                'and storage.coal_supply.committed)')


@pytest.mark.parametrize('stage', ['unpaid', 'one_chest', 'two_chests', 'partial_corridor', 'complete'])
def test_healthy_partial_or_complete_coal_bundle_keeps_downstream_construction(stage):
    lua = native()
    if stage == 'complete':
        lua.execute('coal_all()')
    elif stage in {'one_chest', 'two_chests', 'partial_corridor'}:
        lua.execute('coal_build("alpha","chest")')
        if stage == 'two_chests':
            lua.execute('coal_build("beta","chest")')
        elif stage == 'partial_corridor':
            lua.execute('local cell=coal_corridor("alpha");local s=cell.steps[1];'
                        'local p={route=cell.route,layout=cell.layout,part=s.part,receipt="partial:receive"};'
                        'campaign.prepare_solid_route(p);campaign.build_solid_route(p)')
    lua.execute('p=downstream_args();before=paid_calls;before_inserters=stock.inserter;'
                'campaign.prepare_solid_route(p);game.tick=game.tick+1;campaign.build_solid_route(p);'
                'assert(paid_calls==before+1 and stock.inserter==before_inserters-1)')


def test_prior_coal_runtime_cannot_retain_the_old_downstream_gate():
    lua = native()
    lua.execute('coal_build("alpha","chest");retained=storage.coal_supply;'
                'gate=retained.construction_gate;before=paid_calls;retained.revision=3')
    with pytest.raises(LuaError, match='reconciliation'):
        lua.execute((ROOT/'src/jev_factorio/lua/coal_supply.lua').read_text())
    lua.execute('assert(storage.coal_supply==retained and retained.construction_gate==gate '
                'and paid_calls==before and retained.committed)')


@pytest.mark.parametrize('boundary', ['prepare', 'build'])
@pytest.mark.parametrize('target', ['alpha', 'beta'])
@pytest.mark.parametrize('part', ['receive', 'belt:1', 'send'])
@pytest.mark.parametrize('change', ['deleted', 'replaced'])
def test_downstream_revalidates_every_paid_coal_corridor_before_payment(boundary, target, part, change):
    lua = native()
    lua.execute('coal_all();p=downstream_args();before=paid_calls;before_inserters=stock.inserter')
    if boundary == 'build':
        lua.execute('campaign.prepare_solid_route(p);journal=storage.solid_routes.cells[p.route].pending')
    lua.globals().changed_target = target
    lua.globals().changed_part = part
    lua.execute('local cell;for _,v in pairs(storage.solid_routes.cells) do '
                'if v.target.role==changed_target then cell=v end end;'
                'paid=cell.parts[changed_part];old=paid.entity;old.valid=false;game.tick=game.tick+1')
    if change == 'replaced':
        lua.execute('campaign.entities[paid.role]=entity(old.name,old.type,'
                    'old.position.x,old.position.y,(old.bounding_box.right_bottom.x-old.bounding_box.left_top.x)/2)')
    with pytest.raises(LuaError):
        lua.execute(f'campaign.{"prepare" if boundary == "prepare" else "build"}_solid_route(p)')
    lua.execute('assert(paid_calls==before and stock.inserter==before_inserters '
                'and storage.coal_supply.committed and paid.entity==old)')
    if boundary == 'prepare':
        lua.execute('assert(storage.solid_routes.cells[p.route]==nil)')
    else:
        lua.execute('assert(storage.solid_routes.cells[p.route].pending==journal and journal.phase=="prepared")')


@pytest.mark.parametrize('boundary', ['prepare', 'build'])
@pytest.mark.parametrize('uncertain', ['prepared', 'dispatching', 'fault'])
def test_other_coal_corridor_journal_or_fault_blocks_downstream_payment(boundary, uncertain):
    lua = native()
    lua.execute('coal_build("alpha","chest");local cell=coal_corridor("alpha");'
                'cp={route=cell.route,layout=cell.layout,part=cell.steps[1].part,receipt="coal:pending-route"};'
                'campaign.prepare_solid_route(cp);campaign.build_solid_route(cp);'
                'p=downstream_args();before=paid_calls;before_inserters=stock.inserter;'
                'coal_cell=storage.solid_routes.cells[cp.route]')
    if boundary == 'build':
        lua.execute('campaign.prepare_solid_route(p);journal=storage.solid_routes.cells[p.route].pending')
    # Model retained uncertainty arriving after the last observation or prepare;
    # no observer runs to set controller flags before the native payment gate.
    if uncertain == 'fault':
        lua.execute('coal_cell.fault="ambiguous_dispatch"')
    else:
        lua.execute('local s=coal_cell.steps[2];coal_cell.pending={'
                    f'phase="{uncertain}",part=s.part,receipt="coal:other-action",spec=s' + '};'
                    'other_journal=coal_cell.pending')
    with pytest.raises(LuaError, match='reconciliation'):
        lua.execute(f'campaign.{"prepare" if boundary == "prepare" else "build"}_solid_route(p)')
    lua.execute('assert(paid_calls==before and stock.inserter==before_inserters)')
    if uncertain != 'fault':
        lua.execute('assert(coal_cell.pending==other_journal)')
    if boundary == 'build':
        lua.execute('assert(storage.solid_routes.cells[p.route].pending==journal and journal.phase=="prepared")')
