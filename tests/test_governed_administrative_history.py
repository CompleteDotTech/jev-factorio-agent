"""Actual205-row projected CP; crypto transport mocked to its exact captured authority/signature."""
import base64,json
from copy import deepcopy
from pathlib import Path
import pytest
from jev_factorio import memory as module
from jev_factorio import paid_selection_reconciliation as paid
from jev_factorio.solid_funding_evidence import _funding_events
RAW=(Path(__file__).parent/'fixtures/native297-projected-checkpoint.json').read_bytes()
DATA=json.loads(RAW)
ADMIN=DATA['history'][-1]
@pytest.fixture
def authenticated(monkeypatch):
    expected=ADMIN['budget_carry']
    def verify(raw,sig,trust):
        if (raw!=base64.b64decode(expected['authority_base64']) or sig!=base64.b64decode(expected['signature_base64']) or trust!=expected['trust_pin']):
            raise ValueError('Captured crypto boundary differs')
    monkeypatch.setattr(paid,'_verify_signature',verify)
    return deepcopy(DATA)
def base_memory(value):
    from dataclasses import fields
    keys={field.name for field in fields(module.CampaignMemory)}
    return module.CampaignMemory.from_bytes(encode({key:item for key,item in value.items() if key in keys}),DATA["session_id"],"rocket_launch")
def encode(value):return json.dumps(value,separators=(',',':')).encode()
def test_actual_composed_loader_65_history_and_complete_archive_boundary(authenticated,monkeypatch,tmp_path):
    p=tmp_path/'controller.json';p.write_bytes(RAW);calls=[]
    class Index:
        def find(self,source,input_sha256,*,memory):
            calls.append((source,input_sha256));return None  # exact live rows must remain in actual memory
        def close(self):calls.append('closed')
    from jev_factorio import blocked_recovery_archive
    def build(path,memory):
        assert path==p and memory.blocked_recovery_archive==DATA['blocked_recovery_archive']
        assert len(memory.blocked_recovery['attempts'])==205
        calls.append('authenticated-full-archive');return Index()
    monkeypatch.setattr(blocked_recovery_archive,'build_index',build)
    loaded=module.load_checkpoint(p,DATA['session_id'],'rocket_launch')
    assert loaded.history==DATA['history'] and loaded.active_plan==DATA['active_plan']
    assert calls[0]=='authenticated-full-archive' and len(calls)==3
    loaded._blocked_recovery_archive_index.close()
def test_rolling_history_preserves_signed_admin_and_ordinary64(authenticated):
    memory=base_memory(DATA)
    class Index:
        def find(self,*a,**k):return None
    memory._blocked_recovery_archive_index=Index()
    for i in range(70):memory.event('ordinary',tick=i)
    assert len(memory.history)==65 and memory.history[-1]==ADMIN
    assert len(module.ordinary_history(memory.history))==64
    assert memory.history[0]=={'kind':'ordinary','tick':6}
    assert _funding_events(memory.history)==[]
@pytest.mark.parametrize('change',['ordinary65','duplicate','unsigned','signature','scope','paid_row'])
def test_malformed_history_fail_closed(authenticated,change):
    data=authenticated
    if change=='ordinary65':data['history']=[{'kind':'ordinary'} for _ in range(65)]
    elif change=='duplicate':data['history'].append(deepcopy(ADMIN))
    elif change=='unsigned':data['history'][-1]={'kind':'paid_duplicate_selection_reconciled'}
    elif change=='signature':data['history'][-1]['budget_carry']['signature_base64']=base64.b64encode(b'bad').decode()
    elif change=='scope':data['history'][-1]['target_source_revision']['commit']='a'*40
    elif change=='paid_row':data['blocked_recovery']['attempts'][-2]['selection_batch']['offered'][0]['candidate_sha256']='a'*64
    if change=='paid_row':
        # Scope parsing must not fabricate archive authority; supported loader's full check rejects changed rows.
        memory=base_memory(data)
        class Index:
            def find(self,*a,**k):return None
        with pytest.raises(ValueError):module.validate_history_authority(memory,archive_index=Index())
    else:
        with pytest.raises((ValueError,KeyError,TypeError)):base_memory(data)
def test_archive_failure_never_returns_memory(authenticated,monkeypatch,tmp_path):
    from jev_factorio import blocked_recovery_archive
    p=tmp_path/'controller.json';p.write_bytes(RAW)
    monkeypatch.setattr(blocked_recovery_archive,'build_index',lambda *a:(_ for _ in ()).throw(ValueError('Archive invalid')))
    with pytest.raises(ValueError,match='Archive invalid'):module.load_checkpoint(p,DATA['session_id'],'rocket_launch')

