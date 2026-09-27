# Tracker #103: offline implementation packet

## Status and source boundary

This implementation is **not tracker completion**. The original handoff was
based on `6424661f88d465dcbae585dca64f0ee3c05e23b8`. Integration verified all
packet checksums and applied its 33-file patch in an isolated worktree on
`e0447d7782032fc742b25120f3ccbdae01e12fe2`, preserving the newer dashboard
and recovery instructions from PRs #106/#107. PRs #90 and #91 are already
represented by the base source and are not duplicated.

Review during integration found an alias-validation gap in grouped fuel service:
a second observation above the low-fuel threshold could conceal a conflicting
quantity for the same native unit. Identity and fuel agreement are now checked
before filtering due consumers. Regression cases cover both sides of the
threshold. This source delivery does not deploy a controller, change host
capacity, or establish native acceptance. Deployment and campaign evidence
must be collected separately through the existing operational workflow.

## Grouped-fuel follow-on after PR #108

PR #108 merged the earlier packet as
`294f59bfe5496595a0670a4997a505a4b19bbf54`. Its SSH-signed integration,
alias regression and bounded cleanup-test correction remain intact. The
follow-on described here extends #99; it does not reintroduce the earlier
cumulative packet or claim its merged work as new.

Three controlled regressions still reproduced on that merged baseline: known
distant optional burners and exhausted optional/primary transfer plans could
still expand fuel acquisition. The follow-on corrects those bounds, adds an
identity-bound inventory-depletion proxy for bounded estimated-lead reserves,
and keeps site-level acquisition failures authoritative across quantity changes.
Forty-one additional cases cover these paths, uncertainty, deadlines and restart.
See [the grouped fuel policy](GROUPED_FUEL_POLICY.md) for the exact contract.
Independent review, signed publication, exact-head CI and native acceptance of
this follow-on are separate from the completed PR #108 integration.

## Local changes and integration order

| Issue | Packet changes | Explicitly unfinished |
| --- | --- | --- |
| #98 | Remove exclusive global input/output burner overrides; collect ready owned output before refilling upstream; preserve required-producer and boiler service. | Native science/research acceptance. |
| #99 | Aggregate due small-burner deficits by native identity; preserve held coal; bound optional visits/reserves; observation-only depletion proxy; retain site-level failure history; sequential fresh decisions. | Native capacity/path and true consumption/trip/throughput evidence; independent publication and full acceptance of the follow-on. |
| #93 | Compile using the effective composed planner once; avoid replacing/recompiling its candidates; lazy distinct capital planning. | Broader matched native planning/decision measurements. |
| #95 | Reuse a detached successful checkpoint capture only for exactly equal typed state and an unchanged on-disk stamp. Changed state retains synchronous durable writes. | Full causal-log serialization optimization and production latency measurement. |
| #96 | Nested observation wall/process-CPU partition, separate trace operation/capture/emission counters, checkpoint copy counts, sanitized bounded quantile report. | Low-level opaque FLE transport/retry decomposition, complete record/sleep/iteration partition, fresh native baseline and final metrics. |
| #97 | Read-only visible ancestor audit; counter epoch/reset handling; numeric allocation proposal and operator runbook. | Canonical reviewed infrastructure change, real host readback, authorized remediation and comparable native measurements. |
| #94 | Omit the duplicate legacy actor inventory helper only when the installed craft adapter validates same-observation inventory. | Broad entity-read elimination and full coherent actor/native observation path, native behavior/latency evidence. |
| #92 | Read-only manifest preflight and staged native runbook; reject insufficient original window and changed cutoff. | Actual native rollout, paid material flow, 30-minute useful progress, crash/restart acceptance. |
| #100 | No implementation in this packet. | Paid general solid-route contract, native adapter/telemetry/recovery and review. |
| #101 | No implementation in this packet. | Depends on reviewed #100 and completed #99 semantics. |
| #102 | No implementation in this packet. | Depends on reviewed #100 and #98. |

