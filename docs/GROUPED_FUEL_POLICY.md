# Bounded grouped manual fuel service (#99)

This is a source/fixture-tested manual service policy, not coal automation or
native performance acceptance. It builds on the corrected maintenance semantics
from #98; it does not replace ready science or ready owned output with a global
fuel-first override.

## Current action and demand accounting

The required small burner is retained first. Other due burners enter only from
currently demanded, uncovered owned production cells. One native unit cannot
multiply its deficit through role aliases, and inconsistent aliases fail closed
even when one alias reports fuel above the refill threshold. The original
five-coal target for burner inserters/mining drills is unchanged.

The group is bounded to 16 consumers, 50 acquired coal, 64 tiles per optional
service leg and 7,200 estimated extra service ticks. Optional legs whose known
geometry exceeds those bounds are deferred with a fixed reason. Unknown geometry
is not zero cost or a successful pathfinding result. Exhausted optional transfer
budgets are excluded; an exhausted required transfer fails closed. These are
admission bounds, not proof that a native walk is reachable.

Existing `SupplyLedger` reservations and background locks remain unavailable.
Any spendable carried coal is used for one bounded transfer before gathering
more. When no spendable stock remains, the acquisition target includes observed
held coal without spending it. Optional observed inventory insertability limits
new acquisition. Absence of that field remains unknown, not infinite capacity;
the hard 50-item policy bound and fresh native preconditions remain in force.

Every action still passes through initial observation, planning, fresh
preconditions, dispatch and receipt verification. A group is advisory planning
context, not a multi-actor dispatch or a speculative inventory credit. Each
subsequent service decision recomputes from fresh facts.

## Reserve estimation and its limits

`FuelHistory` consumes already validated controller observations. It creates no
extra backend calls, checkpoints, native state or persistent identity. It keeps
at most 64 consumers and 17 samples per consumer, scans at most 512 entity rows,
and requires at least 600 ticks and two positive depletion intervals. The context
is bound to the session, world/version, actor, player, surface and force, plus
native consumer identity, type and position. Refills, empty/censored fuel,
identity changes, conflicting aliases, missing/invalid fields, regressing clocks,
gaps over 7,200 ticks and ambiguous/prepared mutations discard history. Restart
starts cold. The dynamic snapshot attribute is excluded from `for_jev()`.

The rate is explicitly an **observed inventory-depletion proxy**, not an
attributed burner-consumption counter. Simultaneous consumption and insertion
can be missed; the policy never claims guaranteed coverage or native payback.
Unknown, stale or incomplete rates produce no invented optional reserve.

Let `D` be the admitted combined deficit, `r` the sum of eligible depletion
estimates, and `L0` the estimated coal-site walk, service time and acquisition time
for `D`. The existing catalog-policy constants are used as estimates, not measured
travel/mining times. With `m` estimated mining ticks per item, optional reserve is:

```
ceil(r * L0 / (1 - r * m))
```

This accounts for the estimated time to gather the reserve itself. The result
is capped at 20 optional coal, remaining room below the 50-coal total bound and
observed insertable capacity. If `r * m >= 1`, geometry/rates are unknown, or a
science refill/power deadline would be jeopardized, optional reserve is zero.
A capped reserve is not represented as satisfying an uncapped coverage forecast.
Current science/power safety remains with the deterministic scheduling policy.

Diagnostics use `fuel_service.schema = 2`: deficit, acquisition target, whether
this plan actually gathers, held/spendable coal, optional reserve and its basis,
lead estimate, visit-order basis, bounds and fixed deferred-reason counts. The
individual rates and native unit identities remain private derived context.

## Failure budgets and crash boundaries

Changing a reserve changes the existing inventory-target plan ID. The selection
boundary therefore retains a conservative floor from old coal acquisition
failure counts for the same observed resource site. It does not rename old plans,
clear failures, create another persistent ledger or reset campaign history.
Unattributed legacy coal failures remain a floor. Other observed sites are not
charged unrelated attributed failures.

The existing separately receipt-proven partial-gather/remainder recovery path
retains its prior semantics; ordinary reserve compilation cannot create that
path. Both normal selection and passive-wait yielding use the common failure
counter. Fresh decision context is copied from authoritative memory, and failure
counts survive normal checkpoint/restart.

No authoritative durability boundary was weakened. The new rate history is
reproducible scheduling context only; it cannot release pending work, authorize a
transfer, modify native inventories or prove successful movement.

## Reproduction and remaining acceptance

```
PYTHONPATH=src python -m pytest tests/test_grouped_fuel_service.py \
  tests/test_fuel_service_bounds.py tests/test_fuel_history.py \
  tests/test_fuel_reserves.py tests/test_fuel_controller_context.py -q
```

The new negative fixtures reproduced four failures against the recovered prior
packet before correction: a high-stock conflicting alias, a distant optional
consumer, an exhausted optional transfer, and an exhausted primary transfer.
Other fixtures cover finite capacity, held/partial coal, science and boiler
priority, rate invalidation, bounded history, same-site quantity changes,
checkpoint preservation and no extra backend observations.

Independent review, configured SSH-signed publication, hosted matrix checks,
native capacity/true travel measurements, wider production scheduling scenarios
and matched native trip/science-throughput acceptance remain required. This
small-burner policy is not the paid coal/distribution capability in #100/#101.
