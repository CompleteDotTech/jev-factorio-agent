# Experimental paid solid corridors (#100)

This implements a bounded solid-route foundation in Python and Lua. It is
**not a completed #100 native acceptance result**, a production deployment, a
coal-mining network (#101), or an automatic downstream investment policy (#102).
The production CLI and supervisor do not expose this treatment. All new runtime
paths are opt-in through the Python composition API; existing CLI runs remain
unchanged. A native qualification and reviewed immutable-treatment handoff are
required before production adoption.

## Supported implementation contract

A route consists of two normal-quality ordinary electric inserters and 1–24
normal-quality ordinary transport belts in one cardinal straight line. The
inserters' pickup-facing direction is opposite belt motion. Components occupy
half-tile-centered adjacent positions. Construction is downstream-first: receive
inserter, last belt through first belt, then send inserter. Geometry is bounded
and deterministic; orientation, endpoint footprints, collisions, neighbor joins,
inserter targets and owned electric supply are checked, not inferred from a
screenshot. Directions use the Factorio 2.0 numeric convention. Factorio 1.1,
modded prototypes and engine behavior remain unqualified for this extension.

At most four explicit intents are accepted. Source and target roles cannot be
shared between intents; native identity aliases are rejected. Supported source
inventories are owned wooden/iron/steel chests or assembler-1/assembler-2 output.
A destination is an owned assembler-1/assembler-2 input inventory, or a supported
coal-only fuel inventory (stone/steel furnace, burner drill/inserter or boiler).
The source and destination must belong to the bound actor's same surface and
force. The actor must satisfy the existing fair-action controls.

For an assembler, only fixed deterministic solid recipes with one unit-yield
product and bounded integer ingredients are admitted. Fluids, probability/range
outputs, non-unit yields, productivity/module modifiers and non-normal quality
are rejected. This includes a possible gear-output to red-science-input corridor;
it does not include every science recipe or arbitrary factory graphs. Source
inventories, belts and held stacks must contain only the intended normal-quality
item. Destination item compatibility and insertability are checked.

Turns, splitters, underground belts, joined branches, shared consumers, mixed
lanes, arbitrary chest replenishment, demolition, adoption of foreign entities,
coal-source construction and automatic route demand discovery are out of scope.
Unsupported topology is an explicit unavailability/fault, not permission to place
an approximation. Foreign transport close to endpoints/corridors is conservatively
rejected. A route can therefore be rejected even where a larger routing algorithm
could find a safe path. The 512-probe per-intent search and bounded neighborhood
reads never grow into a global entity scan.

## Composition and candidate admission

`solid_controller.solid_loop_type(base)` composes with an existing hierarchical
controller class. It requires explicit `solid_intents`, `factory_scheduling="ready-work"`
and a durable checkpoint. An intent has exactly `source`, `target`, `item`, and
`destination` (input or fuel). The new backend adapter is attached only by this
opt-in composition. See `tests/test_solid_route_integration.py` for executable,
network-free examples. These are fixtures, not production launch instructions.

The compiler appends at most one feasible next component per route to the
existing production frontier; it does not replace ready science with an exclusive
infrastructure plan. Existing candidate ranking, investment admission and failure
budgets still apply. All remaining components of a route must be affordable, not
merely its next component. SupplyLedger accounts for held inventory and background
crafting; the route's durable remaining kit remains reserved when a one-component
plan is cleared. An intent requests the foundation capability explicitly:
**payback is not calculated**, and no automatic #101/#102 candidate is claimed.

A project's failure identity binds the immutable source/target roles, transported
item, destination inventory and component ordinal, rather than observation tick,
stock quantity or replaceable endpoint unit IDs. Changing geometry/identity cannot
silently erase the same requested project's failure budget. The actual command
still binds the exact observed physical layout, route, part and receipt.

## Native command and persistence boundaries

`factory_solid_build` accepts exactly `route`, `layout`, `part`, `receipt`.
The actual Python NativeFactory.call/command path prints and decodes the native
preparation reply, then performs the existing fair approach and ordinary fair
placement sequentially. A returned message is an acknowledgment, never proof of
payment or transport. Observation validates that proof separately.

The control sequence remains observation → planning → fresh preconditions →
durable prepared action → native preparation/approach/placement → durable returned
or ambiguous state → fresh observation → verified receipt. Native preparation
freezes the layout and reserves the exact receipt. A receipt cannot be reused for
another component or corridor. Existing `fair.place` owns ordinary inventory
payment; this extension does not create free construction items.

| Boundary | Behavior |
| --- | --- |
| Before preparation | Revalidate actor, epoch, geometry, full remaining normal-quality kit and pending state. |
| Native prepared journal | May replay the exact command once when fresh native evidence proves placement has not started. The replay budget is checkpointed first. |
| Dispatch began, result ambiguous | Stop for reconciliation; never assume failure or automatically place again. |
| Paid placement returned, ownership registration interrupted | Reconcile only the exact saved placed journal with its paid inventory delta and matching entity. |
| Lost acknowledgment after persisted receipt | Verify the existing paid component through observation; no second debit. |
| Native committed route missing from controller checkpoint | Accept only when it matches this controller's durable pending command. Otherwise stop rather than adopt it. |
| Paid prefix regresses, endpoint replaced, epoch differs | Preserve pending/ownership/failure history and enter an uncertain state. |
| Checkpoint/fsync failure | Propagate the durability failure; do not authorize another mutation. |

The controller checkpoint extension has `solid_routes_schema`, `solid_intents`,
`solid_epoch`, and `solid_commitments`. Commitments contain detached immutable
layout/endpoints/steps and the monotone paid prefix, not advisory flow metrics.
The intent and epoch are bound before inner composed observers can write their
own checkpoint. Extension-aware loads validate all fields; a legacy reader
rejects the extension instead of discarding ownership. No silent migration of an
existing campaign, native world reset, cutoff change or failure-history reset is
implemented. `lua/factory.lua` is unchanged. Actual native game-save/controller
crash consistency still requires isolated engine qualification.

Manual configure/transfer actions that would change a committed endpoint recipe
or the transported-item inventory are rejected. Other required recipe ingredients
can still be supplied by ordinary allowed transfers. Topology/ownership and
conservation faults are sticky; replenishing a source or recovery from unexplained
external edits requires a separately reviewed reconciliation path. A spatial
reservation protocol with every other future capital builder is not claimed;
conflicting placement is rejected or reported as a route fault, not demolished.

## What flow evidence means

The native installer also binds the explicit `straight-solid-corridor-v1` contract
family; a different experimental protocol-1 implementation is not a compatible
reattachment. Do not stack implementations that share command/storage names.

The observer returns a versioned coherent route envelope bound to session, game
tick, actor, surface and force. It records proposed layout, paid prefix, connected
topology and flow separately. Empty Lua-map JSON ambiguity is normalized only at
the adapter boundary; malformed nonempty values, stale ticks, inconsistent
identity, duplicate receipts and ownership aliases remain errors.

Flow uses owned source-output, target-input, both belt lanes and inserter-held
inventory samples. Assembler counters include completed products and the current
craft transition, so input consumed at craft start is not mistaken for loss.
Changes to recipes, counter resets, unexplained additions, quality/mixed items,
broken topology and negative conservation stop verification.

For assembler input, the method is `stoichiometric_balance`. For fuel, only
positive destination stock changes are a conservative observed delivery lower
bound (`exclusive_fuel_lower_bound`). Its `unattributed_loss` is **not attributed
coal burn**. Fuel can be delivered and consumed between snapshots without its
full quantity being provable. Initial items in the pipeline are excluded from
new delivery credit. This accounting is not cryptographic proof against an
external world editor; matching simultaneous unauthorized edits can be
unobservable. Qualified native trials require exclusive, controlled ownership.

The current-flow predicate requires connected live identity, reason
`observing_flow`, at least three positive samples, at least three new units sent
and received, at least 120 elapsed game ticks, and a positive delivery no older
than 600 ticks. Those are bounded commissioning rules, not a sustained production
or science acceptance window. Backpressure, missing power or source depletion
keeps historical evidence but makes current-flow verification false. Old delivery
counts cannot indefinitely mark an idle route productive. Fully paid routes are
not rebuilt or subjected to an endless wait action when blocked.

Diagnostics are bounded by the four intents and an explicit constant reason
allowlist. Candidate/model summaries include item, owned roles, paid/total counts,
state, reason and current-flow status. Raw native identifiers remain private
runtime evidence; do not publish raw snapshots or session logs.

## Research and acceptance boundary

An optional ResearchLog must explicitly declare `RunConfiguration(solid_routes=True,
factory_scheduling="ready-work", ...)` before the adapter attaches. Ordinary runs
retain the default false flag. The configuration property exposes the existing
frozen configuration, not a mutable manifest. Existing old records remain
readable but absence is not evidence of treatment equivalence.

The production acceptance-capture schema does not yet support this treatment.
It now **rejects** solid-route records/envelopes/configurations rather than quietly
stripping their evidence and evaluating the remainder. The CLI, supervisor,
preflight and #92 operational handoff still need reviewed treatment support,
immutable intent/source binding, native deployment readback and rollback. This
experimental source cannot itself authorize a new production treatment.

## Reproducible offline checks

```sh
PYTHONPATH=src python -m pytest tests/test_solid*.py tests/test_prevalidation.py -q
PYTHONPATH=src:tests python benchmarks/solid_routes_fixture.py --repetitions 10
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_dashboard_browser.py -q
PYTHONPATH=src python -m pytest tests/ -q
python -m compileall -q src tests benchmarks
```

The first command covers Python contracts, the real Lua source in Lupa's Lua 5.2,
real NativeFactory.call serialization over fake RCON, and composed-controller
write-ahead/receipt behavior. The fixture reports logical call/payment counts and
wall/process CPU durations for identical gear-input and coal-fuel API doubles.
Synthetic inventory transitions are not belt physics, native trips, game time or
research throughput. Do not count a Lua-double test as an engine test. The full
suite additionally needs the repository's optional dependencies and Chromium;
exclude or report unavailable cases explicitly rather than claiming a full pass.

## Remaining native and delivery gates

#100 requires an isolated native fixture with supported endpoints, ordinary paid
placement, real flow beyond initial stock, all directions, meaningful fault and
crash/save/restart boundaries, followed by normal reviewed SSH-signed publication
and campaign rollout qualification. Source tests do not satisfy those outcomes.

#101 still needs coal mining/bootstrap, network self-fuel accounting and fair
multi-consumer distribution; this contract deliberately does not share endpoints.
#102 still needs actual recurring dependency discovery, haul/payback economics,
route selection and proof of contribution to science/research. #92 still needs
matched component measurements and its full authorized 30-minute useful-progress
window. Keep the tracker and unmet leaves open until their stated criteria pass.
