"""Real extension Lua exercised with modeled Factorio API shapes, not engine acceptance."""
from pathlib import Path
import pytest
from lupa.lua52 import LuaRuntime, LuaError

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    for file in ("tests/fixtures/solid_routes_runtime.lua", "tests/fixtures/coal_supply_runtime.lua",
                 "src/jev_factorio/lua/solid_routes.lua", "src/jev_factorio/lua/coal_supply.lua"):
        lua.execute((ROOT / file).read_text())
    lua.execute("configure_coal()")
    return lua


def test_native_source_bundle_is_unpaid_and_contains_two_distinct_consumers():
    lua = runtime()
    lua.execute('row=coal_offer();assert(row.state=="proposed" and next(row.parts)==nil and next(row.flow)==nil);assert(paid_calls==0)')
    assert lua.eval('coal_offer("beta").target.unit_number ~= row.target.unit_number')


def test_receiving_corridor_precedes_mining_and_receipt_replay_is_not_payment():
    lua = runtime()
    lua.execute('p=coal_build("alpha","chest")')
    with pytest.raises(LuaError, match="receiver must precede"):
        lua.execute('campaign.prepare_coal_source(coal_args("alpha","drill"))')
    lua.execute('campaign.prepare_coal_source(p);campaign.build_coal_source(p);assert(paid_calls==1)')


def test_two_independent_paid_drills_require_multiple_positive_observations():
    lua = runtime()
    lua.execute('result=coal_all();assert(next(result.coal_supply.sources.alpha.flow)~=nil);assert(result.coal_supply.sources.alpha.flow.delivered_lower==0)')
    lua.execute('coal_pulse();coal_pulse();result=coal_pulse()')
    for target in ("alpha", "beta"):
        row=lua.eval(f'result.coal_supply.sources.{target}')
        assert row['state']=='ready'
        assert row['flow']['mined']==6
        assert row['flow']['delivered_lower']==5
        assert row['flow']['positive_samples']==3
        assert row['flow']['burned_lower_joules']==8_000_000


@pytest.mark.parametrize("mutation", [
    'stock["electric-mining-drill"]=1', 'stock.inserter=1', 'stock["transport-belt"]=0',
    'player.crafting_queue_size=1',
])
def test_complete_bundle_kit_required_before_first_payment(mutation):
    lua=runtime();lua.execute('p=coal_args("alpha","chest");'+mutation)
    with pytest.raises(LuaError): lua.execute('campaign.prepare_coal_source(p)')
    assert lua.eval('paid_calls')==0
    assert not lua.eval('storage.coal_supply.committed')


def test_ambiguous_native_payment_is_not_repeated_or_adopted():
    lua=runtime();lua.execute('p=coal_args("alpha","chest");campaign.prepare_coal_source(p);lose_place_receipt=true')
    with pytest.raises(LuaError): lua.execute('campaign.build_coal_source(p)')
    lua.execute('coal_offer()')
    with pytest.raises(LuaError): lua.execute('campaign.prepare_coal_source(p)')
    assert lua.eval('paid_calls')==1
    assert lua.eval('coal_offer().pending.phase')=='dispatching'


@pytest.mark.parametrize('mutation', [
    'ore1.amount=ore1.amount+1',
    'burner1.fuel.values.coal=burner1.fuel.values.coal+2',
    'campaign.entities["coal:alpha:chest"].output.values.coal=10',
    'campaign.entities["coal:alpha:drill"].direction=8',
    'campaign.entities["coal:alpha:drill"].productivity_bonus=.1',
    'campaign.entities["alias"]=campaign.entities["coal:alpha:drill"]',
    'burner1.unit_number=99999',
])
def test_unsupported_identity_yield_or_unattributed_inventory_fails_closed(mutation):
    lua=runtime();lua.execute('coal_all();'+mutation+';game.tick=game.tick+60;result=campaign.observe()')
    assert lua.eval('result.coal_supply.sources.alpha.state')=='fault'
    assert lua.eval('next(result.coal_supply.sources.alpha.flow)==nil')


def test_manual_transfer_is_journaled_and_not_credited_as_automatic_flow():
    lua=runtime();lua.execute('coal_all();campaign.transfer("alpha","coal",3,"manual:1",false);game.tick=game.tick+60;row=coal_offer()')
    assert lua.eval('row.flow.manual_inserted')==3
    assert lua.eval('row.flow.delivered_lower')==0
    assert lua.eval('row.flow.burned_lower_joules')==0
    with pytest.raises(LuaError): lua.execute('campaign.transfer("alpha","coal",3,"manual:1",false)')


