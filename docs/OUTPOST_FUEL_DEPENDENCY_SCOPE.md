# Outpost service: preserve the actual dependency scope

References: #98, #99, #93 and tracker #103. This is a follow-up to merged #120,
not a replacement of that correction and not a coal-network implementation.

## Reproduction on the #121 base

Base commit: `36083ada50137b45dd8eeb221a8ff9bd8a647b10`.

Two commissioned outposts have required raw-ore demand, empty ore chests and one
coal per drill. A valid, nearby owned furnace output buffer has a burner arm at
one coal. The fixture contains no direct input route: that would conflict with
legacy outpost ownership. The arm lies within the existing optional-visit bound.

When no plate work is required, or when current plate demand is covered by owned
buffered or spendable carried stock, planning raw-ore service nevertheless included
the downstream arm. It requested 12 coal for three consumers instead of the two
justified four-coal deficits. The accepted plan had valid current outpost/buffer
contracts and an allowed gather step. This is a demand-admission bug, not a
reproduction of the old singleton-frontier override.

`MiningOutpostPlanner._need` passed the downstream furnace role as a mandatory
service source. `service_plan` then included that source unconditionally, before
its ordinary uncovered-demand filter. A drill's current need does not establish
that its downstream output arm currently needs more production or fuel.

## Correction and unchanged invariants

A service source may now be `None`. Raw-outpost callers use this explicit absence
of a required downstream cell; the primary drill is still mandatory. Other
outposts and downstream cells join through existing uncovered-demand checks.
Input-route and output-buffer callers continue to pass their required cell.

The shared five-coal target, bounded reserve/geometry policy, actor and destination
capacity hints, unit-alias deduplication, held stock, fresh dispatch checks and
sequential receipt reconciliation are unchanged. Genuine uncovered plate demand
still includes its arm. Reserved carried plates are not counted as available
coverage. Partial held/spendable coal still permits immediate required service.

No plan IDs, native Lua, observation contracts, checkpoint schemas, persistence
barriers or production-treatment defaults change. In particular, #121's first
resumed-observation publication transaction remains byte-identical.

## Reproducible local checks

```sh
PYTHONPATH=src python -m pytest tests/test_outpost_fuel_dependency_scope.py -q
PYTHONPATH=src python -m pytest tests/test_outpost_followup_coverage.py -q
PYTHONPATH=src:tests python benchmarks/benchmark_outpost_fuel_scope.py --repetitions 100
```

The dependency tests fail 8 cases and pass 4 controls on the unmodified base;
all 12 pass after the correction. The controls retain justified downstream-arm
service and distinguish spendable from reserved plate inventory.

The additional 38 cases test background-work composition with the negotiated
craft receipt capability, malformed capacity hints, changed observations,
site-level failure preservation, depleted-tail collection, plan serialization,
alias accounting versus topology permission, and a real composed controller
reconstructed after a synthetic lost acknowledgment. These additional cases
also pass on unmodified #121: they add coverage, not 38 new behavior fixes.

For a paired benchmark, use the same benchmark and test fixture files with
`PYTHONPATH` pointing to the unmodified base's `src` and then the candidate's
`src`. Fixture setup is excluded; each scenario has one excluded warmup, separate
wall/process-CPU measurements, and nearest-rank p95. Report the exact module
hashes. Quantities change from 12 to 8 only in the unneeded-arm scenarios;
the genuine-shortfall control remains 12. These are changed policy workloads,
not an equal-work speed comparison. They do not measure native service trips,
mining, network latency, science throughput or host capacity.

## Delivery and native boundary

A source patch and green fixture tests are not reviewed SSH-signed publication,
final-head hosted CI, deployment, or native acceptance. The source/test evidence
packet binds its exact base, resulting tree, patch and executed commands.

The coal-source/multi-consumer lane in #101 remains separate. This correction
neither supplies its source/bootstrap/network contract nor qualifies native coal
flow. Keep #92/#103 and any unmet leaf acceptance open until the actual authorized
run demonstrates their specified useful progress, material flow, latency/resource
and crash/reconciliation outcomes. Preserve the original campaign and cutoff.
