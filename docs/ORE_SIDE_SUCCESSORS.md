# Phase 3: opt-in ore-side successor production

This capability builds a **separate** iron- or copper-smelting cell beside observed
ore while retaining the established factory. It does not remove or rebind the
canonical `recipe:iron-plate` or `recipe:copper-plate` producer. The additional
fixed roles are `growth:iron-plate` and `growth:copper-plate`; each has its own
native unit, site anchor, output buffer, input route and paid component receipts.

This is source functionality, not a deployment or a measured gameplay result.
Use `--ore-side-successors` only with an existing resumed rocket campaign,
`--background-work`, ready-work scheduling, furnace output buffers and input belts.
The CLI requires `--resume --resume-controller` and validates checkpoint migration
before initializing a backend. Direct Python controller construction also checks
resumed memory before installing adapters. Existing mining-outpost runtimes and
`--mining-outposts` are deliberately unsupported in this first composition: they
are not disabled, erased or silently adopted.

## Lifecycle and admission

The composed controller offers a successor when an established predecessor has
made at least 20 products, an observed joint site is available, no direct canonical
input route is already offered/owned, and the current admitted work includes at
least ten units of corresponding manual mining or ore delivery. This is a bounded
investment heuristic, **not measured return on investment**. Critical existing
fuel/research/producer-refill work keeps its existing urgency. There can be only
one unqualified project at a time and one successor per supported material.

`proposed -> committed acquisition -> reserved -> output_building ->
output_commissioning -> input_building -> producing -> preferred`

`paused` is retained controller project state after a bounded reconciled failure
budget or deadline; `fault` means invalid native identity/flow evidence and stops
the controller for reconciliation. Pausing never deletes paid components, removes
failure keys, drops pending work, or manufactures success.

Before the first furnace placement, carry the full native construction bill:
one furnace, one burner drill, two burner inserters, one wooden chest, the surveyed
belt count, at least twenty additional science belts, thirty coal and ten seed ore.
All are acquired through the existing ordinary mining/crafting/transfer paths.
The Lua prepare/build path checks this kit again; dispatch still uses real walking,
normal interaction reach and item-paid placement. The successor project protects
already-carried unbuilt construction pieces from unrelated spending. Coal and seed
ore are not locked away from predecessor or emergency servicing. All ordinary
per-step reservations and fresh preconditions remain authoritative.

The joint-site survey reuses the existing bounded generated-area ore-side search.
The legacy **40-tile retrofit survey and 64-belt cap are unchanged**. The successor
moves the *new production site* near ore instead of stretching a route from the old
furnace. It does not move the old furnace or generate free infrastructure. The
existing `existing_manual_cell` rejection remains in force for occupied canonical
roles. Surveys, valid offers, and paid ownership are distinct states.

## Three different kinds of evidence

### 1. Paid construction and identity

Every furnace, chest, arm and belt/drill component uses the existing builders.
Preparation is not placement. A new source or component can enter the controller's
retained ownership only when it matches the exact current pending action and its
native receipt. Subsequent observations and restarts must preserve those frozen
identities. Old readers reject the named checkpoint extension rather than ignoring
it and losing paid ownership.

### 2. Automated ore-to-plate flow

Up to **ten paid seed ore** can commission the output buffer before the input route
is built. Further manual ore insertion into the successor is disallowed; no direct
furnace extraction is allowed. The standard output-buffer certificate and then the
standard input-route certificate are required. They check ownership, topology,
material conservation, multiple positive samples and genuinely new mined products
beyond preloaded work. Component placement or an output chest containing old seed
plates cannot qualify the successor.

Ordinary planners and their future-supply ledger do not treat uncollected
successor output as freely available stock. Only the successor-aware planner can
select a controlled collection. The predecessor remains independently selectable,
and a growth route does not prohibit gathering ore for its legacy counterpart.

### 3. Useful downstream work and a sustained sampled window

After verified automated input flow, the planner may collect output for real
current demand in trials of at most fifty plates per action, capped at **200 plates
before qualification**. This is not a permanent preference switch. No extra trial
is created solely to invent proof; ordinary recipe demand must use the material.

A conservative provenance ledger subtracts the ten manually seeded plates first.
For each ordinary spend it treats other carried stock as consumed before attributing
any successor credit. Unexplained inventory changes clear only attribution credit,
not assets, production counters, project state, pending work or failure history.
This assumes the existing single-writer gameplay contract; it is not an adversarial
anti-cheat system capable of detecting external net-zero inventory swaps.

