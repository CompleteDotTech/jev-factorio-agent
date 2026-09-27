# Negotiated atomic observation v2 — issue #94

## Delivery and evidence boundary

This is an opt-in continuation of `consolidated_observations`, not a claim that a
running controller was upgraded. The actual Python adapter and Lua observer have
fixture coverage; the Lua fixtures use a synthetic Lua 5.2 engine, not Factorio.
The runtime targeted for native qualification is the existing 2.0-based adapter.
No blanket 1.1, Space Age, modded-map or future-API compatibility is claimed.

The non-consolidated backend and unsupported adapter combinations retain their
legacy reads. A newly installed consolidated adapter must read back the exact
`JEV_ATOMIC_READY|2` marker. Missing/malformed negotiation raises before use. Once
negotiated, malformed snapshots fail; they never cause silent legacy reads or
return an empty inventory placeholder. Old `observation.lua` v1 remains available
for its existing tested adapter path. A live treatment must not be toggled in
place to force this path; use the existing authorized source/configuration handoff.

## Single-command contract

The v2 command captures the existing fair-control heartbeat, actor position and
main inventory, the complete currently composed campaign observation, bootstrap
state and discovery evidence in one synchronous Lua command. It checks a common
game tick. There are no parallel calls or `send_commands` atomicity claims.
`fair.observe` still renews its existing bounded action lease; the read neither
starts movement/mining nor changes ownership, receipts, inventory or research.

Python checks session, character unit, force, surface, tick monotonicity, player
binding, speed/pause state, finite positions, inventory types, bootstrap identity,
query bounds and cache counts before publishing the result. Craft-job inventory
must match this same snapshot exactly; a decorator cannot overwrite it with a
conflicting count. Receipts and all existing capability fields are preserved and
still pass through the controller's ownership/receipt/action validators.

The FLE actor-state RPC, standalone fair-control RPC, actor/chest inventory
helpers, broad FLE entity conversion and water/oil `nearest` helpers are absent
from the negotiated observation path. This does **not** remove fresh pre-dispatch
or receipt-verification observations. An action can still make its ordinary
native/FLE calls. It does not make a shared RCON client safe for concurrency.

## Bootstrap and discovery bounds

Bootstrap reads at most 129 native entities, only same-force burner drills and
wooden chests within 1,000 tiles. More than 128 fails closed, not a truncated
"complete" world. The first selection is deterministic by native unit; subsequent
observations pin it. Missing/replaced/out-of-scope pinned drills fail, preventing
silent adoption of another producer. A bootstrap drill placed by FairActions
retains its returned native unit. Its later fuel insert checks that unit again
before payment. This is a read binding, not proof of paid factory ownership.
The existing ownership contract alone authorizes production/route mutations.

The five raw-resource positive targets retain the existing fair cursor-selectable
search, over generated terrain around the campaign origin (maximum 1,024 tiles).
Each cache hit rechecks validity, amount, minability, unit, surface and cursor
selectability. Cache keys include session, actor, surface, force, exploration
radius, generation and a 16-tile movement cell. Expiry is 1,800 ticks. Absence is
not cached. Reattachment/restart drops the cache. Topology/mining/unknown action
attempts invalidate generation before dispatch, including ambiguous failures.

Water and oil are fresh bounded witnesses, **not globally nearest resources**.
Each query is capped at 129 results within 256 tiles of the actor. Oil overflow
is unknown; water may use a bounded vanilla water/deepwater tile witness. Anchors
outside that scope and unsupported tile types remain unknown. They never credit
material or authorize placement; normal native placement and reach checks remain.

Global native factory surveys already required for connector/ownership semantics
are retained. The existing fair raw-resource search is radius-bounded, not newly
capped by this change. No reduction of all native engine query cost is asserted.
These constraints must be included in matched native workloads and acceptance.

## Reproducible checks

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_atomic_observation.py tests/test_atomic_observation_lua.py \
  tests/test_campaign_observation.py tests/test_observation_inventory_reuse.py \
  tests/test_fair_actions.py
PYTHONPATH=src python benchmarks/benchmark_atomic_observation.py --samples 100
```

The benchmark drives the actual Python adapter paths with fixed fake transport,
checks equal game facts (excluding new protocol/bounds diagnostics), and records
logical RPC/helper counts, response envelope bytes, CPU/wall distributions and
source hashes. It excludes real server work/network/FLE conversions, so its timing
is not native latency or a speedup estimate. Native Lua fixtures separately cover
positive/negative caching, discovery invalidation, bootstrap, bounded queries and
wire-to-Python contract compatibility. Profile wall residuals are not CPU/network.

## Required native and delivery gates

Before production, independently review and SSH-sign the exact source, pass hosted
checks and merge normally. Qualify the v2 schema against the deployed game/FLE
versions and a controlled isolated save, all consolidation/craft-job combinations,
empty/bootstrap and mature factories, depleted targets, missing components, receipt
failures and restarts. Retain identical observations/action mix in before/after
comparisons; publish logical call counts separately from measured transport packets,
CPU/wall/bytes, cache hit/miss, source/dependency/configuration and resource pressure.

Preserve the original campaign identity, treatment, pending receipts, failure
history and cutoff during an authorized handoff. Resolve old incidents using their
exact recorded native source before upgrading; never rewrite a pending receipt or
pretend new Lua has the old digest. Roll back through the same owner-controlled
handoff, with no world reset or shared-host/WSL restart. The full useful-science and
research window in #92 remains required; no fixture or source merge substitutes.