def test_manual_ack_loss_retains_receipt_without_second_transfer():
    lua=runtime();lua.execute('coal_all();lose_manual_reply=true')
    with pytest.raises(LuaError): lua.execute('campaign.transfer("alpha","coal",3,"manual:1",false)')
    assert lua.eval('storage.coal_supply.rows.alpha.manual_total')==3
    assert lua.eval('storage.coal_supply.rows.alpha.manual_pending==nil')
    with pytest.raises(LuaError): lua.execute('campaign.transfer("alpha","coal",3,"manual:1",false)')


def test_immutable_treatment_and_reattachment_preserve_paid_state():
    lua=runtime();lua.execute('coal_build("alpha","chest")')
    lua.execute((ROOT/'src/jev_factorio/lua/solid_routes.lua').read_text())
    lua.execute((ROOT/'src/jev_factorio/lua/coal_supply.lua').read_text())
    lua.execute('configure_coal();assert(paid_calls==1)')
    with pytest.raises(LuaError): lua.execute('campaign.set_coal_targets({"beta","alpha"})')


def test_actual_burning_item_prototype_and_heat_are_included_not_falsely_consumed():
    lua=runtime();lua.execute('coal_all();burner1.fuel.values.coal=3;burner1.burner.currently_burning={name={name="coal"},quality={name="normal"}};'
        'burner1.burner.remaining_burning_fuel=3000000;burner1.burner.heat=1000000;game.tick=game.tick+60;row=coal_offer()')
    assert lua.eval('row.state')=='ready'
    assert lua.eval('row.flow.burned_lower_joules')==0


def test_journaled_manual_transfer_in_same_tick_does_not_become_an_unexplained_mutation():
    lua=runtime();lua.execute('coal_all();campaign.transfer("alpha","coal",3,"manual:same-tick",false);row=coal_offer()')
    assert lua.eval('row.state')=='ready'
    assert lua.eval('row.flow.manual_inserted')==3
    assert lua.eval('row.flow.delivered_lower')==0


def test_power_loss_preserves_paid_network_and_manual_recovery_instead_of_rebuilding():
    lua=runtime();lua.execute('coal_all();before=paid_calls;witness1.energy=0;witness2.energy=0;'
        'campaign.entities["coal:alpha:drill"].energy=0;campaign.entities["coal:beta:drill"].energy=0;'
        'game.tick=game.tick+60;row=coal_offer()')
    assert lua.eval('row.state')=='ready'
    assert lua.eval('row.reason')=='no_power'
    assert not lua.eval('row.power.energized')
    lua.execute('campaign.transfer("alpha","coal",3,"manual:power",false);coal_offer();assert(paid_calls==before)')


def test_full_destination_and_depletion_do_not_rebuild_paid_sources():
    lua=runtime();lua.execute('coal_all();before=paid_calls;burner1.fuel.capacity=4;game.tick=game.tick+60;row=coal_offer()')
    assert lua.eval('row.reason')=='backpressure'
    lua.execute('ore1.amount=0;ore2.amount=0;burner1.fuel.values.coal=1004;burner2.fuel.values.coal=1004;'
        'game.tick=game.tick+60;row=coal_offer();assert(paid_calls==before)')
    assert lua.eval('row.state')=='depleted'


def test_missing_manual_receipt_is_an_ambiguous_transfer_not_free_delivery():
    lua=runtime();lua.execute('coal_all();lose_manual_receipt=true')
    with pytest.raises(LuaError):lua.execute('campaign.transfer("alpha","coal",3,"manual:lost",false)')
    lua.execute('row=coal_offer()')
    assert lua.eval('row.state')=='fault'
    assert lua.eval('row.manual_pending.phase')=='dispatching'
    assert lua.eval('storage.coal_supply.rows.alpha.manual_total')==0


