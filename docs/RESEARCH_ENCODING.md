# V1 research encoding reuse — issue #95

## What changes and what does not

The writer previously serialized the complete event payload for its unsigned
hash input, validated that full event, then serialized the payload again for the
final signed-by-hash record. It now validates the complete detached envelope once,
encodes each top-level value once, and reuses those immutable value bytes in the
unsigned hash input and final canonical event. Only the small event-hash field is
added between the two assemblies. More small metadata encodes occur; the large
payload encode count is one instead of two. This is not a claim that the whole
writer makes only one JSON encoder call.

Canonical V1 bytes, ASCII escaping, sort order, finite-number rejection, line
termination, SHA-256 chain inputs, size/schema checks, redaction, sequencing and
correlation fields are unchanged. The reader/verifier deliberately retains its
independent complete-event encoder. No serialization optimization is trusted as
proof of its own hash format.

No queue, debouncing or asynchronous writer is introduced. The process ownership
check and lock still serialize the complete append. Short writes, flush errors
and fsync failures poison the writer; sequence, monotonic watermark and previous
hash advance only after successful durable append. Returned event dictionaries
and caller payloads cannot modify already written bytes or chain state.

## Persistence inventory and barriers

| State/evidence | Authority and boundary retained |
| --- | --- |
| Prepared/pending action and stable receipt identities | The existing checkpoint precedes the native mutation. No timer deferral. |
| Paid components, ownership, reservations, failure histories and route commitments | Existing schema validation and synchronous checkpoint replacement; old pending source must reconcile before source upgrades. |
| Checkpoint `last_tick`, active plan, steps, observation-dependent state | Still part of exact typed comparison; any changed field captures/serializes and durably writes. No removal of ticks. |
| Exact repeated checkpoint | Already-merged detached capture cache reuses only successfully synced bytes; external replacement/deletion invalidates. |
| Research manifest and events | Exclusive run creation, ordered canonical hash chain, file fsync on every append; directory fsync on creation/sealing where supported. |
| Research final seal | Final event and separate integrity document follow the existing durable sequence. |
| Legacy gameplay/UI logs and profiler data | Remain non-authoritative diagnostics. They cannot replace checkpoint or research write barriers. |

This extension does not eliminate all trace/model/legacy-record copies and does
not assign the whole observation-to-record residual to logging. Those measurements
must be kept separately attributed. The unchanged checkpoint implementation still
owns exact typed equality, successful-sync bookkeeping and external file stamps.

## Executable validation

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_research_encoding.py tests/test_research_log.py \
  tests/test_causal_trace.py tests/test_checkpoint_io.py
PYTHONPATH=src python benchmarks/benchmark_research_encoding.py --samples 50
```

Tests compare independent canonical bytes/hashes over nested randomized payloads,
Unicode and surrogate escaping, signed zero, booleans versus numbers, wrong keys,
nonfinite values, size limits, detached return values and failed write/flush/sync.
Existing corruption, chain, replay and checkpoint failure coverage remains required.

The benchmark executes actual synchronous writers on a temporary local filesystem
with deterministic clocks/identities solely inside that offline fixture. Both arms
produce identical manifest/event/seal bytes and retain the same sync barriers. It
reports payload serialization counts, file/directory sync counts, bytes and CPU/wall
distributions. It is not a Factorio/native filesystem latency or throughput claim.

## Remaining delivery/native evidence

Independent review, configured SSH signing, final-head hosted checks and normal
merge are required. Qualify crash/restart at preparation, dispatch, receipt, partial
route and checkpoint replacement boundaries in an isolated native environment.
Then compare source-bound checkpoint/research costs and useful output over matched
workloads in #92. No production process, campaign identity/treatment/cutoff or shared
host is changed by this writer optimization. Native acceptance remains open.
