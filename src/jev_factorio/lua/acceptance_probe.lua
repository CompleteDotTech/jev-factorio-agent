-- Fixed diagnostic query: no world writes, runtime initialization or campaign callbacks.
local rt = jev_fle_runtime
local campaign = rt and rt.campaign
local agent = rt and rt.agent_characters and rt.agent_characters[1]
local index = rt and rt.jev_player_index or 1
local player = type(index)=="number" and index%1==0 and index>0 and game.get_player(index) or nil
local result = {
    schema=1, tick=game.tick, speed=game.speed, tick_paused=game.tick_paused,
    marked=storage.jev_factorio_session==true, runtime_present=rt~=nil,
    session_id=rt and rt.jev_session_id or "", player_index=index,
    connected=player~=nil and player.connected==true,
    bound=agent~=nil and agent.valid and player~=nil and player.character==agent,
    campaign_present=campaign~=nil, fair_present=rt~=nil and rt.fair~=nil,
    actor_unit=agent and agent.valid and agent.unit_number or 0,
    surface_index=agent and agent.valid and agent.surface.index or 0,
    force_index=agent and agent.valid and agent.force.index or 0,
    mods=script.active_mods, entities={}, input_routes={}, output_buffers={}, truncated=false
}
local count=0
for role, entity in pairs(campaign and campaign.entities or {}) do
    count=count+1
    if count>512 then result.truncated=true;break end
    if entity.valid then
        result.entities[role]={name=entity.name,unit_number=entity.unit_number,
            position={x=entity.position.x,y=entity.position.y}}
    end
end
local function route_rows(runtime, destination, maximum)
    local sources=0
    for source,cell in pairs(runtime and runtime.cells or {}) do
        sources=sources+1
        if sources>maximum then result.truncated=true;break end
        local row={source_unit=cell.source_unit,layout=cell.layout,parts={},fault=cell.fault~=nil}
        local parts=0
        for part,entry in pairs(cell.parts or {}) do
            parts=parts+1
            if parts>66 then result.truncated=true;break end
            row.parts[part]={role=entry.role,receipt=entry.receipt,unit_number=entry.unit_number,paid=entry.paid}
        end
        destination[source]=row
    end
end
route_rows(rt and rt.input_routes,result.input_routes,4)
route_rows(rt and rt.output_buffers,result.output_buffers,5)
rcon.print(helpers.table_to_json(result))
