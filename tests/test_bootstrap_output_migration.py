"""Generated migration commands run against offline native API doubles."""
from copy import deepcopy
import json

import pytest

from jev_factorio.bootstrap_output import MODULE, PROFILE, ROLE
from jev_factorio.backends.native_attachment import CLOSED_WORLD_PROFILE, CALLBACKS_EXPR
from jev_factorio.backends.native_bootstrap_output_migration import (
    command, reconciliation_command, validate_scope, proposed_manifest,
    quiescence_readback_command,
)
from test_bootstrap_output_lua import runtime


def scope():
    return dict(origin='legacy_authorized_current_asset',session_id='session',actor_unit=2543,
        surface_index=1,force_index=1,original_bound_drill_unit=2544,drill_unit=2544,chest_unit=2545,
        drill_position={'x':49,'y':-81},drop_position={'x':48.5,'y':-82.296875},
        chest_position={'x':48.5,'y':-82.5},lock_device=1,lock_inode=2,
        authorization_sha256='a'*64,checkpoint_sha256='b'*64,source_fingerprint='c'*64,
        source_tree='d'*40,source_revision='e'*40,attachment_sha256='f'*64,
        native_identity_report_sha256='1'*64)


def setup():
    lua=runtime(install=False)
    attachment=dict(qualified=True,session_id='session',actor_unit=2543,modules={MODULE:False},
        native_installation=dict(schema='jev.native-installation.v2',session_id='session',actor_unit=2543,
            profile=CLOSED_WORLD_PROFILE,assets={'original-pinned-asset':'2'*64}))
    lua.globals().helpers=lua.table_from({'json_to_table':lambda value:lua.table_from(json.loads(value),recursive=True)})
    lua.globals().old=lua.table_from(attachment['native_installation'],recursive=True)
    lua.execute('''jev_fle_runtime.bootstrap_output_install_authorization=nil
player.connected=true;player.cheat_mode=false;player.walking_state={walking=false};player.mining_state={mining=false}
game.speed=1;game.tick_paused=false;rcon={print=function(value) printed=value end}
jev_fle_runtime.native_installation=old
local f=jev_fle_runtime.fair;local c=jev_fle_runtime.campaign
old.callbacks='''+CALLBACKS_EXPR)
    return lua,attachment


def test_generated_install_retains_original_callbacks_assets_and_proves_current_asset():
    lua,attachment=setup()
    lua.execute('original_transfer=jev_fle_runtime.campaign.transfer;original_place=jev_fle_runtime.fair.place')
    lua.execute(command(attachment,scope()))
    lua.execute('''assert(printed=='JEV_BOOTSTRAP_OUTPUT_INSTALLED|1' and calls==0)
assert(jev_fle_runtime.campaign.transfer==original_transfer and jev_fle_runtime.fair.place==original_place)
assert(jev_fle_runtime.native_installation.assets['original-pinned-asset']==string.rep('2',64))
assert(jev_fle_runtime.bootstrap_output_v1.observe().historical_paid_placement_proven==false)''')
    assert lua.globals().jev_fle_runtime.native_installation.profile==PROFILE


def test_generated_quiescence_query_never_publishes_prepared_install_or_adopts_assets():
    lua,attachment=setup()
    lua.globals().helpers.table_to_json=lambda value:'readonly-proof'
    lua.execute(quiescence_readback_command(attachment,scope()))
    lua.execute("assert(printed=='readonly-proof' and not jev_fle_runtime.bootstrap_output_v1 and calls==0)")
    assert lua.globals().jev_fle_runtime.native_installation.profile==CLOSED_WORLD_PROFILE