Apply maintenance semantics first, then batching and planning reuse. Checkpoint,
attribution, audit and observation units are separately reviewable, but their
combined tests must be rerun. Transport consumers must not be built against an
unreviewed invented contract. None of these issue rows should be closed from
this packet alone. In particular, missing implementation is not merely a missing
native-evidence checkbox.

## Maintenance policy and diagnostics

The compiler no longer discards all science choices when an unrelated route arm
or drill falls below two coal. Inner planners first check required owned output
that is already available. The collection path requires a current commissioned
output-buffer contract, retains batching, excludes private growth outputs, and
uses existing transfer/precondition/ownership checks. A needed producer without
output still exposes maintenance. Boiler protection remains in the original
ready-work policy. Fault/uncertain state, in-flight craft locks and existing
failure budgets remain authoritative.

`maintenance_policy` records the observation tick, bounded required/ready quantity
and reason. It is diagnostic, not dispatch authority. Tests reproduce the paired
fuel=2/fuel=1 science cases and stocked-output pickup defect before applying the
change. They do not simulate a completed native research milestone.

## Fuel batching scope

`planning.fuel_service.service_plan` groups due, demanded production-cell burners,
using the existing five-coal small-burner target, capped at 16 consumers/50 coal.
Native aliases cannot multiply demand. A missing/conflicting native identity
fails closed. Existing ledger reservations remain unavailable. Carried spendable
fuel can be partially inserted; no acquisition is required merely to reach an
arbitrary full-load constant. After a receipt the next decision replans.

The two-burner fixture has a combined eight-coal deficit rather than repeated
four-coal acquisitions. This is **manual service**, not automated coal logistics.
Unknown burn rate yields no invented reserve. The follow-on uses a bounded,
identity-bound inventory-depletion proxy only with sufficient observations; it
defers optional reserves for science/power deadlines. This proxy is not an
attributed native consumption counter. Manhattan/catalog-policy lead estimates
are labeled, not presented as observed travel time. Optional
`inventory_insertable.coal` bounds acquisition when provided, but this patch does
not claim the native adapter now supplies that measurement. Full #99 acceptance
therefore remains unfinished even apart from deployment.

## Planning reuse and cache safety

This is decision-local planner composition, not a cross-tick plan cache. Fresh
snapshots and catalog changes construct a new effective planner; no cached plan
grants future action permission. Parent background/prefetch choices survive the
ownership filter. Distinct capital work may legitimately construct its own
planner lazily. The paired benchmark compares #98/#99-corrected source before and
after #93 and checks identical complete frontiers for its two declared cases.

```sh
PYTHONPATH=src:tests python benchmarks/benchmark_composed_planning.py
```

## Checkpoint durability

A cached capture is detached with `asdict` after a successful write/sync. Exact
type-aware recursive comparison distinguishes bool/int/float, signed zero and
container type; unsupported values use the original slow path. External file
changes, replacement/deletion, any changed authoritative field, or a failed write
invalidate reuse. No field was removed from the authoritative snapshot.

Changed checkpoints still serialize, write a temporary file, fsync that file,
atomically replace the destination and fsync the directory. No changed pending
identity, receipt or ownership transition is asynchronously debounced. The cache
is private in-memory metadata, not a new checkpoint schema. A restarted reader
starts cold. Tests cover nested pending edits, invalid numeric data, external
edits and existing crash/storage failures.

```sh
PYTHONPATH=src python benchmarks/benchmark_checkpoint_capture.py --samples 100
```

The matched fixture retains 10 writes, 20 fsync calls and 122960 written bytes in
both arms. Copies/serializations fall from 100 to 10. The final checkpoint digest
is identical. Host-specific timing is not a native speedup claim.

## Attribution and reporting

`ObservationProfile` keeps inclusive helper/RPC timings but additionally tracks
nested exclusive intervals. Wall and process CPU are separate clocks; process
CPU includes other threads of the same Python process, not the native server.
Within a completed ordered observation, exclusive RPC/helper/decode intervals
plus residual reconcile to their respective totals. Native profiler values remain
separate and are never added to enclosing RPC wall time.

