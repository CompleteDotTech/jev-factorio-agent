# Consistent production capabilities

`BackgroundWorkLoop.planner_type` is the explicit planner factory used for all
independent work. The base loop selects `ReadyWorkPlanner`; output-buffer and
input-route mixins override it with their existing capability-aware planners.
Research resupply, ingredient probes, boiler service, and projected job-completion
lookahead use that same type. A planning failure never downgrades to a generic
planner that ignores an owned buffer or route.

A projected completed craft is only a planning view. Every proposed step is still
checked against the original snapshot, the job's output lock, current native
preconditions, and the controller's ownership and write-ahead barriers. A second
craft or construction is still forbidden while the acknowledged craft runs.
There are no added observations, native mutations, configuration flags, schema
changes, or relaxed receipt rules in this change.

The regression in the September 22 review was small furnace-chest collections
while a science craft ran. The generic planner's batching applies to furnaces,
not chests. The composed planner now retains the buffer's batch threshold during
background work and research prefetch. A stopped producer's real tail is still
collectable; the scheduler must not wait for output that cannot arrive.

Validation:

```sh
PYTHONPATH=src python -m pytest tests/test_planner_consistency.py \
  tests/test_background_work.py tests/test_output_buffer_integration.py \
  tests/test_input_route_integration.py -q
PYTHONPATH=src python -m pytest tests/ -q
```

The fixtures are synthetic and do not establish a native throughput improvement.
CI retains the exact tracked source, its checksum, the tested commit, and JUnit
results for seven days. It does not archive an untracked environment, credentials,
or live game saves. Review or replay against a live campaign remains a separate
operation; this change does not restart or deploy to that campaign.
