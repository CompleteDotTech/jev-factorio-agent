# Stage 4: measured capacity and checkpoint overhead

This change extends current `main` after Stages 1, 2, and 3, including Stage 3's
capital-investment lifecycle. Stage 4 preserves that state and behavior. It does
not acquire an additional optional capacity kit; it may place an already carried
kit, then uses the existing paid configuration, supply, collection, and
verification paths. The single extra role per recipe remains
`capacity:<recipe>:2`. No new CLI flag or checkpoint fields are introduced. No
native mutation protocol, world, game speed, polling cadence, research
prerequisites, background lock, or campaign deadline is changed.

## Sampled capacity evidence

`planning/capacity_evidence.py` consumes the controller's existing validated
observations. It never calls Factorio. History is process-local and deliberately
starts empty after reconstruction. It tracks at most 64 producers and 32 samples
per producer. Healthy samples are spaced at least 120 game ticks apart. A new
capacity investment requires at least three samples spanning 1,200 ticks, with
no observation gap exceeding 900 ticks. These constants are conservative policy
thresholds, not benchmarks or measurements of optimal settings.

The same native unit, machine name, configured recipe and nominal recipe rate
must persist. Missing/unsupported fields, counter regression, role disappearance,
replacement, tick regression or session changes discard old evidence. Even a
short observed power/fuel/input outage between regular samples invalidates a
healthy window. Duplicate ticks do not manufacture samples. Duplicate entity
aliases cannot be counted twice.

The initial measurement policy supports only enabled deterministic solid recipes
with one output of exactly one unit. It avoids assuming how a multi-product or
multi-unit recipe's lifetime counter should translate to attributable output.
The current engine-facing observer already captures `products_finished`, input,
output, fuel, energy and crafting status; this PR adds no native API calls.

An eligible window needs:

- At least two finished products and two units removed from the observed cell,
  not just placed machines or commands returning successfully.
- At least 80% active samples, with no sampled input or power/fuel starvation.
- Less than ten units currently buffered and no more than one unit of net output
  accumulation across the window. For output-buffer cells, the owned chest is
  included; moving products from the furnace into its chest is not downstream
  removal.
- Measured finished products per game tick between 75% and 125% of the catalog
  rate. Very low/unknown rates and unsupported modifiers do not justify adding
  machines.

Activity fractions are **point-sampled fractions, not continuous utilization**.
Removal is an inventory/counter balance, not proof of the downstream consumer or
end-to-end throughput. Unobserved outages can exist between samples. This evidence
is a scheduling gate, never inventory authority or a native flow certificate.

The economic planner retains its current workload, spare-input, paid-producer,
power/fuel and payback checks. It estimates additional benefit using the measured
existing rate plus the proposed machine's catalog rate. The latter is a forecast,
not proof of future output. New construction requires a carried kit; existing paid
extra cells continue to be serviced even while new history warms up. A suitable
investment can also use a research-wait slot, after useful production/refill work
has already been considered. At most eight producer roles are considered there.
The fresh pre-dispatch observation rechecks the measured source identity and
health before a new additional-machine placement.

Derived evidence is attached as private planner context rather than modifying
native snapshot fields. Summaries are recorded separately in gameplay records,
`observation_validated` research events, and the selected investment's materials.
Replaying an old checkpoint does not recover an unrecorded observation window or
authorize redispatch of an ambiguous placement.

## Checkpoint writes

`checkpoint_io.py` centralizes the existing atomic write path. Every changed
serialized state is written, flushed, file-synced, atomically replaced, and on
POSIX directory-synced. The background, input-route and outpost memory extensions
now inherit that one durable implementation instead of syncing the same directory
again for each extension layer.

Only an exact repeat of successfully persisted bytes can be coalesced. The cache
also binds the absolute destination and its device, inode, size, mtime and ctime.
Missing, externally edited, replaced or symlinked destinations invalidate it.
A fresh memory instance starts without a cache. The cache is cleared on every
save failure and is never serialized. An I/O failure poisons the owning controller
instance so it cannot continue into another game observation or mutation.

