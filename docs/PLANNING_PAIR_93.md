# Issue #93 paired composed-planner fixture

Run from a clean checkout with the test dependencies installed:

```sh
PYTHONPATH=src:tests python benchmarks/benchmark_composed_planning.py --paired --samples 100 > planning-pair.json
```

The benchmark constructs the same input/output route controller and the same
ready-science and ready-science-crafting observations in both arms. `current`
uses the corrected demand-aware scheduler once. `redundant_control` deliberately
compiles and discards one complete frontier, then compiles the same final
frontier. This reconstructs the removed redundant *work* with equal current
scheduling semantics. It is not an unmodified historical source revision, which
had different fuel-first scheduling and cannot serve as an equal-frontier
comparison. The benchmark fails if any paired final candidate, order, plan
body, or blocker differs.

Each pair alternates arm order, after one unmeasured warm-up per arm. The JSON
records process CPU and wall-clock nanoseconds, planner constructor and material
expansion invocation counts, median and nearest-rank p95 per arm, and paired
control-minus-current deltas. It fingerprints the package/test Python tree,
benchmark file, and pyproject before planner imports and checks for changes
after sampling. Use the recorded interpreter, platform and logical CPU count
when comparing runs. Process clocks can be coarse on some hosts; small timing
differences are descriptive, not a test threshold.

These deterministic fixtures cannot establish native Factorio planning latency
or useful gameplay throughput. Issue #93 requires separately reviewed native
measurements on the deployed exact source, with capacity and PR #91 deployment
conditions recorded in the staged acceptance issue.
