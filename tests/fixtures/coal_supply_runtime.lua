-- Additional API-shape double. Counts/transport are advanced by the test, NOT physics.
for _,e in ipairs(all_entities) do e.valid=false end
all_entities={};force.mining_drill_productivity_bonus=0
stock={inserter=16,["transport-belt"]=100,["wooden-chest"]=4,["electric-mining-drill"]=4,coal=100}
local function proto(h) return {collision_box={left_top={x=-h,y=-h},right_bottom={x=h,y=h}}} end
prototypes={entity={["wooden-chest"]=proto(.35),["electric-mining-drill"]=proto(1.49),
    inserter=proto(.4),["transport-belt"]=proto(.4)},item={coal={fuel_value=4000000}}}
prototypes.entity["electric-mining-drill"].mining_drill_radius=2.49
prototypes.entity["electric-mining-drill"].vector_to_place_result={0,-2}
prototypes.entity["electric-mining-drill"].resource_drain_rate_percent=100
prototypes.entity["electric-mining-drill"].drops_full_belt_stacks=false
function coal_burner(x,y)
    local e=entity("stone-furnace","furnace",x,y,.7)
    e.burner={remaining_burning_fuel=0,heat=0,currently_burning=nil};e.fuel.values.coal=4
    return e
end
burner1=coal_burner(10,0);burner2=coal_burner(10,12)
witness1=entity("assembling-machine-1","assembling-machine",6.5,-6.5,1.4)
witness2=entity("assembling-machine-1","assembling-machine",6.5,18.5,1.4)
pole1=entity("small-electric-pole","electric-pole",7.5,4.5,.2)
pole2=entity("small-electric-pole","electric-pole",7.5,16.5,.2)
function coal_resource(x,y)
    local e=entity("coal","resource",x,y,.01);e.amount=1000;e.minable=true
    e.prototype={infinite_resource=false,resource_category="basic-solid",mineable_properties={minable=true,
        products={{type="item",name="coal",amount=1}}}}
    return e
end
ore1=coal_resource(3.5,.5);ore2=coal_resource(3.5,12.5)
campaign.entities={alpha=burner1,beta=burner2,power1=pole1,power2=pole2,witness1=witness1,witness2=witness2}
campaign.receipts={}
game.connected_players={player}
local observe=campaign.observe
campaign.observe=function()
    local result=observe()
    for role,e in pairs(campaign.entities) do if result.entities[role] then result.entities[role].electric_network_id=e.electric_network_id end end
    return result
end
campaign.transfer=function(role,item,quantity,receipt,extracting)
    assert(not extracting and (stock[item] or 0)>=quantity and not campaign.receipts[receipt],"fixture transfer rejected")
    local e=campaign.entities[role];stock[item]=stock[item]-quantity;e.fuel.values[item]=(e.fuel.values[item] or 0)+quantity
    if lose_manual_receipt then error("fixture lost manual acknowledgement before receipt") end
    campaign.receipts[receipt]={role=role,item=item,quantity=quantity,unit_number=e.unit_number,extracting=false,tick=game.tick}
    if lose_manual_reply then error("fixture lost manual reply after receipt") end
    transfers=transfers+1;return "transferred"
end
surface.can_place_entity=function(s)
    if blocked then return false end
    local proto=prototypes.entity[s.name];if not proto then return false end
    local b=proto.collision_box;local x,y=s.position.x,s.position.y
    for _,e in ipairs(all_entities) do if e.valid and e.type~="resource" then
        local t=e.bounding_box
        if x+b.right_bottom.x>t.left_top.x and x+b.left_top.x<t.right_bottom.x
            and y+b.right_bottom.y>t.left_top.y and y+b.left_top.y<t.right_bottom.y then return false end
    end end
    return true
end
local topology=update_topology
update_topology=function()
    topology()
    for _,e in ipairs(all_entities) do if e.type=="mining-drill" then
        local v=({[0]={0,-2},[4]={2,0},[8]={0,2},[12]={-2,0}})[e.direction]
        e.drop_position={x=e.position.x+v[1],y=e.position.y+v[2]};e.drop_target=nil
        for _,t in ipairs(all_entities) do if t.valid and t.type=="container" then
            if math.abs(t.position.x-e.drop_position.x)<.35 and math.abs(t.position.y-e.drop_position.y)<.35 then e.drop_target=t end
        end end
    end end
end
fair.place=function(name,p,direction)
    if storage.coal_supply then assert(not storage.coal_supply.placement_reserved(name,p,direction),"fixture paid placement conflicts") end
    assert(surface.can_place_entity{name=name,position=p,direction=direction} and stock[name]>=1,"fixture paid placement rejected")
    stock[name]=stock[name]-1;paid_calls=paid_calls+1
    local kind=name=="wooden-chest" and "container" or name=="electric-mining-drill" and "mining-drill" or name=="inserter" and "inserter" or "transport-belt"
    local h=name=="wooden-chest" and .35 or name=="electric-mining-drill" and 1.49 or .4
    local e=entity(name,kind,p.x,p.y,h);e.direction=direction
    if kind=="mining-drill" then e.mining_area={left_top={x=p.x-2.49,y=p.y-2.49},right_bottom={x=p.x+2.49,y=p.y+2.49}} end
    update_topology()
    if lose_place_receipt then error("fixture return loss after actor placement") end
    return {name=name,position=e.position,unit_number=e.unit_number}
end
function configure_coal()
    campaign.set_solid_intents({{source="coal:alpha:chest",target="alpha",item="coal",destination="fuel"},
        {source="coal:beta:chest",target="beta",item="coal",destination="fuel"}})
    campaign.set_coal_targets({"alpha","beta"})
end
function coal_offer(target)
    local row=campaign.observe().coal_supply.sources[target or "alpha"]
    assert(row,"Missing coal fixture offer");return row
end
function coal_args(target,part)
    local row=coal_offer(target);return {target=target,layout=row.layout,part=part,receipt="coal:"..target..":"..part}
end
function coal_build(target,part)
    local p=coal_args(target,part);campaign.prepare_coal_source(p);campaign.build_coal_source(p);return p
end
function coal_corridor(target)
    campaign.observe()
    for _,row in pairs(storage.solid_routes.offers) do if row.target.role==target then return row end end
    for _,row in pairs(storage.solid_routes.cells) do if row.target.role==target then return row end end
    error("Missing coal corridor")
end
function coal_build_corridor(target)
    local cell=coal_corridor(target)
    for _,s in ipairs(cell.steps) do
        local p={route=cell.route,layout=cell.layout,part=s.part,receipt=target..":"..s.part}
        campaign.prepare_solid_route(p);campaign.build_solid_route(p)
    end
    campaign.observe();return cell
end
function coal_all()
    coal_build("alpha","chest");coal_build("beta","chest")
    coal_build_corridor("alpha");coal_build_corridor("beta")
    coal_build("alpha","drill");coal_build("beta","drill")
    return campaign.observe()
end
function coal_pulse()
    ore1.amount=ore1.amount-2;ore2.amount=ore2.amount-2
    -- Two mined, delivered and one burned per consumer in this modeled interval.
    burner1.fuel.values.coal=burner1.fuel.values.coal+1
    burner2.fuel.values.coal=burner2.fuel.values.coal+1
    game.tick=game.tick+60;return campaign.observe()
end
