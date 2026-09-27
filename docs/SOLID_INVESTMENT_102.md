# Bounded downstream investment on the paid corridor contract (#102)

This is an experimental, source-tested Python composition feature. It is not a
native Factorio validation, a deployed treatment, a coal network, or completed
#102/#92 acceptance. It uses the exact `straight-solid-corridor-v1` contract
specified in `SOLID_ROUTES_100.md`; it does not introduce a second route DTO,
command, flow sampler, checkpoint owner, or native storage implementation.

## Treatment and supported scope

The existing explicit-intent composition gains `solid_science_policy=True`.
With its default `False`, explicit-intent foundation behavior is retained. With
`True`, configured intents are an allowlist, not unconditional construction
orders. The controller computes demand and payback before adding at most two
new offers. At most one paid construction project progresses at a time. A selected kit
prerequisite also binds one optional funding intent, with a fixed deadline and
action/failure bounds; ready science and urgent work remain eligible.

The policy only considers the current research's automation/logistic science
requirements, capped at 120 packs per type. The owned lab must be observed and
powered. Existing carried/owned supply, reservations and background jobs are
accounted for using the existing SupplyLedger and material-expansion catalog.
The supported recurring recipes are the two science packs, gears, inserters,
belts, electronic circuits and copper cable, but the narrower native corridor
recipe/geometry contract still decides actual endpoint eligibility. A recipe's
presence in the policy set is not a claim that every recipe yield or layout is
supported natively.

Valuation requires currently available, unmixed owned source output; future
production forecasts cannot finance a build. The target recipe must need that
item for the current research horizon, and both endpoints must pass the existing
identity, power, role, item and corridor checks. There is no new global scan,
resource observation, actor mutation, remote call, or model evaluation during
this calculation. Stockpiled plates alone do not establish science progress.

## Count queued production and destination inputs once

The research material bill already credits complete batches backed by current
machine inputs as forecast products. Those inputs are pledged to the credited
batches; they cannot also satisfy the additional recipe-input bill. The route
valuation now deducts only the **uncommitted input residue**, computed with the
same queued-batch arithmetic used by `SupplyLedger.capture`. Completed output,
carried/reserved stock and the separate in-flight batch retain their existing
ledger semantics. No new stock category is made spendable or persisted.

For the fixture's one-gear/one-copper red-science recipe and a remaining
120-pack horizon with no carried gears or finished packs:

| Gears / copper in destination | Old incoming gear deficit | Correct incoming gear deficit |
| --- | ---: | ---: |
| 0 / 120 | 120 | 120 |
| 40 / 40 | 40 | 80 |
| 60 / 60 | 0 | 60 |
| 60 / 40 | 20 | 60 |
| 120 / 40 | 0 | 0 |

The final row matters: removing all destination-stock credit would also be
wrong. Its 40 complete batches are already forecast, but the remaining 80 gears
are still available to fund the additional 80 batches once copper arrives.
Transforming input pairs into completed science must not change the remaining
incoming-gear requirement. Fresh preconditions recompute this accounting; a
newly supplied complete horizon still rejects a new construction payment.

Valuation diagnostics expose `destination_input_units`, `queued_input_units`,
`uncommitted_input_units`, and the basis
`net_recipe_bill_less_uncommitted_input`. They are current-observation estimates,
not proof that machines ran, that an ingredient traveled on a belt, or that
science was delivered. Native route/recipe qualification remains unchanged.
The conservation tests exercise additional recipe coefficients as arithmetic
fixtures only; they do not expand the supported native transport contract.

The shared forecast rejects duplicate ingredient entries rather than silently
collapsing them into one input. Optional paid-kit valuation also requires a
supported deterministic destination recipe; an unsupported recipe cannot use
observed input stock to justify construction. Existing queued-batch and surplus
diagnostics remain the active interface.

This correction is deliberately limited to avoiding a duplicate credit at the
valued destination. It does not make the bounded recipe bill a global factory
allocation or throughput optimizer. In particular, it does not route residual
input from a different machine, qualify unsupported sources, acquire a missing
kit, or construct an unconfigured topology.

## Declared economics, not action permission

Manual handling volume is bounded by current source supply and required input.
The policy estimates a handling batch as the smaller of 20 and the observed
catalog stack size. This is a declared policy assumption, not measured optimal
inventory packing or a guaranteed full-batch transfer.

The default manual-service estimate is a round trip between endpoint positions
plus two service allowances, using existing scheduling constants. Manhattan
distance is geometric evidence, not native pathfinding or measured elapsed time.
As an alternative, at least three successful extract and three insert attempt
records can supply median game-tick service durations. Records must be new,
verified, chronological and non-overlapping, have unique IDs/receipts, match the
native endpoint unit, canonical transfer plan ID and item-bound receipt, and be
within the 216,000-tick age/duration bounds. Only the most recent 64 history
entries are scanned. Legacy, ambiguous, duplicate, wrong-item, wrong-unit,
overlapping or stale records are not measured evidence.

