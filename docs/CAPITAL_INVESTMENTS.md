# Staged capital investments (Stage 3)

Ready-work rocket campaigns can bootstrap a recurring intermediate producer even
when the producer's construction kit requires that same intermediate. This is a
planning/controller change, not a new native mutation protocol or a throughput claim.
The serial production planner is unchanged. No additional CLI flag is required.

## Admission and commitment

The existing policy gates remain: deterministic, enabled, solid recipes;
recurring intermediate/science demand; at least 40 forecast products; estimated
avoided handcraft-queue occupancy exceeding construction effort plus 1,200 ticks.
Stage 3 stages only handcraftable `crafting` recipes. Other production paths retain
their existing behavior. An already paid machine is reused, not replaced.

A bounded independent frontier examines at most 16 relevant products and prepares
at most four offers while current research runs. Due producer/lab deliveries and
urgent fuel maintenance outrank these optional investments. The planner does not
cancel research, assume a future unlock, or spend forecast production. One selected
investment can be committed at a time; merely enumerating offers writes no intent.

The selected `capital_investment` checkpoint field freezes the product, recipe,
producer role, machine type, bounded batch target, cost/workload estimates, and a
fingerprint of the relevant catalog graph. Acquired kit stock is earmarked against
unrelated tasks, and that protection is checked again before a fresh dispatch.
Holds count only actual carried resources for the remaining kit, never machine
inputs, expected crafting outputs, or uncollected stock. The native inventory and
ordinary step reservations remain authoritative; earmarking creates no items.

## Stages

1. **Kit:** acquire one construction kit through the active capability-aware planner,
   with optional economic recursion disabled. For a machine requiring five gears,
   bootstrap those five gears instead of indefinitely handcrafting production batches.
2. **Build:** place the paid machine with the existing normal movement/reach/payment path.
3. **Configure:** select its unlocked recipe and continue its ordinary infrastructure setup.
4. **Supply:** supply a bounded real-input batch through individually verified actions.
5. **Verify:** observe both an increase in the bound machine's `products_finished`
   counter and positive output of the intended recipe. Placement, configuration,
   input transfer, elapsed time, global output, or an acknowledgement alone is insufficient.

The phase label records the most recently committed investment step. Ordinary urgent
maintenance may interleave; the next required stage is derived from current facts.
Only the construction kit is earmarked, not an unlimited lifetime production budget.
After observed production the intent is released and the normal planner reuses the cell.

## Persistence, ambiguity, and boundaries

Intent persists separately from a single action plan, including when an acknowledged
kit craft moves into the existing background-job record. It survives per-step replanning
and controller restarts. Existing background output locks, pending-action verification,
and no-replay barriers still apply. A second construction or craft cannot spend an
unfinished background job's outputs.

Initial machine identity is bound from the existing generic placement postcondition
for the exact retained investment plan. This does not add a new native placement
receipt or stronger first-observation provenance than that inherited contract. Once
bound, a disappeared/replaced machine, changed configured recipe, regressing counter,
or changed relevant catalog fails closed while preserving pending work and intent.
An ordinary uncertain dispatch can still reconcile against later valid evidence;
capital identity faults are not confused with an acknowledgement timeout.

The deadline is fixed at commitment: `min(216000, max(7200, investment_ticks * 4))`
ticks after the observed start. It never resets on replanning and never overrides
the campaign cutoff. Expiration releases optional intent only when no action or
background job is pending. Two reconciled failures of the same investment step
also exhaust the optional investment. Paid entities remain in place; no automatic
mining/removal, rebuilding, or ownership reassignment occurs. Exhausted optional
investments do not erase the ordinary non-investing acquisition path.

Input/output routes and mining outposts use their existing capability-aware acquisition
and execution guards. No new tick handler, Lua mutation, API call, coal outpost,
transport automation, or capacity expansion is introduced. Construction investments
are still estimates of queue relief, not calibrated wall-clock payback predictions.

## Evidence and compatibility

Commitment, selected phases, machine binding, abandonment, and completion appear in
controller history. Commitment/completion/abandonment also appear as causal trace
events. A live intent is included in gameplay records and checkpoints. The existing
research-log integrity verifier accepts these events; this does not add semantic
capital-state reconstruction to the legacy replay interpreter.

Legacy checkpoints without the field remain readable without resetting their pending
actions. Checkpoints with no live capital intent omit the field when saved. Older
software must not read a checkpoint with live capital intent: its unknown-field
rejection protects the commitment. Do not remove the field to force a downgrade.
Apply or revert code only at a reconciled, stopped controller boundary. A local source
revert is not a Factorio save restoration and does not undo paid construction.

## Validation and native measurement

`tests/test_capital_investments.py` exercises the actual planners, final candidate
frontier, dispatcher, background receipts, checkpoint loader, and durable research
logger using explicitly synthetic worlds. It includes restart-between-stage cases
across the base, buffered, input-route, and mining-outpost compositions, plus failure,
resource-hold, staleness, deadline, and serialization regressions. The prior gear test
now checks acquisition of the five-gear kit rather than a permanent 20-gear fallback.

These tests do not run Factorio. Before claiming faster gameplay, run isolated matched
saves with Stage 2 baseline versus Stage 3 source, preserving world/session/actor,
normal speed, expenditure limits, and the original campaign deadline. Measure kit
acquisition effort, time to first demonstrated output, machine utilization, science
consumption, actor gathering/travel time, input starvation, abandoned investments,
and milestone latency. Retain negative outcomes and setup costs. Do not benchmark
by resetting an active campaign or silently extending its allowance.
