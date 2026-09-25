# Why the production campaign is progressing slowly

Evidence captured September 25, 2026, at 21:51 UTC. This branch contains analysis and sanitized log evidence only. It does not change the controller, enable features, reset failure budgets, or modify the running world.

## Conclusion

**The agent is not completely stalled, but its useful production rate is extremely low.** It spends most measured decision time observing the world and then manually services a small factory in short batches. The factory repeatedly lacks intermediate ingredients, so successful transfers and a `running` status do not imply sustained research or progress toward a rocket.

On accepted revision `a88963db68031b61988effb1bbe655667caa12bb`, the captured records span **11:43:01–21:49:24 UTC: 10 hours, 6 minutes**. They show 280 decision records, 60 gathering actions, 96 insertions, 74 extractions, and 41 native craft-job requests. Every record reports `running`, yet production increased by only **20 chemical science packs, 20 logistics science packs, and 78 automation science packs**. Advanced oil processing reached **26.67%** from zero; steel axe was added to the researched list. There is no rocket victory in the latest snapshot. The action named `factory_launch_fish` collected a fish; it is not evidence of a rocket launch.

My highest-confidence explanation is the combination of **expensive observations, manual material movement, and intermittent upstream supply**. Earlier connection/pathfinding failures and exhausted investment budgets also explain lost time and restricted automation. Host contention is a plausible contributor to observation latency, but this dataset cannot apportion that latency among host paging, RCON transport, native scans, and Python processing.

## Evidence and scope

The [evidence directory](evidence/campaign-progress-20260925/) contains:

- [actions.csv](evidence/campaign-progress-20260925/actions.csv): all 880 captured records, with timestamps, actions, results, research, revision, and source line references.
- [action-log-excerpts.txt](evidence/campaign-progress-20260925/action-log-excerpts.txt): controller action lines from five gameplay logs.
- [gameplay-projection.jsonl.gz](evidence/campaign-progress-20260925/gameplay-projection.jsonl.gz): allowlisted structured log projections, including before/after inventories, production counters, factory state, timing, routing diagnostics, and failure budgets.
- [summary.json](evidence/campaign-progress-20260925/summary.json): computed metrics for all captured records and separately for the accepted revision.
- [latest-state.json](evidence/campaign-progress-20260925/latest-state.json): the last recorded native observation, configuration, and route diagnostics.
- [manifest.json](evidence/campaign-progress-20260925/manifest.json): source-prefix hashes, capture boundaries, source counts, checkpoint/supervisor projection, and artifact checksums.
- [analyze.py](evidence/campaign-progress-20260925/analyze.py): offline reproduction of the aggregate calculations.

The five source files contain 880 complete JSONL records from **September 24, 10:33:51 UTC through September 25, 21:49:24 UTC**. They belong to the active production campaign and include older revisions and interrupted/resumed segments. The 280-record current-revision subset is selected by exact recorded commit equality, not by assuming every historical record used today's code.

The export reads each file to its initial byte length and retains complete lines. Files are captured sequentially, so this is not an atomic world/checkpoint snapshot. Source prefix and individual source-line SHA-256 values allow later comparison with private originals. Raw prompts, responses, credentials, authentication files, environment settings, and complete research-event archives are not published. These are log projections, not replay-complete originals. No logs were cleared or rotated for this task.

## 1. Observation overhead dominates current-revision decision time

| Measurement | Observed value |
|---|---:|
| Initial observations | 280; median 32.15 s, p95 64.77 s |
| Pre-dispatch observations | 279; median 31.79 s, p95 63.10 s |
| Post-dispatch observations | 276; median 32.25 s, p95 73.91 s |
| Sum of those three observation phases | 29,927 s / 8.31 hours |
| Recorded interval | 36,383 s / 10.11 hours |
| Dispatch | 276; median 4.20 s, p95 71.51 s |
| Model response calls | 181; median 0.86 s, p95 8.27 s; total 303 s |
| Checkpoint-write call time | Total 450 s; median 1.49 s per record's aggregate |

Observation phase totals are approximately **82% of the elapsed recorded interval**. This is an approximate accounting comparison, not continuous CPU utilization: the first record's work began before its record timestamp, and records are not a full wall-clock profiler. It nevertheless identifies a much larger measured cost than model response latency. The model was not called in 99 of 280 records.

Do not add nested `approach` or `transfer_rpc` timings to `dispatch`; do not add inclusive performance-call timers to phase totals. The separate inclusive observation call total is 28,911 s, and includes different measurement boundaries from the 29,927 s phase total. The transfer RPC itself has a median of only 0.046 s in 212 returned measurements. This does not prove all networking is fast, but it weakens a blanket claim that remote transfer latency is the main problem.

