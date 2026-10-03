from copy import deepcopy
from types import SimpleNamespace
import pytest
from jev_factorio.compatible_recovery import validate_selected_paid_handoff
from jev_factorio import paid_selection_reconciliation as paid


def memory():
    plan={"id":"13","steps":[{"kind":"craft"}]}
    return SimpleNamespace(status="running",step_index=0,active_plan=plan,
        pending=None,attempt=None,native_pending=None,native_attempt=None,
        background_job=None,background_attempt=None,transfer_recovery=None,
        reservations={},history=[{"kind":"paid_duplicate_selection_reconciled","selected_plan":deepcopy(plan)}],
        blocked_recovery_archive=None)


def test_selected_handoff_requires_authenticated_carry(monkeypatch):
    m=memory()
    monkeypatch.setattr(paid,"validate_representation_budget_carry",lambda *args,**kwargs: (_ for _ in ()).throw(ValueError("bad signature")))
    with pytest.raises(ValueError,match="bad signature"):
        validate_selected_paid_handoff(m)


@pytest.mark.parametrize("field,value",[("pending",{}),("native_pending",{}),("native_attempt",{}),
    ("background_job",{}),("background_attempt",{}),("transfer_recovery",{}),("reservations",{"actor":{}}),
    ("step_index",True),("step_index",1),("status","completed"),("active_plan",{"id":"20"})])
def test_selected_handoff_rejects_ambiguous_or_changed_state(monkeypatch,field,value):
    m=memory();setattr(m,field,value)
    monkeypatch.setattr(paid,"validate_representation_budget_carry",lambda *a,**k: pytest.fail("must reject before carry"))
    with pytest.raises(ValueError):validate_selected_paid_handoff(m)


def test_archive_is_required_and_rechecked(monkeypatch):
    m=memory();m.blocked_recovery_archive={"entry_count":1024}
    with pytest.raises(ValueError,match="archive coverage"):validate_selected_paid_handoff(m)
    class Index:
        def __init__(self):self.calls=0
        def validate_files(self):self.calls+=1
    index=Index();m._blocked_recovery_archive_index=index
    def validate(actual,record,*,archive_index):
        assert actual is m and archive_index is index
        return {"rows":[{},{}],"seen_candidate_sha256":{"13","20"}}
    monkeypatch.setattr(paid,"validate_representation_budget_carry",validate)
    validate_selected_paid_handoff(m)
    assert index.calls==2


def test_duplicate_admin_and_missing_bill_are_rejected(monkeypatch):
    m=memory();m.history*=2
    with pytest.raises(ValueError,match="unique signed"):validate_selected_paid_handoff(m)
    m=memory();monkeypatch.setattr(paid,"validate_representation_budget_carry",lambda *a,**k:{"rows":[{}],"seen_candidate_sha256":{"13","20"}})
    with pytest.raises(ValueError,match="billed rows"):validate_selected_paid_handoff(m)
