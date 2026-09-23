# Stage 1: productive scheduling and immediate supply

This is a ready-work policy change motivated by the review of
`review/current-logs-20260923T0245Z`. Its source baseline is
`6c651b8c3da3fd7a29342a1141fdebc4f841da12`. It does not deploy to the
campaign or modify its saved world, deadline, receipts, or ownership.

## Immediate requirement versus forecast

`ReadyWorkPlanner._need()` no longer replaces an immediate raw-material request
with the entire horizon stockpile. A furnace needing twenty ore can receive
those twenty before the actor acquires a three-hundred-ore forecast. Explicit
speculative candidates still use the horizon and retain the existing fifty-item
gather bound, site identity, and one-tree wood behavior. The serial planner is
unchanged. Small critical requests are not suppressed by speculative thresholds.

## Producer coverage and bounded operating buffers

`planning/productive_work.py` considers at most 64 entity entries and only
identified, configured, relevant solid-item producers. Carried, unreserved,
unlocked materials can refill an existing producer while its previous inputs
are still processing. No new machines or native action types are introduced.

The operating target is at most twenty recipe batches, bounded by demand,
observed input-stack space, carried stock, and the existing transfer limit.
Recipe energy and machine speed give an estimated input coverage interval.
Manhattan distance, service time, and a safety margin are explicitly policy
estimates, not measured travel times or native performance claims. Missing speed
or geometry remains unknown; an observed complete-batch unblock does not require
a timing estimate. Known fuel or electricity is required for proactive service.

A refill normally moves at least ten items, or the smaller complete operating
target. One- or two-item transfers remain legal when they can restart a complete
batch or satisfy a small demand. Boiler emergency service, infrastructure,
commissioning, binding, and unreconciled crafting keep their existing barriers.
Due refill evidence includes the observation tick. The local ranking gives a
current coverage-due refill urgency above optional stockpiling and below urgent
lab or fuel deliveries. Stale metadata cannot claim that priority.

## Work while research continues

When ordinary scheduling would wait for research, the planner may prepare one
bounded extra batch of each current research pack (up to twenty per pack), beyond
what is committed to the lab. It need not wait until all remaining research units
are in the lab. Stock already carried, collectible, queued, in flight, or in an
acknowledged craft is credited in the forecast, not made spendable. Lab stock is
never borrowed. Current research is not cancelled, and preparation never assumes
a future recipe unlock.

The active capability-aware planner is used, including furnace buffer and input
route guards. At most 32 independent ingredient probes prevent a waiting first
prerequisite from hiding another legal task. The chosen action is still admitted
against real carried stock. When preparation has no admitted task, the passive
plan records a reason, bounded probe count, and rejection categories under
`materials.productive_work`.

Existing background-controller research waits can yield on their next normal
observation when the new work is available. The non-background controller uses
its existing bounded research-progress heartbeat. This patch does not change
pending-action release conditions, polling cadence, or the single dispatcher.
No output is considered complete merely because time elapsed or a forecast grew.

## Validation and boundaries

`tests/test_productive_work.py` covers immediate and speculative quantities,
partial refills, operating watermarks, free-stack bounds, missing timing/energy
facts, reserved/locked inventory, capability composition, stale route evidence,
research preparation, forecast deduplication, research tails, independent
ingredients, priority ranking, unchanged serial behavior, and real-controller
paid execution and acknowledged-wait yielding over synthetic snapshots.

Run the normal suite with its declared dependencies:

```sh
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m compileall -q src
```

The dashboard browser suite additionally requires its pinned Playwright browser.
An environment without Chromium must report that limitation rather than call a
browser launch failure a scheduling regression. CI retains the normal browser
workflow and Python-version matrix; no checks have been removed or weakened.

These are offline scheduling and conservation checks, not native throughput
benchmarks. Before making speedup claims, use isolated matched-save native trials
and compare milestone latency, science consumption, producer starvation, actor
gathering/travel, refill size, and time spent waiting with feasible work available.
The exported review logs do not include a resumable saved world.

Rollback is a source revert at a safe controller boundary. No checkpoint schema
migration is needed. Never discard or replay an ambiguous pending operation to
apply or revert this policy. Existing-world automation migration, construction-kit
investment, expanded production capacity, and persistence tuning are later stages.
