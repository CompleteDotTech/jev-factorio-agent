"""Byte-equivalent event encoding and durable writer regressions."""
from __future__ import annotations
import copy
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest
import jev_factorio.research_log as log


def writer(tmp_path, monkeypatch):
    return log.ResearchLog(tmp_path/'run', log.RunConfiguration('mock','hierarchical','deterministic'),
        repo_dir=tmp_path, environ={}, monotonic_ns=lambda:1,
        utc_now=lambda: datetime(2026,1,1,tzinfo=timezone.utc))


def test_large_payload_serialized_once_not_twice(tmp_path, monkeypatch):
    sink=writer(tmp_path,monkeypatch)
    original=json.dumps
    traversals=[]
    def encode(value,*args,**kwargs):
        if isinstance(value,dict) and ('large_payload' in value or
                isinstance(value.get('payload'),dict) and 'large_payload' in value['payload']):
            traversals.append(1)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(json,'dumps',encode)
    try:
        sink.emit('observation',{'large_payload':list(range(1000))})
        assert len(traversals)==1
    finally: sink.close()


def event(payload):
    return {'schema':log.EVENT_SCHEMA,'schema_version':1,
        'run_id':'cafb9fe3-8ba4-4cc2-ae74-a3a8fe55bd49','sequence':2,'event_type':'observation',
        'time':{'utc':'2026-01-01T00:00:00.000000Z','monotonic_ns':1,'factorio_tick':10},
        'session_id':'fixture','correlation':{},'payload':payload,'prev_hash':'sha256:'+'1'*64}


def independent(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


@pytest.mark.parametrize('payload', [
    {}, {'zero':-0.0,'one':1.0,'integer':1,'bool':True,'none':None},
    {'unicode':'\u2603\ud800\u0000','newline':'\n','quote':'"','backslash':'\\'},
    {'event_hash':'not metadata','list':[{},[],[True,None,1e-200]],'nested':{'a':{'b':3}}},
])
def test_event_bytes_and_hash_match_independent_encoder(payload):
    candidate=event(payload)
    expected=copy.deepcopy(candidate)
    expected['event_hash']='sha256:'+hashlib.sha256(independent(expected)).hexdigest()
    encoded=log._encode_event(candidate)
    assert encoded==independent(expected)+b'\n' and candidate==expected
    log.validate_event(candidate)


def test_random_nested_payload_byte_equivalence():
    rng=random.Random(10395)
    def value(depth=0):
        kind=rng.randrange(6 if depth<4 else 4)
        if kind==0: return rng.choice([None,True,False])
        if kind==1: return rng.randrange(-100000,100000)
        if kind==2: return rng.choice([-0.0,1.0,1e-250,1e250])
        if kind==3: return rng.choice(['','a','\n"','\u2603','event_hash','sha256:'])
        if kind==4: return [value(depth+1) for _ in range(rng.randrange(5))]
        return {str(i):value(depth+1) for i in range(rng.randrange(5))}
    for _ in range(200):
        candidate=event({'random':value()})
        expected=copy.deepcopy(candidate)
        expected['event_hash']='sha256:'+hashlib.sha256(independent(expected)).hexdigest()
        assert log._encode_event(candidate)==independent(expected)+b'\n'


@pytest.mark.parametrize('change', [lambda e:e.update(sequence=True),lambda e:e.update(extra=0),
    lambda e:e['payload'].update(number=float('nan')),lambda e:e['payload'].update(items={1:'bad'}),
    lambda e:e.update(event_type='Bad'),lambda e:e.update(correlation={'bad':'identity'})])
def test_invalid_input_remains_rejected(change):
    candidate=event({});change(candidate)
    with pytest.raises(ValueError): log._encode_event(candidate)


def test_size_limit_keeps_no_new_event_visible(tmp_path,monkeypatch):
    sink=writer(tmp_path,monkeypatch)
    path=tmp_path/'run/events.jsonl';before=path.read_bytes()
    try:
        with pytest.raises(ValueError,match='size limit'):
            sink.emit('observation',{'large_payload':'x'*log.MAX_RECORD_BYTES})
        assert path.read_bytes()==before and sink._sequence==1
    finally:sink.close()


@pytest.mark.parametrize('failure', ['write','flush','sync'])
def test_encoding_optimization_does_not_advance_failed_writer(tmp_path,monkeypatch,failure):
    sink=writer(tmp_path,monkeypatch)
    original=sink._stream
    class Stream:
        def write(self,data):
            if failure=='write': return len(data)-1
            return original.write(data)
        def flush(self):
            if failure=='flush': raise OSError('synthetic failure')
            return original.flush()
        def fileno(self):return original.fileno()
        def close(self):return original.close()
    sink._stream=Stream()
    if failure=='sync':monkeypatch.setattr(log.os,'fsync',lambda *args: (_ for _ in ()).throw(OSError('synthetic failure')))
    previous=sink._previous_hash
    with pytest.raises(OSError):sink.emit('observation',{'large_payload':[1,2,3]})
    assert sink._failed and sink._sequence==1 and sink._previous_hash==previous
    with pytest.raises(RuntimeError):sink.emit('observation',{})
    sink.close()


def test_returned_event_mutation_cannot_change_chain_or_input(tmp_path,monkeypatch):
    sink=writer(tmp_path,monkeypatch)
    payload={'large_payload':[1,{'x':2}]}
    emitted=sink.emit('observation',payload)
    saved=sink._previous_hash
    emitted['payload']['large_payload'][1]['x']=99
    payload['large_payload'].append(3)
    follow=sink.emit('decision',{})
    assert follow['prev_hash']==saved
    sink.finish()
    assert log.verify_run(tmp_path/'run')['complete'] is True
