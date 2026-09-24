# Phase 2: bounded service and local retrofit discovery

Baseline: `b47481a742e21d47f45e4b782453b3eb2d20f47b` (PR #63).
This phase improves servicing and discovery around the existing factory. It does
not replace canonical producers, commission a successor factory, enable outposts,
reset failure history, change game speed, or deploy to the running campaign.

## Paid, deadline-aware service visits

`planning/service_visits.py` continues to use the same transfer commands, native
receipts, write-ahead dispatcher, fresh preconditions, and per-step verification.
It retains the selected first transfer exactly, including a one-item critical
research tail. The default remains at most three transfers; the existing explicit
maximum of four is retained. Background work and native crafting remain single
step, and capital acquisition does not become a service visit.

The new `planning/service_policy.py` limits optional appended work to 900 estimated
actor ticks and eight Manhattan tiles per leg. These are declared heuristics, not
measured native travel times or safety guarantees. Unknown geometry fails closed
to the original transfer. Optional furnace work must fit before the earliest known
research-refill deadline; missing active-research timing also prevents extensions.
An observed low-fuel emergency in another active burner/boiler prevents extensions.
A bounded emergency scan handles at most 64 entity roles; a larger factory falls
back to the original action rather than assuming uninspected roles are healthy.

All optional deliveries are limited to the intersection of actual carried stock
and the reservation-aware supply ledger. Nothing picked up during the visit, or
acknowledged by an unfinished craft job, becomes available to fund later steps.
Observed native stack room remains a delivery bound. Normal dispatch still checks
actual room, ownership, fuel/route restrictions and native receipts.

A lab visit can now deliver additional currently needed science packs already
carried, up to the normal 20-pack planning/stack limits. The first selected delivery
is unchanged. This path works even when no missing-material focus was created.
There is no new handcrafting, research cancellation, travel to another cell, or
minimum batch requirement. Furnace/output-buffer visits retain their existing
same-cell membership and cannot hand-feed a protected automated ore route.

Committed visit materials expose `service_visit`: sampled tick, participating
native units, first/extra duration estimates, declared limits, deadline and rejected
extension reasons. A fully rejected extension returns the original plan unchanged;
it is not presented as successful service or a measured travel saving.

## Bounded local retrofit discovery

`lua/input_routes.lua` still surveys within **40 tiles**, still limits each route
to **64 belts**, and still requires paid parts, science reserves, a commissioned
output buffer, pure observed ore, clear placement, native topology and repeated
material conservation/flow evidence. `existing_manual_cell` remains an ownership
guard in the separate production-site planner; this phase does not remove it.

Previously each unsuccessful due survey reconsidered the same nearest eight of
up to 128 returned resource entities. The survey now rotates through that bounded
returned set, at most eight per due survey. The cursor is bound to the source unit
and output-layout identity; it is advisory coverage state, not an action retry or
failure-budget reset. Sorting is deterministic, and nearer receiver geometries are
tried first. Existing offers retain their identities; committed/paid routes never
get replaced by the rotating survey.

There is no guarantee of exhaustive surface or even complete-radius coverage:
`find_entities_filtered(limit=128)` returns a bounded subset. Diagnostics explicitly
flag reaching this result limit. Dense purity samples reaching 65 entities reject
rather than silently treating a truncated sample as proof of purity.

Hard per-survey caps:

| Work | Cap |
| --- | --- |
| Returned resource entities | 128 |
| Examined resource candidates | 8 |
| Candidate receiver enumeration | 7 x 7 x 4, then geometry filtering |
| Path attempts | 128 |
| Total dequeued path nodes | 16,384 |
| Uncached belt placement probes | 4,096 |
| Receiver + drill + belt placement checks | At most 4,324 |

The minimum route length can reject a path before search. Zero local ore skips
all receiver, drill and path probes. Unsuccessful surveys retain the existing
300-tick cooldown. During that cooldown observations report the last survey's
**original** tick and a cache marker without repeating the extra radius query
merely to regenerate the same rejection text. Changed producer/output identities
are reported as stale evidence, never silently credited with an old proposal.

Rejection categories include missing producer, uncommissioned output, committed
outpost conflict, reserved-layout unavailability, missing survey API, invalid
survey evidence, no ore in the radius, obstructed receiver, depleted/mixed sample,
purity sample limit, route length, path-search budget, placement-query budget and
no clear route within the remaining budget. An engine exception is reported as a
typed invalid-survey reason, not copied into diagnostic output. Reasons and work
counts are retained in `factory.input_routes.diagnostics` and therefore reach the
existing input-route planner context. A cached counter describes that sampled
survey: do not sum it once per later observation as new work.

## What this does not solve

Distant ore around 119–127 tiles from legacy furnaces remains outside a 40-tile
retrofit survey. Rotating a bounded local sample does not solve that geometry,
long-distance logistics, or occupied canonical producer identity. The next larger
capability is a separately owned ore-side successor, commissioned and verified
before demand routing changes, as specified in `PLANNING_AUTONOMY.md`.

Outposts remain an already available opt-in drill-to-chest capability with manual
hauling. No deployment flag or checkpoint was changed here. Richer versioned
capture export, native development-VM trials and condition-scoped recovery remain
separate work; this patch does not claim to verify the earlier private gzip bundle.

## Validation and acceptance

```sh
PYTHONPATH=src python -m pytest tests/test_phase2_automation.py tests/test_demand_service.py tests/test_input_routes_lua.py -q
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m compileall -q src
```

The tests exercise the real planner/controller and Lua adapter under synthetic
fixtures. Expected deterministic gains are a single committed visit for two
carried science deliveries, discovery of a valid ninth resource on the next due
survey rather than repeated rejection of the first eight, and one radius scan
instead of repeated diagnostic scans during cooldown. They are not native-game
throughput or model-cost measurements.

Before deployment, run matched isolated development copies of the **existing save
and matching checkpoint**, keeping source, policy/model, inventory, research,
ownership, pending actions and complete failure history identified. Preserve the
live campaign and its single writer; do not rewind it or copy a test world back.
Run at least three paired baseline/treatment trials plus the two-hour treatment
soak specified in `PLANNING_AUTONOMY.md`.

Native gates: no regression in useful downstream production or time to the next
verified science/rocket milestone; fewer transfer/model decisions per served lab
batch where multiple packs are carried; no new missed refill deadlines; bounded
survey counters and stable paid-route identity; zero repeated paid actions or
lost assets after ambiguous acknowledgments and restarts. Retain actual travel,
manual mining, transfer quantities, idle categories, model tokens and per-call
latency. Missing metrics cannot establish an improvement or pass cutover.

Required fault cases include unrelated inventory changes, exhausted failure
budgets, source replacement, partial builds, full inventory, mixed/depleted ore,
new obstacles, power/fuel loss, output backpressure, foreign connections and
ambiguous RPC completion. Reuse the existing per-step reconciliation tests and
native continuity checks. Source merge alone does not authorize production cutover.
