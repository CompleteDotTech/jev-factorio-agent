"""Build the additive command for a separately ROOT-signed one-use installer.

This module does not perform RCON or sign/authorize anything. The host installer
must verify its own signature, original writer lock, exact checkpoint, source,
attachment, current native endpoint proof and durable P/R before dispatch.
Cancellation/failed returns consume its nonce and require read-only reconciliation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.resources import files
import json
import math
import re

from ..bootstrap_output import MODULE, PROFILE
from .native_attachment import CLOSED_WORLD_PROFILE, CALLBACKS_EXPR

HASH_FIELDS = {"authorization_sha256", "checkpoint_sha256", "source_fingerprint",
               "source_tree", "source_revision", "attachment_sha256", "native_identity_report_sha256"}
REQUIRED = HASH_FIELDS | {"origin", "session_id", "actor_unit", "surface_index", "force_index",
    "original_bound_drill_unit", "drill_unit", "chest_unit", "drill_position", "drop_position",
    "chest_position", "lock_device", "lock_inode"}


def asset_sha256():
    return hashlib.sha256(files("jev_factorio").joinpath("lua/bootstrap_output_v1.lua").read_bytes()).hexdigest()


def validate_scope(scope):
    if not isinstance(scope, dict) or set(scope) != REQUIRED:
        raise ValueError("Bootstrap reconciliation requires exact bounded scope")
    if scope["origin"] != "legacy_authorized_current_asset":
        raise ValueError("Legacy reconciliation cannot claim historical paid ownership")
    if not isinstance(scope["session_id"], str) or not 1 <= len(scope["session_id"]) <= 128:
        raise ValueError("Invalid bootstrap session")
    for key in HASH_FIELDS:
        size = 40 if key in {"source_revision", "source_tree"} else 64
        if (not isinstance(scope[key], str)
                or not re.fullmatch("[0-9a-f]{" + str(size) + "}", scope[key])
                or scope[key] == "0" * size):
            raise ValueError("Missing exact bootstrap authority/source/evidence pin")
    for key in ("actor_unit", "surface_index", "force_index", "original_bound_drill_unit",
                "drill_unit", "chest_unit", "lock_device", "lock_inode"):
        if type(scope[key]) is not int or not 1 <= scope[key] <= 2**53 - 1:
            raise ValueError("Invalid bootstrap identity/ownership lock")
    if scope["original_bound_drill_unit"] != scope["drill_unit"] or scope["drill_unit"] == scope["chest_unit"]:
        raise ValueError("Bootstrap producer/endpoint identity changed")
    for key in ("drill_position", "drop_position", "chest_position"):
        p = scope[key]
        if (not isinstance(p, dict) or set(p) != {"x", "y"}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in p.values())):
            raise ValueError("Invalid bootstrap endpoint geometry")
    if any(abs(scope["chest_position"][a] - scope["drop_position"][a]) >= .5 for a in ("x", "y")):
        raise ValueError("Bootstrap chest is not the original producer's unique output")
    return deepcopy(scope)


def proposed_manifest(attachment, scope):
    scope = validate_scope(scope)
    if (not isinstance(attachment, dict) or attachment.get("qualified") is not True
            or attachment.get("session_id") != scope["session_id"]
            or type(attachment.get("actor_unit")) is not int
            or attachment.get("actor_unit") != scope["actor_unit"]
            or attachment.get("modules", {}).get(MODULE) is not False):
        raise ValueError("Bootstrap install requires the exact qualified original attachment")
    old = attachment.get("native_installation")
    if not isinstance(old, dict) or old.get("profile") != CLOSED_WORLD_PROFILE:
        raise ValueError("Bootstrap install requires the retained closed-world callback chain")
    result = deepcopy(old)
    if MODULE in result.get("assets", {}):
        raise ValueError("Bootstrap asset already exists; reconcile rather than reinstall")
    result["assets"][MODULE] = asset_sha256()
    result["profile"] = PROFILE
    return result


def command(attachment, scope):
    """One command, old callbacks/assets retained, prepared journal before effects."""
    scope = validate_scope(scope)
    proposed = proposed_manifest(attachment, scope)
    old = attachment["native_installation"]
    encode = lambda value: "helpers.json_to_table(" + json.dumps(json.dumps(value, sort_keys=True,
        separators=(",", ":"), allow_nan=False)) + ")"
    asset = files("jev_factorio").joinpath("lua/bootstrap_output_v1.lua").read_text()
    # Full callback/asset equality BEFORE any mutation. No broad discovery,
    # campaign reset, old receipt rewrite, source install or actor movement.
    return '''local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair);local n=assert(rt.native_installation)
local function same(a,b)
    if type(a)~=type(b) then return false end
    if type(a)~="table" then return a==b end
    for k,v in pairs(a) do if not same(v,b[k]) then return false end end
    for k in pairs(b) do if a[k]==nil then return false end end
    return true
end
local old=''' + encode(old) + '''
assert(n.schema==old.schema and n.session_id==old.session_id and n.actor_unit==old.actor_unit
    and n.profile==old.profile and same(n.assets,old.assets),"Native bootstrap attachment changed")
local callbacks=''' + CALLBACKS_EXPR + '''
assert(same(n.callbacks,callbacks),"Native bootstrap callback chain changed")
local scope=''' + encode(scope) + '''
local player=assert(f.actor());local actor=assert(player.character)
assert(rt.jev_session_id==scope.session_id and actor.unit_number==scope.actor_unit
    and actor.surface.index==scope.surface_index and actor.force.index==scope.force_index
    and player.connected and not player.cheat_mode and game.speed==1 and not game.tick_paused)
assert(not player.walking_state.walking and not player.mining_state.mining
    and (not f.job or f.job.status=="completed" or f.job.status=="failed"))
local journal=rt.coal_manual_journal_v1;assert(not journal or not journal.pending)
for _,row in pairs(journal and journal.rows or {}) do
    assert(not row.pending and not row.fault and row.status~="pending",
        "Coal gather history requires reconciliation")
end
local cycle=rt.coal_manual_cycle_v2;assert(not cycle or not cycle.active)
local coal=rt.coal_supply
for _,row in pairs(coal and coal.rows or {}) do
    assert(not row.pending and not row.manual_pending and not row.fault,"Coal ownership requires reconciliation")
end
local routes=rt.solid_routes
for _,cell in pairs(routes and routes.cells or {}) do
    assert(not cell.pending and not cell.fault,"Solid ownership requires reconciliation")
end
local connectors=c.connector_ledger;assert(not connectors or not connectors.active)
for _,row in pairs(connectors and connectors.routes or {}) do assert(not row.pending) end
assert(not rt.launch_readiness or not rt.launch_readiness.pending_fish)
assert(not c.entities["bootstrap-output:iron-ore"],"Bootstrap role conflict")
for _,entity in pairs(c.entities) do
    assert(entity.unit_number~=scope.chest_unit,"Bootstrap endpoint already has a campaign role")
end
assert(not rt.bootstrap_output_v1 and not rt.bootstrap_output_install_authorization)
rt.bootstrap_output_install_authorization=scope
do
''' + asset + '''
end
assert(same(callbacks,''' + CALLBACKS_EXPR + ''')==false) -- Only new capability callbacks were added.
for name,callback in pairs(callbacks) do
    assert((''' + CALLBACKS_EXPR + ''')[name]==callback,"Original native callback changed")
end
local proposed=''' + encode(proposed) + '''
for name,digest in pairs(old.assets) do assert(proposed.assets[name]==digest) end
n.assets=proposed.assets;n.profile=proposed.profile;n.callbacks=''' + CALLBACKS_EXPR + '''
local output=assert(rt.bootstrap_output_v1.observe())
assert(output.origin=="legacy_authorized_current_asset"
    and output.historical_paid_placement_proven==false and output.drill_unit==scope.drill_unit
    and output.chest_unit==scope.chest_unit and output.authorization_sha256==scope.authorization_sha256)
rcon.print("JEV_BOOTSTRAP_OUTPUT_INSTALLED|1")'''


def reconciliation_command(attachment, scope):
    """Complete only the retained prepared installation under fresh ROOT review.

    The original consumed attempt is not dispatched again. This command never
    reloads an asset, spends inventory, calls transfer, or clears action journals.
    Missing/changed prepared code or authority remains blocked for separate repair.
    """
    scope = validate_scope(scope)
    old = attachment['native_installation']
    proposed = proposed_manifest(attachment, scope)
    encode = lambda value: 'helpers.json_to_table(' + json.dumps(json.dumps(
        value, sort_keys=True, separators=(',', ':'), allow_nan=False)) + ')'
    return '''local rt=assert(jev_fle_runtime)
local c=assert(rt.campaign);local f=assert(rt.fair);local n=assert(rt.native_installation)
local b=assert(rt.bootstrap_output_v1,"No prepared bootstrap installation")
local function same(a,b)
 if type(a)~=type(b) then return false end
 if type(a)~="table" then return a==b end
 for k,v in pairs(a) do if not same(v,b[k]) then return false end end
 for k in pairs(b) do if a[k]==nil then return false end end
 return true
end
local old=''' + encode(old) + ';local proposed=' + encode(proposed) + '''
local scope=''' + encode(scope) + '''
assert(same(b.install_authorization,scope),"Prepared installation authority changed")
assert(n.schema==old.schema and n.session_id==old.session_id and n.actor_unit==old.actor_unit)
assert((n.profile==old.profile and same(n.assets,old.assets))
 or (n.profile==proposed.profile and same(n.assets,proposed.assets)),"Prepared asset pins changed")
assert(b.protocol==1 and b.session_id==scope.session_id and b.actor_unit==scope.actor_unit
 and b.surface_index==scope.surface_index and b.force_index==scope.force_index
 and (b.phase=="ready" or b.phase=="install_prepared")
 and b.original_place==f.place and b.original_transfer==c.transfer
 and type(b.complete_install)=="function" and type(b.reconcile_pending)=="function"
 and type(b.observe)=="function" and type(b.extract)=="function"
 and type(b.place)=="function" and type(b.bind_paid)=="function"
 and not b.placement_pending and not b.transfer_pending)
local player=assert(f.actor());local actor=assert(player.character)
assert(player.connected and not player.cheat_mode and game.speed==1 and not game.tick_paused
 and not player.walking_state.walking and not player.mining_state.mining
 and (not f.job or f.job.status=="completed" or f.job.status=="failed"))
local journal=rt.coal_manual_journal_v1;assert(not journal or not journal.pending)
for _,row in pairs(journal and journal.rows or {}) do
 assert(not row.pending and not row.fault and row.status~="pending")
end
local cycle=rt.coal_manual_cycle_v2;assert(not cycle or not cycle.active)
local coal=rt.coal_supply
for _,row in pairs(coal and coal.rows or {}) do assert(not row.pending and not row.manual_pending and not row.fault) end
local routes=rt.solid_routes
for _,cell in pairs(routes and routes.cells or {}) do assert(not cell.pending and not cell.fault) end
local connectors=c.connector_ledger;assert(not connectors or not connectors.active)
for _,row in pairs(connectors and connectors.routes or {}) do assert(not row.pending) end
assert(not rt.launch_readiness or not rt.launch_readiness.pending_fish)
local callbacks=''' + CALLBACKS_EXPR + '''
local additions={bootstrap_observe=true,bootstrap_extract=true,bootstrap_place=true,
 bootstrap_bind_paid=true,bootstrap_reconcile_pending=true,bootstrap_complete_install=true}
local original={}
for name,callback in pairs(callbacks) do if not additions[name] then original[name]=callback end end
assert(same(b.original_callbacks,original),"Retained original callback chain changed")
assert(same(n.callbacks,original) or same(n.callbacks,callbacks),"Prepared callback chain changed")
b.complete_install()
n.assets=proposed.assets;n.profile=proposed.profile;n.callbacks=''' + CALLBACKS_EXPR + '''
local output=assert(b.observe())
assert(output.authorization_sha256==scope.authorization_sha256
 and output.origin=="legacy_authorized_current_asset"
 and output.historical_paid_placement_proven==false
 and output.drill_unit==scope.drill_unit and output.chest_unit==scope.chest_unit)
rcon.print("JEV_BOOTSTRAP_OUTPUT_RECONCILED|1")'''


def ownership_readback_command():
    """Read-only same-command output for the signed installer's full P/R."""
    return '''local rt=assert(jev_fle_runtime);local b=assert(rt.bootstrap_output_v1)
local n=assert(rt.native_installation);local player=assert(rt.fair.actor());local actor=assert(player.character)
rcon.print(helpers.table_to_json({schema="jev.bootstrap-output-install-readback.v1",tick=game.tick,
 session_id=rt.jev_session_id,actor_unit=actor.unit_number,surface_index=actor.surface.index,
 force_index=actor.force.index,phase=b.phase,install_authorization=b.install_authorization,
 placement_pending=b.placement_pending,transfer_pending=b.transfer_pending,
 output=b.phase=="ready" and b.observe() or false,
 native_installation={schema=n.schema,session_id=n.session_id,actor_unit=n.actor_unit,
 profile=n.profile,assets=n.assets}}))'''


def quiescence_readback_command(attachment, scope):
    """The exact pre-effect installer checks, read-only under the original lock.

    Endpoint geometry/full stock is independently revalidated by the installer's
    pinned native-identity query and again by the native binding callback.
    """
    prefix, separator, _ = command(attachment, scope).partition(
        'rt.bootstrap_output_install_authorization=scope')
    if not separator:
        raise ValueError('Installer pre-effect boundary changed')
    return prefix + '''rcon.print(helpers.table_to_json({schema="jev.bootstrap-output-quiescence.v1",
 tick=game.tick,session_id=rt.jev_session_id,actor_unit=actor.unit_number,
 surface_index=actor.surface.index,force_index=actor.force.index,quiescent=true,
 original_callback_and_asset_pins_match=true,bootstrap_capability_absent=true}))'''
