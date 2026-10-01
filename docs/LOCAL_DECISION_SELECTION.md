# Local production decisions

With `--factory-scheduling ready-work`, decisions now carry a structured local
production objective and a per-candidate evidence table. The ultimate milestone
and its native completion predicate remain unchanged. A bounded delivery can
receive high local benefit without purporting to contain an entire rocket plan.
The default serial scheduler keeps its existing active-goal rubric.

## Shared evidence for Jev and the deterministic baseline

The current snapshot and native recipe catalog supply machine state, material
costs, item quantities, low-fuel evidence, research refill deadlines and the
ready-work batch target. Travel uses Euclidean distance as a **lower bound**, not
a path. Actor duration reuses the explicitly declared travel, mining and service
heuristics in `planning/scheduling.py`; crafting time comes from the catalog and
assumes ordinary handcrafting. These are estimates, not measured native timings,
model confidence, throughput forecasts or promises of success. Unknown geometry
or duration remains `null` with an explicit reason.

Deterministic ranking first prefers productive work over passive waits, then
observed low fuel or due science delivery, then a final supplied-machine input.
Among equally urgent options with known costs, it prefers more processed units
per estimated actor tick. Unknown costs are not silently zero. Compiler order
breaks ties. This simple unit-normalized rule is a policy heuristic, not a
calibrated comparison of the economic values of different items. Both model and
fallback see the same ranked, admitted frontier; urgent candidates are considered
before byte-budget trimming. No new action is invented by this ranking.

When the entire frontier is the single `background-wait:` candidate for a
tracked native craft (offered only when no independent ready work exists), there
is nothing to choose or to judge useful, and a model call can only abstain or
reject it. `select_plan` therefore selects it without a call
(`source: passive-wait`, `model_called: false`, diagnostics `model_skipped` and
`passive_wait`). Persistence is unchanged: the write-ahead attempt and any
one-use source authorization are recorded for that fingerprint before this
selection, exactly as for a model decision. Buffer and capital waits, and a wait
offered next to other work, are still judged by the model.

Identical executable alternatives collapse after the existing failure-budget
filter. The retained plan keeps its original identity, and different receipt
identities remain distinct. The compiler's capability, output-lock, ownership,
resource and pending-dispatch restrictions still define the candidate frontier.

## Explicit policy semantics

* `deterministic` uses the evidence-based ranking and never calls Jev.
* `hybrid` executes one distinct feasible ready-work continuation without a model
  call and records `deterministic-singleton`. Genuine alternatives use Jev; any
  fallback keeps the original answers, reason and diagnostics.
* `jev` continues to call Jev even for a singleton, preserving a strict model
  policy for comparison. Serial policy behavior is not silently changed.

Already-committed steps and verification do not requery the model. Every selected
step still undergoes a fresh observation, native precondition check, reservation,
write-ahead persistence, dispatch and independently observed verification. Neither
an estimate nor elapsed time can verify a step or release an ambiguous mutation.

## Auditable failure causes

With persistent recoverable blocks enabled, each Jev selection request is
prepared before its write-ahead checkpoint entry. The entry binds the semantic
native state, source contract, prepared request's semantic content and candidates
actually offered. The provider receives that prepared payload; complete plan/evidence and
question validation prevents it from judging a different executable plan.

Completed recoverable rejections can open a batch of previously unoffered
feasible candidates. At most three batches are evaluated for the same semantic
state across observations and restarts. Exhausting all alternatives and reaching
the batch limit with unseen candidates are distinct diagnostics. A pending,
unknown, invalid or provider-blocked outcome stops advancement. An unresolved
request is never blindly replayed. Tick-only changes, planned receipt clocks and
generated craft identifiers do not create fresh eligibility; meaningful native
evidence changes or an authorized source-contract change can reopen selection.
Provider-blocked outcomes end the invocation with a durable, precise operational
block, including a circuit refusal that made no HTTP call. They do not consume
gameplay failure budgets. Recovery belongs to the existing provider/supervisor
owner; unchanged-state restart is not permission to bypass its incident budget
or replay an unresolved request.