The source provides an actionable investigation target. [FLEBackend.observe](../src/jev_factorio/backends/fle.py#L185) serially requests fair state, position/session, inventory, resource targets, entity scans and output inventories before [NativeFactory.observe](../src/jev_factorio/backends/native_factory.py#L52) requests the campaign snapshot and performs more resource discovery. Three broad observation passes around a small transfer magnify their cost.

**Recommendation:** instrument each observation subcall, payload size, native scan duration, RPC duration, and local serialization/IO duration. Consolidate read-only native fields into bounded snapshots where their semantics permit it. Cache static catalog and unchanged discovery information; use explicit invalidation after construction, mining depletion, actor/session changes, or topology changes. Keep authoritative receipt checks and fresh mutation preconditions. Do not simply remove pre-dispatch verification or rely on stale inventory.

## 2. Enabled belt support cannot reach this factory's ore

The latest accepted configuration has `ready-work`, background work, furnace output buffers, and furnace input belts enabled. **Mining outposts and ore-side successors are both disabled.** Enabling a capability is not proof that an input route was built.

Both primary furnace routes report:

```
reason: ore_outside_local_survey
fallback: batched_manual_supply
survey_radius: 40
resource_count: 0
path_attempts: 0
cached: false
max_belts: 64
```

The iron furnace is at `(12, 32)`; an observed iron mining location used during this campaign is around `(46, -81)`, approximately 118 tiles away. [The native survey](../src/jev_factorio/lua/input_routes.lua#L157) searches only 40 tiles around the producer. This is an expected topology mismatch, not evidence that belts are randomly being ignored or that the routing search exhausted its budget. Waiting for the same local survey will not bring distant ore into range.

The latest world counts show **one burner mining drill, one lab, six stone furnaces**, four assembler-2 machines, one assembler-1, two chemical plants, one refinery, and 526 pipes. Counts alone do not establish capacity or utilization, but they show how little mining infrastructure supports the large manually serviced footprint.

**Recommendation:** test one supported automation strategy in dev: a paid mining outpost or an ore-side successor. They have different gates and are mutually exclusive in the current CLI; do not enable both. Prove native ore extraction, fuel/power, belt or direct-insertion topology, receipt identity, and positive material flow through smelting before production rollout. A long-haul route needs an explicit supported design and material budget; blindly increasing survey radius is not a complete fix. See [outpost eligibility](../src/jev_factorio/planning/mining_outposts.py#L24), [successor proposal conditions](../src/jev_factorio/successor_controller.py#L241), and [CLI constraints](../src/jev_factorio/main.py#L100).

## 3. Manual transfers and supply imbalance keep science intermittent

There were **170 insertion/extraction records out of 280** on the accepted revision, plus 60 gathering records. Iron alone required **39 insertion records for 672 ore** and **55 extraction records for 672 plates**. Median iron-plate extraction was 13; six extractions took only one plate. These counts describe observed handling, not unique factory throughput.

The latest snapshot makes the supply problem concrete:

| Component | Observed state |
|---|---|
| Lab | 20 red packs, 1 green pack, no blue packs recorded in input |
| Chemical science assembler | 20 sulfur; no engine units or advanced circuits recorded in input; not crafting |
| Engine assembler | 21 pipes and 20 gears; no steel recorded in input; not crafting |
| Advanced-circuit assembler | 40 plastic and 20 green circuits; no copper cable recorded in input; not crafting |
| Logistics science assembler | 20 belts; no inserters recorded in input; not crafting |
| Automation science assembler | 190 red packs in output; not crafting |
| Primary iron and copper furnaces | Fuel present, empty ore inputs, not crafting |

Thus there is red science waiting in a machine while blue science and its prerequisites lack ingredients. The evidence supports **poorly sustained dependency supply**, not merely a missing command to resume research. The 20 blue packs produced over 606.4 wall-clock minutes correspond to approximately **0.033 packs/minute over this observed interval**; this includes controller delays and is not an intrinsic machine-capacity estimate.

Existing batching and service logic should be improved rather than duplicated. [Ready-work collection](../src/jev_factorio/planning/ready_work.py#L88) already batches some outputs, while preserving small immediate deficits. [Factory planning](../src/jev_factorio/planning/factory.py#L162) limits raw gathering and craft batches, and [service visits](../src/jev_factorio/planning/service_visits.py#L15) group bounded same-cell transfers. Broadly raising every batch size could starve another dependency or waste materials.

**Recommendation:** use measured replenishment lead time and bottleneck demand to maintain reserves of steel, cable, circuits, engines and all required science packs. Prefer a batch large enough to keep the bottleneck working until the next service opportunity. Record why batch/service candidates are ineligible. Compare useful science delivered per actor-minute and per decision, not raw action count. Preserve inventory reservations and independently verified receipts for each transfer in a bundled visit.

## 4. Earlier failures consumed time and constrained investment

Older source segments record ambiguous connection dispatches, `no_connection_route`, `Plan failure budget exhausted`, and `Unverified action outcome; inspect/reconcile before another mutation`. The latest evidence retains multiple capital budgets at two failures and connection-specific failures. Those historical stops must not be blamed on today's accepted pathfinding fix without checking revision and incident identity.

The 280 current-revision records all report `running`; they do not show another repair stop. This separates the earlier recovery problem from the current throughput problem. There are only four `verify` actions in this subset, including successful recovery and passive-plan checks. That is not evidence of a dominant infinite verify loop. Research progress and supplies change across the short passive sequence.

[Capital recovery](../src/jev_factorio/capital_controller.py#L113) intentionally preserves paid structures and abandons repeated same-step failures or expired investments. **Do not reset failure counters or blindly retry ambiguous mutations.** Instead, expose the blocked investment, exact step and failure reason, surviving paid structures, and what world change would make a new reviewed proposal safe. Match old attempts to their code revision before attributing them to a new defect.

## 5. Background work exists, but its eligibility may be too restrictive

[Background selection](../src/jev_factorio/background.py#L198) already permits bounded independent work while research or crafting waits, and [research scheduling](../src/jev_factorio/planning/scheduling.py#L187) already implements polling/backoff. A proposal to simply add background work would miss the existing implementation.

A source-derived hypothesis worth testing is the [future-research supply gate](../src/jev_factorio/planning/scheduling.py#L133): it requires current science inventory to cover all remaining units before preparing future research, while ordinary current refills are bounded. This may prevent useful preparation during otherwise safe windows of a long technology. The exported logs do not establish how much idle time this particular gate caused.

**Recommendation:** emit eligibility/rejection reasons for independent work, including current coverage, replenishment lead, reservations, future demand, and investment state. Only after measuring missed opportunities, test a coverage-margin rule that protects current research while allowing bounded preparation. Do not sacrifice present science supply to speculative next-stage work.

## Prioritized implementation and acceptance plan

| Priority | Proposed change | Evidence required before accepting improvement |
|---|---|---|
| P0 | Profile and consolidate observation subcalls | Paired native measurements from the same save/configuration; lower observation p50/p95 without receipt, identity, or stale-state regressions |
| P0 | Keep the current research chain supplied | Sustained positive blue/green/red consumption; fewer empty-input observations; report throughput and research progress over at least a 30-minute window |
| P1 | Pilot one paid ore automation mode | End-to-end positive native flow, paid material accounting, fewer manual ore trips, preserved existing structures |
| P1 | Tune bounded batches and same-cell visits | Lower decisions per useful science pack, no ingredient starvation or duplicate transfers, improvement beyond merely processing more items |
| P1 | Improve progress-based monitoring | Alert when research/material/technology progress is absent for a declared window, even if the process says running; distinguish waiting, observing, travelling, supply-starved, and repair-required |
| P2 | Review blocked investment and background eligibility | A concrete rejected candidate and causal reason; safe alternatives or reviewed environment-sensitive retry criteria without resetting budgets |
| P2 | Evaluate host resources and research lookahead | Synchronized pressure/RPC/observation samples; measured benefit from a scoped resource change or coverage-margin policy |

These are proposed acceptance criteria, not achieved results. Use dev first for planner or infrastructure changes, retain the production save/session, and measure a comparable baseline. Keep speed, scenario, native receipts, and research goals fixed when comparing. Faster OBS rendering or faster model inference alone is not supported here as the principal remedy for low factory throughput.

## Reproduce and interpret

Run from the repository root:

```console
python docs/evidence/campaign-progress-20260925/analyze.py
```

This recomputes both metric subsets from the compressed projection and compares them with `summary.json`. The percentile uses the sorted sample at `floor(0.95 * (n - 1))`. Cumulative production deltas use absent item counters as zero; they do not measure instantaneous utilization. Per-record action logs can include attempts that need later verification, and repeated attempt history is not counted as additional production. Native state—not a process label or synthetic test result—is the basis for the progress assessment.