`trace_capture` covers result capture; `trace_emit` covers redaction and the
synchronous sink operation. These are distinguished from the operation itself.
They remain inclusive diagnostic counters, not a global non-overlapping iteration
partition. The current record's metrics scope still ends before record emission.
Helper retry/backoff and transport breakdown remain null/unavailable where the
underlying FLE helper retains its own opaque transport.

```sh
PYTHONPATH=src python -m jev_factorio.latency_report "$PRIVATE_GAMEPLAY_LOG" > latency.json
PYTHONPATH=src python benchmarks/benchmark_observation_attribution.py --samples 1000
```

The reporter supports bounded JSONL/gzip, rejects mixed epochs/treatments,
malformed partitions, unknown metric labels and truncated records. It publishes
fixed metrics and validated source digests, not private IDs or raw payloads.
Median uses the middle-pair mean; p95 uses nearest rank. Per-record/per-observation
aggregates are not mislabeled as individual call samples. The UTC record-to-next
observation gap can contain unmeasured emission and intentional sleep; it is not
a watchdog/action-delay measurement. Legacy CPU/partition evidence stays unknown.

## Inventory-read compatibility

The craft-job adapter already requires inventory tied to the same native receipt
observation and tick. Only that installed, validated path omits the earlier FLE
actor inventory read. A per-observation completion marker prevents an empty
placeholder from escaping if a wrapper bypasses validation. Malformed native
evidence raises; no silent fallback masks a broken contract. Non-craft modes keep
the helper. Bootstrap chest inventory and entity reads remain unchanged. This
patch does not claim a single-tick atomic snapshot for the entire legacy path.

## Validation and evidence boundaries

Integration on the newer base passed **2726 tests with 73 skips** on Linux/Python
3.12, including Chromium browser tests and installed Lua/export dependencies.
The focused fuel-grouping and transfer-recovery suite passed 37 cases after the
alias fix. Compileall, the hierarchical mock smoke, evaluation and complete
179-event research-log verification also passed. The checkpoint fixture retained
10 writes, 20 fsync calls and 122960 bytes across 100 saves, with only 10 captures
and serializations. These checks establish offline correctness, not native
gameplay throughput. Final-head hosted checks are recorded on the integration PR.

In the original handoff environment, the unmodified Linux non-browser suite passed 2585 tests
with 77 skips. The patch passed 2686 tests with 77 skips: 101 additional passing
cases. `compileall`, the 40-step-limit hierarchical mock command, evaluation and
179-event complete research hash-chain verification exited successfully. These
are local results, not hosted CI or native acceptance. Python 3.13/Linux was available;
Python 3.10/3.12 hosted matrix execution remains required. Chromium was unavailable,
so the browser suite was explicitly excluded rather than marked passing. Optional
FLE/export tests that skip without their dependencies remain skipped. Lupa tests
execute Lua fixtures, not an actual Factorio server. No paid provider calls or
native campaign runs were made.

```sh
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_dashboard_browser.py -q
PYTHONPATH=src python -m compileall -q src
PYTHONPATH=src python -m jev_factorio --controller hierarchical --backend mock \
  --mock-model --target bootstrap_mining --steps 40 --tick-seconds 0 \
  --checkpoint "$RUN_TMP/campaign.json" --log-file "$RUN_TMP/campaign.jsonl" \
  --run-dir "$RUN_TMP/research-evidence"
PYTHONPATH=src python -m jev_factorio.evaluation "$RUN_TMP/campaign.jsonl"
PYTHONPATH=src python -m jev_factorio.research_log "$RUN_TMP/research-evidence"
```

Source acceptance still requires signed commits from the configured identity,
independent review, exact-head hosted checks, normal PR merges and clean owned
checkout synchronization. Do not use an unsigned Contents API commit, direct-main
write, admin merge bypass or a newly invented signing identity as a substitute.
