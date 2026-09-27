"""Issue #95: unchanged-state copies may be skipped, durable changes may not."""
import json

import pytest

from jev_factorio.memory import CampaignMemory
import jev_factorio.checkpoint_io as checkpoint


def test_exact_repeats_skip_both_capture_and_serialization(tmp_path,monkeypatch):
    memory=CampaignMemory('fixture','rocket_launch')
    path=tmp_path/'state.json'
    counts={'capture':0,'serialize':0}
    original_capture,original_dumps=checkpoint.asdict,checkpoint.json.dumps
    def capture(value):
        counts['capture']+=1
        return original_capture(value)
    def dumps(*args,**kwargs):
        counts['serialize']+=1
        return original_dumps(*args,**kwargs)
    monkeypatch.setattr(checkpoint,'asdict',capture)
    monkeypatch.setattr(checkpoint.json,'dumps',dumps)
    for _ in range(20): memory.save(path)
    assert counts=={'capture':1,'serialize':1}
    assert memory._checkpoint_metrics['status']=='unchanged'


def test_deep_pending_mutation_cannot_alias_the_cached_capture(tmp_path):
    memory=CampaignMemory('fixture','rocket_launch')
    memory.pending={'dispatch':'prepared','nested':{'quantity':3}}
    path=tmp_path/'state.json'
    memory.save(path)
    memory.pending['nested']['quantity']=4
    memory.save(path)
    assert memory._checkpoint_metrics['status']=='written'
    assert json.loads(path.read_text())['pending']['nested']['quantity']==4


@pytest.mark.parametrize('before,after',[(1,True),(1,1.0),(0.0,-0.0),([1],[True])])
def test_python_equal_is_not_identical_json_state(tmp_path,before,after):
    memory=CampaignMemory('fixture','rocket_launch')
    memory.history=[{'typed':before}]
    path=tmp_path/'state.json'
    memory.save(path)
    initial=path.read_bytes()
    memory.history[0]['typed']=after
    memory.save(path)
    assert memory._checkpoint_metrics['status']=='written'
    assert path.read_bytes()!=initial


def test_external_edit_still_forces_durable_replacement(tmp_path):
    memory=CampaignMemory('fixture','rocket_launch')
    path=tmp_path/'state.json'
    memory.save(path)
    expected=path.read_bytes()
    path.write_text('{}')
    memory.save(path)
    assert memory._checkpoint_metrics['status']=='written'
    assert path.read_bytes()==expected


def test_new_invalid_float_cannot_hide_behind_cache(tmp_path):
    memory=CampaignMemory('fixture','rocket_launch')
    path=tmp_path/'state.json'
    memory.save(path)
    memory.history.append({'value':float('nan')})
    with pytest.raises(ValueError): memory.save(path)
    assert memory._checkpoint_cache is None
