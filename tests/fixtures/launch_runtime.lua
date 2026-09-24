-- Synthetic engine fixture; game-world functions below stand in for native APIs.
local function inventory(initial)
    local stock=initial or {};local value={stock=stock,limit=10000}
    value.get_contents=function()
        local result={};for name,count in pairs(stock) do if count>0 then
            result[#result+1]={name=name,count=count,quality="normal"} end end
        return result
    end
    value.get_item_count=function(name) return stock[name] or 0 end
    value.can_insert=function(q) return (stock[q.name] or 0)+q.count<=value.limit end
    value.is_empty=function() return #value.get_contents()==0 end
    value.remove=function(q) local n=math.min(stock[q.name] or 0,q.count);stock[q.name]=(stock[q.name] or 0)-n;return n end
    value.insert=function(q) local n=math.min(q.count,math.max(0,value.limit-(stock[q.name] or 0)));stock[q.name]=(stock[q.name] or 0)+n;return n end
    return value
end
make_inventory=inventory
main=inventory({["raw-fish"]=0,["cargo-landing-pad"]=1});cargo=inventory()
pads={};builds=0;launches=0;mines=0;force={index=1,technologies={["rocket-silo"]={researched=true}},recipes={}}
surface={index=1};character={valid=true,unit_number=10};events={};game={tick=100,speed=1}
defines={inventory={rocket_silo_rocket=99},rocket_silo_status={rocket_ready=9},build_check_type={manual=1},events={on_player_mined_entity=42}}
script={active_mods={base="2.0.77"},get_event_handler=function(event)return events[event] end,
    on_event=function(event,handler) events[event]=handler end}
prototypes={entity={["cargo-landing-pad"]={tile_width=8}}}
helpers={table_to_json=function(value) return value end};rcon={print=function(value) returned=value end}
player={index=1,connected=true,character=character,force=force,surface=surface,position={x=0,y=0},crafting_queue_size=0,
    get_item_count=main.get_item_count,get_main_inventory=function()return main end}
player.can_reach_entity=function(entity) return entity.reachable~=false end
player.update_selected_entity=function(position) player.selected=not obscured and fish or nil end
fish={name="fish",type="fish",valid=true,minable=true,surface=surface,position={x=2,y=0}}
rocket={valid=true,unit_number=31}
silo={name="rocket-silo",type="rocket-silo",valid=true,unit_number=30,surface=surface,force=force,
    position={x=10,y=0},rocket=rocket,send_to_orbit_automatically=false,rocket_silo_status=9,
    get_inventory=function(index) assert(index==99);return cargo end}
surface.find_entities_filtered=function(q)
    if q.name=="cargo-landing-pad" then return pads end
    if q.type=="fish" and fish and fish.valid then return {fish} end
    return {}
end
surface.is_chunk_generated=function(_)return true end
surface.can_place_entity=function(q) return not obstacle and q.name=="cargo-landing-pad" end
surface.find_entity=function(name,position)
    for _,entity in ipairs(pads) do if entity.name==name and entity.position.x==position.x and entity.position.y==position.y then return entity end end
end
storage={jev_session_id="test-factory",agent_characters={[1]=character},campaign={entities={["recipe:rocket-part"]=silo}}}
storage.fair={actor=function()assert(game.speed==1 and player.character==character);return player end,
    stop=function()player.mining_state={mining=false} end,
    place=function(name,position,direction)
        assert(not obstacle and player.get_item_count(name)>=1 and not pads[1])
        assert(main.remove{name=name,count=1}==1);builds=builds+1
        local pad={name=name,valid=true,unit_number=50,position=position,force=force,surface=surface,can_insert=function(q)return not pad_full end};pads={pad}
        if placement_error then error("lost placement result") end
        return pad
    end}
storage.campaign.observe=function()return {tick=game.tick,entities={}}end
storage.campaign.launch=function(role) assert(role=="recipe:rocket-part");launches=launches+1
    if launch_error then error("lost launch response") end
end
storage.campaign.transfer=function(role,item,quantity,receipt,extracting)
    transfer_seen={role=role,item=item,quantity=quantity,receipt=receipt,extracting=extracting}
end
storage.campaign.craft=function(name,batches)craft_seen={name=name,batches=batches}end
function harvest_event()
    mines=mines+1
    events[42]{entity=fish,player_index=player.index,buffer=inventory({["raw-fish"]=5})}
    fish.valid=false;main.insert{name="raw-fish",count=5}
end
function observe()return storage.campaign.observe().launch_readiness end
function add_pad()pads={{name="cargo-landing-pad",valid=true,unit_number=50,position={x=0,y=2},force=force,surface=surface,can_insert=function(q)return not pad_full end}} end
