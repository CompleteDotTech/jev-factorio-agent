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
Funding and selected-step fields already exist in controller checkpoints. A new
optional diagnostic `solid_funding_catalogs` map declares catalog and admission
digests before acquisition. It never authorizes an action. Legacy checkpoints
load with an empty map; a measurement window lacking the initial declaration
cannot prove a new acquisition. No native command or persistence barrier is added.

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

Plan commits must be new occurrences in the bounded history, with the exact
controller event fields, a recognized selection source, and the funding event
before the plan event. Identical plan values at one game tick remain possible:
history overlap tracks occurrences rather than rejecting equal values globally.
Initial history cannot preload a future plan event. Paid component growth must
match the one component and receipt of a recorded build attempt, including a
carried pending attempt after a lost reply; observation alone cannot pay a route.

An abandonment reason also needs independent support: an elapsed deadline,
observed changed binding, exhausted action budget with missing kit stock, or an
already exhausted failure budget/causally observable failed transition. A newly
written budget or `plan_failed` label does not prove its own cause. Catalog/payback recomputation and an
intermediate changed binding may be absent from retained snapshots; those
releases fail measurement with `solid_funding_abandonment_trigger_unproven`.
This is an evidence limitation, not a controller rejection or a reason to retry
or alter the campaign. Preserve that evidence for richer native qualification.

Initial funding events pass the same shape, identity and proof validation as
record transitions and cannot postdate the initial checkpoint. Short histories
must retain their preceding entries; only a full eight-event record can explain
loss through ring truncation. Abandonment counts must equal the observer's
`max(2, prior)` or its causally supported single plan-failure increment. Retained
budget changes without such evidence fail measurement. A maxed action budget
with an incomplete carried kit requires release at the first eligible observation
without an active kit plan; a complete carried kit can still be commissioned.

The ordered initial funding suffix must also end at the checkpoint's ownership.
Its first proof can anchor a truncated earlier project, but later increments,
releases and project switches must reconcile. An observation-only commit retains
its active plan; another commit needs a causal clear first. A matching fresh
failed-plan event can account for the first exact kit budget increment from zero
to one while retaining funding. It cannot justify an exhausted-budget release.
Deadline deferral requires one unseen current-process attempt, matching the
selected plan, action, dispatch phase and observation interval.

New commitments capture the bounded kit/red-green recipe closure, static
technology definitions and reservations used in acquisition. These definitions
are independent of researched technologies and the current research selection.
Replaying the kit bill with fresh research facts must reproduce the existing
funding catalog binding; replaying acquisition against the initial observation
must produce the exact captured step. This prevents a self-consistent step hash
for unrelated crafting from proving kit acquisition. Paid components likewise
require index zero and the exact build-step hash, including the stable receipt.
An unsatisfied effect alone does not prove a failed admission: retained first
failures and ordinary second-failure abandonment need an observable failed step
precondition. Uncaptured admission/reservation causes remain unmeasurable.
Action-budget release waits until the active kit plan has cleared. Final
checkpoint history must match the last record's suffix and replay to its funding
state; invented, omitted or contradictory final transitions fail measurement.

