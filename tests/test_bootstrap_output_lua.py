"""Offline native API simulation: ownership/effects are never live acceptance."""
from importlib.resources import files

import pytest


def runtime(*, install=True):
    lua = pytest.importorskip('lupa.lua52').LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
game={tick=100};defines={inventory={chest=1}}
force={index=1};surface={index=1};player_stock=0;chest_stock=33;calls=0
local inv={valid=true,get_contents=function() return {['iron-ore']=chest_stock} end,
 get_item_count=function() return chest_stock end}
drill={valid=true,unit_number=2544,force=force,surface=surface,
 position={x=49,y=-81},drop_position={x=48.5,y=-82.296875}}
chest={valid=true,unit_number=2545,force=force,surface=surface,position={x=48.5,y=-82.5},
 get_inventory=function() return inv end}
surface.find_entity=function(name,pos) if name=='wooden-chest' then return chest else return drill end end
surface.find_entities_filtered=function() return {chest} end
actor={valid=true,unit_number=2543,force=force,surface=surface}
target={get_insertable_count=function() return 100 end,get_item_count=function() return player_stock end,
 get_contents=function() return {['iron-ore']=player_stock} end}
player={character=actor,get_main_inventory=function() return target end,
 get_item_count=function() return 1 end,can_reach_entity=function() return true end}
jev_fle_runtime={jev_session_id='session',fair={actor=function() return player end,
 place=function() calls=calls+1;error('must not place') end},campaign={entities={},receipts={}}}
local c=jev_fle_runtime.campaign
c.transfer=function(role,item,q,id,extracting)
 calls=calls+1;player_stock=player_stock+q;chest_stock=chest_stock-q
 c.receipts[id]={role=role,item=item,quantity=q,unit_number=2545,extracting=extracting,tick=game.tick}
 if reply_loss then error('simulated reply loss after actual receipt') end
end
jev_fle_runtime.bootstrap_output_install_authorization={origin='legacy_authorized_current_asset',
 session_id='session',actor_unit=2543,surface_index=1,force_index=1,
 authorization_sha256=string.rep('a',64),drill_unit=2544,chest_unit=2545,
 drill_position=drill.position,drop_position=drill.drop_position,chest_position=chest.position}
''')
    if install:
        lua.execute(files('jev_factorio').joinpath('lua/bootstrap_output_v1.lua').read_text())
    return lua


def test_current_asset_adoption_never_claims_paid_history_or_spends_items():
    lua = runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1
local row=b.observe();assert(row.output['iron-ore']==33 and row.capacity.count==100)
assert(row.origin=='legacy_authorized_current_asset' and row.historical_paid_placement_proven==false)
assert(jev_fle_runtime.campaign.entities['bootstrap-output:iron-ore']==chest and calls==0)
assert(not pcall(function() b.place('wooden-chest',{x=0,y=0},0) end) and calls==0)
''')


def test_actual_transfer_reply_loss_reconciles_existing_receipt_without_retry():
    lua = runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1;reply_loss=true
assert(not pcall(function() b.extract('iron-ore',20,'100:factory_extract:bootstrap-output:iron-ore:iron-ore') end))
assert(calls==1 and player_stock==20 and chest_stock==13 and b.transfer_pending)
b.reconcile_pending();assert(calls==1 and not b.transfer_pending and #b.reconciliations==1)
''')


def test_later_producer_output_cannot_erase_same_command_conservation_proof():
    lua = runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1;reply_loss=true
pcall(function() b.extract('iron-ore',20,'100:factory_extract:bootstrap-output:iron-ore:iron-ore') end)
game.tick=120;chest_stock=chest_stock+7
b.reconcile_pending();assert(calls==1 and chest_stock==20 and player_stock==20)
''')


def test_proven_partial_effect_needs_explicit_exact_outcome_and_retains_actual_receipt():
    lua = runtime(install=False)
    lua.execute('''local c=jev_fle_runtime.campaign;local original=c.transfer
c.transfer=function(role,item,q,id,extracting) original(role,item,7,id,extracting);error('partial') end''')
    lua.execute(files('jev_factorio').joinpath('lua/bootstrap_output_v1.lua').read_text())
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1;local c=jev_fle_runtime.campaign
local id='100:factory_extract:bootstrap-output:iron-ore:iron-ore'
assert(not pcall(function() b.extract('iron-ore',20,id) end))
assert(not pcall(function() b.reconcile_pending() end) and b.transfer_pending)
assert(not pcall(function() b.reconcile_pending(id,8) end) and b.transfer_pending)
b.reconcile_pending(id,7)
assert(calls==1 and c.receipts[id].quantity==7 and player_stock==7 and chest_stock==26)
''')


def test_foreign_quality_and_count_overflow_reject_observation():
    lua = runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1
chest.get_inventory=function() return {valid=true,get_contents=function()
 return {{name='iron-ore',count=33,quality={name='rare'}}} end} end
assert(not pcall(function() b.observe() end))
chest.get_inventory=function() return {valid=true,get_contents=function()
 return {{name='iron-ore',count=9007199254740991},{name='iron-ore',count=1}} end} end
assert(not pcall(function() b.observe() end))
''')