def test_reserves_only_owned_resource_and_protects_unjournaled_source_or_target_inserts():
    lua=runtime();lua.execute('coal_build("alpha","chest")')
    assert lua.eval('storage.coal_supply.resource_reserved(ore1)')
    assert not lua.eval('storage.coal_supply.resource_reserved(witness1)')
    assert not lua.eval('storage.coal_supply.external_insert_allowed(burner1,"coal")')
    assert not lua.eval('storage.coal_supply.external_insert_allowed(campaign.entities["coal:alpha:chest"],"coal")')
    assert lua.eval('storage.coal_supply.external_insert_allowed(witness1,"coal")')


@pytest.mark.parametrize('mutation',[
    'ore1.prototype.infinite_resource=true', 'ore1.prototype.mineable_properties.products[1].amount=2',
    'ore1.prototype.mineable_properties.required_fluid="sulfuric-acid"',
    'prototypes.entity["electric-mining-drill"].resource_drain_rate_percent=50',
    'prototypes.entity["electric-mining-drill"].drops_full_belt_stacks=true',
])
def test_unsupported_mining_prototypes_get_no_offer_or_payment(mutation):
    lua=runtime();lua.execute(mutation+';result=campaign.observe()')
    assert lua.eval('next(result.coal_supply.sources)==nil')
    assert lua.eval('paid_calls')==0


def test_negative_qualification_is_bounded_and_does_not_reprobe_every_observation():
    lua=runtime();lua.execute('ore1.amount=0;campaign.observe();first=storage.coal_supply.last_survey_tick;'
        'ore1.amount=1000;game.tick=game.tick+1;result=campaign.observe()')
    assert lua.eval('next(result.coal_supply.sources)==nil')
    assert lua.eval('storage.coal_supply.last_survey_tick==first')
    lua.execute('game.tick=game.tick+600;coal_offer()')


@pytest.mark.parametrize('consumer_count', [3, 4])
def test_all_supported_consumer_counts_build_paid_disjoint_branches(consumer_count):
    lua = LuaRuntime(unpack_returned_tuples=True)
    for file in ('tests/fixtures/solid_routes_runtime.lua', 'tests/fixtures/coal_supply_runtime.lua',
                 'src/jev_factorio/lua/solid_routes.lua', 'src/jev_factorio/lua/coal_supply.lua'):
        lua.execute((ROOT/file).read_text())
    lua.globals().consumer_count = consumer_count
    lua.execute('''
        targets={"alpha","beta","gamma","delta"};selected={};intents={};resources={ore1,ore2};burners={burner1,burner2}
        for i=3,consumer_count do
            local y=(i-1)*12
            burners[i]=coal_burner(10,y);resources[i]=coal_resource(3.5,y+.5)
            campaign.entities[targets[i]]=burners[i]
            campaign.entities["extra-power-"..i]=entity("small-electric-pole","electric-pole",7.5,y+4.5,.2)
            campaign.entities["extra-witness-"..i]=entity("assembling-machine-1","assembling-machine",6.5,y+6.5,1.4)
        end
        for i=1,consumer_count do selected[i]=targets[i];intents[i]={source="coal:"..targets[i]..":chest",target=targets[i],item="coal",destination="fuel"} end
        campaign.set_solid_intents(intents);campaign.set_coal_targets(selected)
        for _,target in ipairs(selected) do coal_build(target,"chest") end
        for _,target in ipairs(selected) do coal_build_corridor(target) end
        for _,target in ipairs(selected) do coal_build(target,"drill") end
        before=paid_calls;units={};receipts={}
        for _,target in ipairs(selected) do
            local row=coal_offer(target)
            assert(row.state=="ready" and row.flow.delivered_lower==0)
            for _,part in pairs(row.parts) do
                assert(not units[part.unit_number] and not receipts[part.receipt] and part.paid==1)
                units[part.unit_number]=true;receipts[part.receipt]=true
            end
        end
        for n=1,3 do
            for i=1,consumer_count do resources[i].amount=resources[i].amount-2;burners[i].fuel.values.coal=burners[i].fuel.values.coal+1 end
            game.tick=game.tick+60;campaign.observe()
        end
        result=campaign.observe();assert(paid_calls==before)
        for _,target in ipairs(selected) do assert(result.coal_supply.sources[target].flow.positive_samples==3) end
    ''')
    from coal_supply_fixtures import snapshot
    from jev_factorio import coal_supply
    state = snapshot(lua)
    assert len(coal_supply.sources(state)) == consumer_count
    assert coal_supply.flow_complete(state)  # API-double predicate, not engine acceptance.
