"""Explicit signed paid-response reconciliation; never a provider or actor call.

Ordinary checkpoint/ledger readers remain strict. Only this separate supervisor
API can propose correction of an already-paid legacy representation defect.
The original checkpoint, rows, signed authority and research bytes are required
inputs and must remain retained by the durable caller.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import base64
import json
import os
import re
from pathlib import Path
import stat
import subprocess


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate reconciliation JSON key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _require(value, message):
    if not value:
        raise ValueError(message)


# v1 is an explicit supervisor recovery authority, not a caller-selected trust root.
# This public enrollment pin is independent of persisted checkpoint/history data.
RECONCILIATION_SIGNERS_BASENAME = "source-signature-allowed-signers-0095-v3.txt"
RECONCILIATION_SIGNERS_SHA256 = "d8463a2453db96f179b86bd9682c77fe80d90bd299c8e36ef9ed361d8ae91bfd"

_AUTHORITY_FIELDS = {"schema", "authorization_id", "checkpoint_sha256",
        "events_sha256", "manifest_sha256", "integrity_sha256", "session_id", "source_revision",
        "prior_index", "paid_index", "model_call_id", "selected_plan_id", "confidence_floor",
        "max_request_bytes", "owner_absent", "native_pending_none", "fresh_candidate",
        "fresh_candidate_evidence", "fresh_tick", "fresh_native_proof_sha256", "native073_proof_sha256", "writer_lock_pin", "target_source_revision", "source_handoff_sha256", "source_handoff_status", "ordinary_gates_sha256", "budget_carry_sha256"}

def _verify_signature(raw, signature, trust_pin):
    """Verify actual OpenSSH authority and the complete pinned public trust file."""
    _require(type(trust_pin) is dict and set(trust_pin) == {"path","sha256","size","device","inode","uid","mode","mtime_ns","ctime_ns"}
             and type(trust_pin.get("path")) is str and Path(trust_pin["path"]).name == RECONCILIATION_SIGNERS_BASENAME
             and trust_pin.get("sha256") == RECONCILIATION_SIGNERS_SHA256,
             "Reconciliation authority trust root is not enrolled")
    path = Path(trust_pin["path"])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    descriptors = []
    try:
        before = os.fstat(fd)
        def identity(st):
            return (st.st_dev, st.st_ino, st.st_size, st.st_uid,
                    stat.S_IMODE(st.st_mode), st.st_mtime_ns, st.st_ctime_ns)
        expected = tuple(trust_pin[key] for key in
                         ("device", "inode", "size", "uid", "mode", "mtime_ns", "ctime_ns"))
        _require(all(type(value) is int for value in expected)
                 and stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                 and before.st_uid == 1000 and stat.S_IMODE(before.st_mode) == 0o400
                 and 0 < before.st_size <= 32768, "Invalid signed authority trust metadata")
        trust = os.read(fd, 32769)
        _require(len(trust) == before.st_size and _hash(trust) == trust_pin["sha256"]
                 and identity(before) == identity(os.fstat(fd)) == identity(path.lstat())
                 == expected and path.lstat().st_nlink == 1, "Signed authority trust changed")
        for label, data in (("signature", signature), ("trust", trust)):
            descriptor = os.memfd_create(label, os.MFD_CLOEXEC)
            descriptors.append(descriptor)
            _require(os.write(descriptor, data) == len(data), "Incomplete signature capability")
            os.lseek(descriptor, 0, 0)
        result = subprocess.run([
            "/usr/bin/ssh-keygen", "-Y", "verify", "-f", f"/proc/self/fd/{descriptors[1]}",
            "-I", "Timothy.Gregg@complete.tech", "-n", "file", "-s",
            f"/proc/self/fd/{descriptors[0]}"], input=raw, capture_output=True,
            pass_fds=tuple(descriptors), timeout=15)
        _require(result.returncode == 0, "Supervisor authority signature rejected")
        _require(identity(os.fstat(fd)) == identity(path.lstat()) == expected
                 and os.fstat(fd).st_nlink == path.lstat().st_nlink == 1,
                 "Signed authority trust changed during verification")
    finally:
        os.close(fd)
        for descriptor in descriptors:
            os.close(descriptor)


def prepare_paid_duplicate_projection(checkpoint_raw, events_raw, manifest_raw,
                                      integrity_raw, authority_raw, signature,
                                      trust_pin, fresh_native_proof_raw, native073_proof_raw, source_handoff_raw):
    """Return an evidence-bound proposal and receipt, without writing anything.

    A root supervisor must retain all inputs, validate the fresh native proof,
    then commit under the original held writer lock with durable one-use P/R.
    The selected plan still enters the ordinary fresh observation/dispatch gates.
    """
    from .blocked_persistence import _candidate_semantic_sha256, _validate_state
    from .judgments import select_plan
    from .skills import Plan
    for raw in (checkpoint_raw, events_raw, manifest_raw, integrity_raw, authority_raw, signature, fresh_native_proof_raw, native073_proof_raw, source_handoff_raw):
        _require(type(raw) is bytes and 0 < len(raw) <= 4 * 1024 * 1024,
                 "Missing or oversized complete reconciliation input")
    _verify_signature(authority_raw, signature, trust_pin)
    authority = _json(authority_raw)
    _require(type(authority) is dict and set(authority) == _AUTHORITY_FIELDS
        and authority["schema"] == "jev.paid-selection-representation-reconciliation.v1",
        "Invalid explicit reconciliation authority")
    for field, raw in (("checkpoint_sha256", checkpoint_raw), ("events_sha256", events_raw),
                       ("manifest_sha256", manifest_raw), ("integrity_sha256", integrity_raw)):
        _require(authority[field] == _hash(raw), "Complete reconciliation evidence differs")
    _require(type(authority["authorization_id"]) is str and authority["authorization_id"]
             and authority["owner_absent"] is True and authority["native_pending_none"] is True,
             "Owner/native quiescence is not authorized")
    _require(type(authority["fresh_tick"]) is int and authority["fresh_tick"] >= 0
             and type(authority["confidence_floor"]) in {int, float}
             and 0 <= authority["confidence_floor"] <= 1
             and type(authority["max_request_bytes"]) is int and authority["max_request_bytes"] > 0,
             "Invalid ordinary gate or fresh observation settings")
    fresh = _json(fresh_native_proof_raw)
    _require(authority["fresh_native_proof_sha256"] == _hash(fresh_native_proof_raw)
             and fresh["checkpoint_sha256"] == authority["checkpoint_sha256"]
             and fresh["session_id"] == authority["session_id"]
             and fresh["source_revision"] == authority["target_source_revision"]
             and fresh["tick"] == authority["fresh_tick"]
             and fresh["candidate"] == authority["fresh_candidate"]
             and fresh["candidate_evidence"] == authority["fresh_candidate_evidence"]
             and fresh["native_pending_none"] is True and fresh["owner_absent"] is True,
             "Complete supervisor fresh native equivalence proof differs")
    # Pin the unchanged ordinary gate source, rather than accepting a callback.
    from . import judgments
    _require(authority["ordinary_gates_sha256"] == _hash(Path(judgments.__file__).read_bytes()),
             "Ordinary decision gates changed")
    checkpoint = _json(checkpoint_raw)
    _require(authority["fresh_tick"] >= checkpoint["last_tick"], "Fresh native proof predates checkpoint")
    _require(checkpoint["session_id"] == authority["session_id"]
             and checkpoint["active_plan"] is None and checkpoint["step_index"] == 0
             and all(checkpoint.get(key) is None for key in
                     ("pending", "attempt", "background_attempt", "background_job",
                      "native_pending", "native_attempt", "transfer_recovery"))
             and checkpoint.get("reservations") == {},
             "Checkpoint is not a quiescent no-action selection boundary")
    handoff = _json(source_handoff_raw)
    from .blocked_persistence import _source
    target = _source(authority["target_source_revision"])
    _require(target["commit"] != "0" * 40 and target["source_sha256"] != "0" * 64
             and _hash(source_handoff_raw) == authority["source_handoff_sha256"]
             and type(authority["source_handoff_status"]) is str and authority["source_handoff_status"]
             and handoff["status"] == authority["source_handoff_status"]
             and handoff["old_commit"] == authority["source_revision"]["commit"]
             and handoff["new_commit"] == handoff["source_after"]["commit"] == target["commit"]
             and handoff["source_after"]["source_sha256"] == target["source_sha256"]
             and handoff["checkpoint_sha256"] == _hash(checkpoint_raw)
             and all(handoff[key] is False for key in
                     ("native_observation_called", "model_called", "actor_action_dispatched", "automatic_retry_allowed")),
             "Successful complete target source handoff differs")
    state = checkpoint["blocked_recovery"]
    _require(state["source_revision"] == authority["source_revision"], "Original recovery header differs")
    prior_index, paid_index = authority["prior_index"], authority["paid_index"]
    _require(type(prior_index) is int and type(paid_index) is int
             and 0 <= prior_index < paid_index == len(state["attempts"]) - 1,
             "Reconciliation must identify the exact last paid row")
    prior, paid = state["attempts"][prior_index], state["attempts"][paid_index]
    _require(prior["outcome"] == "rejected" and paid["outcome"] == "pending"
             and prior["source_revision"] == paid["source_revision"] == authority["source_revision"]
             and prior["decision_input_sha256"] != paid["decision_input_sha256"]
             and prior["selection_batch"]["state_sha256"] == paid["selection_batch"]["state_sha256"]
             and prior["selection_batch"]["offered"] == paid["selection_batch"]["offered"],
             "Not the exact paid duplicate representation defect")
    events = [_json(line) for line in events_raw.splitlines()]
    # Verify the complete hash chain and sealed manifest/integrity via the
    # existing pure research verifier before interpreting a model response.
    from .research_log import digest, validate_manifest, validate_event, INTEGRITY_SCHEMA
    manifest, integrity = _json(manifest_raw), _json(integrity_raw)
    validate_manifest(manifest)
    previous = digest(manifest)
    last_monotonic = -1
    for number, event in enumerate(events, 1):
        validate_event(event)
        _require(event["sequence"] == number and event["session_id"] in (None, authority["session_id"])
                 and event["run_id"] == manifest["run_id"]
                 and event["time"]["monotonic_ns"] >= last_monotonic
                 and event["prev_hash"] == previous
                 and digest({key: value for key, value in event.items() if key != "event_hash"}) == event["event_hash"],
                 "Research event chain differs")
        previous = event["event_hash"]
        last_monotonic = event["time"]["monotonic_ns"]
        _require((number == 1 and event["event_type"] == "run_started"
                  and event["payload"] == {"manifest_hash": digest(manifest)})
                 or (number > 1 and event["event_type"] != "run_started"), "Research start differs")
        _require(number == len(events) or event["event_type"] != "run_finished", "Post-terminal research")
    _require(type(integrity.get("schema_version")) is int and type(integrity.get("event_count")) is int
             and events[-1]["event_type"] == "run_finished" and integrity == {
        "schema": INTEGRITY_SCHEMA, "schema_version": 1, "run_id": manifest["run_id"],
        "manifest_hash": digest(manifest), "event_count": len(events), "final_event_hash": previous},
        "Research stream is not completely sealed")
    call = authority["model_call_id"]
    def one(kind):
        rows = [event["payload"] for event in events if event["event_type"] == kind
                and event["payload"].get("model_call_id") == call]
        _require(len(rows) == 1, "Paid response correlation is ambiguous")
        return rows[0]
    request, response, decision = one("model_request"), one("model_response"), one("decision")
    _require(request["is_mock"] is False and response["status"] == "ok"
             and request["requested_model"] == response["requested_model"]
             == response["resolved_model"] == manifest["configuration"]["requested_model"]
             and manifest["configuration"]["mock_model"] is False
             and manifest["configuration"]["confidence_floor"] == authority["confidence_floor"]
             and manifest["configuration"]["policy"] == "jev"
             and manifest["provenance"]["git"]["commit"] == authority["source_revision"]["commit"]
             and all(payload["session_id"] == authority["session_id"]
                     and payload["supervisor_provenance"]["code_revision"] == authority["source_revision"]
                     for payload in (request, response, decision))
             and all(type(request[key]) is str and request[key]
                     and request[key] == response[key] == decision[key]
                     for key in ("model_call_id", "decision_id", "observation_id"))
             and decision["diagnostics"]["max_request_bytes"] == authority["max_request_bytes"],
             "Real paid model/source/session/correlation differs")
    _require(request["factorio_tick"] == response["factorio_tick"] == decision["factorio_tick"] == paid["tick"]
             and decision["model_called"] is True and decision["plan_id"] == authority["selected_plan_id"]
             and decision["diagnostics"]["outcome"] == "selected"
             and decision["confidence_floor"] == authority["confidence_floor"]
             and not any(event["event_type"] == "plan_committed" or event["event_type"].startswith("action_")
                         for event in events), "Paid selection/action truth differs")
    created = [event["payload"] for event in events if event["event_type"] == "candidate_set_created"
               and event["payload"]["factorio_tick"] == paid["tick"]]
    _require(len(created) == 1, "Complete paid candidate set is ambiguous")
    plans = [Plan.from_dict(row) for row in created[0]["plans"]]
    # Restore the ONE redacted public authorization digest from the complete
    # signed historical native proof, then bind the exact original WAL preimage.
    native = _json(native073_proof_raw)
    _require(_hash(native073_proof_raw) == authority["native073_proof_sha256"],
             "Complete original native proof differs")
    authorization_hash = native["ownership_witness"]["authorization_sha256"]
    _require(native["ownership_readback"]["output"]["authorization_sha256"] == authorization_hash
             and native["ownership_readback"]["install_authorization"]["authorization_sha256"] == authorization_hash,
             "Native authorization history does not crosslink")
    packet = deepcopy(request["state"])
    output = packet["facts"]["factory"]["bootstrap_output"]
    _require(output["authorization_sha256"] == "[REDACTED]", "Unexpected original trace redaction")
    output["authorization_sha256"] = authorization_hash
    ranking = packet["deterministic_ranking"]
    by_id = {plan.id: plan for plan in plans}
    _require(type(ranking) is list and len(ranking) == len(by_id) and set(ranking) == set(by_id),
             "Exact original offered order is not retained")
    plans = [by_id[plan_id] for plan_id in ranking]
    from .blocked_persistence import _selection_request_sha256, _stable, _source
    plan_rows = [plan.to_dict() for plan in plans]
    _require(_selection_request_sha256(packet, request["questions"], plan_rows,
             current_tick=paid["tick"]) == paid["selection_batch"]["request_sha256"],
             "Exact original WAL request is not reconstructed")
    original_input = {"schema": 1, "session_id": checkpoint["session_id"],
        "source_revision": _source(paid["source_revision"]), "target": checkpoint["target"],
        "policy": "jev", "confidence_floor": authority["confidence_floor"],
        "state": _stable(packet, current_tick=paid["tick"]),
        "plans": _stable(plan_rows, path=("plans",), current_tick=paid["tick"]),
        "selection_batch": paid["selection_batch"]}
    input_raw = json.dumps(original_input, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _require(_hash(input_raw) == paid["decision_input_sha256"],
             "Exact original paid input is not reconstructed")
    # Reconstruct the complete pre-question compiler state, including recipe
    # records factored inside native facts. Exact WAL bytes above stay unchanged.
    from .blocked_persistence import _selection_semantic_evidence, selection_state_sha256
    shared = packet.get("shared_recipes")
    unfactored = deepcopy(packet)
    unfactored.pop("shared_recipes", None)
    raw_state = (_selection_semantic_evidence({"candidate_evidence": {"state": unfactored},
                 "shared_recipes": shared})["state"] if shared is not None else unfactored)
    for key in ("execution_contract", "judgment_contract", "candidate_plans", "shared_plan_materials", "shared_recipes"):
        raw_state.pop(key, None)
    raw_state["candidate_evidence"] = deepcopy(decision["selection_support"]["candidate_evidence"])
    class RecordedResponse:
        def evaluate(self, context, questions):
            return deepcopy(response["answers"])
    replay = select_plan(RecordedResponse(), raw_state, plans,
                         authority["confidence_floor"], authority["max_request_bytes"])
    _require(replay.plan_id == decision["plan_id"] and replay.diagnostics["outcome"] == "selected",
             "Unchanged ordinary gates rejected the paid response")
    selected = next(plan.to_dict() for plan in plans if plan.id == replay.plan_id)
    corrected = []
    for plan in plans:
        corrected.append({"plan_id": plan.id, "candidate_sha256": _candidate_semantic_sha256(
            plan.to_dict(), raw_state["candidate_evidence"].get(plan.id), current_tick=paid["tick"])})
    corrected.sort(key=lambda row: (row["plan_id"], row["candidate_sha256"]))
    _require([row["plan_id"] for row in corrected] == [row["plan_id"] for row in paid["selection_batch"]["offered"]]
             and corrected != paid["selection_batch"]["offered"], "No exact representation correction")
    fresh_hash = _candidate_semantic_sha256(authority["fresh_candidate"],
        authority["fresh_candidate_evidence"], current_tick=authority["fresh_tick"])
    selected_hash = next(row["candidate_sha256"] for row in corrected if row["plan_id"] == selected["id"])
    _require(fresh_hash == selected_hash, "Fresh native candidate is not semantically equivalent")
    proposal = deepcopy(checkpoint)
    proposal["blocked_recovery"]["source_revision"] = deepcopy(target)
    projected = proposal["blocked_recovery"]["attempts"][paid_index]
    projected["selection_batch"]["offered"] = corrected
    projected["outcome"] = "selected"
    _validate_state(proposal["blocked_recovery"], proposal["session_id"])
    proposal["active_plan"], proposal["step_index"] = selected, 0
    proposal["status"], proposal["reason"] = "running", ""
    original_state_hash = selection_state_sha256(raw_state, plan_rows,
        session_id=checkpoint["session_id"], source_revision=authority["source_revision"],
        target=checkpoint["target"], policy="jev", confidence_floor=authority["confidence_floor"],
        current_tick=paid["tick"])
    _require(original_state_hash == paid["selection_batch"]["state_sha256"] == prior["selection_batch"]["state_sha256"],
             "Original pre-question state is not reconstructed")
    target_state_hash = selection_state_sha256(raw_state, plan_rows,
        session_id=checkpoint["session_id"], source_revision=target,
        target=checkpoint["target"], policy="jev", confidence_floor=authority["confidence_floor"],
        current_tick=paid["tick"])
    budget_carry = dict(schema="jev.paid-selection-representation-budget-carry.v1",
        session_id=checkpoint["session_id"], target=checkpoint["target"], policy="jev",
        confidence_floor=authority["confidence_floor"], tick=paid["tick"],
        original_state_sha256=original_state_hash, target_state_sha256=target_state_hash,
        pre_question_state=deepcopy(raw_state), plans=deepcopy(plan_rows),
        authority_base64=base64.b64encode(authority_raw).decode("ascii"),
        signature_base64=base64.b64encode(signature).decode("ascii"), trust_pin=deepcopy(trust_pin))
    scope_sha = representation_budget_scope_sha256(budget_carry, authority["source_revision"],
        target, [prior, paid], corrected, selected)
    _require(authority["budget_carry_sha256"] == scope_sha,
             "ROOT signed exact paid budget carry scope differs")
    receipt = dict(schema="jev.paid-selection-representation-reconciliation-receipt.v1",
        authorization_id=authority["authorization_id"], authority_sha256=_hash(authority_raw),
        original_source_revision=deepcopy(authority["source_revision"]),
        target_source_revision=deepcopy(target), source_handoff_sha256=_hash(source_handoff_raw),
        original_checkpoint_sha256=_hash(checkpoint_raw), events_sha256=_hash(events_raw),
        original_attempts=deepcopy([prior, paid]), corrected_offered=deepcopy(corrected),
        selected_plan=deepcopy(selected), same_semantic_candidates=True, budget_carry=budget_carry,
        original_prior_index=prior_index, original_paid_index=paid_index,
        original_paid_batch_count=sum(1 for row in state["attempts"] if "selection_batch" in row),
        active_attempt_count=len(state["attempts"]), archive=deepcopy(checkpoint["blocked_recovery_archive"]),
        original_request_sha256=paid["selection_batch"]["request_sha256"],
        original_input_sha256=paid["decision_input_sha256"],
        restored_authorization_sha256=authorization_hash,
        original_offered_order=ranking, representation_correction_only=True,
        paid_attempt_count_unchanged=True, duplicate_paid_batches_count=2, ordinary_gates_sha256=authority["ordinary_gates_sha256"],
        fresh_native_proof_sha256=authority["fresh_native_proof_sha256"],
        field_patches=[
            {"path": ["blocked_recovery", "source_revision"], "before": deepcopy(state["source_revision"]), "after": deepcopy(target)},
            {"path": ["blocked_recovery", "attempts", paid_index, "selection_batch", "offered"], "before": deepcopy(paid["selection_batch"]["offered"]), "after": deepcopy(corrected)},
            {"path": ["blocked_recovery", "attempts", paid_index, "outcome"], "before": "pending", "after": "selected"},
            {"path": ["active_plan"], "before": None, "after": deepcopy(selected)},
            {"path": ["status"], "before": checkpoint["status"], "after": "running"},
            {"path": ["reason"], "before": checkpoint["reason"], "after": ""}],
        retained_history_prefix_count=len(checkpoint["history"]),
        model_called=False, native_action_called=False, automatic_retry_allowed=False)
    proposal["history"].append({"kind": "paid_duplicate_selection_reconciled", **deepcopy(receipt)})
    # The external durable receipt pins the final CP; the history entry retains
    # the pre-publication receipt without a circular checkpoint digest.
    proposal_raw = json.dumps(proposal, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    receipt["projected_checkpoint_sha256"] = _hash(proposal_raw)
    return proposal, receipt


def projection_bytes_under_held_lock(lock_fd, lock_path, checkpoint_raw, events_raw,
                                     manifest_raw, integrity_raw, authority_raw,
                                     signature, trust_pin, fresh_native_proof_raw,
                                     native073_proof_raw, source_handoff_raw):
    """Root signed-supervisor entrypoint; returns bytes for its durable CAS.

    Never acquires another flock or writes a checkpoint. The supervisor must
    re-read exact current CP/provider/source/owner/fresh-native pins immediately
    before its one-use durable prepared/CAS/result publication. Ambiguous CAS
    or receipt publication is consumed and must be reconciled, never retried.
    """
    _require(os.geteuid() == 0, "Paid reconciliation requires root supervisor")
    authority = _json(authority_raw)
    def original_lock():
        pin = authority["writer_lock_pin"]
        _require(type(lock_fd) is int and lock_fd >= 0 and type(lock_path) is str
                 and lock_path == pin["path"], "Original writer-lock capability differs")
        st = os.fstat(lock_fd)
        current = Path(lock_path).lstat()
        fields = ("device", "inode", "size", "uid", "mode", "mtime_ns", "ctime_ns")
        expected = tuple(pin[key] for key in fields)
        metadata = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_uid,
            stat.S_IMODE(value.st_mode), value.st_mtime_ns, value.st_ctime_ns)
        _require(all(type(value) is int for value in expected) and pin["uid"] == 1000
                 and pin["mode"] == 0o600 and stat.S_ISREG(st.st_mode)
                 and st.st_nlink == current.st_nlink == 1
                 and metadata(st) == metadata(current) == expected,
                 "Original writer-lock full identity differs")
        lines = [line for line in Path(f"/proc/self/fdinfo/{lock_fd}").read_text().splitlines()
                 if line.startswith("lock:")]
        _require(len(lines) == 1, "Original FD does not already hold the exclusive lock")
        match = re.fullmatch(r"lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+([0-9]+)\s+"
            r"([0-9a-f]+):([0-9a-f]+):([0-9]+)\s+0\s+EOF", lines[0])
        _require(match is not None and int(match[1]) > 0
                 and (int(match[2], 16), int(match[3], 16), int(match[4])) ==
                     (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino),
                 "Original per-FD exclusive ownership differs")
        _require(metadata(os.fstat(lock_fd)) == metadata(Path(lock_path).lstat()) == expected
                 and os.fstat(lock_fd).st_nlink == Path(lock_path).lstat().st_nlink == 1,
                 "Original lock changed during ownership proof")
    original_lock()
    proposal, receipt = prepare_paid_duplicate_projection(checkpoint_raw, events_raw,
        manifest_raw, integrity_raw, authority_raw, signature, trust_pin,
        fresh_native_proof_raw, native073_proof_raw, source_handoff_raw)
    original_lock()
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return encode(proposal), encode(receipt)


def authenticate_representation_history_receipt(record, session_id, campaign_target):
    """Authenticate complete signed scope; row/archive authority is checked by loaders."""
    from .blocked_persistence import (_source, _candidate_semantic_sha256, selection_state_sha256)
    _require(type(record) is dict and record.get("kind") == "paid_duplicate_selection_reconciled"
             and record.get("schema") == "jev.paid-selection-representation-reconciliation-receipt.v1",
             "Invalid paid reconciliation receipt")
    carry = record.get("budget_carry")
    required = {"schema", "session_id", "target", "policy", "confidence_floor", "tick",
        "original_state_sha256", "target_state_sha256", "pre_question_state", "plans",
        "authority_base64", "signature_base64", "trust_pin"}
    _require(type(carry) is dict and set(carry) == required
             and carry["schema"] == "jev.paid-selection-representation-budget-carry.v1",
             "Invalid scoped representation budget carry")
    _require(carry["session_id"] == session_id and carry["target"] == campaign_target
             and carry["policy"] == "jev" and type(carry["tick"]) is int and carry["tick"] >= 0,
             "Paid carry campaign scope differs")
    raw = base64.b64decode(carry["authority_base64"], validate=True)
    sig = base64.b64decode(carry["signature_base64"], validate=True)
    _require(0 < len(raw) <= 4*1024*1024 and 0 < len(sig) <= 32768,
             "Paid carry signed authority bound differs")
    _verify_signature(raw, sig, carry["trust_pin"])
    authority = _json(raw)
    _require(type(authority) is dict and set(authority) == _AUTHORITY_FIELDS
             and authority["owner_absent"] is True and authority["native_pending_none"] is True,
             "Incomplete ROOT paid carry authority")
    source = _source(record["original_source_revision"])
    target = _source(record["target_source_revision"])
    _require(authority["schema"] == "jev.paid-selection-representation-reconciliation.v1"
             and authority["authorization_id"] == record["authorization_id"]
             and _hash(raw) == record["authority_sha256"]
             and authority["source_revision"] == source and authority["target_source_revision"] == target
             and authority["session_id"] == session_id
             and authority["checkpoint_sha256"] == record["original_checkpoint_sha256"]
             and authority["events_sha256"] == record["events_sha256"]
             and authority["source_handoff_sha256"] == record["source_handoff_sha256"]
             and authority["confidence_floor"] == carry["confidence_floor"]
             and authority["ordinary_gates_sha256"] == record["ordinary_gates_sha256"],
             "Paid carry signed receipt crosslinks differ")
    _require(record["same_semantic_candidates"] is True
             and record["representation_correction_only"] is True
             and record["paid_attempt_count_unchanged"] is True
             and type(record["duplicate_paid_batches_count"]) is int
             and record["duplicate_paid_batches_count"] == 2
             and all(record[key] is False for key in ("model_called","native_action_called","automatic_retry_allowed")),
             "Paid carry causal facts differ")
    original = record["original_attempts"]
    _require(authority["budget_carry_sha256"] == representation_budget_scope_sha256(
        carry,source,target,original,record["corrected_offered"],record["selected_plan"]),
        "Paid carry ROOT signed exact scope differs")
    _require(type(original) is list and len(original) == 2
             and original[0]["outcome"] == "rejected" and original[1]["outcome"] == "pending"
             and original[0]["source_revision"] == original[1]["source_revision"] == source
             and original[0]["selection_batch"]["offered"] == original[1]["selection_batch"]["offered"]
             and original[0]["selection_batch"]["state_sha256"] == original[1]["selection_batch"]["state_sha256"] == carry["original_state_sha256"]
             and original[1]["selection_batch"]["request_sha256"] == record["original_request_sha256"]
             and original[1]["decision_input_sha256"] == record["original_input_sha256"]
             and original[0]["decision_input_sha256"] != original[1]["decision_input_sha256"]
             and carry["tick"] == original[1]["tick"], "Paid carry exact original rows differ")
    for revision, expected in ((source, carry["original_state_sha256"]),
                               (target, carry["target_state_sha256"])):
        calculated = selection_state_sha256(carry["pre_question_state"], carry["plans"],
            session_id=session_id, source_revision=revision, target=campaign_target,
            policy=carry["policy"], confidence_floor=carry["confidence_floor"], current_tick=carry["tick"])
        _require(calculated == expected, "Paid carry exact normalized state differs")
    evidence = carry["pre_question_state"].get("candidate_evidence", {})
    canonical = sorted(({"plan_id": plan["id"], "candidate_sha256":
        _candidate_semantic_sha256(plan,evidence.get(plan["id"]),current_tick=carry["tick"])}
        for plan in carry["plans"]), key=lambda row:(row["plan_id"],row["candidate_sha256"]))
    _require(canonical == record["corrected_offered"]
             and [row["plan_id"] for row in canonical] == [row["plan_id"] for row in original[1]["selection_batch"]["offered"]],
             "Paid carry canonical seen candidates differ")
    _require(type(authority["fresh_tick"]) is int and authority["fresh_tick"] >= carry["tick"],
             "Paid carry fresh proof predates original state")
    chosen_hash=next((row["candidate_sha256"] for row in canonical
                     if row["plan_id"] == authority["selected_plan_id"]),None)
    _require(chosen_hash is not None and _candidate_semantic_sha256(authority["fresh_candidate"],
        authority["fresh_candidate_evidence"],current_tick=authority["fresh_tick"]) == chosen_hash,
        "Paid carry signed fresh candidate differs")
    selected = next((plan for plan in carry["plans"] if plan["id"] == authority["selected_plan_id"]),None)
    _require(selected == record["selected_plan"], "Paid carry selected plan differs")
    return original, canonical, source, target, carry


def validate_representation_budget_carry(memory, record, *, archive_index=None):
    """Authenticate signed scope and exact current/archive-backed billed rows."""
    from .blocked_persistence import _validate_state, find_attempt
    original, canonical, source, target, carry = authenticate_representation_history_receipt(
        record, memory.session_id, memory.target)
    state = _validate_state(memory.blocked_recovery,memory.session_id)
    rows=[]
    for position, before in enumerate(original):
        expected = deepcopy(before)
        if position == 1:
            expected["selection_batch"]["offered"] = deepcopy(canonical)
            expected["outcome"] = "selected"
        row = find_attempt(memory,source,before["decision_input_sha256"],archive_index=archive_index)
        _require(row == expected, "Paid carry preserved billed row differs or is absent")
        rows.append(deepcopy(row))
    return {"target_source":target,"target_state_sha256":carry["target_state_sha256"],
            "rows":rows,"seen_candidate_sha256":{row["candidate_sha256"] for row in canonical}}


def scoped_representation_budget_rows(memory, aliases, *, archive_index=None):
    """Return rows only for a receipted state under a source already queried."""
    result=[];seen=set();matches=0
    for record in memory.history:
        if not isinstance(record,dict) or record.get("kind") != "paid_duplicate_selection_reconciled":continue
        scope=validate_representation_budget_carry(memory,record,archive_index=archive_index)
        if (scope["target_source"],scope["target_state_sha256"]) not in aliases:continue
        matches+=1
        _require(matches==1,"Ambiguous paid representation budget carry")
        result.extend(scope["rows"]);seen.update(scope["seen_candidate_sha256"])
    return result,seen


def representation_budget_scope_sha256(carry, original_source, target_source,
                                       original_attempts, corrected_offered, selected_plan):
    """Pure unsigned scope proposal; its digest requires explicit ROOT signature."""
    metadata={key:value for key,value in carry.items()
              if key not in {"authority_base64","signature_base64","trust_pin"}}
    body={"carry":metadata,"original_source_revision":original_source,
          "target_source_revision":target_source,"original_attempts":original_attempts,
          "corrected_offered":corrected_offered,"selected_plan":selected_plan}
    return _hash(json.dumps(body,sort_keys=True,separators=(",",":"),allow_nan=False).encode())
