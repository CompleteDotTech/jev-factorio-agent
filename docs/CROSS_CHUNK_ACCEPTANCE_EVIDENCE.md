# Cross-chunk acceptance evidence (offline design)

The existing `campaign_report.analyze` binds `process_id` across its input, and
`integration_evidence.analyze_rows` binds process/execution/run/segment identity.
Neither accepts a restart by concatenating logs. `ProgressMonitor` is explicitly
process-local. `complete_capture` and `native_acceptance` validate one trial;
the latter rejects mixed invocations. None of these reports proves a continuous
multi-process campaign.

## Input and boundaries

`cross_chunk_acceptance.analyze(manifest_path)` reads a private JSON manifest
with schema `jev-factorio.cross-chunk-evidence.v1`. It names one exact immutable
gameplay file and byte span per ordered chunk. Paths are relative to the
manifest directory, regular files only. Each span has start/end offsets and a
SHA-256; it must begin/end at a complete JSONL record boundary. Every chunk
in a new gameplay file must start at byte zero, and all ordered spans for that
file must cover through EOF without gaps or overlap. Symlinked path components
are rejected. Every chunk
also binds exact pre/post checkpoint bytes and an exact original attachment
receipt hash. Adjacent checkpoint hashes must match, and spans in a reused log
must be contiguous; duplicated or missing bytes are rejected. The stable
session, save, source commit/tree, treatment, witness, actor, receipt, policy,
model, and controller identities are supplied once and checked against every
available gameplay row/checkpoint. A source or treatment migration requires a
new manifest/window with a separately reviewed transition; it is not silently
spliced into the 1800-second window.

The exact manifest shape is:

```json
{
  "schema": "jev-factorio.cross-chunk-evidence.v1",
  "identity": {
    "session_id": "opaque-session", "save_sha256": "64 lowercase hex",
    "source_commit": "40 or 64 lowercase hex", "source_tree": "40 or 64 lowercase hex",
    "treatment_sha256": "64 lowercase hex", "witness_sha256": "64 lowercase hex",
    "attachment_receipt_sha256": "64 lowercase hex", "actor_unit": 1,
    "policy": "jev", "requested_model": "model", "controller": "hierarchical"
  },
  "max_observation_gap_seconds": 60,
  "max_science_stall_seconds": 600,
  "chunks": [{
    "gameplay": "chunk-1.jsonl", "start_byte": 0, "end_byte": 123,
    "span_sha256": "64 lowercase hex",
    "before_checkpoint": "before.json", "before_sha256": "64 lowercase hex",
    "after_checkpoint": "after.json", "after_sha256": "64 lowercase hex",
    "owner_result": "result.json", "owner_result_sha256": "64 lowercase hex",
    "owner_status": "verified"
  }]
}
```

The implementation requires two or more chunks, so it cannot accidentally
replace the existing single-invocation reports. The owner result must contain
`status=verified`, but the generic result shape does not prove the native paid
ledger. `save_sha256`, source tree, treatment, witness and attachment receipt
hashes remain manifest-level declarations until a separate owner-receipt
qualification pass reads their exact private bodies. This is an intentional
reason the output never says native acceptance passed.

Each chunk carries a content-free owner outcome (`verified`, never ambiguous,
blocked, timed-out or stalled), immutable owner result hash, and an explicit
pre/post checkpoint pair. These owner attestations are *inputs*, not a
substitute for inspecting their private receipt bodies. A final acceptance
review must establish those receipts and native ledger bindings before using
this report. The analyzer rejects missing, malformed, or overlapping evidence.
The normalized owner result has exactly `status`, `identity`, `before_sha256`,
`after_sha256`, and `span_sha256`; each must agree with the manifest. Producing
this normalized file from the actual one-use receipts needs a separately
reviewed qualification step, and it is not a self-authorizing receipt.

## Output and interpretation

The output is a bounded report with exact input/span hashes, row count,
checkpoint chain, progress-anchor interval, maximum observation gap and
science-progress stall, native tick delta, lab receipt delivery, force science
consumption and research progress/completion. Progress anchors require a
positive science-consumption counter delta **and** research advancement. The
useful interval runs from the first to last such anchor; it cannot borrow time
before the first or after the last anchor. Each adjacent observation gap and
each gap between progress anchors must stay below predeclared limits. No
overlapping interval or duplicated row earns time twice. At least 1800 useful
seconds are required.

The analyzer never promotes transport by finding coal or route-shaped objects
in a summary. For #100-#102, a separate source-bound native evidence roll-up
must prove distinct paid fuel consumers, mined coal delivery and burn lower
bounds, a paid downstream ingredient route, subsequent target production,
science lab delivery/consumption, and reduced actor hauling. The existing
`integration_evidence` route/receipt validator supplies the single-invocation
reference semantics; a cross-invocation native roll-up and exact owner receipt
qualification remain required. Until then output always has
`native_acceptance_proven=false` and names these evidence gaps. It is never
deployment or gameplay authority.

Tests must reject a changed span hash, partial line, same-file byte gap or
overlap, checkpoint or identity drift, ambiguous owner result, missing or
regressed native counters, 1800 seconds made only of idle time, excessive
observation/science stall, and one-consumer/unpaid/unattributed route claims.
Positive fixtures may prove only cross-chunk continuity and science measurement;
they must not assert coal/downstream acceptance from synthetic stock.