The measured basis is explicitly `verified_attempt_game_ticks`. It includes the
attempt's full decision-to-receipt game interval, not exclusively walking,
transport, mining, process CPU, or wall-clock time. Transfer batch sizes may differ
from the handling assumption, so total avoided service remains an estimate even
with measured component durations. The sample counts and basis are reported.

Construction opportunity cost includes the remaining kit's catalog recipe
processing energy and bounded estimated construction service for each component.
Carrying the kit does not make its opportunity cost zero. A proposed route is
admitted only when the estimated manual burden exceeds this build estimate by
25%. This margin is a policy parameter in code, not a measured performance result.
Unknown recipe costs or unsupported material shortages reject the offer.

The complete unreserved remaining kit must be carried before the first paid
placement. When it is incomplete, the [bounded kit funding extension](SOLID_KIT_ACQUISITION_102.md)
can acquire the next prerequisite through an existing paid hand-craft or owned-output
extraction, but only if the entire bounded bill is fundable from current stock.
It reprices acquisition handling and the source items consumed by that bill.
It does not mine raw materials, finance future production, place new endpoints,
bootstrap power, share endpoints, or discover arbitrary routing. Those are real
scope limits, not native accomplishments hidden behind a flag.

## Composition, ranking and fresh dispatch

Use `solid_controller.solid_loop_type(ExistingLoop)` with the existing authorized
backend/controller composition, `factory_scheduling="ready-work"`, a private
durable checkpoint, explicit `solid_intents`, and `solid_science_policy=True`.
`tests/test_solid_investment.py` supplies executable network-free examples. This
is not a new production CLI switch or permission to change a supervisor's
immutable treatment.

The ordinary production frontier is compiled once and retained. Justified route
offers are appended rather than replacing it with a singleton. Decision-local
annotations give optional investment priority below ready science and required
supply/emergency work. Only an exact annotation produced for the current decision
can affect ranking. Forging, editing or retaining a stale annotation cannot buy
priority. The annotation never grants native action permission.

After selection, the fresh precondition observation recomputes current research,
source stock, recipe deficit, power, payback and route legality. The existing
full-kit, pending-action, receipt and one-actor guards still apply. Changed stock,
completed research, missing kit or changed endpoints prevents a new mutation.
Once a project is durably prepared or paid, its exact ownership and receipt are
preserved rather than repriced into a new project or abandoned through ID churn.
A native prepared receipt survives advancing ticks; the existing bounded replay
rule, not a newly generated receipt, determines whether it may resume.

## Partial construction and manual fallback

The corridor is built downstream-first and the sender is built last. Before the
sender is paid, a valid partial geometry with no pending native mutation cannot
carry new source items. Sequential manual source extraction and target insertion
of the intended item therefore remain allowed during this phase. This prevents
partial construction from starving the same dependency it is meant to serve.

This exception does not unlock recipe changes, component theft/demolition,
unrelated roles, malformed geometry, ambiguous/pending mutations or a connected
pipeline. Once the sender is paid, exclusive transported-item locks protect the
flow accounting. Other recipe ingredients retain their prior allowed service.
A blocked connected route is not silently demolished, reset, re-owned, or
credited with productive flow.

## Checkpoint, native and research binding

`solid_science_policy` is a strict boolean in the controller checkpoint, output
configuration and ResearchLog manifest. A legacy explicit-intent checkpoint that
lacks the field means the old false treatment only; it cannot be resumed as true.
Resume-time policy changes fail before native attachment. Ordinary controller
loads still reject the solid extension rather than dropping ownership state.

The native extension's implementation revision is now 4 within the same contract
family, reflecting the narrower partial-service guard. An already attached older
revision is refused rather than silently patched, detached or reset. This is a
fail-closed compatibility boundary, not a tested in-world migration procedure.
The base `lua/factory.lua`, ordinary CLI and supervisor remain unchanged.

The legacy production acceptance projector rejects this experimental treatment.
Do not remove that gate merely to run #92. A reviewed source/treatment preflight,
existing authorized operational handoff, deployed source/configuration readback,
native paid flow and crash qualification are still required.

## Reproduction and remaining acceptance

```sh
PYTHONPATH=src python -m pytest tests/test_solid_investment.py \
  tests/test_solid_partial_service.py tests/test_solid_route_integration.py \
  tests/test_solid_demand_accounting.py tests/test_demand_service.py -q
PYTHONPATH=src python -m pytest tests/test_solid*.py -q
```

Coverage includes a real composed input/output controller retaining ready lab
delivery at fuel levels 2, 1 and 0 while exposing a justified route, actual
controller selection/payment over an API double, fresh-state rejection, receipt
recovery after advancing ticks, preserved paid commitments, current-research
scope, locked kits, stale evidence, immutable policy and complete research chains.
The native Lua runs in a Lua 5.2 VM with API-shaped doubles. These tests do not
exercise Factorio physics, real coal distribution, research progress or native
latency. Independent review, signed publication/CI/merge, representative native
hauling comparisons and the full useful-progress window remain open.


The experimental mixed coal composition retains downstream paid-kit funding.
An incomplete coal corridor is not a downstream investment slot; fresh kit
permission applies the same distinction. Pending work from either family still
blocks new acquisition, and private coal-source stock cannot finance a kit.
