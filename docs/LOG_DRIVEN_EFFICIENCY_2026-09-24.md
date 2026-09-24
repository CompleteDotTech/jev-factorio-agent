# Log-driven efficiency review, 2026-09-24

## Evidence and scope

The production review sampled 238 decision records from two invocations on the
same campaign: 178 records from 11:27:34–13:59:22 UTC and 60 from
16:33:51–16:56:58 UTC. The later invocation runs revision `d9d2892`.
The private export contains selected diagnostic fields, not credentials or model
prompts. Raw campaign logs remain outside Git.

These are inclusive phase measurements. Nested phases must not be added together.
Different host load and different work make this unsuitable as a controlled
before/after comparison.

| Phase | Median | 95th percentile |
| --- | ---: | ---: |
| Planning | 12.079 s | 22.798 s |
| Observe | 3.655 s | 5.863 s |
| Fresh pre-dispatch observation | 3.989 s | 6.079 s |
| Transfer RPC | 0.055 s | 0.106 s |
| Dispatch, including walking/mining | 1.616 s | 101.617 s |
| Checkpoint writes per decision, aggregated | 2.073 s | 3.736 s |

The 203 recorded selection sources include 63 singleton shortcuts, 139
deterministic fallbacks and one JEV selection. This sample does not support
blaming repeated model calls for the dominant elapsed time.

## Targeted changes

### Validate research payloads once

`Redactor.clean` previously validated every subtree again while recursively
redacting it. The new private recursion operates on the tree validated by the
public entry point. Invalid numbers, unsupported types, sensitive values and key
collisions retain their rejection/redaction behavior. Canonical output bytes,
hashing, checkpoint writes and log fsync behavior are unchanged.

Offline microbenchmarks (100 iterations, five repeats, median) measured 3.704 ms
to 2.352 ms for a fixed 128-entity fixture and 1.594 ms to 1.267 ms for the largest
sanitized captured record. This is a redaction CPU improvement, not evidence of a
similar improvement in planning time, native gameplay throughput or stream FPS.

### Collect useful observed stock with travel in mind

The previous recursive factory planner selected the first stocked producer in
role-name order. Selection now compares useful quantity against the existing
service and travel cost estimates when observed geometry is available. Useful
quantity is capped by the actual deficit, observed output and existing 200-item
transfer bound. This avoids preferring a distant large pile for a one-item need.

Unknown geometry preserves deterministic ordering. Private successor outputs
remain excluded, and fresh native preconditions still govern execution.

Of the 68 extract records sampled, 19 requested fewer than ten items, but none
showed another observed producer holding more useful stock. The regression
fixtures establish the previous unfavorable choice and the new behavior; the
captured production sample establishes no counterfactual pickup improvement.

### Use native fluid ports and distinguish preflight rejection

The production water-to-sulfur connection failed before construction: FLE
serialized the input fluid as `\"water\"` (including quotes) and its port at
`(11.5, 53)`, while the native fluidbox reported the actual rotated pipe target
at `(11.5, 49.5)`. Exact fluid matching rejected the serialized value. The
controller then spent 76 observations/18,205 native ticks waiting for a
postcondition that could not occur and stopped uncertain.

Pipe connections now read the actual native fluidbox filters, flow directions
and target positions. Explicit missing-port, no-route and insufficient-material
checks can return a typed rejection before placement starts. The controller
records a non-success `connection_preflight_rejected` outcome, releases that
plan and charges its existing failure budget. It does not claim completion.
Two failures still exhaust the normal plan budget.

Transport failures, malformed observations, arbitrary exceptions and failures
after placement starts retain ambiguous pending state. An old ambiguous attempt
cannot acquire this new proof retroactively. The original incident requires
separate audited reconciliation; the native before/after snapshots retained all
51 pipes and the same 24-connector hash throughout the 154 sampled states.

The added outcome is restricted to `factory_connect` in checkpoint validation.
Older readers may reject checkpoints containing it; preserve an incident archive
and use the compatible reader for recovery instead of deleting outcome history
to downgrade.

### Avoid walking when a build is already in reach

The live PR #70 follow-up placed five paid pipes, then failed a native walking
request. The actor was at approximately `(26.02, 39.86)` and the adapter chose
an obstructed fixed-side approach at `(24, 41)` for a nearby empty build tile.
Unlike an existing entity, the empty tile could not take the old
`can_reach_entity` shortcut. Every placement therefore requested another walk.

Placement now has a build-specific approach: a read-only query compares the
actual actor-to-target squared distance with native `player.build_distance`,
using the same center-distance rule as the existing placement guard. A target
already in range needs no walk. Distant targets choose a collision-free approach
on the actor's side, within the same build range. Transfers keep their existing
entity-reach behavior. Native `can_build_from_cursor`, item payment, placement
and postcondition checks remain unchanged.

This is not a license to reinterpret partial construction as a preflight
rejection. The failed live attempt retained its ambiguity: inventory changed
from 51 to 46 pipes and the native connector set from 24 to 29, with all five
new identities accounted for. Recovery must retain that paid construction and
reject the old plan through a separate audited operator reconciliation. A new
plan surveys the current world; it does not replay the old dispatch.

## Applying the supplied speedrun strategy

The useful transferable principles are minimizing travel, keeping acknowledged
crafting and research supplied during independent work, and preparing the next
production bottleneck. The existing ready-work/background controller already
implements bounded independent work and research resupply. It does not permit
concurrent unacknowledged mutation or spend forecast output.

Human speedrun split times and 90-SPM targets are not controller deadlines or
automatic capacity commands. The current observation/action overhead, world
geometry and paid-infrastructure constraints require native measurement before
adopting a capacity target. Restarting the world to obtain a better seed is not
part of this change.

The current [official rocket-silo reference](https://wiki.factorio.com/Rocket_silo)
describes a landing pad and payload requirement, including raw fish as a payload.
The existing planner's rocket-ready branch directly requests launch; a complete
2.0.77 endgame acceptance test should explicitly cover these requirements.
This review does not treat a moving wiki page or a synthetic fixture as proof of
native launch compatibility, nor silently replace the campaign's victory contract.

## Preserved safeguards

Do not increase wood gathering blindly: its observation identity binds one tree.
Do not remove fresh observations, skip changed write-ahead checkpoints, relax
service-visit bounds or reinterpret an old ambiguous mutation as safe to replay.
The active campaign, paid inventory, world, session, failure budgets and cutoff
must survive deployment. Native action verification remains distinct from an
offline regression test or backend success string.
