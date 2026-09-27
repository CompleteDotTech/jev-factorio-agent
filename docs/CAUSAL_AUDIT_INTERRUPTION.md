# Causal audit interruption boundary (#95 / #96)

A recorder failure is not a gameplay/backend failure and does not authorize another
mutation. `CausalTrace` now applies the same failed-instance boundary to ordinary
exceptions and interruptions (`KeyboardInterrupt`, `SystemExit`, `GeneratorExit`)
during result capture, payload assembly, normalization, or sink emission.

## State and exception contract

On an interrupted recording boundary, the original interruption instance propagates
unchanged. The trace becomes permanently failed before unwinding; no later step,
observation, capture, action, or event can reuse it. A sink might have written none,
some, or all of the event, so its apparent later availability is not recovery proof.
The normal controller checkpoint remains at the last durable prepared/returned state.
There is no retry, replay, new identity, receipt rewrite, lock release, or failure-budget
reset. Restart still requires the existing authorized reconciliation of persisted state.

A malformed result-capture return fails during guarded capture/assembly, producing a
content-free `ResearchLogError`. It must not escape as a `TypeError` that a controller
could mistake for an ambiguous backend return. The typed callback contract remains a
mapping unpackable into the event payload; no permissive conversion of a list, string,
number, or `None` is introduced.

When recording an *existing* operation/step exception itself fails, that secondary
recording failure is suppressed solely to preserve the original exception. The trace
is poisoned even if error classification/capture fails before reaching the sink. An interruption raised by the actual backend or provider remains
the original operation failure; if its error event is successfully recorded, that alone
does not poison the recorder. Existing controller reconciliation rules govern its effects.

## Measurement and durability

Operation wall/process-CPU timing is unchanged and excludes capture and sink work.
Payload assembly is now inside the capture span, including its failure count. Capture,
emit and operation durations are nested/inclusive where indicated; do not sum them as
independent elapsed-time phases. Disabled tracing does not execute the capture callback.
Successful payload redaction/detachment, event ordering and return values are unchanged.

The patch changes no checkpoint or event schema, queue, journal, receipt, fsync barrier,
native adapter, deployment default, or campaign cutoff. It does not implement distributed
ownership, signal masking, or an atomic cross-process transaction.

## Validation and evidence boundary

Run the added interruption cases and existing causal/research tests, then the full
applicable Linux suite. Reproduce the new failure cases on the exact unmodified base
before applying the correction. The tests use deterministic backend/provider/sink doubles
and real temporary checkpoint files. They cover failure before/after a custom sink append,
prepared/returned/verification boundaries, post-action capture failure, flat and hierarchical
controllers, unchanged checkpoint bytes, no subsequent backend calls, primary exception
identity, malformed captures, fixed failure counters, and success/disabled controls.

These are source/audit durability regressions, not Factorio-engine crash tests, native
performance results, independent review, or #92 acceptance. Required signed publication,
exact-head hosted checks, normal review/merge, and native paid-flow/useful-progress
validation remain separate gates. A future rollback uses a reviewed source revert; it
must not overwrite a live checkpoint or erase an unresolved pending action.
