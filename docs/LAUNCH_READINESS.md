# First-rocket launch readiness (base 2.0.77)

This phase closes the endgame prerequisite gap identified after the speedrun
review. It does not implement scalable laboratory groups, a launch-wide critical
path, module optimization, native VM acceptance or production deployment.

The capability is installed with the native factory adapter. Its supported
profile is deliberately **unmodified base 2.0.77** (`base`, optionally `core`, no
Space Age/quality/other mods). Unknown versions/mods produce unsupported evidence
and cannot authorize endgame actions. No game-version or mod setting is changed.
Earlier ordinary production remains available; a missing readiness report is
never interpreted as an old-version permission to launch without prerequisites.

## Why rocket-ready is insufficient

The previous planner requested launch as soon as the silo reported `rocket_ready`.
Base 2.0.77 additionally needs a cargo landing pad and a valid payload. The new
sequence is:

1. Complete the real rocket-silo research and acquire/build one paid landing pad,
   or reuse an existing same-force pad without replacing or claiming to build it.
2. Prefer an already-carried raw fish, then an already-carried satellite. Otherwise
   collect one from owned available output, harvest a fish already in normal
   reach, or use the existing catalog-driven satellite production path.
3. Hold one actual payload aside while the existing rocket-part planner continues.
4. Once this rocket is ready, transfer **one** payload from actual main inventory
   to its dedicated rocket inventory, verifying paid stock, exact silo/rocket
   identities, capacity and the native receipt.
5. Request launch only with pad, payload, destination capacity and manual readiness
   present. The engine's `launch_rocket()` result remains authoritative.
6. Wait for the existing native force-launch/victory observation. A receipt saying
   launch was requested is not a victory certificate.

Raw-fish harvesting uses the existing timed native mining controls and normal
reach/cursor checks. One fish entity produces **five** raw-fish items in the
pinned game; one is reserved as payload. The remainder is ordinary inventory.
No fish is spawned, no resource patch is fabricated, and mining is not instant.

## Bounded operation and fallback

| Operation | Bounds and fallback |
| --- | --- |
| Pad discovery | Same force/surface, at most two returned pads; ambiguous ownership stops preparation |
| Pad proposal | At most 17 x 17 local two-tile-grid sites, already generated terrain only; existing reservation/placement guards retained |
| Pad construction | One paid item through native `build_from_cursor`, after ordinary walking; no demolition, expansion of search or free entities |
| Fish discovery | At most 16 fish returned inside radius 16; only fish already within normal native reach are eligible |
| Fish harvest | One timed mining attempt, original target identity, up to 30 seconds of backend waiting and existing fair-control lease |
| Payload | Exactly one raw fish or one satellite, empty cargo for new insertion, one paid transfer |
| Launch | One native request per retained runtime; ambiguous acceptance is observed, never replayed |

A ready-work planner may harvest an already-reachable fish during a passive
research/output wait, only after current supply/capacity work has been considered.
It does not interrupt selected urgent supply, committed capital work, or an
in-flight craft. There is no water-scouting trip or off-route movement in this
first implementation. A fish outside current reach is not placement/mining
permission; the post-research planner can use the satellite fallback.

The landing pad has an 8 x 8 footprint and a two-tile build grid. Candidate centers
are snapped to that grid even when the actor is at odd coordinates. An obstructed
local area yields a blocker instead of removing predecessor production.

Existing loaded cargo is used only when it contains exactly one supported payload
unit. Multiple payloads or unexpected cargo are not removed, overwritten or
silently consumed. A satellite requires room for its pinned 1,000 space-science
launch products in the destination pad. A full pad blocks loading/launching that
satellite without a paid mutation; fish has no launch products. Existing automatic
launch settings are never changed: this first manual contract blocks that case.

This is a bounded preference, not a global cost optimizer. The satellite fallback
can be materially expensive; general critical-path/payback scheduling is a later
phase. The pad is prepared after its real unlock, not before research permits it.

## Reservations, receipts and continuity

`launch_readiness.py` defines the typed observation, commands and completion
predicates. The observation is session/actor/surface/force/tick bound. The one
preferred **carried** payload is excluded from general demand forecasts and paid
spending; other explicit reservations remain additional. A queued or forecast
payload is never granted spendable status. The dedicated payload command may
consume that reserved unit; ordinary insert/craft/craft-job operations may not.

`factory_launch_pad`, `factory_launch_fish`, and `factory_launch_payload` are normal
serialized plan steps with fresh allowed checks, costs, timeouts and completion
receipts. Their operation IDs do not change with resource quantity or a later
observation to escape the existing controller failure history. The existing
route/output/successor validity barriers remain ahead of these commands.

