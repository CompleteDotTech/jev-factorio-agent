-- Deterministic API-shape fixture, NOT the native Factorio engine or physics.
game={tick=300,speed=1}
rcon={print=function(_) end};helpers={table_to_json=function(value) return value end}
defines={inventory={chest=1,assembling_machine_input=2},build_check_type={manual=1}}
force={index=1}; surface={index=1}; all_entities={}; paid_calls=0; transfers=0
local function keys(values) local names={};for k,n in pairs(values) do if n>0 then names[#names+1]=k end end;table.sort(names);return names end
function inv(values)
    local result={valid=true,values=values,capacity=200}
    result.get_item_count=function(item) if item then return values[item] or 0 end;local n=0;for _,v in pairs(values) do n=n+v end;return n end
    result.get_insertable_count=function(item) return math.max(0,result.capacity-result.get_item_count()) end
    result.is_empty=function() return result.get_item_count()==0 end
    return setmetatable(result,{__len=function() return 20 end,__index=function(_,i)
        if type(i)~="number" then return nil end
        local name=keys(values)[i]
        return name and {valid_for_read=true,name=name,count=values[name],quality={name="normal"}} or {valid_for_read=false}
    end})
end
local function point_in(p,b) return p.x>b.left_top.x and p.x<b.right_bottom.x and p.y>b.left_top.y and p.y<b.right_bottom.y end
function entity(name,kind,x,y,half)
    local e={valid=true,quality={name="normal"},name=name,type=kind,unit_number=100+#all_entities,position={x=x,y=y},direction=0,
        force=force,surface=surface,bounding_box={left_top={x=x-half,y=y-half},right_bottom={x=x+half,y=y+half}},
        energy=100,products_finished=0,productivity_bonus=0,input=inv({}),output=inv({}),fuel=inv({}),
        held_stack={valid_for_read=false},prototype={get_supply_area_distance=function(quality) assert(quality.name=="normal");return 10 end},electric_network_id=1,crafting=false}
    e.get_inventory=function(kind) return kind==1 and e.output or e.input end
    e.get_output_inventory=function() return e.output end
    e.get_fuel_inventory=function() return e.fuel end
    e.get_module_inventory=function() return nil end
    e.get_recipe=function() return e.recipe end
    e.is_crafting=function() return e.crafting end
    e.lines={inv({}),inv({})}
    for _,line in pairs(e.lines) do line.get_contents=function()
        local values={};for k,n in pairs(line.values) do values[#values+1]={name=k,count=n,quality="normal"} end;return values
    end end
    e.get_transport_line=function(lane) return e.lines[lane] end
    all_entities[#all_entities+1]=e
    return e
end
source=entity("assembling-machine-1","assembling-machine",0.5,0.5,1.4)
target=entity("assembling-machine-1","assembling-machine",7.5,0.5,1.4)
source.recipe={name="iron-gear-wheel",ingredients={{type="item",name="iron-plate",amount=2}},products={{type="item",name="iron-gear-wheel",amount=1}}}
target.recipe={name="automation-science-pack",ingredients={{type="item",name="iron-gear-wheel",amount=1},{type="item",name="copper-plate",amount=1}},products={{type="item",name="automation-science-pack",amount=1}}}
source.output.values["iron-gear-wheel"]=20
pole=entity("small-electric-pole","electric-pole",3.5,3.5,0.2)
actor={valid=true,unit_number=9,surface=surface,force=force}
stock={inserter=10,["transport-belt"]=100}
player={index=1,character=actor,connected=true,force=force,surface=surface,crafting_queue_size=0}
player.get_item_count=function(item) return stock[item] or 0 end
player.get_main_inventory=function() return inv(stock) end
game.get_player=function(index) assert(index==1);return player end
local function type_match(e,t)
    if type(t)=="string" then return e.type==t end
    for _,kind in ipairs(t or {}) do if e.type==kind then return true end end
    return t==nil
end
surface.find_entities_filtered=function(q)
    local result={}
    for _,e in ipairs(all_entities) do
        local ok=e.valid and type_match(e,q.type) and (not q.force or q.force==e.force)
        if ok and q.position then local x,y=q.position.x-e.position.x,q.position.y-e.position.y;ok=x*x+y*y<=(q.radius or 0)^2 end
        if ok and q.area then ok=e.position.x>=q.area[1][1] and e.position.x<=q.area[2][1] and e.position.y>=q.area[1][2] and e.position.y<=q.area[2][2] end
        if ok then result[#result+1]=e;if q.limit and #result>=q.limit then break end end
    end
    return result
end
surface.can_place_entity=function(q)
    if blocked then return false end
    local p=q.position
    for _,e in ipairs(all_entities) do
        if e.valid and p.x+0.4>e.bounding_box.left_top.x and p.x-0.4<e.bounding_box.right_bottom.x
            and p.y+0.4>e.bounding_box.left_top.y and p.y-0.4<e.bounding_box.right_bottom.y then return false end
    end
    return true
end
surface.find_entity=function(name,p)
    for _,e in ipairs(all_entities) do if e.valid and e.name==name and e.position.x==p.x and e.position.y==p.y then return e end end
end
local vectors={[0]={0,-1},[4]={1,0},[8]={0,1},[12]={-1,0}}
local function target_at(p,skip)
    for _,e in ipairs(all_entities) do if e~=skip and e.valid and point_in(p,e.bounding_box) then return e end end
end
function update_topology()
    for _,e in ipairs(all_entities) do if e.type=="inserter" then
        local d=vectors[e.direction]
        e.pickup_position={x=e.position.x+d[1],y=e.position.y+d[2]}
        e.drop_position={x=e.position.x-d[1],y=e.position.y-d[2]}
        e.pickup_target,e.drop_target=target_at(e.pickup_position,e),target_at(e.drop_position,e)
    elseif e.type=="transport-belt" then
        local d=vectors[e.direction];e.belt_neighbours={inputs={},outputs={}}
        for _,other in ipairs(all_entities) do if other.type=="transport-belt" and other~=e then
            if other.position.x==e.position.x-d[1] and other.position.y==e.position.y-d[2] then e.belt_neighbours.inputs[#e.belt_neighbours.inputs+1]=other end
            if other.position.x==e.position.x+d[1] and other.position.y==e.position.y+d[2] then e.belt_neighbours.outputs[#e.belt_neighbours.outputs+1]=other end
        end end
    end end
end
campaign={entities={["recipe:iron-gear-wheel"]=source,["recipe:automation-science-pack"]=target,["utility:power"]=pole}}
campaign.observe=function()
    local entities={}
    for role,e in pairs(campaign.entities) do if e.valid then
        entities[role]={name=e.name,position=e.position,unit_number=e.unit_number,
            recipe=e.recipe and e.recipe.name or "",energy=e.energy}
    end end
    return {tick=game.tick,entities=entities,player_bound=player.character==actor,
        player_connected=player.connected,crafting_queue=player.crafting_queue_size}
end
campaign.transfer=function(role,item,quantity,receipt,extracting)
    assert(receipt==(expected_transfer_receipt or "transfer-fixture") and extracting==(expected_extracting or false),"Transfer argument order changed");transfers=transfers+1;return "forwarded"
end
campaign.configure=function(role,recipe) return recipe end
fair={actor=function() assert(player.character==actor and player.connected);return player end}
fair.place=function(name,p,direction)
    assert(surface.can_place_entity{name=name,position=p} and stock[name]>=1,"Fixture ordinary placement rejected")
    stock[name]=stock[name]-1;paid_calls=paid_calls+1
    local e=entity(name,name=="inserter" and "inserter" or "transport-belt",p.x,p.y,0.4);e.direction=direction
    update_topology()
    if lose_place_receipt then error("fixture return loss after actor placement") end
    return {name=name,position=e.position,unit_number=e.unit_number}
end
storage={campaign=campaign,fair=fair,jev_session_id="solid-fixture",jev_player_index=1,agent_characters={[1]=actor}}
function configure()
    campaign.set_solid_intents({{source="recipe:iron-gear-wheel",target="recipe:automation-science-pack",item="iron-gear-wheel",destination="input"}})
end
function offer()
    local rows=campaign.observe().solid_routes.routes
    local _,row=next(rows);assert(row,"Missing fixture offer");return row
end
function args(row,part)
    return {route=row.route,layout=row.layout,part=part or row.steps[1].part,receipt="receipt:"..(part or row.steps[1].part)}
end
function build_all()
    local row=offer()
    for _,s in ipairs(row.steps) do
        local p=args(row,s.part);campaign.prepare_solid_route(p);campaign.build_solid_route(p)
    end
    campaign.observe()
    return storage.solid_routes.cells[row.route]
end
function pulse()
    source.output.values["iron-gear-wheel"]=source.output.values["iron-gear-wheel"]-1
    target.input.values["iron-gear-wheel"]=(target.input.values["iron-gear-wheel"] or 0)+1
    game.tick=game.tick+60;campaign.observe()
end
