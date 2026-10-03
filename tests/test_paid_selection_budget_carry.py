"""Actual082 rows/state; fixture signature only, no native handoff claim."""
import base64,json,hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from jev_factorio import paid_selection_reconciliation as paid
from jev_factorio import blocked_persistence as bp
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.memory import CampaignMemory
from test_persistent_wal_prospective_validation import ROWS

DATA=json.loads((Path(__file__).parent/'fixtures/native082-paid-representation-state.json').read_text())
TARGET={"commit":"232985851116f3899e57d9962d856a9511537f6d","source_sha256":"d0c0103b1fec4a22acabc4fe60055a2cc5915af5d14095af34d42c03b1fc81ac"}
def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(value):return hashlib.sha256(value).hexdigest()

def fixture(monkeypatch):
    source=deepcopy(ROWS[0]['source_revision']);state=deepcopy(DATA['state']);plans=deepcopy(DATA['plans'])
    kwargs=dict(session_id=DATA['session_id'],target='rocket_launch',policy='jev',confidence_floor=.45,current_tick=DATA['tick'])
    old=bp.selection_state_sha256(state,plans,source_revision=source,**kwargs)
    assert old==ROWS[1]['selection_batch']['state_sha256']=='14ee28ace7fb9cc88ce6c56c9d371e968e88a9096ea7ae45538b1abd3984fd35'
    new=bp.selection_state_sha256(state,plans,source_revision=TARGET,**kwargs)
    canonical=sorted([dict(plan_id=p['id'],candidate_sha256=bp._candidate_semantic_sha256(p,state['candidate_evidence'][p['id']],current_tick=DATA['tick'])) for p in plans],key=lambda r:(r['plan_id'],r['candidate_sha256']))
    assert {r['candidate_sha256'] for r in canonical}=={'6a77e67c31e60148b46fe609ee1af0aac396935ead482d691333b6406805303b','f4b813206b65ef9f9974f21d953b5117d1d2e42915afc1502c394bbe7b53b70a'}
    selected=deepcopy(next(p for p in plans if p['id'].endswith(':13')))
    carry=dict(schema='jev.paid-selection-representation-budget-carry.v1',session_id=DATA['session_id'],target='rocket_launch',policy='jev',confidence_floor=.45,tick=DATA['tick'],original_state_sha256=old,target_state_sha256=new,pre_question_state=state,plans=plans,authority_base64='',signature_base64='',trust_pin={})
    authority=dict(schema='jev.paid-selection-representation-reconciliation.v1',authorization_id='OFFLINE_FIXTURE',source_revision=source,target_source_revision=TARGET,session_id=DATA['session_id'],checkpoint_sha256=DATA['original_checkpoint_sha256'],events_sha256=DATA['events_sha256'],source_handoff_sha256='e'*64,confidence_floor=.45,ordinary_gates_sha256='f'*64,selected_plan_id=selected['id'],budget_carry_sha256=paid.representation_budget_scope_sha256(carry,source,TARGET,ROWS,canonical,selected))
    authority.update(manifest_sha256='1'*64,integrity_sha256='2'*64,prior_index=203,paid_index=204,model_call_id='model:2',max_request_bytes=48000,owner_absent=True,native_pending_none=True,fresh_candidate=selected,fresh_candidate_evidence=state['candidate_evidence'][selected['id']],fresh_tick=DATA['tick'],fresh_native_proof_sha256='3'*64,native073_proof_sha256='4'*64,writer_lock_pin={},source_handoff_status='OFFLINE_ONLY')
    raw=encode(authority);signature=b'EXPLICIT_FIXTURE_SIGNATURE'
    def verify(body,sig,trust):
        if body!=raw or sig!=signature:raise ValueError('Fixture signature rejected')
    monkeypatch.setattr(paid,'_verify_signature',verify)
    carry['authority_base64']=base64.b64encode(raw).decode();carry['signature_base64']=base64.b64encode(signature).decode()
    record=dict(kind='paid_duplicate_selection_reconciled',schema='jev.paid-selection-representation-reconciliation-receipt.v1',budget_carry=carry,original_source_revision=source,target_source_revision=TARGET,authority_sha256=sha(raw),authorization_id=authority['authorization_id'],original_checkpoint_sha256=authority['checkpoint_sha256'],events_sha256=authority['events_sha256'],source_handoff_sha256=authority['source_handoff_sha256'],ordinary_gates_sha256=authority['ordinary_gates_sha256'],same_semantic_candidates=True,representation_correction_only=True,paid_attempt_count_unchanged=True,duplicate_paid_batches_count=2,model_called=False,native_action_called=False,automatic_retry_allowed=False,original_attempts=deepcopy(ROWS),original_request_sha256=ROWS[1]['selection_batch']['request_sha256'],original_input_sha256=ROWS[1]['decision_input_sha256'],corrected_offered=canonical,selected_plan=selected)
    current=deepcopy(ROWS);current[1]['selection_batch']['offered']=deepcopy(canonical);current[1]['outcome']='selected'
    memory=CampaignMemory(DATA['session_id'],'rocket_launch',blocked_recovery=dict(schema=1,session_id=DATA['session_id'],source_revision=deepcopy(TARGET),attempts=current,last_input_sha256=current[1]['decision_input_sha256'],wait_level=3),history=deepcopy(state['history'])+[record])
    return memory,record,new,canonical

