-- Fixed read-only recipe dependency witness. request is validated data, supplied
-- by the owner-controlled caller; this source installs no callback or route.
local out={schema="jev.downstream-recipe-witness.v1",status="unqualified",
    reason="unsupported",base_version=script.active_mods.base,epoch={},request={},
    route={},producer={},consumer={},recipe_dependency_verified=false,
    stock_provenance_qualified=false,mutation_authorized=false}
local function need(ok,reason) if not ok then error(reason,0) end end
local function int(v,lo,hi) return type(v)=="number" and v%1==0 and v>=lo and v<=hi end
local function text(v,n) return type(v)=="string" and #v>0 and #v<=(n or 128) and not v:find("[^ -~]") end
local function size(t,n)
    need(type(t)=="table","malformed")
    local count=0;for _ in pairs(t) do count=count+1;need(count<=n,"bound") end
    return count
end
local function array(t,n)
    local count=size(t,n)
    for key in pairs(t) do need(int(key,1,n),"malformed") end
    for i=1,count do need(t[i]~=nil,"malformed") end
    return count
end
local function bill(entity)
    need(entity and entity.valid and entity.quality and entity.quality.name=="normal"
        and entity.type=="assembling-machine" and entity.productivity_bonus==0,"producer")
    local modules=entity.get_module_inventory()
    need(not modules or modules.is_empty(),"producer")
    local recipe=entity.get_recipe()
    need(recipe and text(recipe.name) and array(recipe.products,1)==1,"recipe")
    local product=recipe.products[1]
    need(product.type=="item" and text(product.name) and int(product.amount,1,1000000)
        and (product.probability==nil or product.probability==1)
        and (product.independent_probability==nil or product.independent_probability==1)
        and product.shared_probability==nil
        and (product.extra_count_fraction==nil or product.extra_count_fraction==0)
        and product.amount_min==nil and product.amount_max==nil
        and product.percent_spoiled==nil and product.quality_min==nil
        and product.quality_max==nil and product.quality_change==nil,"recipe")
    local ingredients={}
    for _,row in ipairs(recipe.ingredients or {}) do
        need(row.type=="item" and text(row.name) and int(row.amount,1,1000000)
            and (row.probability==nil or row.probability==1)
            and (row.independent_probability==nil or row.independent_probability==1)
            and row.shared_probability==nil
            and (row.extra_count_fraction==nil or row.extra_count_fraction==0)
            and row.percent_spoiled==nil and row.amount_min==nil
            and row.amount_max==nil and row.quality_min==nil
            and row.quality_max==nil and row.quality_change==nil
            and ingredients[row.name]==nil,"recipe")
        ingredients[row.name]=row.amount
    end
    need(array(recipe.ingredients or {},32)==size(ingredients,32),"recipe")
    return {name=recipe.name,product=product.name,product_amount=product.amount,ingredients=ingredients}
