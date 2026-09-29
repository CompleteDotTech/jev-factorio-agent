-- Preserve the installed factory/route closures while exposing the connector
-- ownership ledger through campaign.observe() for controller snapshots.
local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign)
local solid=assert(rt.solid_routes)
assert(rt.connector_observer_bridge_v1==nil,"Connector observer bridge already installed")
assert(type(c.observe)=="function" and c.observe==solid.observer,
    "Unexpected connector observer owner")
assert(type(c.observe_connector_ownership)=="function",
    "Connector ownership observer is missing")

local previous=c.observe
local bridge={protocol=1,previous_observe=previous,previous_solid_observer=solid.observer}
bridge.observer=function(...)
    local factory=previous(...)
    assert(type(factory)=="table","Factory observer returned no snapshot")
    local ownership=c.observe_connector_ownership()
    assert(type(ownership)=="table" and ownership.protocol==1
        and ownership.session_id==rt.jev_session_id
        and ownership.tick==factory.tick,
        "Connector ownership does not match the factory observation")
    factory.connector_ownership=ownership
    return factory
end

rt.connector_observer_bridge_v1=bridge
solid.observer=bridge.observer
c.observe=bridge.observer
