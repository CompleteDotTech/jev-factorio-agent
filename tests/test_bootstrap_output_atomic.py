"""Same-RPC bootstrap ownership publication and failure atomicity."""
from copy import deepcopy
import hashlib
import json

import pytest

from jev_factorio.bootstrap_output import MODULE, PROFILE, ROLE, binding
from jev_factorio.state import GameSnapshot
from jev_factorio.observation import profile_backend
from test_atomic_observation import setup
from test_bootstrap_output_binding import snapshot


def prepared(monkeypatch,tmp_path):
    backend,native,payload,calls=setup(monkeypatch,craft=True)
    row=deepcopy(snapshot().factory['bootstrap_output'])
    row.update(tick=10,session_id='atomic-fixture',actor_unit=17,force_index=2,bound_at_tick=9)
    row['capacity'].update(tick=10,session_id='atomic-fixture',actor_unit=17,force_index=2)
    backend._native_attachment=dict(session_id='atomic-fixture',actor_unit=17,modules={MODULE:True},
        native_installation={'profile':PROFILE,'assets':{MODULE:'b'*64}})
    payload['factory']['entities']={ROLE:dict(name='wooden-chest',unit_number=2545,
        position=row['chest_position'],output={'iron-ore':33})}
    payload['bootstrap']=dict(query_limit=129,placed_entities=['burner-mining-drill','wooden-chest'],
        drill=dict(name='burner-mining-drill',unit_number=2544,position=row['drill_position'],
            drop_position=row['drop_position'],status='no-fuel',fuel={}),
        output_connected=True,iron_ore_collected=33)
    payload['_bootstrap_output_payload']=dict(schema=1,tick=10,session_id='atomic-fixture',actor_unit=17,
        output=row,native_pending=False)
    witness=deepcopy(snapshot()._bootstrap_output_ownership_witness)
    witness.update(session_id='atomic-fixture',actor_unit=17,force_index=2,bound_at_tick=9)
    raw=json.dumps(witness).encode();path=tmp_path/'ownership.json';path.write_bytes(raw);path.chmod(0o400)
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS',str(path))
    monkeypatch.setenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256',hashlib.sha256(raw).hexdigest())
    return backend,native,payload,calls


def test_ownership_stock_and_capacity_arrive_in_one_validated_native_command(monkeypatch,tmp_path):
    backend,native,payload,calls=prepared(monkeypatch,tmp_path)
    state=backend.observe()
    assert len(calls)==1 and 'JEV_BOOTSTRAP_OUTPUT|' in calls[0]
    assert binding(state)['capacity']['count']==17 and state.iron_ore_collected==33
    assert state.factory['bootstrap_output_pending'] is False


def test_future_paid_native_output_observation_needs_no_legacy_witness_environment(monkeypatch,tmp_path):
    backend,native,payload,calls=prepared(monkeypatch,tmp_path)
    row=payload['_bootstrap_output_payload']['output']
    row.update(origin='native_paid_bootstrap_placement',binding_id='paid:2544:2545',
        authorization_sha256=False,historical_paid_placement_proven=True,
        paid_drill_unit=2544,paid_chest_unit=2545)
    monkeypatch.delenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS')
    monkeypatch.delenv('JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256')
    state=backend.observe()
    assert binding(state) is not None and len(calls)==1
    assert state._bootstrap_output_ownership_witness is None
    assert binding(state)['origin']=='native_paid_bootstrap_placement'


@pytest.mark.parametrize('section,key,value',[
    ('output','authorization_sha256','c'*64),('output','chest_unit',999),
    ('capacity','tick',9),('capacity','count',True),('output','historical_paid_placement_proven',True),
])
def test_bad_endpoint_capacity_authority_does_not_publish_snapshot_or_cache(monkeypatch,tmp_path,section,key,value):
    backend,native,payload,calls=prepared(monkeypatch,tmp_path)
    valid=backend.observe();before=deepcopy(valid.factory)
    target=payload['_bootstrap_output_payload']['output']
    if section=='capacity':target=target['capacity']
    target[key]=value
    with pytest.raises(ValueError),profile_backend(backend):backend._factory.observe(valid)
    assert valid.factory==before and valid.tick==10
    assert native._coherent_tick==10 and backend._bootstrap_output_pending is False


def test_pending_journal_blocks_actor_mutation_before_discovery_or_tool_call(monkeypatch,tmp_path):
    backend,native,payload,calls=prepared(monkeypatch,tmp_path)
    payload['_bootstrap_output_payload']['native_pending']=True
    payload['_bootstrap_output_payload']['output']['native_pending']=True
    state=backend.observe()
    assert binding(state) is None and backend._bootstrap_output_pending is True
    before=len(calls)
    with pytest.raises(ValueError,match='requires reconciliation'):backend.act('place_burner_drill')
    assert len(calls)==before
