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

Decision records and canonical decision events distinguish model abstention,
low choice confidence, missing start evidence, low benefit/disruption confidence,
malformed answers, invalid provider payloads, transient provider failures, request
rejection before dispatch, and byte/count-pruned candidate IDs. Confidence floors
and answer-distribution validation are unchanged. A failed JSON decode after a
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
