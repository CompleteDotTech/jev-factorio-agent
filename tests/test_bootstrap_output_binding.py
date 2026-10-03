"""Strict offline ownership/capacity fixtures; not historical paid evidence."""
from copy import deepcopy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from jev_factorio.bootstrap_output import ROLE, MODULE, binding, allowed
from jev_factorio.backends.native_bootstrap_output import read_witness, decode


def snapshot():
    identity=dict(session_id='session',actor_unit=2543,surface_index=1,force_index=1)
    row=dict(protocol=1,tick=100,**identity,role=ROLE,origin='legacy_authorized_current_asset',
        binding_id='a'*64,authorization_sha256='a'*64,bound_at_tick=99,
        ownership_effective_now=True,native_pending=False,historical_paid_placement_proven=False,
        paid_drill_unit=False,paid_chest_unit=False,drill_unit=2544,chest_unit=2545,
        drill_position={'x':49,'y':-81},drop_position={'x':48.5,'y':-82.296875},
        chest_position={'x':48.5,'y':-82.5},output={'iron-ore':33},
        capacity=dict(schema=1,tick=100,**identity,quality='normal',inventory='character_main',item='iron-ore',count=17))
    witness={k:deepcopy(row[k]) for k in ('session_id','actor_unit','surface_index','force_index',
        'drill_unit','chest_unit','drill_position','drop_position','chest_position','origin',
        'authorization_sha256','bound_at_tick')}
    witness.update(schema='jev.bootstrap-output-ownership.v1',asset_sha256='b'*64)
    factory=dict(bootstrap_output=row,acceptance_runtime=deepcopy(identity),drill_output_role=ROLE,
        player_bound=True,player_connected=True,inventory_insertable={'coal':3635},
        inventory_insertable_evidence={'items':{'coal':3635}},
        entities={ROLE:dict(name='wooden-chest',unit_number=2545,position=row['chest_position'],output=row['output'])})
    return SimpleNamespace(world_kind='fle',session_id='session',tick=100,factory=factory,
        iron_ore_collected=33,_coherent_observation_verified=('session',100),
        _atomic_inventory_verified=('session',100),_bootstrap_output_ownership_witness=witness)


def test_separate_same_tick_iron_capacity_authorizes_only_current_bounded_stock():
    s=snapshot();old=deepcopy(s.factory['inventory_insertable_evidence'])
    p=dict(role=ROLE,item='iron-ore',quantity=17,receipt='100:factory_extract:bootstrap-output:iron-ore:iron-ore')
    assert binding(s) is not None and allowed(p,s)
    assert not allowed({**p,'quantity':18},s)
    assert s.factory['inventory_insertable']=={'coal':3635}
    assert s.factory['inventory_insertable_evidence']==old


@pytest.mark.parametrize('section,key,value',[
    ('row','tick',True),('row','actor_unit',True),('runtime','actor_unit',True),
    ('row','tick',99),('row','session_id','other'),('row','chest_unit',999),
    ('row','authorization_sha256','c'*64),('row','authorization_sha256','0'*64),
    ('row','historical_paid_placement_proven',True),('capacity','tick',99),
    ('row','paid_drill_unit',2544),('row','paid_chest_unit',2545),('row','binding_id','other'),
    ('capacity','count',True),('capacity','actor_unit',True),('capacity','quality','rare'),
    ('witness','actor_unit',True),('witness','bound_at_tick',True),('row','native_pending',True),
    ('machine','unit_number',True),
])
def test_malformed_stale_or_unowned_binding_cannot_authorize_pickup(section,key,value):
    s=snapshot()
    target={'row':s.factory['bootstrap_output'],'runtime':s.factory['acceptance_runtime'],
        'capacity':s.factory['bootstrap_output']['capacity'],'witness':s._bootstrap_output_ownership_witness,
        'machine':s.factory['entities'][ROLE]}[section]
    target[key]=value
    assert binding(s) is None


def test_pinned_witness_file_is_owner_immutable_and_exact(tmp_path,monkeypatch):
    s=snapshot();value=s._bootstrap_output_ownership_witness
    path=tmp_path/'ownership.json';raw=json.dumps(value).encode();path.write_bytes(raw);path.chmod(0o400)
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS',str(path))
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256',hashlib.sha256(raw).hexdigest())
    attachment=dict(session_id='session',actor_unit=2543,native_installation={'assets':{MODULE:'b'*64}})
    assert read_witness(attachment)==value
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256','0'*64)
    with pytest.raises(ValueError):read_witness(attachment)
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256',hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):read_witness({**attachment,'actor_unit':999})
    if os.name=='posix':
        path.chmod(0o600)
        with pytest.raises(ValueError):read_witness(attachment)


def test_future_paid_binding_has_typed_native_units_and_canonical_identity():
    s=snapshot();row=s.factory['bootstrap_output']
    row.update(origin='native_paid_bootstrap_placement',authorization_sha256=False,
        historical_paid_placement_proven=True,paid_drill_unit=2544,paid_chest_unit=2545,
        binding_id='paid:2544:2545')
    assert binding(s) is row
    row['binding_id']='paid:999:2545'
    assert binding(s) is None


def test_unbound_sidecar_still_preserves_pending_actor_fence():
    result=dict(tick=100,session_id='session',actor_unit=2543,factory={'entities':{}})
    sidecar=dict(schema=1,tick=100,session_id='session',actor_unit=2543,output=False,native_pending=True)
    assert decode('JEV_BOOTSTRAP_OUTPUT|'+json.dumps(sidecar),result,{'modules':{MODULE:True}}) is None
    assert result['factory']['bootstrap_output_pending'] is True
    sidecar['actor_unit']=True
    with pytest.raises(ValueError):decode('JEV_BOOTSTRAP_OUTPUT|'+json.dumps(sidecar),result,{'modules':{MODULE:True}})