Acquisition replay uses the preceding durable reservation owners, observed paid
commitments and failure counters for the entire simulated kit, not just its first
step. Compact supported-research and relevant unlock/recipe definitions support demand,
payback and global queue/building admission checks. Both catalog and admission
facts must match the starting checkpoint's declarations, with runtime base-version
agreement when that evidence is present. A changed or undeclared catalog needs a
new independently captured measurement baseline; changing event hashes cannot
declare its own authority. These are consistency anchors, not log authentication.
Research progress uses the same immutable declaration. The final checkpoint must
retain every initial declaration exactly, including its original observation tick.
New declarations in a final checkpoint are unproven: retained observations do not
provide independently anchored catalog definitions from which to derive them.
Such a window fails measurement and needs a separately captured baseline after
the new project is observed; copying or inventing digests cannot grant authority.
Initial and final history suffixes retain exhausted project identities across
abandonment. The checkpoint kit budget must be at least two, and a later commit
for that abandoned identity is forbidden even when its action count restarts at one.
New verified service outcomes require a matching fresh dispatch or completion of
the exact retained pending attempt before contributing to admission payback.
Kit attempts bind the captured step's receipt and observed endpoint, including
explicit null fields for crafting. Decision source and model-call state must be
possible under the recorded deterministic, hybrid or strict Jev policy.
Verified observation labels cannot clear an active kit. Only its matching
existing-effect verification, dispatched attempt or proven failure can clear it,
and final checkpoint activity must agree with the tracked plan.
Ordinary plans also block new kit selection until a causal completion or failure.
Policy-enabled ordinary `plan_committed` events retain detached plan definitions
for this replay; those definitions are removed from model-facing history. Every
fresh ordinary commit must match the same record's complete selection decision,
policy, model-call flag, plan identity, source and initial observation tick.
Mock selections require mock-world observations. An initial catalog declaration
must predate the matching funding start, including retained historical proofs.
No new kit may be committed while an ordinary capital investment is active,
even if the funding is released within the same record.
Ordinary progress skips a prefix already satisfied in the before-observation,
then binds the selected step to its exact dispatch/completion. Pending completion
retains its original index; an observation barrier cannot advance progress. Final
ordinary step indexes must match replay. Clearing a failed ordinary plan requires
one fully shaped failure event and the exact prior-plus-one failure count.
This ordinary-plan replay applies only to policy-enabled evidence. Policy-off
paid construction does not emit funding plan definitions and remains measurable
without them. Every new outcome inserted into the policy's bounded payback
history needs owned dispatch/completion evidence, including nonservice outcomes:
even a craft, expired wait or rejected connection can evict older service samples.
Outcomes whose completion cause cannot be reconciled make the window unmeasurable.

Detailed funding audit proofs stay in checkpoint/gameplay history. Model-facing
history contains only compact event identity, tick and reason; unrelated research
definitions are excluded from captured admission evidence. Catalog capture and
serialization overhead still require native measurement.

New commits check receive/send/belt project budgets as well as the kit budget.
Ordinary acquisition failure counts also matter. New commit events capture a
detached selected `step`, allowing its exact recipe/role counter to be checked.
Every new commit must include that step; omitting it fails measurement. Older
events already retained in the initial checkpoint do not prove a new commitment.
The controller's exact budget scope is unchanged.

Each new commit also binds to the complete recorded decision and selection
source. Craft/extract records need a uniquely new, schema-valid pending or
completed attempt for that kit plan, action, process and observation interval,
including its dispatch phase; a verified action cannot omit its outcome.
Observation-only commits are unverified, while an already-satisfied `verify`
record remains valid. If one record releases a project and funds another, paid
growth is checked for every distinct funding identity, not just the old lock.

For a new `verify`-only commit, the captured step must be unsatisfied in `state`
and satisfied in `after_state`; a boolean verified flag cannot establish the
effect. New craft/extract attempts also bind their step hash and observed effect
to that captured step. Older verify-only events lacking it are unprovable.
Fresh build attempts must belong to the current process, have unseen IDs and
start within the current observation boundary. Explicit carried pending builds
remain tied to their original owner across verification or process resumption.

Deferred cleanup retains the observer's original deadline/layout reason instead
of replacing it with `kit_failure_budget`. The cause is detached, process-local
diagnostic state, matched to the current funding proof and tick; a fresh observer
reconstructs it after restart. That deferred cause adds no checkpoint field or
durability barrier; the catalog declarations above are separate audit metadata.

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

Every retained record outcome suffix and the final bounded outcome ring must
match replay exactly, including order and payload. Fresh ordinary dispatches
require observed preconditions, carried stock and durable solid/coal reservation
coverage before they can supply payback samples or defer a funding deadline.
Deadline deferrals also require an observed active plan and exact current step;
a retained plan can dispatch without making a new model decision. Pending
completions retain their original admission boundary. Catalog-dependent capacity
admission and capital-held admission without a complete captured before-boundary
remain unproven rather than receiving forecast or missing-catalog credit. Retained
background jobs replay their native receipt, monotonic progress and exact attempt
identity; independent foreground work uses CraftJob.permits and keeps output
locks. New background admission binds the owned craft step, paid inputs, native
receipt and returned attempt. Explicit preflight rejection and retained wait
closure require matching terminal outcomes and causal events; they do not count
as successful service. New kit commitments are forbidden while a background job
is active, including verify-only commits. Deferred release diagnostics retain the first cause
for the exact funding proof until cleanup; this does not turn an uncaptured
demand/catalog change into a measurable audit trigger.