def loop(memory):
    value=HierarchicalLoop.__new__(HierarchicalLoop);value.memory=memory;value.persist_recoverable_blocks=True;value._blocked_recovery_archive_index=None
    value.provenance={'code_revision':TARGET};value.target='rocket_launch';value.policy='jev';value.confidence_floor=.45
    value._persistent_wait=lambda snapshot,key:{'state':'wait','fingerprint':key}
    return value

def test_actual_target_two_billed_rows_canonical_seen_and_no_provider_replay(monkeypatch):
    memory,record,state_hash,canonical=fixture(monkeypatch)
    rows=bp.selection_attempts_for_state(memory,TARGET,state_hash,compatible_state_hashes=[(TARGET,state_hash)])
    assert len(rows)==2 and rows[0]['source_revision']==rows[1]['source_revision']==ROWS[0]['source_revision']
    assert memory.blocked_recovery['attempts'][0]==ROWS[0]
    carried,seen=paid.scoped_representation_budget_rows(memory,[(TARGET,state_hash)])
    assert len(carried)==2 and seen=={r['candidate_sha256'] for r in canonical}
    controller=loop(memory)
    from jev_factorio import judgments
    monkeypatch.setattr(judgments,'question_batch',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('No new paid request')))
    state=deepcopy(DATA['state']);state['history']=controller._model_history()
    assert state['history']==DATA['state']['history'] and memory.history[-1] is record
    assert bp.selection_state_sha256(state,DATA['plans'],session_id=memory.session_id,source_revision=TARGET,target=memory.target,policy='jev',confidence_floor=.45,current_tick=DATA['tick'])==state_hash
    result=controller._persistent_selection_with_alternatives(SimpleNamespace(session_id=memory.session_id,tick=DATA['tick']),state,[__import__('jev_factorio.skills',fromlist=['Plan']).Plan.from_dict(p) for p in DATA['plans']])
    assert result['record']['state']=='wait'

@pytest.mark.parametrize('change',['inventory','source','state'])
def test_unmatched_state_or_source_does_not_widen(monkeypatch,change):
    memory,_,state_hash,_=fixture(monkeypatch);source=deepcopy(TARGET)
    if change=='source':source['commit']='9'*40
    if change=='state':state_hash='9'*64
    if change=='inventory':
        state=deepcopy(DATA['state']);state['facts']['inventory']['iron-ore']=999
        state_hash=bp.selection_state_sha256(state,DATA['plans'],session_id=memory.session_id,source_revision=TARGET,target=memory.target,policy='jev',confidence_floor=.45,current_tick=DATA['tick'])
    assert paid.scoped_representation_budget_rows(memory,[(source,state_hash)])==( [],set())

