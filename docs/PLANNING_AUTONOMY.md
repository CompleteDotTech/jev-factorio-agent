# Bounded planning and staged factory autonomy

This change is the first, low-risk phase of an autonomy review. It does **not**
implement brownfield production replacement, longer conveyor routes, automatic
failure-budget resets, a different model policy, or production deployment.
Private campaign logs are deliberately not copied into this public repository.

## Implemented in this phase

1. **Separate current work from discretionary lookahead.** Ready-work plans carry
   a snapshot-bound `work_intent`. At equal urgency, the deterministic ranking
   puts a current prerequisite ahead of discretionary stockpiling, before comparing
   estimated handling volume per occupied actor tick. Urgent fuel, due science,
   producer refill, and validated capital stages keep their existing precedence.
   Unknown/stale intent cannot claim priority. Passive waits still rank last.
   JEV receives the distinction in its context but retains its policy-defined
   selection authority. This is not a guaranteed JEV choice or an unconditional
   ban on a useful larger batch. `processed_units` explicitly means handling
   volume, **not** useful production or downstream consumption.
2. **Reuse bounded economic demand within one candidate frontier.** Capability-aware
   lookahead workers copy the parent's economic workload rather than rebuilding
   its bill of materials. The cache never crosses a compilation or observation.
   Worker mutation cannot change the parent. No observation is skipped and no
   forecast becomes spendable inventory.
3. **Stop impossible local route work early.** A zero-resource local survey returns
   before enumerating furnace receiver layouts or placement/path probes. The
   40-tile radius, 64-belt path cap, survey cooldown, identity checks, paid build
   contract, and rejection diagnostics are unchanged. A later due survey can
   discover changed physical conditions normally.
4. **Expose the controller's actual filtering boundary.** `planning_diagnostics`
   reports generated, eligible, duplicate, and ranked IDs, retained plan failure
   rejections, and candidate cost/scope evidence. Its boundary is explicitly
   `post_capability_frontier`: it does not pretend to include every earlier Lua
   survey, capability, or capital rejection. `failure_budgets` is an observational
   copy, never a reset. The outpost-enabled flag is explicit in new records.
5. **Preserve layered diagnostics.** Background recording now merges its parent
   extras, so capital-investment state is not hidden when background work runs.
6. **Audit captures offline.** See [EVIDENCE_AUDIT.md](EVIDENCE_AUDIT.md). Hash,
   projection, token-coverage, receipt-deduplication and missing-data checks do not
   initialize a backend or issue any game/model calls.

No checkpoint schema, reservation, pending-action reconciliation, native transfer,
flow certificate, goal deadline, or fair-actuation rule is relaxed. The existing
hybrid singleton shortcut remains; the explicit `jev` policy still calls JEV.
A fresh pre-dispatch observation after model evaluation remains mandatory.

## Existing protections that should not be reimplemented or bypassed

`planning/demand.py` separates carried, reserved, collectible, queued, in-flight
and acknowledged stock. `planning/productive_work.py` already offers bounded
producer refill and research preparation. `planning/service_visits.py` already
bundles compatible same-cell tasks under per-step receipts and reservations.
`background.py` uses a durable native job lifecycle; acknowledged output is not
verified output and cannot be spent early. `planning/capacity_evidence.py` already
requires sampled supplied production and output movement for extra capacity.
`capital_controller.py` already retains capital commitments and abandoned budgets.

The optional mining-outpost controller already builds a paid drill-to-chest cell
and verifies flow. It reduces hand mining but declares bounded manual hauling;
it is not an ore-to-furnace conveyor network. An omitted flag in an old export
must not be interpreted as disabled.

## Why changing a route limit is insufficient

`lua/input_routes.lua:survey` seeks ore within 40 tiles of an existing producer;
its path search separately caps routes at 64 belts, a narrow search corridor,
and a finite placement-query budget. The proposal also requires an owned,
commissioned output buffer, suitable resource purity and amount, a clear receiver,
no foreign-network joins, and no conflicting outpost commitment. Without a valid
native proposal, the Python input-route planner cannot offer construction to JEV.

`lua/production_sites.lua:observe_production_sites` offers a compact ore-side joint
layout only when the canonical recipe role is unoccupied. `existing_manual_cell`
is a deliberate identity guard, not a rejection that a prompt can overrule.
Removing it and overwriting the canonical entity would invalidate existing output
and input ownership, source-unit identities, and receipts. Increasing only the
path cap cannot discover distant ore; increasing only the survey can still leave
an unbuildable route and does not permit an additional producer role.

