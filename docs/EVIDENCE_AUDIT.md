# Offline evidence audit

```sh
PYTHONPATH=src python -m jev_factorio.evidence_audit /path/to/immutable/capture --output /path/to/new-report.json
```

The command is read-only with respect to the capture and campaign. It creates
only the explicitly requested new report (exclusive creation, no overwrite), or
writes JSON to standard output. It does not read `.env`, create a backend, call a
model, change a checkpoint, or contact a game server.

## Input and integrity

A directory must contain `SHA256SUMS`, `README.md`, `NOTE.md`,
`capture-manifest.json`, `controller-snapshot.json`, `gameplay-recent.jsonl.gz`,
`gameplay-timeline.jsonl`, and `supervisor-events-recent.jsonl`. Every listed file,
including a historical note, is checked. Additional listed files are also checked.
The note and checkpoint are not assumed contemporaneous with the gameplay window.

The supplied manifest must describe the decompressed bytes, SHA256, record counts
and first/last timestamps. The audit checks gzip integrity, complete JSONL records,
count consistency and the timeline's timestamp/tick/action/outcome/verified
projection. Symlinks, traversal, duplicate manifest entries, excessive read or
expansion budgets, regressed observations, duplicate records, and mixed treatment
identities are rejected rather than silently skipped. Work on a stable copied
bundle, not files still being written. A changed file must be recaptured, not
silently rehashed to conceal a mismatch.

A matching bundle proves consistency with **its supplied** hashes. It does not
prove external authenticity or independently verify the original growing source
byte ranges, a full checkpoint, or fair gameplay. Sequential files are not an
atomic cross-file snapshot. A controller can be a few ticks ahead of the last
complete gameplay record without proving a replay or reset.

## What the report does and does not measure

- Action-label counts are record counts, not unique successful native actions.
  An accepted background job may have `verified: false` until its later receipt
  and output are verified. Poll/observe/verify records are not automatically
  failures, game-idle periods, or extra model calls.
- Model token totals require both a known per-record model-call flag and usage
  for every reported call. Known subtotals and missing coverage are separate.
  Dollars remain unknown; the tool does not guess model prices.
- Transfer receipts are deduplicated across observations. Initial receipts are
  excluded as historical baseline; a missing initial receipt map prevents an
  invented new-transfer total. Receipt IDs cannot silently change meaning. The
  native rolling receipt window may still omit work between samples.
- Player-position differences provide a sampled **lower bound**, not actual
  walking distance. Detours and repeated backtracking can be invisible. Native
  approach/path telemetry is required for an actual travel-cost gate.
- Native force production counters, when present and monotonic, give an output
  delta. They do not prove which producer made it or that a downstream consumer
  used it. Useful production, actor idle, recovery reliability and acceptance
  remain unavailable unless independently validated with richer evidence.
- Timings are inclusive; observation and dispatch can contain checkpoint work,
  and selection contains model latency. Existing `performance.summarize` reports
  instrumented versus legacy records rather than filling missing timings with zero.

The command always returns `not_a_native_acceptance_run` in its metrics. Passing
hashes and synthetic tests cannot authorize deployment.

## Required next-capture coverage

Do **not** overwrite an archived projection to add fields. Produce a new versioned
capture and retain its source, policy, allowlist, redaction policy and SHA256SUMS.
Use the operational fields below in addition to current inventory/entity/research
and transfer-receipt evidence, while excluding credentials, endpoints, environment,
raw provider bodies and model prompts/questions/answers.

| Layer | Preserve |
| --- | --- |
| Record identity | Exact revision/source hash, session, run, segment, execution ID, policy/model and feature flags |
| Native state | Force produced counters, owned producer `products_finished`, input/output/fuel/power, recipe, native unit/position, crafting queue and native job receipts; outpost/input/output/site observations |
| Planning | Existing decision candidate plans and `candidate_evidence`, `local_objective`, new `planning_diagnostics`; early native eligibility diagnostics with their own boundary and survey work counts |
| Execution | Current pending/attempt state, phase spans and `attempt_outcomes`, fair-action metrics and per-step receipts; preserve prepare/dispatch/returned/verification boundaries |
| Background/capital | `background_job`, `background_attempt`, `capital_investment`, input/outpost ownership commitments and science reserves |
| Recovery | Retained `failure_budgets`, typed failure, relevant precondition fingerprint, probation/global budgets and supervisor intervention provenance |
| Cost | Incremental `performance`, per-call model usage and latency, observation purpose, actual cache reuse/invalidation; do not double-count accumulated histories |
| Campaign goal | Research/science deltas, completed milestones, rocket parts and launch evidence; downstream consumption and held-stock changes |

Some listed recovery, survey-count and downstream-consumption fields are follow-on
instrumentation, not emitted by this first-phase patch. Existing full gameplay
logs already carry many performance/capacity/attempt fields that a narrow exporter
can accidentally discard. The new report's field coverage identifies omissions;
it does not reconstruct unavailable data.

See [PLANNING_AUTONOMY.md](PLANNING_AUTONOMY.md) for bounded implementation phases,
regression expectations, matched development VM tests, and production cutover gates.