def test_paid_row_failure_closes_authenticated_index(authenticated,monkeypatch,tmp_path):
    from jev_factorio import blocked_recovery_archive
    data=authenticated
    data['blocked_recovery']['attempts'][-2]['selection_batch']['offered'][0]['candidate_sha256']='a'*64
    p=tmp_path/'controller.json';p.write_bytes(encode(data));closed=[]
    class Index:
        def find(self,*a,**k):return None
        def close(self):closed.append(True)
    monkeypatch.setattr(blocked_recovery_archive,'build_index',lambda *a:Index())
    with pytest.raises(ValueError):module.load_checkpoint(p,DATA['session_id'],'rocket_launch')
    assert closed==[True]
def test_actual_selected_handoff_and_one_auth_per_event(authenticated,monkeypatch):
    from jev_factorio.compatible_recovery import validate_selected_paid_handoff
    memory=base_memory(DATA)
    class Index:
        def find(self,*a,**k):return None
        def validate_files(self):pass
    memory._blocked_recovery_archive_index=Index()
    validate_selected_paid_handoff(memory)
    original=paid._verify_signature;calls=[]
    def once(*a):calls.append(True);return original(*a)
    monkeypatch.setattr(paid,'_verify_signature',once)
    memory.event('ordinary',tick=DATA['last_tick'])
    assert calls==[True] and memory.history[-1]==ADMIN
def test_unsigned_new_admin_event_does_not_mutate_history():
    memory=module.CampaignMemory(session_id=DATA['session_id'],target='rocket_launch')
    before=deepcopy(memory.history)
    with pytest.raises(ValueError):memory.event('paid_duplicate_selection_reconciled')
    assert memory.history==before

def test_actual205_selected_lineage_keeps_two_bills_and_seen(authenticated,monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace
    from jev_factorio import compatible_recovery as recovery, blocked_reevaluation, blocked_persistence as bp, judgments
    from jev_factorio.provenance import digest_json
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.skills import Plan
    memory=base_memory(DATA)
    class Index:
        def find(self,*a,**k):return None
        def validate_files(self):pass
        def selection_attempts(self,*a,**k):return []
    index=Index();memory._blocked_recovery_archive_index=index
    previous=deepcopy(memory.blocked_recovery['source_revision'])
    current={'commit':'f'*40,'source_sha256':'e'*64}
    owner={'run_id':'offline-history-fixture','segment_id':'source-handoff','execution_id':'offline-qualification'}
    raw=encode(asdict(memory))
    authority=dict(schema=1,authorization_id='offline-exact-selected-lineage',checkpoint_sha256=__import__('hashlib').sha256(raw).hexdigest(),
        session_id=memory.session_id,target=memory.target,previous_source=previous,current_source=current,
        decision_contract_sha256='a'*64,scope=recovery.scope(memory),owner_invocation=owner,
        supervisor_history_sha256='b'*64,lock_path=str(Path(__file__).resolve()),provider_state_sha256='c'*64,provider_identity_sha256='d'*64)
    # Only future Git contract transport is mocked; paid authority/state/rows are real captured values.
    monkeypatch.setattr(blocked_reevaluation,'validate_source_revision',lambda *a,**k:{'source_head':current['commit'],'decision_contract_sha256':'a'*64})
    monkeypatch.setattr(recovery,'validate_budget_contract',lambda *a:None)
    record=recovery.validate_authorization(authority,raw,memory,current,owner)
    memory.compatible_source_recoveries.append(record);memory.blocked_recovery['source_revision']=deepcopy(current)
    assert recovery.approved_sources(memory,current)==[previous,current]
    assert memory.history==DATA['history'] and len(memory.blocked_recovery['attempts'])==205
    carry=ADMIN['budget_carry'];state=deepcopy(carry['pre_question_state']);plans=deepcopy(carry['plans'])
    aliases=[(source,bp.selection_state_sha256(state,plans,session_id=memory.session_id,source_revision=source,
        target=memory.target,policy='jev',confidence_floor=.45,current_tick=carry['tick'])) for source in (previous,current)]
    rows=bp.selection_attempts_for_state(memory,current,aliases[-1][1],archive_index=index,compatible_state_hashes=aliases)
    carried,seen=paid.scoped_representation_budget_rows(memory,aliases,archive_index=index)
    assert len(rows)==len(carried)==2 and len(seen)==2
    controller=HierarchicalLoop.__new__(HierarchicalLoop);controller.memory=memory;controller.persist_recoverable_blocks=True
    controller._blocked_recovery_archive_index=index;controller.provenance={'code_revision':current}
    controller.target='rocket_launch';controller.policy='jev';controller.confidence_floor=.45
    controller._persistent_wait=lambda snapshot,key:{'state':'wait','fingerprint':key}
    monkeypatch.setattr(judgments,'question_batch',lambda *a,**k:pytest.fail('No model request after carried bills'))
    result=controller._persistent_selection_with_alternatives(SimpleNamespace(session_id=memory.session_id,tick=carry['tick']),state,[Plan.from_dict(p) for p in plans])
    assert result['record']['state']=='wait'