Coverage includes the active attempt tail and authenticated archived rows.
Historical rows without batch metadata remain valid historical evidence, but do
not authorize guessing which candidates were offered. Archive bytes, hashes,
pending actions, receipt verification and the existing ownership gates remain
authoritative. The idle bound limits observation waits per invocation; a
supervisor must not blindly restart an unchanged exhausted frontier.

Decision records and canonical decision events distinguish model abstention,
low choice confidence, missing start evidence, low benefit/disruption confidence,
malformed answers, invalid provider payloads, transient provider failures, request
rejection before dispatch, and byte/count-pruned candidate IDs. Confidence floors
and answer-distribution validation are unchanged.

Decision contract schema 2 adds a separate `useful_progress` choice for each
candidate. It asks whether current action-specific evidence supports any useful
progress toward the local objective, independently of the ordinal benefit
magnitude. The existing floor applies to the answer's probability of `useful`:
a plan is eligible only when `useful` is the answer's plurality label and its
probability is at least the floor, so `unsupported` (or a `useful` label with
less mass than the floor, possible only above 0.5) rejects the plan as
`no_demonstrated_progress` or `low_usefulness_confidence`. The model's separately
reported confidence is not a veto: it is not tied to the answer's probabilities (a
live answer put 0.60 on `useful` while reporting 0.20, and was rejected on that
number alone), and it is recorded in the diagnostics for audit only, as for the
benefit gate. Missing answers,
missing start evidence, low choice/disruption confidence and contradictory
negative benefit evidence also reject it. Uncertainty between two positive
benefit levels affects ranking rather than eligibility. Positive probability
mass or expected benefit alone cannot admit a plan. `diagnostics.usefulness_gate`
records the choice, the `useful` probability, the reported confidence, the floor
and the result. The legacy `benefit_gate`
diagnostic remains descriptive with `eligibility_authority=false`; it no longer
supplies admission authority. Native preconditions, payment, capacity, exact
receipts and fresh postconditions remain authoritative at dispatch/verification.
The explicit contract version participates in the source-bound decision input;
old provider payloads lacking the new judgment fail closed.

The request is bounded by serialized bytes, not provider tokens. The default
budget is 48,000 bytes and `--max-request-bytes` (or `JEV_MAX_REQUEST_BYTES`)
sets it within 8,000 to 262,144. On the live campaign one candidate with its
context is about 26 KB and each further candidate about 10 KB, so the former
32 KB budget offered a single candidate even when an executable second plan
existed. When the budget drops candidates the controller prints the offered
and dropped counts, the request size and the dropped plan IDs, and every
decision records `request_bytes`, `max_request_bytes` and `pruned_candidate_ids`
in its diagnostics. If all offered candidates are then rejected, the decision
keeps the reason `Candidate evidence insufficient` and adds
`alternatives_not_shown`, so a readback can tell "no alternative existed" from
"alternatives were never shown".

A budget change alters which candidates are offered but not the decision fingerprint, so a
checkpoint already blocked and recorded under the old budget will not re-ask on its own;
use `--reevaluate-blocked-once` after a decision-contract change.

A failed JSON decode after a
provider call remains attributed as a model call. A skipped call cannot inherit
usage or resolved-model metadata from an earlier request.

## Validation and evaluation

`tests/test_local_decisions.py` exercises the real controller with synthetic
backends, unchanged pre-dispatch checks, canonical logging/replay, native catalog
fixtures and deterministic geometry. It compares old compiler-first and new
ranked choices in controlled collection and starvation scenarios. These tests
establish selection behavior, not native gameplay improvement or a Jev advantage.

For native acceptance, use matched initial saves, seeds and equal game/wall-time
budgets; compare strict Jev, hybrid and the improved deterministic scheduler.
Retain exact code/model/configuration identity and separate operational recovery
segments. Evaluate useful output, starvation, travel, milestone times, native
failures, calls, latency and cost. Do not use a higher Jev-acceptance rate or fewer
wait records as a substitute for better gameplay. The existing research evaluator
and paired experiment workflow remain authoritative for validated run evidence.

No live campaign, game server, viewer, save or deployment is changed by this PR.