The first supported downstream witness is a **completed native background craft**
using at least three attributable successor plates, producing gears, copper cable,
electronic circuits, automation science or logistic science. The normal craft
adapter verifies full ingredient debit, acceptance, recipe/actor identity, exact
craft events, valid queue completion and actual resulting inventory. Mere enqueue
acceptance, cancellation, another job, preloaded output, or missing output cannot
provide a witness. The accepted witness is retained immutably.

Qualification additionally requires a sampled native production window spanning
at least **36,000 ticks**, three positive production samples and three new products.
No observation gap or observed production stall may exceed 1,800 ticks. Such a gap
restarts only the tentative sampling window, not failure/ownership history. This
is sampled liveness, not proof of perfectly constant throughput between samples.
The qualification certificate binds the source unit, input layout and downstream
job receipt. The demand planner then prefers the qualified successor's available
output; an empty successor falls back to available predecessor supply without
rewinding or reassigning either entity.

## Bounded failure and restart behavior

Controller memory explicitly stores project anchors, predecessor/source units,
deadlines and status, plus frozen output/input receipts and proof identities.
Initial opt-in from a legacy checkpoint is permitted only at an idle, reconciled,
running boundary with no active plan, pending operation, reservations, capital
intent or background job. The source world is not reset and history is preserved.

Two reconciled failed marked steps across the project, even under different plan
IDs, pause that project. A 216,000-tick project deadline also bounds acquisition and
commissioning. Changing quantity, receipt, or tick cannot erase those failures.
Unknown native outcomes still follow existing ambiguous-dispatch reconciliation;
they are not automatically retried. A lost placement acknowledgment with an exact
owned postcondition can be verified after restart without placing another furnace.
Faulted routes and changed identities remain fail-closed, rather than authorizing
manual feeding through an unsafe route or rebuilding over paid components.

The supervisor forwards the explicit feature flag, includes it in immutable
production-treatment configuration, and preserves successor memory during repair
validation. It does not turn the feature on for an existing supervisor silently.
An existing treatment needs an authorized stopped handoff; its original run/session,
cutoff, ownership, failure and pending-action constraints must remain intact.

## Deliberate limits

This phase does **not** automate fuel delivery or transport plates all the way to
research/rocket assemblers. Plate collection remains paid manual hauling. It does
not implement arbitrary producer graphs, every recipe, outpost composition,
condition-scoped probation after an exhausted project, demolition, migration of
old producer roles, or general-purpose throughput optimization.

The initial useful-output proof supports receipt-tracked handcrafting, not attribution
through an arbitrary downstream assembler network. A factory doing all downstream
work in assemblers may produce useful output yet never satisfy this narrow witness.
That is a conservative qualification limitation, not permission to infer success.
The bounded trial/deadline will preserve the predecessor rather than claim a proven
replacement. Copper and iron share the same typed lifecycle but remain separate
owned identities.

The earlier private compressed capture is not verified by these changes. Its missing
telemetry and checksums remain a separate evidence task. Future exports must retain
`ore_side_successors`, `successor_evidence`, `successor_projects`, native `successors`,
input/output/site evidence, craft-job observations, per-step receipts, timings and
model usage, with credentials/prompts excluded. Archived captures must not be
rewritten to appear complete.

## Validation and native cutover gate

Synthetic tests run the actual Lua joint-site/output/input/craft adapters and the
Python planner/controller. They exercise distant additive sites, paid full-kit
checks, both materials, proof ordering, mixed carried-stock attribution, seed-only
nonqualification, cancelled or missing craft output, timing gaps/stalls, unchanged
predecessor identity, manual-feed guards, trial caps, repeated attachment, reserved
construction pieces, lost-acknowledgment restart, old-reader rejection and supervisor
memory preservation. They do not constitute Factorio VM performance measurements.

Before live enablement: use isolated development copies of the existing save and
matching checkpoint with one writer. Record exact source, session/map/save identity,
feature flags, starting tick, inventories, research, pending work, component receipts
and complete failure budgets. Do not reset or overwrite production or copy a test
world back into the live campaign. Run at least three paired baseline/treatment
trials and the established two-hour uninterrupted soak.

Acceptance requires no duplicate paid actions, source-role replacements, inventory
conservation failures, lost assets/history or premature preference switches; bounded
failure behavior and successful restart at every build boundary; separately verified
output flow, input flow, downstream craft use and the complete sampled window.
Measure useful downstream products and the next science/rocket milestone alongside
manual mining, actual travel, plate/fuel hauling, idle categories, model tokens/cost
and recovery rates. Source tests alone cannot establish a throughput improvement.
Missing native evidence blocks production cutover. Rollback preserves the current
world and all new paid assets; it is not a save rewind.