There is no debounce timer, dropped poll counter, ignored dispatch substage, or
relaxed acknowledgement barrier. `prepared`, approach/transfer substages,
`returned`, verification, ownership commitments and reservation changes remain
changed states and therefore durable writes. An identical state can be skipped
only because precisely that state is already durable at the same destination.
A research log event still records each checkpoint request and whether it actually
wrote, was unchanged, was disabled or failed.

This assumes the existing single-writer ownership model. Stat checks do not
replace the supervisor lock and are not a defense against a hostile concurrent
writer. Filesystem synchronization guarantees remain those of the host filesystem.
Newly created parent-directory ancestry is not recursively synced.

## Overhead reporting

In hierarchical ready-work mode, existing observation, candidate-generation,
model-evaluation and checkpoint call boundaries are timed using `perf_counter_ns`.
The existing phase events provide planning, selection, dispatch, approach,
transfer-RPC and verification durations. Checkpoint records additionally split
serialization, file sync and directory sync, including actual bytes written and
counts of coalesced requests. Flat and serial decision policies are unchanged;
serial checkpoints also benefit from the common durable writer.

```sh
PYTHONPATH=src python -m jev_factorio.performance path/to/gameplay.jsonl
PYTHONPATH=src python -m jev_factorio.performance path/to/gameplay.jsonl.gz
```

The streaming, offline reader never initializes a backend, reads `.env`, queries a
provider or alters a campaign. Use one nonduplicated gameplay export. It counts
legacy records without treating missing instrumentation as zero overhead, reports
call/phase count, total, mean and maximum, and rejects malformed or incomplete
records rather than quietly omitting them.

**Durations are inclusive and must not be summed as disjoint elapsed time.**
Selection contains model calls; observation and dispatch can contain checkpoint
work. Collection ends at the checkpoint before record construction, so JSONL
serialization/output and final research-event emission are outside that record's
timing scope. Failed iterations may have research events but no gameplay record.
The report does not infer wall time or speedup from game ticks, count repeated
attempt histories as new work, or claim that actor waiting means an idle factory.

## Reproducible offline checks

```sh
PYTHONPATH=src python -m pytest tests/test_capacity_evidence.py \
    tests/test_checkpoint_io.py tests/test_performance_report.py -q
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python scripts/benchmark_checkpoint_io.py
```

The benchmark uses a synthetic composed background/input/outpost memory with 100
save requests. Compare the script under baseline and treatment source, on the
same host/filesystem. The important deterministic results are write/sync counts
and identical final-byte checksums, not noisy host timing.

In the implementation environment, the baseline performed 100 file syncs and 300
directory syncs for 100 identical saves. Stage 4 performed one file sync and one
directory sync. When all 100 saves changed the state, both performed 100 file
syncs; directory syncs fell from 300 to 100. Final checkpoint bytes matched for
both workloads. This does **not** establish a native Factorio speedup or quantify
the frequency of duplicate states in a campaign.

## Native acceptance and rollout

Use isolated matched saved worlds, not a reset of the live campaign. Compare
baseline versus Stage 4 at the same map, recipe catalog, initial inventory and
campaign budget. Report time to the next verified milestone, science consumption,
cell production/output movement, player gathering and travel, observed starvation,
capacity expenditures, model/observation/planning overhead and checkpoint costs.
Separate game ticks, wall-clock execution and operational downtime. Multiple runs
are necessary before asserting throughput improvement.

Enable/revert only through an authorized, stopped, reconciled source transition.
Do not discard or replay pending actions or downgrade readers that understand
Stage 2 ownership. Stage 4 itself adds no serialized checkpoint fields, but an old
reader still needs all prior capability extensions. On reconstruction, capacity
history is unknown until new observations accumulate; paid machinery is retained.