def test_partial_after_binding_before_manifest_finishes_without_second_adoption_or_asset_reload():
    lua,attachment=setup()
    code=command(attachment,scope())
    # Simulate an interruption after the native asset/bind returned but BEFORE
    # host-visible manifest publication. Preserve every prepared/native effect.
    lua.execute(code.split('assert(same(callbacks,')[0])
    assert lua.globals().jev_fle_runtime.native_installation.profile==CLOSED_WORLD_PROFILE
    lua.execute('binding_before=jev_fle_runtime.bootstrap_output_v1.binding')
    with pytest.raises(Exception):
        lua.execute(code) # Consumed installer cannot be blindly repeated.
    lua.execute(reconciliation_command(attachment,scope()))
    lua.execute('''assert(printed=='JEV_BOOTSTRAP_OUTPUT_RECONCILED|1' and calls==0)
assert(jev_fle_runtime.bootstrap_output_v1.binding==binding_before)''')


def test_partial_between_binding_and_native_role_registration_finishes_exact_prepared_scope():
    lua,attachment=setup()
    lua.execute('''setmetatable(jev_fle_runtime.campaign.entities,{__newindex=function()
error('interruption before role registration') end})''')
    with pytest.raises(Exception):lua.execute(command(attachment,scope()))
    lua.execute('''assert(jev_fle_runtime.bootstrap_output_v1.phase=='install_prepared'
 and jev_fle_runtime.bootstrap_output_v1.binding and calls==0)
setmetatable(jev_fle_runtime.campaign.entities,nil)''')
    lua.execute(reconciliation_command(attachment,scope()))
    lua.execute("assert(jev_fle_runtime.campaign.entities['bootstrap-output:iron-ore']==chest and calls==0)")


@pytest.mark.parametrize('mutation', [
    "jev_fle_runtime.coal_manual_journal_v1={pending={}}",
    "jev_fle_runtime.coal_manual_journal_v1={rows={{fault='ambiguous'}}}",
    "jev_fle_runtime.coal_manual_cycle_v2={active={}}",
    "jev_fle_runtime.coal_supply={rows={{pending={}}}}",
    "jev_fle_runtime.solid_routes={cells={{pending={}}}}",
    "jev_fle_runtime.campaign.connector_ledger={active={}}",
    "jev_fle_runtime.campaign.connector_ledger={routes={{pending=1}}}",
    "jev_fle_runtime.launch_readiness={pending_fish={}}",
    "jev_fle_runtime.campaign.entities['foreign-role']=chest",
])
def test_prepared_native_journal_or_conflicting_role_blocks_before_any_registration(mutation):
    lua,attachment=setup();lua.execute(mutation)
    with pytest.raises(Exception):
        lua.execute(command(attachment,scope()))
    lua.execute('assert(not jev_fle_runtime.bootstrap_output_v1 and calls==0)')


def test_changed_prepared_authority_cannot_be_adopted_by_reconciliation():
    lua,attachment=setup()
    lua.execute(command(attachment,scope()).split('assert(same(callbacks,')[0])
    changed=scope();changed['checkpoint_sha256']='3'*64
    with pytest.raises(Exception):
        lua.execute(reconciliation_command(attachment,changed))
    assert lua.globals().jev_fle_runtime.native_installation.profile==CLOSED_WORLD_PROFILE


@pytest.mark.parametrize('mutation',[
    'jev_fle_runtime.native_installation.callbacks.transfer=nil',
    'jev_fle_runtime.native_installation.callbacks.unknown=function() end',
    'jev_fle_runtime.campaign.transfer=function() end;jev_fle_runtime.native_installation.callbacks.transfer=jev_fle_runtime.campaign.transfer',
])
def test_reconciliation_cannot_omit_add_or_replace_retained_callback(mutation):
    lua,attachment=setup()
    lua.execute(command(attachment,scope()).split('assert(same(callbacks,')[0])
    lua.execute(mutation)
    with pytest.raises(Exception):lua.execute(reconciliation_command(attachment,scope()))
    assert lua.globals().jev_fle_runtime.native_installation.profile==CLOSED_WORLD_PROFILE


@pytest.mark.parametrize('field',['authorization_sha256','checkpoint_sha256','source_fingerprint',
    'source_tree','source_revision','attachment_sha256','native_identity_report_sha256'])
def test_zero_exact_pins_are_not_authority(field):
    value=scope();value[field]='0'*len(value[field])
    with pytest.raises(ValueError): validate_scope(value)