def future_runtime():
    lua=runtime(install=False)
    lua.execute('''local rt=jev_fle_runtime
rt.bootstrap_output_install_authorization.origin='future_native_paid_bootstrap_only'
items={['burner-mining-drill']=1,['wooden-chest']=1}
player.get_item_count=function(name) return items[name] or 0 end
rt.fair.place=function(name,position,direction)
 calls=calls+1;items[name]=items[name]-1
 local entity=name=='wooden-chest' and chest or drill
 return {unit_number=entity.unit_number,position=entity.position}
end''')
    lua.execute(files('jev_factorio').joinpath('lua/bootstrap_output_v1.lua').read_text())
    return lua


def test_future_native_paid_binding_retains_each_real_item_delta_and_rejects_duplicates():
    lua=future_runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1
b.place('burner-mining-drill',drill.position,0)
assert(not pcall(function() b.place('burner-mining-drill',drill.position,0) end) and calls==1)
b.place('wooden-chest',chest.position,0);b.bind_paid(2544,2545)
local row=b.observe();assert(row.historical_paid_placement_proven and row.paid_drill_unit==2544
 and row.paid_chest_unit==2545 and row.authorization_sha256==false)
assert(items['wooden-chest']==0 and items['burner-mining-drill']==0 and calls==2)
''')


def test_paid_record_reply_loss_reconciles_without_a_second_native_place():
    lua=future_runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1
setmetatable(b.paid,{__newindex=function(t,k,v) rawset(t,k,v);error('after paid record') end})
assert(not pcall(function() b.place('burner-mining-drill',drill.position,0) end))
assert(b.placement_pending and calls==1 and items['burner-mining-drill']==0)
b.reconcile_pending();assert(not b.placement_pending and calls==1 and b.paid[2544])
''')


@pytest.mark.parametrize('record', ['false', '{result={unit_number=2544,position={x=49,y=-81}}}'])
def test_missing_prepared_placement_identity_or_paid_record_stays_ambiguous(record):
    lua=future_runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1
b.placement_pending='''+record+'''
if not b.placement_pending then b.placement_pending={name='burner-mining-drill',item_before=1} end
assert(not pcall(function() b.reconcile_pending() end))
assert(b.placement_pending and calls==0)
assert(not pcall(function() b.place('wooden-chest',chest.position,0) end) and calls==0)
''')


def test_missing_receipt_after_effect_stays_ambiguous_and_blocks_next_transfer():
    lua = runtime()
    lua.execute('''local b=jev_fle_runtime.bootstrap_output_v1;reply_loss=true
local id='100:factory_extract:bootstrap-output:iron-ore:iron-ore'
pcall(function() b.extract('iron-ore',20,id) end)
jev_fle_runtime.campaign.receipts[id]=nil
assert(not pcall(function() b.reconcile_pending() end) and b.transfer_pending)
assert(not pcall(function() b.extract('iron-ore',1,id) end) and calls==1)
''')


@pytest.mark.parametrize('mutation', [
    'chest.unit_number=999', 'chest.force={index=2}', 'chest.surface={index=2}',
    "jev_fle_runtime.campaign.entities['bootstrap-output:iron-ore']=nil",
    'drill.drop_position={x=0,y=0}',
    'jev_fle_runtime.campaign.observe=function() return {} end',
])
def test_current_endpoint_changes_reject_before_transfer(mutation):
    lua = runtime()
    lua.execute(mutation)
    lua.execute('''assert(not pcall(function()
jev_fle_runtime.bootstrap_output_v1.extract('iron-ore',1,'100:factory_extract:bootstrap-output:iron-ore:iron-ore')
end) and calls==0)''')


@pytest.mark.parametrize('receipt', ['101:factory_extract:bootstrap-output:iron-ore:iron-ore',
    '100:extra:factory_extract:bootstrap-output:iron-ore:iron-ore',
    '00000000000000000:factory_extract:bootstrap-output:iron-ore:iron-ore'])
def test_native_receipt_shape_and_tick_are_bounded(receipt):
    lua = runtime()
    lua.globals().invalid_receipt = receipt
    lua.execute('''assert(not pcall(function()
jev_fle_runtime.bootstrap_output_v1.extract('iron-ore',1,invalid_receipt)
end) and calls==0)''')