@pytest.mark.parametrize('change',['authority','signature','row','coordinated_row','alias','target','state','missing'])
def test_malformed_carry_stays_history_and_blocks_lookup(monkeypatch,change):
    memory,record,state_hash,_=fixture(monkeypatch)
    if change=='authority':record['authority_sha256']='9'*64
    if change=='signature':record['budget_carry']['signature_base64']='!!!!'
    if change=='row':memory.blocked_recovery['attempts'][0]['reason']='wrong'
    if change=='coordinated_row':
        memory.blocked_recovery['attempts'][0]['reason']='wrong';record['original_attempts'][0]['reason']='wrong'
    if change=='alias':record['corrected_offered'][0]['candidate_sha256']='9'*64
    if change=='target':record['target_source_revision']['commit']='9'*40
    if change=='state':record['budget_carry']['target_state_sha256']='9'*64
    if change=='missing':memory.blocked_recovery['attempts'].pop(0)
    assert record in loop(memory)._model_history()
    with pytest.raises((ValueError,KeyError,TypeError)):
        bp.selection_attempts_for_state(memory,TARGET,state_hash,compatible_state_hashes=[(TARGET,state_hash)])

def test_archived_exact_billed_rows_keep_carry(monkeypatch):
    memory,_,state_hash,_=fixture(monkeypatch);retained=deepcopy(memory.blocked_recovery['attempts']);memory.blocked_recovery['attempts']=[];memory.blocked_recovery['last_input_sha256']=None
    class Archive:
        def find(self,source,key,**_kw):return next((deepcopy(r) for r in retained if r['source_revision']==source and r['decision_input_sha256']==key),None)
    rows,seen=paid.scoped_representation_budget_rows(memory,[(TARGET,state_hash)],archive_index=Archive())
    assert len(rows)==2 and len(seen)==2


@pytest.mark.skipif(__import__('os').name!='posix',reason='Native OpenSSH/memfd POSIX trust qualification')
def test_real_crypto_alternate_trust_key_is_not_checkpoint_authority(tmp_path,monkeypatch):
    import os,stat,subprocess
    if os.geteuid() not in (0,1000):
        pytest.skip('Native trust fixture requires UID1000 ownership or root chown authority')
    if not Path('/usr/bin/ssh-keygen').exists():pytest.skip('Native OpenSSH unavailable')
    folders={}
    for label in ('enrolled','foreign'):
        folder=tmp_path/label;folder.mkdir(mode=0o700);key=folder/'key'
        subprocess.run(['/usr/bin/ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)
        signers=folder/paid.RECONCILIATION_SIGNERS_BASENAME
        signers.write_text('Timothy.Gregg@complete.tech '+key.with_suffix('.pub').read_text())
        if os.geteuid()==0:os.chown(signers,1000,1000)
        signers.chmod(0o400)
        raw=b'fixture paid authority, no native run'
        message=folder/'authority.json';message.write_bytes(raw)
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(key),'-n','file',str(message)],check=True,capture_output=True)
        st=signers.stat();pin=dict(path=str(signers),sha256=sha(signers.read_bytes()),size=st.st_size,device=st.st_dev,inode=st.st_ino,uid=st.st_uid,mode=stat.S_IMODE(st.st_mode),mtime_ns=st.st_mtime_ns,ctime_ns=st.st_ctime_ns)
        folders[label]=(raw,message.with_suffix('.json.sig').read_bytes(),pin)
    # Only fixture policy enrollment is changed; persisted carry does not choose it.
    monkeypatch.setattr(paid,'RECONCILIATION_SIGNERS_SHA256',folders['enrolled'][2]['sha256'])
    before=len(list(Path('/proc/self/fd').iterdir()))
    paid._verify_signature(*folders['enrolled'])
    with pytest.raises(ValueError,match='not enrolled'):paid._verify_signature(*folders['foreign'])
    with pytest.raises(ValueError,match='signature rejected'):
        paid._verify_signature(folders['foreign'][0],folders['foreign'][1],folders['enrolled'][2])
    assert len(list(Path('/proc/self/fd').iterdir()))==before
