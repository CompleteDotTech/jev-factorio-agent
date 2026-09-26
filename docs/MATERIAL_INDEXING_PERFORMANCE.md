# Decision-local material indexes

Candidate against controller `e3d042c1f32cededfc924d4814224fe97133461f`.
Integrated and tested in an isolated checkout. This is a bounded portion of
the production-latency task, not completion of that task or a live deployment.

## Implementation

`Catalog.material_demands()` previously called `enabled()` for every recipe;
locked recipes could each scan all technology effects. It also called
`recipe_for()` for each product, scanning the complete recipe collection each
time. It now builds read-only product-choice and recipe-unlock lookup structures
once per synchronous expansion, turns the current researched list into a set,
and memoizes alternative selection only inside the same invocation.

Recipe choices retain insertion order, ignore hidden/invalid probabilistic
outputs as before, and include disabled alternatives so the existing preferred
recipe rules do not silently select another recipe. Duplicate product or unlock
effects do not duplicate a recipe/technology choice. The standalone public
`recipe_for()`, `unlocks()` and `enabled()` remain current-data queries; they have
not been globally memoized. Catalog's public dictionaries and serialized shape
are unchanged. The structures are read-only, but their recipe dictionary values
are not deeply frozen; nothing retains them across material-planning calls.

`requirements()` builds an enabled-product lookup once per call instead of
rescanning every recipe for every recursive expansion. Shared stock accounting,
co-products, cycle/expansion limits, explicit alternative selection, reservations,
rounding, and inventory normalization remain in the same solver. No world-state,
affordability, placement, native receipts or action preconditions are cached.

Current research, inventory and catalog edits are read again on the next call.
No persistent or decision-crossing cache invalidation protocol is introduced.
The candidate does not optimize capital-frontier compilation, failure-identity
variants, standalone catalog lookups, controller scheduling, or native observation.
Those require additional profiling and regression coverage in a complete checkout.

## Validation

108 added pytest cases compare results or exceptions to exact reference files
from the base revision and exercise ordering/ambiguity/research/inventory/catalog
changes, co-products, duplicate products, reservations and expansion guards.
Non-string unlock effect values retain the old equality-scan behavior: they
cannot match a native recipe name and are ignored by the index.
The reference fixtures retain Git blob identities:

- catalog.py: `ae4ab9c8c592504faa9adda2d1dfbd99945c8b50`
- materials.py: `241d7db19ee675662b3ed31003f14cb82b4b0e9c`

```sh
PYTHONPATH=src python -m pytest tests/test_material_indexing.py -q
PYTHONPATH=src python benchmarks/benchmark_material_indexing.py --output /private/path/new-benchmark.json
```

The original package also ran 11 extracted existing material-solver cases.
Integration validation runs the complete, unmodified repository suite instead
of treating that supplemental subset as a full-suite result.

Final local Linux integration: **2,578 passed, 78 skipped in 104.25 seconds**
on Python 3.12.3. An earlier run had two failures in existing quiescence
freshness tests; their runtime suite plus the indexing tests passed on a focused
repeat (124 passed), and the final full run passed without relaxing any guard.
The underlying intermittent freshness failure was not isolated by this work.

The tests cover valid native-shaped catalog data and selected negative cases,
not every malformed nested catalog accepted by the current weak input validator
or arbitrary Catalog subclasses overriding selection methods. Native staging
and normal source/runtime-bound acceptance remain separate deployment gates.

## Measured microbenchmark

Synthetic seed 1729, eight simultaneous demands, acyclic dependency chains,
partially populated inventory and varying input/output ratios. These are
**synthetic fixtures, not captured or sanitized production snapshots**.
Each size used 31 alternating A/B then B/A pairs after two warmups per arm. The
measurement is the entire `material_demands` call, including all per-call index
construction. Catalog loading is excluded equally from both arms. Times use
`perf_counter_ns`; raw samples, exact hashes and separate cProfile output are in
the benchmark JSON emitted by the command above. Integration rerun on local
Linux used Python 3.12.3. This is not the production VM/interpreter or its
contention profile; the raw integration output is retained privately.

| Recipes / technologies | n per arm | Before median ms | After median ms | Before p95 ms | After p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 128 / 128 | 31 | 5.886998 | 0.519080 | 6.960918 | 0.762424 |
| 384 / 384 | 31 | 46.201341 | 1.323485 | 50.107902 | 1.604779 |
| 768 / 768 | 31 | 191.447414 | 2.765711 | 203.627328 | 5.453260 |

These results support lower cost for these material-expansion fixtures only.
They do **not** establish a reduction in the historical 6.96-second candidate
median, 15.39-second inter-action gap, live fuel trips, observation RPC latency,
starvation, production throughput or campaign progress. No performance SLA or
whole-controller speedup is claimed.

## Deployment remains separate

Publication requires full repository tests, independent exact-head review,
signed commits, scoped PR checks and merge-tree verification. Deployment must
use the existing source/runtime-bound staged deployment and reconciliation gates. Preserve native
world/session, checkpoint history, pending actions and authoritative original
cutoff. Observe two fresh successful autonomous actions and read back OBS scene,
streaming and audio preservation before reporting a live cutover. Local tests
and microbenchmarks do not establish that any production gate has been met.
