-- Synthetic engine fixture, not a Factorio simulation or native acceptance.
local function p(x,y) return {x=x,y=y} end
local function same(a,b) return math.abs(a.x-b.x)<0.01 and math.abs(a.y-b.y)<0.01 end
local function center(a) return p(math.floor(a.x)+0.5,math.floor(a.y)+0.5) end
local function rotate(v,turns) for _=1,turns do v=p(-v.y,v.x) end; return v end
local function add(a,b) return p(a.x+b.x,a.y+b.y) end
handlers={}
defines={inventory={fuel=1,furnace_source=2,furnace_result=3,chest=4},build_check_type={manual=1},events={on_tick=1}}
script={get_event_handler=function(event) return handlers[event] end,on_event=function(event,fn) handlers[event]=fn end}
force={mining_drill_productivity_bonus=0}
prototypes={entity={
    ["burner-mining-drill"]={tile_width=2,tile_height=2,vector_to_place_result=p(-0.5,-1.85)},
    ["burner-inserter"]={inserter_pickup_position=p(0,-1),inserter_drop_position=p(0,1.2)}
}}
entities,resources={},{}
local surface={}
local function inventory()
    local values={}
    return {values=values,get_item_count=function(item) return values[item] or 0 end}
end
function create(name,position,dir,id)
    local e={name=name,position=position,direction=dir,unit_number=id,force=force,surface=surface,valid=true,
        products_finished=20,held_stack={valid_for_read=false},crafting=false,invs={}}
    e.get_inventory=function(kind) e.invs[kind]=e.invs[kind] or inventory(); return e.invs[kind] end
    e.get_recipe=function() return {name="iron-plate"} end
    e.is_crafting=function() return e.crafting end
    e.lines={inventory(),inventory()}
    e.get_transport_line=function(n)
        return {get_item_count=function(item)
            local v=e.lines[n].values
            if item then return v[item] or 0 end
            local total=0;for _,c in pairs(v) do total=total+c end;return total
        end}
    end
    local width=(name=="stone-furnace" or name=="burner-mining-drill") and 2 or 1
    e.bounding_box={left_top=p(position.x-width/2+0.1,position.y-width/2+0.1),right_bottom=p(position.x+width/2-0.1,position.y+width/2-0.1)}
    if name=="burner-mining-drill" then e.mining_area={left_top=p(position.x-1,position.y-1),right_bottom=p(position.x+1,position.y+1)} end
    e.belt_neighbours={inputs={},outputs={}}
    entities[#entities+1]=e
    return e
end
surface.find_entity=function(name,position)
    for _,e in ipairs(entities) do if e.valid and e.name==name and same(e.position,position) then return e end end
end
surface.can_place_entity=function(q)
    if obstacle and obstacle(q) then return false end
    local width=q.name=="burner-mining-drill" and 2 or 1
    for _,e in ipairs(entities) do if e.valid then
        local half=e.name=="stone-furnace" or e.name=="burner-mining-drill"
        local extent=(half and 1 or 0.5)+width/2-0.05
        if math.abs(e.position.x-q.position.x)<extent and math.abs(e.position.y-q.position.y)<extent then return false end
    end end
    return true
end
surface.find_entities_filtered=function(q)
    local result={}; local pool
    if q.type=="resource" or q.name=="iron-ore" or q.name=="copper-ore" then pool=resources else pool=entities end
    for _,e in ipairs(pool) do
        local include=e.valid
        if q.name then include=include and e.name==q.name end
        if q.type and q.type~="resource" then
            local types=type(q.type)=="table" and q.type or {q.type}; local matches=false
            for _,t in ipairs(types) do if e.name==t then matches=true end end
            include=include and matches
        end
        if q.position then include=include and (e.position.x-q.position.x)^2+(e.position.y-q.position.y)^2 <= q.radius^2 end
        if q.area then local box=q.area; include=include and e.position.x>=box.left_top.x and e.position.x<=box.right_bottom.x and e.position.y>=box.left_top.y and e.position.y<=box.right_bottom.y end
        if include then result[#result+1]=e;if q.limit and #result>=q.limit then break end end
    end
    return result
end
resources[1]={name="iron-ore",position=p(0.5,0.5),amount=1000,valid=true,minable=true}
resources[2]={name="iron-ore",position=p(-0.5,-0.5),amount=1000,valid=true,minable=true}
stock={["burner-mining-drill"]=1,["burner-inserter"]=1,["transport-belt"]=200,coal=100}
character={valid=true,unit_number=9}
player={connected=true,character=character,cheat_mode=false,force=force,surface=surface,position=p(0,0),crafting_queue_size=0,
    get_item_count=function(item) return stock[item] or 0 end}
game={tick=300,speed=1}
source=create("stone-furnace",p(10,0),0,17)
source.get_inventory(1).values.coal=10
chest=create("wooden-chest",p(13.5,0.5),0,19)
output_arm=create("burner-inserter",p(12.5,0.5),4,18)
output_arm.pickup_target,output_arm.drop_target=source,chest
storage={jev_session_id="input-test",agent_characters={character},campaign={entities={
    ["recipe:iron-plate"]=source,["out:arm"]=output_arm,["out:chest"]=chest
}}}
local campaign=storage.campaign
storage.fair={actor=function()
    assert(player.connected and player.character==character and game.speed==1 and not player.cheat_mode,"Fair invariant")
    return player
end}
placements=0
storage.fair.place=function(name,pos,dir)
    storage.fair.actor()
    assert((player.position.x-pos.x)^2+(player.position.y-pos.y)^2<100,"Normal reach")
    assert(surface.can_place_entity{name=name,position=pos,direction=dir},"Obstructed native placement")
    assert((stock[name] or 0)>0,"Missing item")
    stock[name]=stock[name]-1;placements=placements+1
    create(name,pos,dir,100+placements)
end
campaign.observe=function() return {tick=game.tick} end
campaign.transfer=function(role,item,count,receipt,extracting)
    local e=campaign.entities[role]; assert(e and e.valid)
    local inv=e.get_inventory(extracting and 4 or 1).values
    if extracting then assert((inv[item] or 0)>=count);inv[item]=inv[item]-count;stock[item]=(stock[item] or 0)+count
    else assert((stock[item] or 0)>=count);stock[item]=stock[item]-count;inv[item]=(inv[item] or 0)+count end
end
storage.output_buffers={cells={["recipe:iron-plate"]={entity=source,source_unit=17,layout="output:17",item="iron-plate",
    chest_role="out:chest",flow={verified=true},parts={chest={entity=chest},inserter={entity=output_arm}}}},
    observer=campaign.observe,transfer=campaign.transfer}
helpers={table_to_json=function(value) last_result=value; return "{}" end}
rcon={print=function() end}
function connect()
    local cell=storage.input_routes.cells["recipe:iron-plate"]
    if not cell or not cell.parts.drill then return end
    local drill,arm=cell.parts.drill.entity,cell.parts.inserter.entity
    drill.drop_target=cell.parts["belt:1"].entity
    drill.drop_position=drill.drop_target.position
    drill.mining_target=resources[1]
    arm.pickup_target=cell.parts["belt:"..cell.belt_count].entity;arm.drop_target=source
    for n=1,cell.belt_count do
        cell.parts["belt:"..n].entity.belt_neighbours={inputs=n>1 and {cell.parts["belt:"..(n-1)].entity} or {},
            outputs=n<cell.belt_count and {cell.parts["belt:"..(n+1)].entity} or {}}
    end
end
function build_all()
    local observed=campaign.observe()
    local row=observed.input_routes.sources["recipe:iron-plate"]
    assert(row,"No surveyed route")
    for _,spec in ipairs(row.steps) do
        player.position=spec.position -- Fixture movement, not a native-control implementation.
        local params={source=row.source,layout=row.layout,part=spec.part,receipt="receipt:"..spec.part,reserve_belts=20}
        campaign.prepare_input_route(params);campaign.build_input_route(params)
    end
    connect(); campaign.observe()
    return storage.input_routes.cells["recipe:iron-plate"]
end
function pulse()
    game.tick=game.tick+60;resources[1].amount=resources[1].amount-1
    source.products_finished=source.products_finished+1
    local inv=chest.get_inventory(4).values;inv["iron-plate"]=(inv["iron-plate"] or 0)+1
    campaign.observe()
end
