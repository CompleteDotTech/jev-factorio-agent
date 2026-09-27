# Bounded kit funding evidence (#92, #102, #103)

The optional paid-kit controller checkpoints a `solid_funding` lock. Before this
change, its gameplay records omitted that lock and the read-only integration
analyzer did not reconcile it. Consequently, a fabricated record could erase or
invent funding across otherwise consistent route/science observations and still
pass measurement checks. This was reproduced on the retained kit integration.
It did not cause the analyzer to grant native acceptance: that gate stays closed.

## Record and transition contract

Solid-controller records now contain `solid_funding_schema: 1` and a detached
`solid_funding` copy (including explicit null). The existing bounded history ring
retains a detached, schema-validated funding proof on each `solid_kit_committed`,
`solid_kit_abandoned`, and `solid_kit_paid_handoff` event. Each subsequent kit plan
commit records its incremented action count, rather than silently advancing it.
All fields are already part of the funding checkpoint contract; no additional
checkpoint schema, native command, actor mutation or persistence barrier is added.

The read-only `solid_funding_evidence.funding_history_issues` helper checks initial
and final checkpoints, every recorded state, stable physical/project bindings,
exact action progression, event times, retained failure budgets, and observable
paid handoff. A new commit must have its exact event proof; exhausted kit budgets
cannot be reset by replaying a project. Release requires either an explicitly
budgeted abandonment or the exact observed paid route prefix. Existing global
analyzer checks still validate native receipts and full controller commitments.

The helper processes new event values only once, since rolling histories repeat
old events. Initial checkpoint history establishes the pre-window event set.
A commit and abandonment in the same record are valid with both proofs. During
first payment, pending receipt verification can finish after the observer defers
funding reconciliation: the funding lock may therefore remain held alongside a
paid route until the next observation. Payment alone never implicitly releases
it. Missing telemetry yields an integrity failure, not an inferred transition.
An already-exhausted failure budget also needs a release proof. Abandonment is
logged at the actual clear (observer or plan clear), not prematurely while a
funded active plan still retains its lock. No new save or mutation is introduced.

Legacy no-funding records remain compatible with the optional policy disabled.
A policy-enabled measurement needs the explicit versioned contract; old records
cannot be retroactively upgraded by assuming missing locks were null. All issues
are fixed labels; no arbitrary exception, endpoint, path or source record is
copied into a public report. Inputs are not mutated.

Both endpoint checkpoints must explicitly contain `solid_funding` when the
policy is enabled; an omitted field is not equivalent to a recorded null. Each
record may introduce at most one new kit-plan commit, matching one controller
step. Repeated entries from the retained history ring do not consume that slot.
An abandonment establishes an exhausted project budget for later transitions
in that record, so a same-record recommit cannot reuse the preceding record's
unexhausted count. A valid commit followed by abandonment remains supported.

Each new commit binds to the exact proposed route and tick in the record's
pre-action observation. A new lock uses the controller's fixed funding horizon;
increments cannot commit after that deadline. Retained locks must have a current
kit failure count below two. A kit acquisition and paid construction cannot be
collapsed into a commit followed by a paid handoff in one record.

Paid ownership visible at a step's initial observation requires handoff when no
controller action was pending on entry. The preceding record (or initial
checkpoint) supplies that pending state. A lost reply may defer release while
verification resolves it; the next eligible observation must release the lock.
Enabled-policy evidence must explicitly retain that pending field; omission is
not proof that the actor was idle. A pending-verification step cannot introduce
a new kit-plan commit.
Expiry follows the same pending-reconciliation boundary. Neither missing handoff
events nor overdue funding may be carried across an otherwise eligible window.

An existing lock also binds to the pre-action observation. An absent or rebound
proposal requires abandonment at that observation; restoring it in the later
snapshot cannot hide the required release. Abandonment times cannot precede the
record's initial observation, but may reflect an intermediate fresh observation.
New kit commits require the matching `plan_committed` event and an acquisition
action, an observation-only rejection, or a verified already-satisfied plan.
Crossing a deadline without incoming pending work requires current dispatch
evidence: a matching pending action or a verified attempt spanning the deadline.
This applies to ordinary production as well as kit work. An action label or an
old outcome repeated in history alone cannot defer reconciliation.

## Evidence boundary and rollout

Tests exercise fabricated retained evidence and the actual Python controller with
paid-action fixtures. They cover a complete bounded kit acquisition and paid
handoff, fresh-precondition abandonment, replay/reset/identity/timing corruption,
missing schema/proof/budget, immutable output copies, and malformed bounded
history. They are not Factorio-engine, provider, flow or latency measurements.

The analyzer still always emits `native_acceptance: not_accepted`, does not
start/resume/deploy a game, and cannot establish mining provenance, external log
authenticity, host capacity, or the original authorized 30-minute progress window.
Run that acceptance only through the established native service-owner handoff.
Do not modify a campaign/cutoff or erase a pending receipt to make logs pass.

For source rollback, omit this unpublished unit or use a reviewed revert after a
future merge. Retain checkpoint ownership; do not manufacture legacy logs by
stripping the new fields. Log enrichment has bounded nonzero serialization cost
and must be included in eventual native CPU/wall/byte/write attribution.
