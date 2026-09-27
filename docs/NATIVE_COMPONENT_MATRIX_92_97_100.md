# Native component and capacity evidence matrix for #92, #97 and #100

This is a preparation ledger for the final reviewed treatment, not a record of
native acceptance. Record UTC collection time, exact source commit/tree,
configuration digest, game/FLE/mod versions, save digest, checkpoint digest,
owner/campaign binding and evidence hash for each result. Keep raw identities,
paths, endpoints, logs and saves private. A fixture pass, Lua double, analyzer
consistency pass, or healthy process does not establish actual Factorio flow.

## Read-only owner and capacity gate

Use the existing supervisor/owner status channel first. Read its campaign phase,
original cutoff, run identity, owner and maintenance locks, deployed commit,
configuration, save/checkpoint pairing, pending action and last useful science or
research receipt. Treat an absent or inaccessible owner binding as unknown. Do not
start a second controller or attach a backend merely to inspect status. Verify the
remaining original window contains rollout margin plus 1,800 consecutive seconds
before any production acceptance attempt.

The #97 collector is read-only. Its default PID is itself, so a controller
capacity claim requires an owner-verified controller PID and visible cgroup
root/leaf. Collect guest and host views separately through the authorized owner:

```sh
PYTHONPATH=src python -m jev_factorio.capacity_audit \
  --pid "$VERIFIED_CONTROLLER_PID" \
  --cgroup-root "$VISIBLE_CGROUP_ROOT" \
  --leaf "$VERIFIED_CONTROLLER_LEAF" --seconds 2 > "$PRIVATE_CAPACITY_REPORT"
```

The explicit `--leaf` is optional when the selected process's cgroup membership
can be read and resolved inside the selected root. An explicit leaf that does
not match that membership is reported as unbound to the controller.

Record the collector's `target_binding` and `capacity.scope`. An explicit cgroup
sample that is not bound to the selected process is evidence about that cgroup,
not about the controller. A finite visible quota is only an upper bound; missing
ancestors or host readback mean effective host capacity is unknown. Compare guest
and host samples only when their windows and identities are independently bound.
The owner must capture active and persistent VM configuration, host headroom,
affinity and memory pressure before proposing a quota or placement change. The
pure `allocation_plan` output has `apply_authorized: false`; it does not apply
or authorize a host change. Keep exact previous settings for rollback and read
back both active and persistent settings after any owner-applied change.

## Isolated native matrix

Run each row on an owner-approved isolated native save with the exact final
source/configuration. Record before/after inventories, unit identities, ticks,
receipts, paid materials, route state, flow counters, actor trips and recovery
observations. Use supported normal-quality endpoints and the documented straight
cardinal 1–24-belt, at-most-four-disjoint-route contract; reject unsupported
turns, joins, splitters, undergrounds and graph layouts explicitly.

| Component and issue | Native action and proof | Negative/restart boundaries |
| --- | --- | --- |
| Ready science and maintenance, #92/#98 | With ready packs, low upstream fuel and a powered empty lab, show candidate offered, selected, delivered and consumed, with research advancing. With fuel genuinely blocking output, show bounded maintenance executes. | Repeat decisions with multiple burners, boiler urgency, stale observations, route fault, locks and exhausted failure identities. |
| Grouped manual fuel, #92/#99 | Start two eligible burners at one coal each; show aggregate acquisition for both five-coal targets when capacity permits, two deliveries, actual burn and measured service trips. | Partial insertion, changed carrying capacity, unreachable/idle consumers, carried versus reserved coal and later urgent boiler demand. |
| Paid solid route, #100 | For each supported orientation and endpoint inventory, show proposal, selected paid construction, verified native unit/payment receipts, topology and new source-to-target movement beyond initial stock. | Interrupt at prepared, pending, native effect/lost reply, returned receipt before persistence and partial-prefix boundaries. Reconcile without duplicate payment or ownership adoption. Test wrong direction/destination, replaced/foreign unit, collision, force/surface/item mismatch, missing power, full target/backpressure, depletion and route fault. |
| Coal network, #92/#101 | Show demand/economic admission, paid electric mining/bootstrap, two distinct fuel consumers receiving and burning attributable newly mined coal beyond initial stock, and net operating-fuel accounting. | Depletion, full targets, shared kit reservation, source/route fault, restart and recovery without free materials. |
| Downstream route, #92/#102 | Show offered/selected/paid route delivering owned ingredients to an actual science dependency, its output consumed, and matched reduction in actor hauling. | Backpressure, unavailable inputs, stale receipt and partial route recovery. |
| Observation, durability and timing, #92/#93–#96 | Verify installed native API/version and helper calls, actual provider requested/resolved model, equivalent semantics, complete non-overlapping timing and persistence counters. | Compatibility fallback, malformed observations, checkpoint crash/install/reconcile, opaque helper time and unknown counter coverage remain explicit. |

For #100 specifically, placement and a connected topology are intermediate
states. Flow needs a new attributable source decrease/production and target
increase/consumption across repeated native observations, with same paid route
and unit identities. Full destinations must report backpressure without falsely
claiming productive flow or rebuilding endlessly. Existing ore-side, buffer,
background-craft, failure-budget and checkpoint behavior needs regression review
on the composed source.

## Integrated treatment and comparison

Freeze the experiment specification before examining treatment data: source,
configuration and model identities; save/seed and workload mix; warmup and
windows; useful output; numerical latency and science regression limits; missing
data rules; rollback triggers and capacity profile. The production activation
must make these flags and explicit route/coal intents immutable across resume and
observable in records/checkpoints. The capture schema must preserve complete
route/coal ownership, funding and flow evidence. The old ore-side capture
deliberately rejects solid records; deleting that rejection or dropping unknown
fields would not qualify the combined treatment. Analyze retained copies with
`jev_factorio.integration_evidence`, which never authorizes deployment or
native acceptance by itself.

Use matched algorithm arms at fixed capacity and matched capacity arms at fixed
source/configuration. Align save, workload, duration and concurrent pressure;
label unmatched trends and confounders. Report counts and median/p95 for
iteration, planning, each observation boundary, selection, native action,
checkpoint/log work and inter-iteration gaps, plus process CPU, calls, bytes,
writes/fsync, fuel and ingredient trips, science per wall minute, PSI/steal and
quota deltas. Do not add nested timing phases or call opaque helper wall time
CPU/network time.

The #92 live gate requires the established owner and immutable handoff, actual
controller/provider execution, exact deployed source/configuration readback,
and at least 1,800 consecutive seconds of useful science delivery, lab
consumption and research progress, including the stalled or corresponding
feasible milestone. Independent review must link private raw evidence to
sanitized results and verify all recovery and source/configuration bindings.
Only then may the relevant child criteria and #92 be marked accepted.