## Prioritized follow-on implementation

These are proposals, not capabilities delivered by this first phase. Benefit
estimates are qualitative; native improvement has not been measured here.

| Phase / priority | Change and affected modules | Expected benefit | Effort / risk |
| --- | --- | --- | --- |
| 1 / P0 | Preserve complete diagnostic exports; `controller.py`, `performance.py`, capture/export integration | Reliable cost and failure diagnosis | Small / low |
| 1 / P1 | Calibrate travel, batching and due-work ranking; `decision_support.py`, `productive_work.py`, `service_visits.py`, `scheduling.py` | Fewer stockpile detours and service trips | Medium / medium |
| 2 / P0 | Exercise existing optional outposts on the development VM with preserved checkpoint ownership; `outpost_controller.py`, `planning/mining_outposts.py` | Remove repeated manual mining where suitable | Small deployment effort / medium native risk |
| 2 / P1 | Bounded alternate-source/receiver survey with typed rejection reasons; `lua/input_routes.lua`, `input_routes.py`, `planning/input_routes.py` | Make feasible local retrofits discoverable | Medium / medium |
| 2 / P1 | Stable service routes and demand-sized collection, without borrowing future receipts; `service_visits.py`, `demand.py`, `ready_work.py` | Reduce manual hauling trips | Medium / medium |
| 3 / P0 | Additional ore-side producer identity and commissioning lifecycle; `production_sites.lua`, output/input Lua and Python contracts, planner, controller memory | Escape occupied canonical-role dead end | Large / high |
| 3 / P1 | Condition-scoped probation after reconciled failures; `memory.py`, `capital_controller.py`, resource/route planners | Recovery without endless retry or lost history | Medium / high |
| 3 / P2 | Measured critical-path and infrastructure payback scheduling; `economics.py`, `capacity_evidence.py`, goal/research planner | Earlier verified research and rocket progress | Large / medium-high |

### Phase 2: safe automation of existing production

First distinguish source starvation, fuel/power, blocked output, slow processing,
missing research prerequisites, and actor service demand. Add capacity only for a
measured supplied producer whose output is being used; another starved furnace
is not an improvement.

A retrofit probe must be bounded by candidate count, resource purity, path-query
budget, paid kit, science reserve and reachable actor work. Cache a negative survey
only against relevant topology/resource evidence with a bounded resurvey interval.
Expose `outside_survey`, `path_budget`, `placement_obstruction`, `mixed_resource`,
`output_uncommissioned`, `outpost_conflict`, and `kit_shortfall` separately. A
failure to propose remains different from an execution failure.

Prefer an already supported paid outpost, a local retrofit, or an explicit
expansion proposal according to expected actor effort saved per useful unit.
Outposts remain a manual-hauling fallback, not a falsely certified belt success.
Never manually feed a fully constructed input route whose contract prohibits it;
preserve/reconcile paid route state rather than falling back through an unsafe
transfer. Fuel and old unaffected production remain serviceable where permitted.

### Phase 3: additive brownfield expansion, not role replacement

Use a bounded lifecycle such as:

`proposed -> kit_reserved -> building -> topology_verified -> producing ->
useful_output_verified -> preferred_for_demand`

Create one successor under a **distinct owned producer ID**, with a separate
recipe-to-producer selection registry. Do not alias the canonical source to a
new unit while its old buffers and routes still bind the original source unit.
Persist every paid component and dispatch receipt. Keep old production operating
while the successor is built and fueled. Admit at most one expansion per resource
and one committed construction project initially.

Commissioning requires native topology plus repeated mining-to-new-product flow,
correct recipe, stable identity, no foreign items, and inventory conservation.
Then demonstrate downstream use above the preloaded-buffer baseline over a
sustained window before changing demand routing. No demolition is needed in the
initial implementation. If construction or validation fails, retain all paid
assets, preserve reservations/reconciliation records, and use the old cell where
its contract permits. An ambiguous placement must be reconciled, never repeated.

A longer connector route is a separate capability with its own kit, ownership,
geometry and verification limits; it is not enabled by loosening an unrelated
constant. Surveying an unexplored or unverified location is not placement authority.

### Condition-aware failure recovery

Retain append-only failure evidence and the old exhausted budget. Record a typed
failure, action semantics, owned entity identities, relevant precondition hash,
and last checked conditions. Permit at most one probationary retry only after a
verified relevant change: obstacle removed for a path error, paid kit replenished
for a known shortage, or power restored for a power-dependent operation. A tick,
new receipt ID, unrelated inventory change, or model suggestion is not recovery.

