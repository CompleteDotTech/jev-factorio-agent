-- Synthetic engine for the actual v2 Lua observer, never native-game evidence.
local function position(x,y) return {x=x,y=y} end
storage={jev_session_id="atomic-fixture",campaign={},fair={}}
game={tick=10,speed=1,tick_paused=false};helpers={};output={}
defines={inventory={chest=1},entity_status={working=1,no_fuel=2}}
force={index=2};surface={index=1};entities={};tiles={};resources={}
query_count,discovery_count,campaign_count,control_count=0,0,0,0
for index,name in ipairs({"wood","coal","iron-ore","copper-ore","stone"}) do
    resources[name]={name=name,position=position(index,0),unit_number=index+100,
        surface=surface,valid=true,minable=true,type="resource",amount=100}
end
actor_items={{name="coal",count=8,quality="normal"}}
player={position=position(3,4),character={unit_number=17},surface=surface,force=force}
player.get_main_inventory=function() return {get_contents=function() return actor_items end} end
player.update_selected_entity=function(p)
    player.selected=nil
    for _, entity in pairs(resources) do if entity.valid and not entity.obscured
        and p.x==entity.position.x and p.y==entity.position.y then player.selected=entity end end
end
surface.find_entity=function(name,p)
    local e=resources[name]
    if e and e.valid and e.position.x==p.x and e.position.y==p.y then return e end
    for _,entity in ipairs(entities) do
        if entity.valid and entity.name==name
            and entity.position.x==p.x and entity.position.y==p.y then return entity end
    end
end
surface.find_entities_filtered=function(q)
    query_count=query_count+1;queries=queries or {};queries[#queries+1]=q
    local result={}
    for _,e in ipairs(entities) do
        local match=e.valid and e.surface==surface
        if q.force then match=match and e.force==q.force end
        local names=type(q.name)=="table" and q.name or {q.name};local named=false
        for _,name in ipairs(names) do if name==e.name then named=true end end
        match=match and named
        if q.position then match=match and (q.position.x-e.position.x)^2+(q.position.y-e.position.y)^2<=q.radius^2 end
        if match then result[#result+1]=e;if q.limit and #result>=q.limit then break end end
    end
    return result
end
surface.find_tiles_filtered=function(q)
    query_count=query_count+1;queries=queries or {};queries[#queries+1]=q
    local result={}
    for _,tile in ipairs(tiles) do
        if tile.valid and (tile.name=="water" or tile.name=="deepwater")
            and (q.position.x-tile.position.x)^2+(q.position.y-tile.position.y)^2<=q.radius^2 then
            result[#result+1]=tile;if #result>=q.limit then break end
        end
    end
    return result
end
storage.fair.actor=function() return player end
storage.fair.observe=function()
    control_count=control_count+1;lease=game.tick+180
    return {tick=game.tick,position=position(player.position.x,player.position.y),
        status="idle",walking=false,mining=false,movement_started=false,path_requests=0,gained=0}
end
storage.fair.discover_mine_target=function(item,origin,radius)
    discovery_count=discovery_count+1
    local entity=resources[item]
    if absent or not entity.valid or entity.amount<=0 or not entity.minable or entity.obscured then return {} end
    return {name=entity.name,position=entity.position,unit_number=entity.unit_number,surface_index=surface.index}
end
storage.campaign.observe=function()
    campaign_count=campaign_count+1
    return {tick=game.tick,exploration_radius=8,entities={},receipts={fresh={sequence=campaign_count}},
        researched={},rockets_launched=0,rocket_baseline=0,player_bound=true,player_connected=true,
        acceptance_runtime={schema=1,session_id=storage.jev_session_id,actor_unit=player.character.unit_number,
            surface_index=surface.index,force_index=force.index,speed=1,tick_paused=false}}
end
function add_drill(unit,x,y)
    local e={name="burner-mining-drill",unit_number=unit,position=position(x,y),drop_position=position(x+2,y),
        surface=surface,force=force,status=1,valid=true,
        get_fuel_inventory=function() return {get_contents=function() return {{name="coal",count=3,quality="normal"}} end} end}
    entities[#entities+1]=e;return e
end
function add_chest(unit,x,y)
    local e={name="wooden-chest",unit_number=unit,position=position(x,y),surface=surface,force=force,valid=true,
        get_inventory=function() return {get_contents=function() return {{name="iron-ore",count=7}} end} end}
    entities[#entities+1]=e;return e
end
helpers.table_to_json=function(value) captured=value;return "{}" end
rcon={print=function(value) output[#output+1]=value end}