The Lua capability records operation intent **before** native mutation. A lost
acknowledgment can be reconciled only from matching native receipts/identity and
observed effects. Inventory gain or a missing fish alone is not a harvest receipt;
a foreign player's mining event cannot certify it. The fish handler preserves the
prior event callback and is safe to reattach without building recursive handlers.

Paid pad/source identities and intent/receipt maps remain in the existing live
FLE runtime. Reattaching the adapter preserves them. The controller checkpoint
retains the original step/pending/reservation/attempt history using its existing
schema. There is no automatic budget reset, generic condition-aware retry, world
reset or repair of missing runtime. Loss of the live runtime is still the existing
resume/handoff blocker, not permission to create a replacement session.

A prepared/failed native operation is conservative: no fresh receipt ID permits
another mutation of that kind. Unknown partial builds/transfers retain paid assets
and become reconciliation work. Even a fully refunded uncertain transfer is not
a new permission to replay it. A successful submitted launch is subsequently
observed, not requested again if its response is lost.

Do not downgrade a live controller with new pending commands or install older
native code over the new wrappers. Source rollout/rollback requires the existing
stopped, reconciled, single-writer procedure. No old save is restored and no
failure or paid-asset history is erased.

## Telemetry and source layout

The ordinary `factory.launch_readiness` observation contains profile support,
fixed fault reasons, pad/site/fish/silo identities, payload/destination readiness,
operation intents and exact receipts. It is retained by the private versioned
acceptance capture. Inspection does not emit raw exception messages or secrets.
An action's returned message is explicitly not its verification evidence.

| Module | Responsibility |
| --- | --- |
| `launch_readiness.py` | Typed commands, current evidence, payload reserve, allowed/completed predicates |
| `planning/launch.py` | Bounded endgame prerequisites and opportunistic reachable fish |
| `lua/launch_readiness.lua` | Native pad/fish/cargo/launch operations, retained intents and receipts |
| `backends/launch_readiness.py` | Existing walking/timed mining plus exact native dispatch, without FLE prototype-enum assumptions |
| Existing planner, demand, skills, factory/craft adapters | Capability integration and reservation enforcement |

The extra native searches are bounded but their real cost is not measured here.
Count fish actions/receipts separately rather than assuming the older cumulative
ore-harvest counters include them. Do not claim fewer model calls or faster victory
from synthetic endgame tests.

## Regression and native acceptance

```sh
PYTHONPATH=src python -m pytest tests/test_launch_readiness.py tests/test_factory.py tests/test_demand_service.py tests/test_background_work.py tests/test_planning_efficiency.py -q
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m compileall -q src
```

Tests execute actual planners, serialized controller restart paths and the Lua
adapter against synthetic engine fixtures. They cover missing/existing pads,
real paid counts, grid alignment, unreachable/obscured fish, event identity,
reserved payloads, empty/full/foreign cargo, changing rocket identity, automatic
launch settings, satellite destination capacity, and lost acknowledgments for
pad/harvest/load/launch. They are **not a native completed rocket run**.

Before production cutover, use an authorized isolated development copy of the
existing matched live save/runtime/checkpoint. Confirm the exact base/version,
actor and single writer. Demonstrate timed fish acquisition where available,
paid pad construction or reuse, exactly one payload consumed, valid destination,
one launch request, and independently observed native victory. Repeat the
acknowledgment/restart/obstruction/full-inventory cases without duplicate mutations
or lost assets. Preserve the old factory, world, speed and complete failure history.

The native gate must also measure the interval from rocket-ready to verified
victory, launch refusals by reason, acquisition cost, manual movement/work and
model calls/tokens. Pass criteria: zero avoidable missing-pad/payload launch
requests, zero duplicate paid actions after ambiguous returns, no continuity or
inventory-conservation violation, and native victory rather than a mock receipt.
The previous acceptance/fault/soak gates remain mandatory; this source merge
alone is not production authorization.

## Primary reference points

- [Version-pinned base prototypes (fish yield and pad grid)](https://github.com/wube/factorio-data/blob/2.0.77/base/prototypes/entity/entities.lua)
- [Version-pinned payload items](https://github.com/wube/factorio-data/blob/2.0.77/base/prototypes/item.lua)
- [2.0.77 LuaEntity: rocket, launch and automatic settings](https://lua-api.factorio.com/2.0.77/classes/LuaEntity.html)
- [2.0.77 inventory defines](https://lua-api.factorio.com/2.0.77/defines.html#defines.inventory)
- [Rocket silo mechanics](https://wiki.factorio.com/Rocket_silo) (unversioned; pinned prototypes/API take precedence)