Keep a global/project attempt and time budget across condition generations to
prevent oscillation. Unknown outcome, lost acknowledgement, identity mismatch,
and partial construction require reconciliation, not probation. Restart must
retain exhaustion and probation state. No failure history is erased.

## Development VM validation and production gate

### Reproducible offline checks

```sh
PYTHONPATH=src python -m pytest tests/test_planning_efficiency.py tests/test_input_routes_lua.py tests/test_evidence_audit.py -q
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m compileall -q src
```

Browser tests additionally require their declared Playwright browser installation.
Lua tests run real Lua under a synthetic engine fixture, not a live Factorio server.

### Matched native protocol

Stop only at an authorized reconciled boundary and record exact source revision,
policy/model/configuration, native session, map/save hash, tick, inventory, entity
identities, research, pending actions, ownership, failure/probation history and
checkpoint hash. Do not reset or overwrite the live world. Use isolated development
copies of the same existing save and matching checkpoint; never copy a benchmark
world back over the live campaign. Keep production's writer isolated.

Use at least three matched baseline/treatment trials, each at least 30 minutes of
native simulation or a predefined next milestone, plus an uninterrupted two-hour
soak. Longer horizons are needed to assess an infrastructure investment. Record
wall time, native ticks, pauses and downtime independently; do not alter game speed
or equate display FPS with simulation progress. Keep policy/model fixed for the
planning comparison; test hybrid versus explicit JEV as a separate experiment.

| Measure | Required evidence | Proposed gate, not a measured result |
| --- | --- | --- |
| Useful production and goal | New native products **and** attributable downstream consumption; science by type, research completion, rocket parts/launch | No useful-throughput regression; no slower next verified milestone |
| Travel and manual work | Actual fair-action path/approach time, mine count, deduplicated transfer receipts and quantity, delivery purpose | Target at least 25% fewer manual trips per 100 useful units; do not fail beneficial batching just for larger inventory |
| Idle and overlap | Actor idle categories separately from productive factory/research/background time; ready-work-at-wait evidence | Target 50% less avoidable actor idle where independent safe work exists; no factory-starvation increase |
| Model/observation cost | Per-call token usage/model version, actual prices or unknown dollars, latency, purpose/cache hit, observation invalidation reason | Target 25% fewer tokens per useful unit in separately controlled hybrid testing; zero stale-action dispatch |
| Planning overhead | Same-snapshot compiler counts and timings, invalidated caches, survey placement/path probes | One bounded workload calculation per frontier; zero layout probes for a zero-ore local survey |
| Infrastructure | All paid bills, operating fuel, conserved stock, topology, repeated new production and downstream consumption | At least three independent native flow samples, then ten minutes of stable useful output before preference switch |
| Recovery | Injected faults with native receipts, state transitions, per-condition and global budgets, restart persistence | Zero duplicated mutations, zero early spending, zero lost paid assets/history; bounded stop or one justified recovery |

All percentages are initial engineering targets, not claims inferred from a short
capture. Report paired results and variability; a single successful run is not a
reliability estimate. Missing evidence fails the corresponding gate, rather than
being treated as zero time/cost/failures.

Fault cases must include depleted/mixed ore, disconnected power, blocked output,
foreign side-load, new obstruction, disappearing entity, full inventory, ambiguous
RPC return, partial paid build, restart after prepare/after native acceptance,
condition change after budget exhaustion, and unrelated changes that must not
re-enable an action. A transferred belt consumed by a science recipe never counts
as installed infrastructure.

**Production cutover is a separate gate.** Require complete checksummed native
artifacts, passing applicable unit/Lua/browser checks, zero conservation/identity
violations, matched production/progress results and fault/soak acceptance. Then
perform a stopped, reconciled forward-compatible source transition. Do not run
an old checkpoint reader over newer ownership state, reset history, discard
pending work, grant items, teleport, change speed, replace the world, or remove
existing production before its successor is verified. Rollback preserves the
current world and every paid asset; it is not a save rewind.

## Phase 2 implementation

See [Phase 2 safe production](PHASE2_SAFE_PRODUCTION.md) for deadline-aware
paid service visits and bounded rotating retrofit surveys. Native VM validation
and additive successor production remain separate gates.

## Ore-side successors

The opt-in [ore-side successor lifecycle](ORE_SIDE_SUCCESSORS.md) adds separately owned
production, paid construction, native flow checks and bounded downstream-use
qualification without replacing the established factory. Development VM
validation and production cutover remain separate gates.
