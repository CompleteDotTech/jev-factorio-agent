"""Reconstruction and writer/reader compatibility for persistent wait rows."""
from __future__ import annotations

import copy
from io import BytesIO
import json

import pytest

from jev_factorio.acceptance_io import records
from jev_factorio.dashboard import EventWriter, Monitor
from jev_factorio import wait_record_codec as codec
from jev_factorio.wait_record_codec import (
    MAX_REFERENCE_BYTES, MAX_REFERENCE_ROWS, MARKER, Decoder, Encoder,
    decode_jsonl, encode_line, iter_stream, parse_json,
)


REVISION = {"commit": "a" * 40, "source_sha256": "b" * 64}


def gameplay(tick: int, *, session: str = "session-1", source=REVISION,
             process: int = 123, amount: int = 5) -> dict:
    return {
        "schema_version": 2, "controller": "hierarchical", "policy": "jev",
        "session_id": session, "world_kind": "fle", "target": "rocket_launch",
        "process_id": process, "code_revision": copy.deepcopy(source),
        "tick": tick, "recorded_at_utc": f"2026-10-01T00:00:{tick:02d}+00:00",
        "status": "blocked", "action": "observe", "outcome": "blocked wait",
        "state": {"tick": tick, "session_id": session,
                  "factory": {"inventory": {"coal": amount},
                              "nearby": [{"name": "coal", "distance": 4.0}]},
                  "unchanged_large_observation": {"facts": ["ore", "belt", "furnace"] * 400}},
        "after_state": {"tick": tick, "session_id": session,
                        "factory": {"inventory": {"coal": amount},
                                    "nearby": [{"name": "coal", "distance": 4.0}]},
                        "unchanged_large_observation": {"facts": ["ore", "belt", "furnace"] * 400}},
        "decision": {"plan_id": None, "source": "jev", "model_called": False,
                     "confidence": 0.41, "probabilities": {"wait": 0.6, "act": 0.4}},
        "model_call": False,
        "persistent_recovery": {"phase": "waiting_for_changed_game_evidence",
                                 "reason": "candidate evidence unchanged",
                                 "next_observation_seconds": 8.0},
        "performance": {"calls": {"observe": {"count": 1, "total_ns": 1100}}},
        "receipt": {"planned_native_receipt": f"{tick}:factory_insert:boiler:coal"},
        "history": [{"kind": "wait", "tick": tick, "note": "still blocked"}],
    }


def test_gameplay_round_trip_preserves_changed_ticks_and_type_identity():
    first, second = gameplay(10), gameplay(11, amount=6)
    second["decision"]["diagnostic_boolean"] = 1
    first["decision"]["diagnostic_boolean"] = True
    encoder, decoder = Encoder("gameplay"), Decoder("gameplay")

    a = encoder.prepare(first, wait=False)
    a_line = json.dumps(a.wire, allow_nan=False).encode() + b"\n"
    encoder.commit(a, len(a_line))
    got_a = decoder.decode(parse_json(a_line[:-1]), len(a_line), anchor_candidate=True)

    b = encoder.prepare(second, wait=True)
    assert b.is_delta and MARKER in b.wire
    b_line = encode_line(b)
    assert len(b_line) < len(json.dumps(second).encode()) * 0.5
    encoder.commit(b, len(b_line))
    got_b = decoder.decode(parse_json(b_line[:-1]), len(b_line), anchor_candidate=True)

    assert got_a == first
    assert got_b == second
    assert type(got_b["decision"]["diagnostic_boolean"]) is int
    assert type(got_a["decision"]["diagnostic_boolean"]) is bool
    assert got_b["tick"] == got_b["state"]["tick"] == 11
    assert got_b["receipt"] == second["receipt"]
    assert got_b["performance"] == second["performance"]


def test_changed_session_source_or_process_forces_a_full_anchor():
    encoder = Encoder("gameplay")
    one = gameplay(1)
    initial = encoder.prepare(one, wait=False)
    encoder.commit(initial, len(json.dumps(initial.wire).encode()) + 1)
    for changed in (gameplay(2, session="other"),
                    gameplay(2, source={"commit": "c" * 40, "source_sha256": "d" * 64}),
                    gameplay(2, process=124)):
        encoded = encoder.prepare(changed, wait=True)
        assert not encoded.is_delta
        assert MARKER not in encoded.wire
        encoder.commit(encoded, len(json.dumps(encoded.wire).encode()) + 1)


