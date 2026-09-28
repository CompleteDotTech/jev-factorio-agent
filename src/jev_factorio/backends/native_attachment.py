"""Read-only qualification of an existing native callback installation.

An existing Factorio runtime keeps Lua closures in memory. Re-evaluating an inner
module replaces those closures while outer route/receipt wrappers still refer to
the old ones. A resumed Python adapter must therefore inspect the full installed
chain before constructing an adapter, and must reuse it without Lua installation.
"""
from __future__ import annotations

import hashlib
from importlib.resources import files

from ..iteration_timing import decode_native


# These bytes were installed from main e759462 in the isolated native fixture.
# Changing an asset requires an explicit reviewed migration, not silent reuse.
PINNED_ASSETS = {
    'fair_actions': 'cacb0396a75807bfd9b6987c732cbefe518e96517eb167611707e1ce48d2cbd4',
    'factory': 'f4f42f22b70ed7dc4a85dec627cdcd6be4d28df5c12e8a38066a13f0998ecdbc',
    'launch_readiness': 'b01fe73bc12055d7fc831f84d097094096610033e4d7a1195d623ea190c03bf5',
    'observation': '3cda1c7cf00ada82108a8dd89f52d523a3dff329f6e79a88f7549044ec025488',
    'observation_v2': '983307da88317e690582511d2514046fd8819b2cd6ffa6d0832725b2a450710f',
    'craft_jobs': 'd4034ff9f53076d14346e40b8190fb975a5a1ecf7df857932c6d9bf66ce51781',
    'output_buffers': '5a2cacb48e4623a27f0851b93ff00fac575c75de44c23f64a38130c21bddc39c',
    'input_routes': 'cef2ff8e7a1dc49ec3df6dc2a4faca78409dad303f0cccf87fe5aeb448b145c4',
    'production_sites': 'f215c67e4febf4e77c620e79d1dfd2ce91dae6dd5c1a386a27d1e805ca0dde2b',
    'mining_outposts': 'c517cf286ec1815fd2ea4860ea823036da19dca8074b4803ce7b284faeffb682',
    'solid_routes': '88b8f605e439a1f16783b9f9cc1e222f002f6be6e9b27494dbc309dce55b5809',
    'coal_supply': '3ec3b94b03c86cf963328ef9a6f75551ab285968ccfd50d2e2a25c72e89a242e',
    'successors': '7cd7999d3a4fee0faeb157c81487091b05366d919e17f34ae51a3061274d90ae',
}


PROBE = r'''local rt=jev_fle_runtime
local c=rt and rt.campaign
local f=rt and rt.fair
local l=rt and rt.launch_readiness
local j=c and c.craft_jobs
local b=rt and rt.output_buffers
local i=rt and rt.input_routes
local s=rt and rt.solid_routes
local q=rt and rt.coal_supply
local o=rt and rt.mining_outposts
local p=rt and rt.production_sites
local x=rt and rt.successors
local a=rt and rt.agent_characters and rt.agent_characters[1]
local player=rt and game.get_player(rt.jev_bound_player_index or 1)
local function good(x) return type(x)=="function" end
local ok=rt and type(rt.jev_session_id)=="string" and #rt.jev_session_id>0
    and a and a.valid and player and player.connected and player.character==a
    and player.force==a.force and player.surface==a.surface and not player.cheat_mode
    and game.speed==1 and not game.tick_paused and f and good(f.actor)
    and good(f.bind) and good(f.observe) and good(f.place) and good(f.tick_handler)
if c then ok=ok and good(c.observe) and good(c.transfer) and good(c.configure)
    and l and l.schema==1 and c.launch==l.launch and c.craft==l.craft
    and good(l.observer) and good(c.observation_snapshot) and good(c.observation_snapshot_v2)
end
if j then ok=ok and good(j.observe_wrapper) and good(j.previous_observe)
    and j.previous_observe==l.observer end
if b then ok=ok and b.protocol==1 and good(b.observer) and good(b.transfer)
    and b.previous_observe==j.observe_wrapper and b.previous_transfer==l.transfer
    and script.get_event_handler(defines.events.on_tick)==b.tick_handler end
if i then ok=ok and i.protocol==1 and i.previous_observe==b.observer
    and i.previous_transfer==b.transfer and good(i.observer) and good(i.transfer) end
if s then ok=ok and s.protocol==1 and s.implementation_revision==4
    and s.contract_family=="straight-solid-corridor-v1"
    and s.reservation_contract=="full-corridor-manhattan-v1" and type(s.coal_api)=="table"
    and c.observe==s.observer and c.transfer==s.transfer and c.configure==s.configure
    and i and b and j end
if q then ok=ok and q.revision==4 and s and s.coal==q
    and c.prepare_coal_source==q.prepare and c.build_coal_source==q.build
    and type(q.admission_evidence)=="boolean" end
if o then ok=ok and o.protocol==1 and i and good(c.observe_mining_outposts) end
if p then ok=ok and p.protocol==1 and i and good(c.observe_production_sites) end
if x then ok=ok and x.protocol==1 and i and b and p and j and not o
    and c.successors_enabled==true and good(c.observe_successors) end
local modules={fair_actions=true,factory=c~=nil,launch_readiness=l~=nil,
    observation=c~=nil,observation_v2=c~=nil,craft_jobs=j~=nil,
    output_buffers=b~=nil,input_routes=i~=nil,production_sites=p~=nil,
    mining_outposts=o~=nil,solid_routes=s~=nil,coal_supply=q~=nil,
    successors=x~=nil}
rcon.print(helpers.table_to_json({schema=1,qualified=ok==true,
    session_id=rt and rt.jev_session_id or "",actor_unit=a and a.unit_number or 0,
    modules=modules,solid_intents=s and s.intents or {},coal_targets=q and q.targets or {},
    coal_admission_evidence=q and q.admission_evidence or false}))'''


def readback(client):
    result = decode_native(client.send_command('/sc ' + PROBE))
    if (not isinstance(result, dict) or set(result) != {
            'schema', 'qualified', 'session_id', 'actor_unit', 'modules',
            'solid_intents', 'coal_targets', 'coal_admission_evidence'}
            or result['schema'] != 1 or result['qualified'] is not True
            or not isinstance(result['session_id'], str) or not result['session_id']
            or type(result['actor_unit']) is not int or result['actor_unit'] < 1
            or not isinstance(result['modules'], dict)
            or set(result['modules']) != set(PINNED_ASSETS)
            or any(type(flag) is not bool for flag in result['modules'].values())):
        raise RuntimeError('Existing native callback installation requires reconciliation')
    return result


def require_asset(attachment, name):
    if attachment is None:
        return False
    if (name not in PINNED_ASSETS or attachment['modules'].get(name) is not True):
        raise RuntimeError('Required native capability was not installed in this session')
    asset = files('jev_factorio').joinpath('lua/' + name + '.lua').read_bytes()
    if hashlib.sha256(asset).hexdigest() != PINNED_ASSETS[name]:
        raise RuntimeError('Native Lua source differs from the verified installed revision')
    return True
