# Native construction journals require their retained controller owner

Refs #100, #101, #102, #92 and #103. This is a source-level recovery correction,
not evidence of Factorio-engine operation or native acceptance.

## Reproduction and boundary

On merge `fa8c29ef1075af976ee529777b85cf21047a489d` (#128), both the solid-route
and coal-source planners copied a native `prepared` journal's receipt into a
freshly offered component plan. After a paid prefix was already tracked, the
controller observers did not bind a journal on that row to an existing durable
pending operation. A journal could therefore be adopted into a new attempt,
including on resume or when it first appeared at fresh preconditions before
the controller had prepared its own pending action.

The original 14-case reproduction produced 12 failures and two legitimate-replay
controls. The final 23-case regression file produces 18 failures and five controls
on unchanged #128. These are controller/backend doubles and a real extension Lua
execution with Factorio API doubles, not an observation of a live campaign.

## Corrected contract

Every observed coal-source or solid-route journal must match the controller's
retained pending operation before commitment reconciliation continues. The owner
binding includes action, target or route, immutable layout, next part, exact
receipt, active plan ID and step index, started tick and attempt step fingerprint.
An identical proposed plan without a prepared pending record is not an owner.
A second mixed-family journal cannot share the actor's one pending attempt.

An unowned or mismatched journal makes the existing controller fault sticky and
keeps the actor closed. Pending identities, attempts, failure history, native
journals and paid construction are not cleared, replaced or replayed. A later
observation in which the journal disappears does not reopen the controller.
No native journal is used to manufacture a new candidate or failure-budget key.

The investment policy's exact-component validation is separate from fresh-plan
admission. It can describe the retained operation with its original receipt as
ticks advance. It cannot establish journal ownership or start an attempt. Only
the existing controller verifier authorizes its bounded one-time prepared replay;
ambiguous dispatch and receipt verification keep their original guards.

The spatial reservation tests explicitly distinguish valid disjoint geometry
from permission to start another action while the actor has a pending journal.
The unchanged geometry becomes eligible again only after modeled reconciliation.

## Durability and integration

This correction changes no checkpoint schema, field, serialization policy,
write boundary, fsync behavior, native Lua implementation, production CLI flag or
supervisor configuration. The check runs inside the existing composed initial-
resume publication barrier. It does not invent a state migration, ignore a
changed checkpoint or authorize a campaign restart.

It preserves the separate #95 checkpoint installation/cleanup work and #129
funding-evidence changes. Reconcile overlapping controller/planning files onto
current main, obtain independent review of the exact resulting source, sign with
the existing authorized SSH identity, run the normal final-head hosted checks,
and use the normal PR merge path. An unsigned source packet is not delivery.

## Validation

With the repository's declared dependencies installed, run:

```sh
python -m pytest tests/test_construction_pending_ownership.py \
  tests/test_solid_investment.py tests/test_solid_corridor_reservations.py -q
python -m pytest tests/ -q --ignore=tests/test_dashboard_browser.py
python -m compileall -q src
```

The dedicated browser gate and optional engine/export dependencies remain
separate. On the initial #128-based candidate, the focused set has 122 passes
and the selected full Linux/Python 3.13.5 suite has 4,298 passes and 77 skips,
versus 4,275 passes and 77 skips on unchanged #128. These totals are not latency,
mining, flow, science, research, actual provider or native recovery measurements.
A later rebased candidate must retain its own exact-source results.

## Recovery, deployment and rollback

Preserve the original campaign, native save, checkpoint, journal, receipts,
ownership and failure history when the guard stops a controller. The established
service owner must reconcile the exact prepared/pending pair; do not clear the
journal, synthesize a receipt, reset the failure budget or retry the old source
simply to bypass the stop. This document grants no operational authority.

Before any authorized deployment, verify its exact source/configuration and
remaining original supervisor window. Use isolated native recovery qualification
first and retain #92's full paid-flow, useful-progress and matched-measurement
gates. No shorter fixture or source merge substitutes for the required native
window. If rollback is necessary, stop through the established service process,
preserve both sides of the durable state, and have the owner validate a compatible
prior release and pending-state disposition before another authoritative action.