def test_decoder_fails_closed_for_tampered_hash_missing_anchor_and_duplicate_keys():
    row = gameplay(1)
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(row, wait=False)
    encoder.commit(anchor, len(json.dumps(anchor.wire).encode()) + 1)
    next_row = gameplay(2)
    delta = encoder.prepare(next_row, wait=True)
    assert delta.is_delta

    with pytest.raises(ValueError, match="anchor"):
        Decoder("gameplay").decode(delta.wire, len(encode_line(delta)))
    decoder = Decoder("gameplay")
    decoder.decode(anchor.wire, len(json.dumps(anchor.wire).encode()) + 1)
    corrupt = copy.deepcopy(delta.wire)
    corrupt[MARKER]["record_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        decoder.decode(corrupt, len(encode_line(delta)))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_json(b'{"x":1,"x":2}')


@pytest.mark.parametrize("field", ["anchor_tick", "record_tick"])
def test_decoder_rejects_boolean_tick_even_when_integer_value_matches(field):
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(gameplay(1), wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    delta = encoder.prepare(gameplay(1, amount=6), wait=True)
    assert delta.is_delta
    assert delta.wire[MARKER][field] == 1
    assert type(delta.wire[MARKER][field]) is int

    corrupt = copy.deepcopy(delta.wire)
    corrupt[MARKER][field] = True
    decoder = Decoder("gameplay")
    decoder.decode(parse_json(anchor_line[:-1]), len(anchor_line))
    with pytest.raises(ValueError):
        decoder.decode(corrupt, len(encode_line(delta)))


@pytest.mark.parametrize("amplification", ["bytes", "nodes"])
def test_decoder_rejects_reference_amplification_before_applying_patch(monkeypatch,
                                                                      amplification):
    anchor_row = gameplay(1)
    if amplification == "bytes":
        anchor_row["amplifier"] = ["x" * 120_000]
        copies = 150
    else:
        anchor_row["amplifier"] = [0] * 5_000
        copies = 250
    anchor_row["copies"] = {f"copy_{index}": None for index in range(copies)}
    anchor_size = len(codec.canonical(anchor_row)) + 1

    reference = {"k": "r", "p": ["amplifier"],
                 "h": codec.digest(anchor_row["amplifier"])}
    copy_fields = {key: copy.deepcopy(reference) for key in anchor_row["copies"]}
    patch_fields = {
        key: ({"k": "o", "d": [], "f": copy_fields} if key == "copies"
              else {"k": "r", "p": [key], "h": codec.digest(value)})
        for key, value in anchor_row.items()
    }
    patch = {"k": "o", "d": [], "f": patch_fields}
    scope = codec._scope("gameplay", anchor_row)
    wire = {MARKER: {
        "version": codec.VERSION, "channel": "gameplay",
        "scope_sha256": codec.digest(scope),
        "anchor_sha256": codec.digest(anchor_row),
        "anchor_tick": 1, "record_tick": 1,
        "record_sha256": "0" * 64, "patch": patch,
    }}
    encoded_size = len(codec.canonical(wire)) + 1
    assert encoded_size < 256_000

    decoder = Decoder("gameplay")
    decoder.decode(anchor_row, anchor_size)
    monkeypatch.setattr(codec, "_apply", lambda *args, **kwargs: pytest.fail(
        "amplifying patch reached reconstruction before budget validation"))
    with pytest.raises(ValueError, match="output budget"):
        decoder.decode(wire, encoded_size)


def test_list_and_stream_readers_bound_cumulative_reconstruction():
    first, second = gameplay(1), gameplay(2, amount=6)
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(first, wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    delta = encoder.prepare(second, wait=True)
    assert delta.is_delta
    raw = anchor_line + encode_line(delta)
    output_bound = len(codec.canonical(first)) + 100

    with pytest.raises(ValueError, match="output budget"):
        decode_jsonl(raw, "gameplay", max_decoded_bytes=output_bound)
    with pytest.raises(ValueError, match="unreconstructable"):
        list(iter_stream(BytesIO(raw), "gameplay", max_decoded_bytes=output_bound))


def test_stream_blank_lines_advance_the_wait_anchor_retention_window():
    first, second = gameplay(1), gameplay(2, amount=6)
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(first, wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    delta = encoder.prepare(second, wait=True)
    assert delta.is_delta
    raw = anchor_line + (b" " * MAX_REFERENCE_BYTES) + b"\n" + encode_line(delta)

    with pytest.raises(ValueError, match="unreconstructable") as error:
        list(iter_stream(BytesIO(raw), "gameplay", skip_blank=True))
    assert "retention window" in str(error.value.__cause__)


def test_decoder_rejects_nonobject_patch_for_a_new_field():
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(gameplay(1), wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    changed = gameplay(2)
    changed["new_field"] = "value"
    delta = encoder.prepare(changed, wait=True)
    assert delta.is_delta
    corrupt = copy.deepcopy(delta.wire)
    corrupt[MARKER]["patch"]["f"]["new_field"] = None

    decoder = Decoder("gameplay")
    decoder.decode(parse_json(anchor_line[:-1]), len(anchor_line))
    with pytest.raises(ValueError, match="New wait delta keys"):
        decoder.decode(corrupt, len(encode_line(delta)))


def test_encoder_forces_full_anchor_before_row_or_byte_window_expires():
    encoder = Encoder("gameplay")
    first = encoder.prepare(gameplay(1), wait=False)
    encoder.commit(first, len(json.dumps(first.wire).encode()) + 1)
    for number in range(MAX_REFERENCE_ROWS):
        encoder.skip(1)
    at_limit = encoder.prepare(gameplay(2), wait=True)
    assert not at_limit.is_delta

    encoder = Encoder("gameplay")
    first = encoder.prepare(gameplay(1), wait=False)
    first_bytes = len(json.dumps(first.wire).encode()) + 1
    encoder.commit(first, first_bytes)
    encoder.skip(MAX_REFERENCE_BYTES + 1)
    beyond_bytes = encoder.prepare(gameplay(2), wait=True)
    assert not beyond_bytes.is_delta

    encoder = Encoder("gameplay")
    first = encoder.prepare(gameplay(1), wait=False)
    first_bytes = len(encode_line(first))
    encoder.commit(first, first_bytes)
    # The pre-candidate span is still in bounds, but the candidate delta itself
    # would push its anchor outside the retained byte window.
    encoder.skip(MAX_REFERENCE_BYTES - first_bytes - 1)
    near_limit = encoder.prepare(gameplay(2), wait=True)
    assert not near_limit.is_delta


def test_decoder_rejects_delta_whose_own_bytes_expire_the_anchor():
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(gameplay(1), wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    delta = encoder.prepare(gameplay(2), wait=True)
    assert delta.is_delta

    decoder = Decoder("gameplay")
    decoder.decode(parse_json(anchor_line[:-1]), len(anchor_line))
    # Simulate intervening dashboard events occupying the entire remaining
    # reference window. The following candidate delta must not use this anchor.
    decoder.decode({"kind": "cycle_returned"},
                   MAX_REFERENCE_BYTES - len(anchor_line), anchor_candidate=False)
    with pytest.raises(ValueError, match="retention window"):
        decoder.decode(delta.wire, len(encode_line(delta)))


def test_acceptance_reader_materializes_lossless_gameplay_rows():
    first, second = gameplay(20), gameplay(21, amount=7)
    encoder = Encoder("gameplay")
    anchor = encoder.prepare(first, wait=False)
    anchor_raw = json.dumps(anchor.wire, allow_nan=False).encode() + b"\n"
    encoder.commit(anchor, len(anchor_raw))
    delta = encoder.prepare(second, wait=True)
    assert delta.is_delta
    raw = anchor_raw + encode_line(delta)
    assert records(raw) == [first, second]
    assert decode_jsonl(raw, "gameplay") == [first, second]


def test_legacy_pr249_omission_rows_remain_opaque_and_cannot_anchor():
    legacy = gameplay(30)
    legacy["compact_record"] = "persistent_wait"
    legacy["history"] = [{"omitted": "persistent_wait_repeat"}]
    raw = json.dumps(legacy, allow_nan=False).encode() + b"\n"

    decoder = Decoder("gameplay")
    assert decoder.decode(parse_json(raw[:-1]), len(raw)) == legacy
    assert decoder.anchor is None
    assert decode_jsonl(raw, "gameplay") == [legacy]

    encoder = Encoder("gameplay")
    prepared = encoder.prepare(legacy, wait=True)
    assert not prepared.is_delta
    assert prepared.wire == legacy


def _dashboard_record(tick: int, *, amount: int = 3) -> dict:
    return {
        "controller": "hierarchical", "policy": "jev", "tick": tick,
        "session_id": "dash-session", "world_kind": "fle", "target": "rocket_launch",
        "status": "blocked", "action": "observe", "outcome": "blocked wait",
        "model_call": False, "code_revision": REVISION, "process_id": 501,
        "persistent_recovery": {"phase": "waiting_for_changed_game_evidence",
                                 "reason": "candidate evidence unchanged",
                                 "next_observation_seconds": 8.0, "model_call": False},
        "state": {"tick": tick, "session_id": "dash-session",
                  "large": ["stable" * 300 for _ in range(24)]},
        "decision": {"confidence": 0.3, "candidate_evidence": {"coal": amount}},
    }


def test_dashboard_writer_and_monitor_expand_wait_events_and_report_corruption(tmp_path):
    path = tmp_path / "events.jsonl"
    with EventWriter(path) as writer:
        writer.emit("run_started", 2, target="rocket_launch", policy="jev")
        writer.emit("decision_recorded", 7, record=_dashboard_record(1))
        writer.emit("cycle_started", 2)
        writer.emit("decision_recorded", 7, record=_dashboard_record(2, amount=4))

    raw_lines = path.read_bytes().splitlines(keepends=True)
    decoded_wire = [parse_json(line[:-1]) for line in raw_lines]
    assert MARKER not in decoded_wire[1]
    assert MARKER in decoded_wire[3]
    monitor = Monitor(path)
    monitor.poll()
    snapshot = monitor.snapshot()
    assert snapshot["view"]["tick"] == 2
    assert snapshot["view"]["state"]["tick"] == 2
    assert snapshot["view"]["decision"]["candidate_evidence"]["coal"] == 4
    assert snapshot["source"]["reconstruction_status"] == "ok"

    corrupted = copy.deepcopy(decoded_wire[-2])
    corrupted[MARKER]["anchor_sha256"] = "0" * 64
    raw_lines[-2] = json.dumps(corrupted).encode() + b"\n"
    bad_path = tmp_path / "corrupt-events.jsonl"
    bad_path.write_bytes(b"".join(raw_lines))
    broken = Monitor(bad_path)
    broken.poll()
    failed = broken.snapshot()
    assert failed["source"]["reconstruction_status"] == "gap"
    assert failed["source"]["invalid"] > 0
    assert failed["view"].get("state") == {}


def test_dashboard_malformed_tail_immediately_clears_old_view_and_marks_gap(tmp_path):
    path = tmp_path / "events.jsonl"
    with EventWriter(path) as writer:
        writer.emit("run_started", 2, target="rocket_launch", policy="jev")
        writer.emit("decision_recorded", 7, record=_dashboard_record(1))

    # Keep the fixture open at the decision event rather than the writer's
    # orderly run_finished event so the stale view reflects an active block.
    initial_lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(initial_lines[:-1]))

    monitor = Monitor(path)
    monitor.poll()
    prior = monitor.snapshot()
    assert prior["view"]["state"]
    assert prior["view"]["decision"]

    with path.open("ab") as stream:
        stream.write(b"{malformed-tail}\n")
    monitor.poll()
    after = monitor.snapshot()
    assert after["source"]["reconstruction_status"] == "gap"
    assert after["view"]["gap"] is True
    assert after["view"]["state"] == {}
    assert after["view"]["decision"] is None
    assert after["view"]["pending"] is None
    assert after["view"].get("mission_record") is None
    assert after["view"]["status"] is None
    assert after["view"]["kind"] is None
    assert after["view"]["stage"] is None
    assert after["view"]["last_event_time"] is None
    assert after["view"]["model_busy"] is False


def test_dashboard_corrupt_wait_envelope_clears_stale_current_status(tmp_path):
    path = tmp_path / "events.jsonl"
    with EventWriter(path) as writer:
        writer.emit("run_started", 2, target="rocket_launch", policy="jev")
        writer.emit("decision_recorded", 7, record=_dashboard_record(1))

    initial_lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(initial_lines[:-1]))

    monitor = Monitor(path)
    monitor.poll()
    before = monitor.snapshot()["view"]
    assert before["status"] == "blocked"
    assert before["kind"] == "decision_recorded"
    assert before["stage"] == 7
    assert before["last_event_time"] is not None

    anchor_event = parse_json(path.read_bytes().splitlines()[-1])
    encoder = Encoder("dashboard")
    anchor = encoder.prepare(anchor_event, wait=False)
    anchor_line = encode_line(anchor)
    encoder.commit(anchor, len(anchor_line))
    next_event = copy.deepcopy(anchor_event)
    next_event["seq"] += 1
    next_event["time"] += 1
    next_event["data"]["record"] = _dashboard_record(1, amount=9)
    delta = encoder.prepare(next_event, wait=True)
    assert delta.is_delta
    corrupt = copy.deepcopy(delta.wire)
    corrupt[MARKER]["anchor_tick"] = True
    with path.open("ab") as stream:
        stream.write(encode_line(type(delta)(corrupt, delta.source, delta.anchor_candidate,
                                               delta.scope, delta.is_delta)))

    monitor.poll()
    after = monitor.snapshot()
    assert after["source"]["reconstruction_status"] == "gap"
    assert after["view"]["gap"] is True
    assert after["view"]["status"] is None
    assert after["view"]["kind"] is None
    assert after["view"]["stage"] is None
    assert after["view"]["last_event_time"] is None
    assert after["view"]["state"] == {}


def test_dashboard_wait_without_source_identity_stays_full():
    row = {"schema": "jev.dashboard.v1", "run_id": "run-1", "seq": 1,
           "kind": "decision_recorded", "stage": 7,
           "data": {"record": _dashboard_record(1)}}
    row["data"]["record"].pop("code_revision")
    encoder = Encoder("dashboard")
    first = encoder.prepare(row, wait=True)
    second_row = copy.deepcopy(row)
    second_row["seq"] = 2
    second_row["data"]["record"]["tick"] = 2
    second = encoder.prepare(second_row, wait=True)
    assert not first.is_delta
    assert not second.is_delta


@pytest.mark.parametrize("value", [None, True, False, 0, 1, 1.0, -0.0, "", "çŸ­", [], {}, [True, 1, 1.0, -0.0]])
def test_small_equal_subtrees_inline_without_losing_json_types(value):
    patch = codec._patch(value, copy.deepcopy(value), ["deep", "path", 12])
    assert patch == {"k": "v", "v": value}
    assert codec.canonical(codec._apply(patch, value, value)) == codec.canonical(value)


@pytest.mark.parametrize("before,after", [(True, 1), (1, 1.0), (0.0, -0.0), ("1", 1)])
def test_minimum_cost_patch_preserves_changed_scalar_type(before, after):
    patch = codec._patch(after, before, ["value"])
    assert patch["k"] == "v"
    assert codec.canonical(codec._apply(patch, before, before)) == codec.canonical(after)


def test_mixed_patch_uses_large_references_and_small_inline_nodes():
    base = {"large": {"facts": ["recipe"] * 400}, "small": True,
            "list": [1, -0.0, {"x": 2}], "remove": "old"}
    current = copy.deepcopy(base)
    current["list"][2]["x"] = 3
    current.pop("remove")
    current["new"] = {"value": 1.0}
    patch = codec._patch(current, base, [])
    assert patch["k"] == "o" and patch["f"]["large"]["k"] == "r"
    assert patch["f"]["small"]["k"] == "v"
    assert patch["f"]["list"]["k"] == "v"
    assert codec.canonical(codec._apply(patch, base, base)) == codec.canonical(current)
    assert len(codec.canonical(patch)) < len(codec.canonical(current)) // 4
    corrupt = copy.deepcopy(patch)
    corrupt["f"]["large"]["h"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        codec._apply(corrupt, base, base)
    corrupt = copy.deepcopy(patch)
    corrupt["f"]["large"]["p"] = ["missing"]
    with pytest.raises(ValueError, match="missing anchor path"):
        codec._apply(corrupt, base, base)


def test_small_changing_fact_maps_do_not_force_alternating_full_anchors():
    encoder, decoder = Encoder("gameplay"), Decoder("gameplay")
    rows = []
    total = 0
    for tick in range(1, 8):
        row = gameplay(tick)
        row["planning_diagnostics"] = {str(i): {"enabled": True, "count": i, "tick": tick}
                                       for i in range(250)}
        prepared = encoder.prepare(row, wait=tick > 1)
        line = encode_line(prepared)
        total += len(line)
        assert decoder.decode(parse_json(line[:-1]), len(line)) == row
        assert prepared.is_delta is (tick > 1)
        encoder.commit(prepared, len(line))
        rows.append(row)
    assert total < sum(len(codec.canonical(row)) + 1 for row in rows)
    assert encoder.bytes_since_anchor <= MAX_REFERENCE_BYTES
