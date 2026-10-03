"""Additive bootstrap qualification preserves the actually installed V5 chain."""
from copy import deepcopy
import json

import pytest

from jev_factorio.bootstrap_output import (
    MODULE, PROFILE, MANUAL_CYCLE_PROFILE as V5_BOOTSTRAP_PROFILE,
)
from jev_factorio.backends.native_attachment import (
    MANUAL_CYCLE_PROFILE, CLOSED_WORLD_PROFILE, LEGACY_MANUAL_CYCLE_PROFILE,
    PROBE, readback, require_asset,
)
from jev_factorio.backends.native_bootstrap_output_migration import (
    asset_sha256, proposed_manifest, command, reconciliation_command,
)
from native_connector_witness_helpers import write_snapshot_witness
from test_native_closed_world_migration import installed_v5
from test_bootstrap_output_migration import setup, scope


def test_exact_v5_assets_modules_and_connector_witness_survive_bootstrap_readback(tmp_path):
    row=installed_v5()
    assert len(row['native_installation']['assets'])==15
    assert row['modules']['coal_manual_cycle_v2'] is False
    assert row['modules']['coal_manual_journal_v1'] is True
    # The original observer witness is qualified BEFORE the additive install.
    receipt,witness=write_snapshot_witness(tmp_path,row)
    original_witness=witness.read_bytes()
    old=deepcopy(row['native_installation'])
    authority=scope();authority.update(session_id=row['session_id'],actor_unit=row['actor_unit'])
    row['qualified']=True
    row['native_installation']=proposed_manifest(row,authority)
    row['modules'][MODULE]=True
    class Client:
        def send_command(self,code):
            assert code=='/sc '+PROBE
            return json.dumps(row)
    observed=readback(Client(),receipt_path=receipt,connector_witness_path=witness)
    assert observed['native_installation']['profile']==V5_BOOTSTRAP_PROFILE
    assert observed['native_installation']['assets']=={**old['assets'],MODULE:asset_sha256()}
    assert observed['modules']['coal_manual_cycle_v2'] is False
    assert witness.read_bytes()==original_witness
    for name in old['assets']:
        assert require_asset(observed,name) is True
    assert require_asset(observed,MODULE) is True
    with pytest.raises(RuntimeError,match='not installed'):
        require_asset(observed,'coal_manual_cycle_v2')
    for mutation in ('cycle','profile','asset','module'):
        broken=deepcopy(row)
        if mutation=='cycle':broken['modules']['coal_manual_cycle_v2']=True
        elif mutation=='profile':broken['native_installation']['profile']=PROFILE
        elif mutation=='asset':broken['native_installation']['assets'][MODULE]='0'*64
        else:broken['modules'][MODULE]=False
        row_before=row;row=broken
        with pytest.raises(RuntimeError):
            readback(Client(),receipt_path=receipt,connector_witness_path=witness)
        row=row_before


@pytest.mark.parametrize('base,target',[(MANUAL_CYCLE_PROFILE,V5_BOOTSTRAP_PROFILE),(CLOSED_WORLD_PROFILE,PROFILE)])
@pytest.mark.parametrize('interrupted',[False,True])
def test_matching_profile_install_and_reconciliation_preserve_three_completed_paid_routes(base,target,interrupted):
    lua,attachment=setup()
    attachment['native_installation']['profile']=base
    lua.globals().jev_fle_runtime.native_installation.profile=base
    lua.execute('''jev_fle_runtime.campaign.connector_ledger={routes={
      one={status='complete',paid_receipt='receipt-one'},
      two={status='complete',paid_receipt='receipt-two'},
      three={status='complete',paid_receipt='receipt-three'}}}
      retained_routes=jev_fle_runtime.campaign.connector_ledger.routes
      original_transfer=jev_fle_runtime.campaign.transfer
      original_place=jev_fle_runtime.fair.place''')
    code=command(attachment,scope())
    if interrupted:
        lua.execute(code.split('assert(same(callbacks,')[0])
        assert lua.globals().jev_fle_runtime.native_installation.profile==base
        lua.execute(reconciliation_command(attachment,scope()))
    else:
        lua.execute(code)
    assert lua.globals().jev_fle_runtime.native_installation.profile==target
    lua.execute('''assert(calls==0 and jev_fle_runtime.campaign.connector_ledger.routes==retained_routes)
      assert(retained_routes.one.paid_receipt=='receipt-one' and retained_routes.two.paid_receipt=='receipt-two'
        and retained_routes.three.paid_receipt=='receipt-three')
      assert(jev_fle_runtime.campaign.transfer==original_transfer and jev_fle_runtime.fair.place==original_place)
      assert(jev_fle_runtime.coal_manual_cycle_v2==nil)''')


@pytest.mark.parametrize('profile',[LEGACY_MANUAL_CYCLE_PROFILE,V5_BOOTSTRAP_PROFILE,PROFILE,False,'unknown'])
def test_unqualified_base_or_already_installed_profile_cannot_be_mapped(profile):
    _,attachment=setup();attachment['native_installation']['profile']=profile
    with pytest.raises(ValueError,match='qualified v5 or v6'):
        proposed_manifest(attachment,scope())