end
local ok,reason=pcall(function()
    need(size(request,7)==7 and text(request.route,256)
        and text(request.producer_role) and int(request.producer_unit,1,9007199254740991)
        and text(request.product_item) and text(request.consumer_role)
        and int(request.consumer_unit,1,9007199254740991)
        and text(request.science_pack),"request")
    for key in pairs(request) do need(key=="route" or key=="producer_role" or key=="producer_unit"
        or key=="product_item" or key=="consumer_role" or key=="consumer_unit"
        or key=="science_pack","request") end
    out.request=request
    need(script.active_mods.base=="2.0.77" and size(script.active_mods,8)==1,"native_version")
    local rt=jev_fle_runtime
    need(rt and rt.campaign and rt.solid_routes and rt.native_installation
        and rt.native_installation.assets and rt.native_installation.assets.solid_routes==
        "88b8f605e439a1f16783b9f9cc1e222f002f6be6e9b27494dbc309dce55b5809"
        and (rt.native_installation.profile=="e759-observation-v2-water-origin-v4"
             or rt.native_installation.profile=="e759-observation-v2-water-origin-v4-manual-cycle-v5")
        and rt.solid_routes.protocol==1 and rt.solid_routes.implementation_revision==4
        and rt.solid_routes.contract_family=="straight-solid-corridor-v1","installed_source")
    local c,r=rt.campaign,rt.solid_routes
    local actor=rt.agent_characters and rt.agent_characters[1]
    local index=rt.jev_bound_player_index
    need(int(index,1,1000000) and text(rt.jev_session_id) and actor and actor.valid
        and int(actor.unit_number,1,9007199254740991),"actor")
    local player=game.get_player(index)
    need(player and player.connected and player.character==actor and not player.cheat_mode
        and #game.connected_players==1 and game.connected_players[1]==player
        and game.speed==1 and not game.tick_paused,"actor")
    out.epoch={session_id=rt.jev_session_id,tick=game.tick,actor_index=index,
        actor_unit=actor.unit_number,surface_index=actor.surface.index,force_index=actor.force.index}
    need(int(game.tick,0,9007199254740991),"epoch")
    need(size(r.cells,4)<=4 and size(c.entities,2048)<=2048,"bound")
    local cell=r.cells[request.route]
    need(cell and cell.route==request.route and cell.pending==nil and cell.fault==nil
        and cell.target and cell.target.role==request.producer_role
        and cell.target.unit_number==request.producer_unit
        and cell.target.inventory=="input" and cell.source and cell.item,"route")
    local producer,consumer,source=c.entities[request.producer_role],
        c.entities[request.consumer_role],c.entities[cell.source.role]
    need(producer and consumer and source and producer~=consumer and source~=producer
        and source~=consumer and producer.unit_number==request.producer_unit
        and consumer.unit_number==request.consumer_unit
        and source.unit_number==cell.source.unit_number,"identity")
    for _,e in ipairs({source,producer,consumer}) do
        need(e.valid and e.quality and e.quality.name=="normal" and e.surface==actor.surface
            and e.force==actor.force,"identity")
    end
    local aliases=0
    for _,e in pairs(c.entities) do
        if e==producer or e==consumer or e==source then aliases=aliases+1 end
    end
    need(aliases==3,"identity")
    need(array(cell.steps,128)>0 and size(cell.parts,128)==#cell.steps,"route")
    local seen_parts,seen_receipts,seen_units={},{},{}
    for _,step in ipairs(cell.steps) do
        need(text(step.part) and not seen_parts[step.part],"route")
        seen_parts[step.part]=true
        local part=cell.parts[step.part]
        need(part and part.paid==1 and text(part.receipt) and part.entity and part.entity.valid
            and part.entity==c.entities[part.role] and part.entity.unit_number==part.unit_number
            and part.entity.surface==actor.surface and part.entity.force==actor.force
            and not seen_receipts[part.receipt] and not seen_units[part.unit_number],"route")
        seen_receipts[part.receipt]=true;seen_units[part.unit_number]=true
    end
    for part in pairs(cell.parts) do need(seen_parts[part],"route") end
    local first,second=bill(producer),bill(consumer)
    local packs={['automation-science-pack']=true,['logistic-science-pack']=true,
        ['military-science-pack']=true,
        ['chemical-science-pack']=true,['production-science-pack']=true,
        ['utility-science-pack']=true,['space-science-pack']=true}
    need(first.product==request.product_item and first.ingredients[cell.item]~=nil
        and second.product==request.science_pack
        and packs[second.product] and second.ingredients[first.product]~=nil
        and cell.target.recipe==first.name,"dependency")
    out.route={id=cell.route,item=cell.item,source_role=cell.source.role,
        source_unit=source.unit_number,target_role=cell.target.role,
        target_unit=producer.unit_number,paid_parts=#cell.steps}
    out.producer={role=request.producer_role,unit=request.producer_unit,recipe=first}
    out.consumer={role=request.consumer_role,unit=request.consumer_unit,recipe=second}
    out.status="observed";out.reason="none";out.recipe_dependency_verified=true
end)
if not ok then
    local allowed={malformed=true,bound=true,request=true,native_version=true,
        installed_source=true,actor=true,epoch=true,route=true,identity=true,
        producer=true,recipe=true,dependency=true}
    out.reason=allowed[reason] and reason or "unsupported"
end
rcon.print(helpers.table_to_json(out))
