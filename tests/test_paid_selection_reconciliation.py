"""Fail-closed entrypoint checks; full actual082 replay is an external offline fixture."""
import json
import pytest
from jev_factorio import paid_selection_reconciliation as recovery

@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}'])
def test_reconciliation_document_rejects_ambiguous_json(raw):
    with pytest.raises(ValueError):recovery._json(raw)

def test_unverified_signature_cannot_reach_projection(monkeypatch):
    def rejected(*args):raise ValueError('fixture signature rejected')
    monkeypatch.setattr(recovery,'_verify_signature',rejected)
    with pytest.raises(ValueError,match='signature rejected'):
        recovery.prepare_paid_duplicate_projection(*([b'{}']*6),{},b'{}',b'{}',b'{}')

@pytest.mark.parametrize('invalid',[None,'{}',b'',b'x'*(4*1024*1024+1)],ids=['none','wrongtype','empty','oversized'])
def test_incomplete_full_evidence_rejected_before_signature(invalid,monkeypatch):
    monkeypatch.setattr(recovery,'_verify_signature',lambda *args:pytest.fail('Incomplete evidence reached crypto'))
    with pytest.raises(ValueError,match='complete reconciliation input'):
        recovery.prepare_paid_duplicate_projection(invalid,*([b'{}']*5),{},b'{}',b'{}',b'{}')

def test_bool_descriptor_never_proves_original_lock(monkeypatch):
    monkeypatch.setattr(recovery.os,'geteuid',lambda:0,raising=False)
    authority=json.dumps({'writer_lock_pin':{'path':'original.lock'}}).encode()
    with pytest.raises(ValueError,match='writer-lock capability'):
        recovery.projection_bytes_under_held_lock(True,'original.lock',b'{}',b'{}',b'{}',b'{}',authority,b'{}',{},b'{}',b'{}',b'{}')
