# Bounded paid kit acquisition for downstream logistics

Related: #102, with #100/#98 foundations and #93/#95 durability/planning boundaries.
This is source and fixture work, not native acceptance for #92 or completion of #103.
The opt-in solid-science composition and native straight-corridor contract remain
unchanged. No new production switch, native Lua command, provider call or operational
authority is introduced.

## Reproduced gap

The earlier policy could value a stocked-source/starved-science corridor but drop
it whenever either inserters or belts were missing. Paired fixtures remove only
inserters, only belts, or both, retaining observed ingredients and profitable
service history. Each now exposes an ordinary paid prerequisite instead of
silently requiring a manually supplied complete kit.

## Funding and cost contract

`planning.solid_funding.acquire` performs a bounded feasibility calculation using
the shared `SupplyLedger`. Only carried unreserved items and currently observed,
uniquely identified, permitted owned output are usable. Machine inputs, queued
or in-flight products, private qualification stock, foreign or aliased inventory,
raw mining, research unlocks, exploration and new machine construction cannot
finance the bill. Background crafting and an urgent low-fuel boiler defer optional
funding. The destination's recipe, current research deficit, power and payback
still must qualify under the downstream investment policy.

Supported acquisition actions are the existing `factory_extract` and
`factory_craft`. Hand recipes must be enabled, deterministic, item-only,
single-output and hand-craftable. The compiler caps expansion at 128 visits, each
quantity at 200, each craft at 20 batches and the full acquisition at 32 actions
and 216,000 estimated game ticks. Recipe cycles and unsupported bills reject the
offer. These are policy/complexity bounds, not performance measurements.

The internal inventory simulation is never published as stock or permission.
Only its first currently legal action becomes a plan. Every following decision
rebuilds the bill from a new observation. Receipt verification and stock deductions
remain in the ordinary controller/native path; there is no new inventory ledger.

Value excludes source items that the kit itself would consume. Construction cost
already includes catalog recipe processing, so the additional acquisition charge
contains only the estimated handling and geometry-based travel. No nested cost is
added twice. Declared economics continue to require the existing 25% margin.
Unknown actor geometry cannot become free travel. Known item headroom must suffice
for the next output; absent per-item capacity is explicitly unknown and native
admission must still validate it. Headroom is not stock.

## Stable intent and existing failure budgets

One `solid-project:<intent digest>:kit` identity covers changing prerequisite
recipes, quantities, ticks and native unit changes. Its digest is the same stable
logical intent used by construction, while live layout and endpoint unit bindings
are separately validated. Acquiring a kit cannot buy a new allowance for an
exhausted corridor component or an exhausted ordinary extraction/craft. Existing
ordinary and kit failure counts are read without renaming, clearing or migrating
history. Counts are not duplicated into a second failure ledger.

Selection creates a `solid_funding` checkpoint value containing the intent,
source/target units, route/layout, catalog digest, original start/deadline and
admitted-action count. Merely inspecting candidates creates no commitment. The
ordinary synchronous plan-commit barrier saves the funding identity before the
fresh precondition observation; the ordinary prepared-action barrier precedes
any mutation. A changed precondition does not receive a new ID or deadline.

A funding intent prevents competing optional capital/funding projects from being
admitted. It does not reserve an absent complete kit or suppress ready science,
required supply, boiler protection, or ordinary safe work. Actual carried
reservations, launch payload holds and background-output locks remain authoritative.
Once construction begins, the existing whole-remaining-kit ownership lock applies.
The funding state is released only after the paid commitment is observed and no
pending mutation remains to reconcile.

Abandonment preserves the failure budget. If an unexecuted active prerequisite
still exists, its funding binding remains through every checkpoint until the
ordinary failed-precondition/plan-clear path has removed the plan. This prevents
an intermediate saved active kit from outliving its required funding identity.
Ambiguous pending work never releases its bindings merely because demand or the
deadline changed. Its receipt must be reconciled first.

## Freshness and compatibility

Fresh checks recompute the same selected intent's economics and next action,
not whichever unrelated intent happens to sort first now. They bind the original
receipt, exact action/parameters/costs/effect, research and relevant recipe catalog.
Changed power, stock, demand, recipe, locked supply, failure budgets, deadline or
physical endpoints prevents dispatch. Sequential actor execution is unchanged.

Older valid solid checkpoints without `solid_funding` load with `None`. A checkpoint
carrying a kit identity must retain the matching funding state and annotation;
removing both annotations cannot disguise it as ordinary unowned work. Invalid
bounds, booleans in integer fields, altered identities, policy changes and
conflicting capital commitments are rejected. An older source version cannot
silently ignore this extra controller field: use the established reviewed
migration/handoff rather than deleting state to make a rollback load.

No authoritative write is asynchronous, debounced or removed. Native Lua and
installation version, the supervisor, production CLI and legacy acceptance
projector are untouched. Their existing qualification barriers remain in force.

## Deterministic validation

```sh
python -m pip install -e '.[test]'
PYTHONPATH=src python -m pytest tests/test_solid_kit_acquisition.py \
  tests/test_solid_investment.py tests/test_solid_route_integration.py \
  tests/test_solid_resume_observation_transaction.py -q
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_dashboard_browser.py -q
python benchmarks/solid_kit_acquisition.py
```

Fixtures cover acquisition-to-paid-construction, the unmodified full planner,
composed input/output science priority with low fuel, save/reload and prepared
receipt recovery, lost acknowledgements, corrupt checkpoints, exhausted budgets,
changed demand/catalog/deadline, competing intents, held stock and known headroom.
The benchmark runs a synthetic backend and emits no native improvement claim.
The non-browser command does not qualify browser behavior; optional skips are
not passing tests. Hosted exact-head checks and independent review are separate.

## Remaining acceptance and rollback

Native validation still must prove actual item payments, route flow, useful science
contribution, reduced hauling and restart reconciliation in an authorized isolated
world and the full original-window 30-minute #92 campaign run. Fixture inventory
arithmetic is not Factorio-engine validation. This does not build #101's coal miner
or multi-consumer fuel network, provide arbitrary automatic intent discovery, or
implement raw-resource/new-power acquisition. Do not close those obligations.

Before publication, reverse only the exact patch in its owned isolated worktree.
After publication, use a reviewed revert through normal repository controls.
For any deployed treatment, use the established service-owner handoff and retain
pending receipts, ownership, failure history and the original campaign cutoff.
Do not hot-swap immutable runtime source, remove a funded pending identity,
reset a campaign or restart a shared host. The old source may reject the extended
checkpoint, so rollback compatibility must be qualified before deployment.
