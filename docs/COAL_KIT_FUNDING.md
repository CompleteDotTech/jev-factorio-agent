# Bounded paid coal-kit funding

Refs #101, #99, #100, #102, #92 and #103. This extension implements missing-kit
acquisition for an **explicitly configured** coal bundle. It does not select an
autonomous profitable coal project, activate the production CLI/supervisor, or
establish native flow, useful production or acceptance. The default remains off.

## Reproduced missing behavior and interface

With two valid proposed coal branches, a powered owned layout, and the required
normal electric drills in accessible owned output rather than player inventory,
the composed controller previously offered neither a coal build nor a kit pickup.
The construction guard correctly rejected the missing carried kit, but deterministic
planning could not turn the owned stock into a legal build prerequisite. No model
prompt could supply that absent action. This is a deterministic fixture diagnosis,
not a fresh measurement of a live world.

Pass `coal_kit_policy=True` only when composing `coal_loop_type(solid_loop_type(Base))`
with the existing explicit coal targets and native coal/solid adapters. The flag
requires the coal composition, is strictly boolean and is bound to checkpoint and
research-manifest configuration. It is not a new CLI option. An existing campaign
must not acquire the flag by changing its runtime in place. Deployment still needs
the original owner's authorized handoff, immutable treatment and cutoff.

The feature reuses the paid downstream bill solver in `planning/solid_funding.py`.
`planning/coal_funding.py` supplies the whole-bundle binding, admission, cost evidence
and fresh permission; `coal_controller.py` supplies bounded durable funding state.
No new native mutation, Lua implementation or RCON concurrency is introduced.
The existing downstream wrapper retains its source-draw and payback inputs.

## Feasibility and accounting

All configured branches must be fresh, proposed, unstarted and powered, with
qualified remaining ore, no paid/pending source work and no pending solid mutation.
The actor must be bound and connected with an empty crafting queue. Urgent boiler
fuel, an active crafting job, a capital project or another optional funding project
prevents new coal funding. Existing required science/manual work remains eligible.
Only one parent candidate compilation is performed per decision.

The complete remaining network bill includes every source chest/drill and every
receiving corridor component, including corridors not yet committed natively. The
solver first protects all currently carried final kit components, then simulates
the missing bill using current spendable carried stock, accessible owned outputs
and enabled deterministic handcraft recipes. It cannot consume a final kit belt as
an ingredient and silently regard that belt as still funded. Other projects' holds,
background locks, private coal-source inventories and their unit aliases remain
excluded. No forecast, unowned entity, mining/gather action or future science output
can fund this feature. Recipes, capacities, quantities and expansions are bounded.

The simulation is not authoritative inventory or an executable multi-action batch.
Only the first actual extraction or craft is offered. Before dispatch the controller
rebuilds the bill from fresh facts and compares the full canonical step, bundle and
catalog binding. Transfer receipts must be canonical and not already registered.
Changes in stock, power, targets, catalog, queue, reservations or pending work cannot
turn old estimates into action permission. Native preparation and payment still
require the full carried construction kit and fresh native preconditions.

Diagnostics report kit components, source/catalog binding and labeled policy/catalog
estimates for acquisition, walking, service and crafting. Admission is explicitly
`explicit_immutable_coal_bundle_not_autonomous_payback`. `avoided_haul_ticks` and
`net_coal_return` remain null; `native_flow_proven` is false. Costs are not measured
wall/CPU latency, verified mining yield, an avoided-trip benefit or a profitability
claim. Demand-aware coal adoption and net energy/hauling economics remain #101 work.

## Durable reservation and failure boundaries

The checkpoint adds `coal_kit_policy` and nullable `coal_funding`. Funding binds the
stable configured-target project key, complete unpaid geometry, full kit, actual
held final components, catalog digest, starting/deadline ticks and selected-action
count. It permits at most 32 acquisition actions and 216,000 game ticks. Game ticks
are not a wall-time campaign extension; the original supervisor cutoff still wins.

Provisional holds contain **only physically carried available final components**,
not a fictional reservation of every missing item. A normal completed pickup/craft
updates these actual holds before ordinary work may consume them. An unresolved
pending action's own cost reservation may already be spent, so it is not counted as
another project's still-carried stock; all other holds remain protected. Unexpected
loss of held kit is uncertain state, not permission to clear reservations and retry.

Selection and prepared-action saves cross the existing durable barriers. A lost
reply keeps the exact pending action/receipt until existing reconciliation resolves
it; restart cannot fund the same acquisition twice. Funding state is handed off only
after the observed paid coal bundle matches the frozen proposal and has passed the
existing ownership/receipt validation. The native whole-network commitment then owns
the unpaid kit; provisional holds are not released merely because a plan was chosen.

Project failure identity depends on configured consumers, not layout, source unit,
tick or receipt. Existing source-component, corridor-component and ordinary action
budgets also prevent acquisition. Exhaustion, expiry or changed binding cannot buy
a fresh budget through a new kit plan ID or a later complete carried kit. A pending
mutation always reconciles before a bounded abandoned project releases its holds.
Failures are never reset. Ready science and required manual service retain their
normal safety filters while the optional project waits or is rejected.

## Checkpoint compatibility and rollback

A legacy checkpoint without the new fields resumes with the feature disabled; it
cannot be used to enable funding silently. A true opt-in checkpoint must contain
valid funding state and must match the configured flag/target list before backend
attachment. A funding-bearing checkpoint also validates its active plan marker,
receipt, action type, single-step shape and exact bundle/catalog identity. Conflicting
capital/downstream funding or invalid counters fail closed.

The new fields are an additive shape for this composed controller, not an automatic
migration permission. An older executable may reject a newly written checkpoint,
even when funding is disabled. Before publication, rollback means leaving this
candidate undeployed. After authorized deployment, use a reviewed compatible source
revert and the existing owner-controlled pending/receipt reconciliation; retain the
checkpoint, paid structures, original treatment and cutoff. Never strip fields,
clear a funding hold, restore an old save, or swap native code under a pending action
to make an older loader accept it. No rollback or engine compatibility is claimed
from Python fixtures.

## Validation and evidence boundary

The focused tests cover owned-output pickup, enabled handcrafting and a complete
missing-kit construction sequence, final-component protection, actual reservations,
legacy/opt-in manifests, malformed checkpoint bindings, stable and existing failure
budgets, receipt reuse/lost reply/restart, deadline and action bounds, changed fresh
preconditions and retention of ordinary candidates. Their backend uses explicit
synthetic recipes and API-shaped observations; constructing both modeled branches
is not proof of Factorio mining, physics, energy balance or sustained coal delivery.

Run `PYTHONPATH=src python -m pytest tests/test_coal_kit_funding.py -q`, existing coal,
solid funding, checkpoint/replay and mixed-controller regressions, and the repository
Linux suite and explicit mock research-integrity smoke. Bind evidence to the exact
final source. Existing prerequisite CI/review does not approve this changed source.

Remaining delivery requires the configured existing SSH signer, focused PR, a
separate exact-final-source review, hosted checks and normal merge. Native #92 must
still qualify the actual deployment/configuration, paid mining and delivery to at
least two consumers beyond bootstrap, downstream useful science/research, recovery,
resource/latency attribution, and the original authorized >=30-minute useful-progress
window. No issue is closed by these fixture results or by source merge alone.
