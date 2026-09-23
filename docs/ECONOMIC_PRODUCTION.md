# Economic production and bounded capacity

Ready-work planners prioritize the native technology unlocking the basic assembler
before recursively pursuing the rocket-silo dependency chain. Existing research
is never cancelled. Player binding, native handcraft queues, boiler service,
research prerequisites, recipe unlocks, material payment, and normal research
completion still belong to the existing engine-facing contract. The serial policy
is unchanged. The next-research preview uses the same capability priority.

## Recurring work

Deterministic single-product solid recipes for recurring intermediates and science
may use a dedicated assembler instead of indefinitely taking the handcraft path.
The remaining current science workload is bounded to 200 research units and 2,000
outputs per product, expanded once against the shared supply ledger. Investment
compares native recipe work and machine speed against recipe-derived construction
material/processing effort plus explicit policy service allowances. Already carried
machine kits have no new acquisition cost. A minimum recurring demand and payback
margin avoid building a factory for ten bootstrap packs. These are labeled policy
estimates, not promises of wall-time speedup: an assembler frees handcraft-queue
capacity and overlaps production even when its individual recipe speed is slower.

Every dedicated machine is paid, configured, connected to normal power or fuel,
supplied with carried materials, and observed producing real output. Existing
machines are reused even for handcraftable recipes. Building a machine's kit uses
a guarded prerequisite path so it cannot recursively invest in another assembler
to manufacture its own construction ingredients. Assembler output collection uses
the same batch threshold as other actively producing solid machines. Wait targets
are capped by actual paid input and the currently crafting batch, including native
stack-cap reductions; time passing alone never verifies output.

## Additional capacity

A supplied, active producer with production history and sustained demand may gain
one additional dedicated producer at `capacity:<recipe>:2`. Expansion requires
spare carried inputs, sufficient current input, working fuel/power, and estimated
remaining processing benefit above construction/service cost. Input-starved,
unpowered, unfueled, short-demand, or unproven producers are not capacity evidence.
The additional machine is placed through the existing paid native builder and is
serviced using ordinary individually verified transfers. Actual output, not a
building count, makes its collection available. It is reused and replenished;
no unbounded chain of new capacity roles is created.

A faster unlocked machine can be selected for a new additional cell when justified
by native recipe cost and speed. Existing paid machines are never automatically
replaced or reassigned. Main stone-furnace input/output cells retain their identity
and their existing buffer/route ownership. Additional generic cells are explicitly
batch-serviced; they are not advertised as automatically belt-commissioned cells.
No protocol widening, silent checkpoint migration, or weakened flow proof is used
to claim otherwise. The native dispatcher, output locks, pending-action barriers,
and original campaign deadlines remain authoritative in every mode.

## Validation

`tests/test_economic_production.py` checks research priority and non-cancellation,
bootstrap versus recurring work, native payment, existing-machine reuse, actual
output bounds, batched assembly collection, construction recursion, unsupported
investments, supplied-capacity gates, one-extra-cell bounds, additive upgrades,
model-capability composition, and actual-controller supply/verification/reuse.
A two-batch synthetic controller run produces and collects 40 science packs with
one paid assembler and 40 consumed plates, without handcrafting. A paired supplied
cell fixture checks that extra output is observed before collection; placement
and a feed receipt alone are insufficient. These are contract/behavior tests, not
native Factorio throughput measurements. Matched-save native experiments remain
necessary to measure starvation, milestone times, service travel, and useful output.

## Staged bootstrap and committed investment

Stage 3 removes the permanent self-ingredient exclusion for supported recurring
crafting recipes. The planner acquires one kit with economic recursion disabled,
then retains a durable objective through paid placement, configuration, supply,
and observed production. Useful investment offers can compete with optional work
while research runs; urgent supply and existing native safety barriers remain
higher priority. See [CAPITAL_INVESTMENTS.md](CAPITAL_INVESTMENTS.md) for policy,
checkpoint compatibility, evidence, failure budgets, and native benchmark requirements.
