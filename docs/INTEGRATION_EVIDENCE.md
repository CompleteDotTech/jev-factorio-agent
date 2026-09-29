# Read-only integration evidence analysis (#92 / #103)

`python -m jev_factorio.integration_evidence` examines retained observations and
checkpoint copies. It does not launch a backend, resume a campaign, attach native
Lua, change a treatment, extend a cutoff, or authorize deployment. It is an
additional analysis path, **not** a replacement for the ore-side acceptance
exporter. That exporter still rejects experimental solid-route evidence.

## Evidence boundary

The analyzer supports complete records from the experimental `ready-work` solid
route controller, with one owned lab and one to four explicit straight-corridor
intents. It checks internal consistency against the composed checkpoint and solid
route contracts. It does not authenticate a save, a log, a model call, source
cleanliness, a deployment, or an operator's predeclaration. A fabricated stream can
be internally consistent. `evidence_kind` is a declared classification, never a
classification inferred or promoted by the program.

Every result retains `native_acceptance: "not_accepted"` and
`deployment_authorized: false`. Pair results also retain
`causal_improvement_proven: false`. A zero exit status means the specified
consistency checks passed, **not** that #92, #100, #101, #102 or #103 can close.
The explicit `remaining_gates` list must be resolved independently.

In particular, stock leaving a coal chest and arriving in two burner inventories
is not evidence of a paid coal miner, bootstrap, network self-consumption, or
operation beyond bootstrap stock. The transport report always leaves
`mined_coal_provenance_verified: false`. Downstream route flow and increasing
assembler output likewise do not, alone, prove that those ingredients caused the
observed science or that player hauling decreased.

The current production CLI/supervisor is not changed. Do not inject experimental
solid flags into an existing immutable campaign or use this command as a resume
or handoff mechanism. See [the staged runbook](NATIVE_ACCEPTANCE_103.md).

## Inputs and private handling

Provide four files from a bounded, retained evidence window:

1. Complete gameplay JSONL, including before/after observations, owned entities,
    native route envelopes, runtime identity, transfer receipts, failure budgets,
    provenance and completed-iteration timing. An optional read-only recipe
    dependency capture may be stored on its matching record as
    `recipe_dependency_witnesses`; keep these bundles with the private evidence.
    A truncated tail is rejected.
2. A predeclared trial JSON document using the exact schema below.
3. The initial checkpoint copy.
4. The final checkpoint copy at the last captured after-state tick.

Keep raw saves, logs, checkpoints, model/provider identifiers, operator paths,
endpoints and native identities private. Use immutable evidence copies, not a
checkpoint that a live actor is updating. The reader rejects symlinks, nonregular
files, changed reads, oversized files, duplicate JSON keys, nonfinite values and
incomplete JSONL. Input files are never rewritten. Both Python entry points
validate checkpoint dictionaries through the complete composed memory loader,
using private captured copies rather than rereading a live path.

The public report contains fixed reason labels, digests and numeric aggregates.
It does not publish native IDs, roles, endpoints, paths, model names or raw error
messages. The output must not already exist; it is created with mode `0600` and
file synchronization. Review even sanitized output before publishing it. Input
hashes identify evidence; they do not authenticate its origin.
File-based paired reports also include a digest binding the exact bytes of all
four captured files in each arm. The parsed-value binding remains separate, so
formatting-only changes remain visible without changing the measured values.

## Single-arm command

Set each environment variable to an existing private evidence file, and select a
new private output path:

```sh
PYTHONPATH=src python -m jev_factorio.integration_evidence \
  --gameplay "$PRIVATE_GAMEPLAY" \
  --trial "$PRIVATE_TRIAL" \
  --initial-checkpoint "$PRIVATE_INITIAL_CHECKPOINT" \
  --final-checkpoint "$PRIVATE_FINAL_CHECKPOINT" \
  --output "$PRIVATE_NEW_REPORT"
```

Exit status `0` means internal consistency checks passed for the selected arm;
`2` means malformed, inconsistent or insufficient evidence. An existing report is
never overwritten, even when new inputs are invalid. A failed analysis can produce
a structured report of fixed issues; malformed input produces a fixed error
message without its payload. Neither outcome changes the game or other services.

Python APIs are `validate_trial(trial)`,
`analyze(gameplay, trial_path, initial_checkpoint, final_checkpoint)` and
`analyze_rows(rows, trial, initial, final)`. The latter accepts already captured
objects but still performs composed checkpoint validation. For actual file
analysis prefer `analyze` so stable-read checks and input-byte digests are retained.

## Trial schema

`examples/integration_trial.fixture.json` is a fully parseable, **fabricated**
example; its hashes, clock window, source and models are not native evidence.
Do not reuse its values for a campaign. Unknown or missing top-level keys fail.
The table describes v1 common fields; v2 adds the complete coal/checkpoint/VM
bindings. V3 retains every v2 binding and requires `downstream_chain`. Existing
v1/v2 trial meaning and same-pack downstream qualification are unchanged.

| Field | Contract |
| --- | --- |
| `schema` | `jev-factorio.integration-trial.v1` |
| `evidence_kind` | `fixture`, `native_isolated`, or `native_campaign`; operator-declared |
| `arm` | `baseline` or `treatment` |
| `comparison_axis` | `algorithm`, `capacity`, or `unmatched` |
| `experiment_sha256`, `workload_sha256`, `initial_save_sha256`, `capacity_profile_sha256` | Four explicit 64-character lowercase hexadecimal evidence bindings |
| `expected_commit`, `expected_source_sha256` | Exact 40-character source commit and 64-character source fingerprint |
| `configuration` | `factory_scheduling: "ready-work"` and all seven booleans listed below; `solid_routes` must be true |
| `solid_intents` | One to four explicit `source`, `target`, `item`, `destination` intents accepted by the existing route contract |
| `campaign_treatment` | Explicit `null`, or schema 1 with all four campaign booleans listed below |
| `requested_model`, `resolved_model` | Predeclared bounded printable model identifiers; retained privately |
| `declared_at_utc` | UTC instant strictly before the first captured record |
| `original_cutoff_utc`, `runtime_cutoff_utc` | The same original authorized UTC cutoff; no captured record may exceed it |
| `minimum_window_seconds` | Integer 1800–86400; never a shorter replacement for the native gate |
| `max_observation_gap_seconds` | Integer 1–120, chosen before inspecting outcomes |
| `max_no_science_progress_seconds` | Integer 1–600, chosen before inspecting outcomes |
| `science_packs` | Nonempty unique list from the existing science-pack whitelist |
| `research_goal` | Declared currently feasible research milestone |
| `downstream_recipes` | Nonempty unique bounded recipe-name list for observed science dependencies; a route counts toward integrated downstream flow only if its target recipe names a declared science pack also newly observed in owned-lab delivery and consumption. This is correlated evidence, not proof that those exact lab units came from that target. An intermediate-only route needs a separate validated dependency-chain witness. |
| `downstream_chain` (v3 only) | One to 16 predeclared route, producer role/recipe/product item, and owned science consumer role/unit/pack identities. The analyzer reports an intermediate-chain diagnostic only when it observes new ordered paid extract and insert receipts at both entities and the lab, positive routed flow, and rising production counters. A separate diagnostic requires the paid consumer insert to first appear with a fresh, exact input-stock increase and later stock drawdown alongside a recipe counter increase. Stale, duplicate, wrong-unit, and ambiguous stock boundaries do not qualify that correlation. These checks remain correlated-only: fungible stock and unobserved transfers prevent exact-unit provenance, and the capture does not validate the native recipe graph. Neither diagnostic qualifies downstream acceptance or closes the science-flow gate. |
| `minimum_timing_samples` | Integer 2–50000, chosen before inspecting outcomes |
| `regression_limits` | `max_iteration_p95_ratio` in 0.01–10 and `min_science_rate_ratio` in 0–10 |

`downstream_recipe_witness.py` and its fixed Lua v1 query are a separate,
read-only source interface for the missing recipe edge. Under an owner-controlled
ordered RCON read, it checks the source-qualified solid-route installation, the
current paid route target and owned science consumer, and deterministic native
item products/ingredients at one actor session and tick. The strict decoder
requires a separately validated expected route and a bounded tick window. It
can establish current recipe dependency only: `stock_provenance_qualified` and
`mutation_authorized` are always false. `observe_capture()` returns a private
binding bundle for one such query; attach it to the matching complete gameplay
record under `recipe_dependency_witnesses` for offline analysis. The analyzer
replays the strict decoder and binds the request, route, actor/session identity
and query tick to the predeclared chain and an owned observation within 120 native
ticks. Duplicate or malformed capture lists cannot qualify a witness. When
present, this current recipe edge is composed with the separate paid-transfer
and fresh-stock/recipe-counter diagnostics. The combined label remains
correlation only: it does not show that the transferred units caused the recipe
output, prove actual science use, or authenticate the evidence. The capture does
not upgrade intermediate routes to science-flow acceptance; later native
qualification must still prove the pinned 2.0.77 query shape, receipt/stock
lineage and actual science use through the retained campaign.

The seven configuration booleans are `background_work`, `furnace_output_buffers`,
`furnace_input_belts`, `mining_outposts`, `ore_side_successors`, `solid_routes`, and
`solid_science_policy`. The optional campaign booleans are `lead_time_supply`,
`coverage_margin_lookahead`, `profile_observations`, and `consolidated_observations`.

The complete configuration, campaign treatment, invocation/segment identity and
source fingerprint must remain unchanged throughout an arm. An explicitly dirty
source fails. Current supervisor provenance has no clean-tree flag: absence is
reported as `not_reported`, not silently converted to clean. Independently bind
and verify the actual deployed tree and fingerprint before making source claims.
At least one in-window record must declare an actual JEV model call with the
predeclared resolved model; the analyzer does not verify that provider execution
really occurred. Deterministic-only captures cannot satisfy that measurement check.

## Window, progress and ownership checks

The measured interval begins at the **first record's after-state**, not an earlier
before-state or an old route counter. The first record's previous work, existing
stock, receipts and cumulative route flow are excluded from new output. Require at
least the declared wall duration and native tick duration at normal speed, bounded
record gaps, stable actor/session/surface/force/mod identities, and monotone ticks.
Observed mod names and versions must be bounded printable strings; a hash of
malformed mod metadata is not an identity proof. The initial checkpoint must be
running, and its background, input, outpost and successor extensions must agree
with the declared checkpoint-backed configuration flags.

All before/after boundaries are checked, not just first/last observations. Paid
prefixes, native component identities and durable receipt IDs may not disappear or
change. Pending placement phases cannot regress; a cleared pending identity must
match its paid receipt. Final checkpoints must contain the observed commitments
and failure history, completed-goal history, and terminal status, with no unresolved action, attempt, reservation or background
work. A valid final checkpoint alone cannot erase a mid-window fault or reset.
Retained capital investments must preserve their paid identity until an observed
completion or recorded abandonment reconciles them. Final input-route and
outpost commitments must cover paid native observations.
This analyzer conservatively rejects any retained initial or in-window pending
action or ambiguous attempt. A clean final checkpoint cannot establish that
such an operation was reconciled; retain it for native recovery review before
using a clean measurement window. The analyzer does not perform reconciliation.

Science output uses new, tick-bound receipts for every declared pack at the same
owned lab, consumption deltas for every declared pack, and a newly completed
declared milestone. Force
consumption is only used while exactly one owned lab is observed. Sustained useful
progress requires consumption alongside advancing research within the predeclared
stall limit. Plate accumulation and a last-minute science burst do not suffice.
A legitimate science delivery batch may span multiple observations.

Coal corridor measurements require new positive flow at three or more observation
boundaries into at least two distinct fuel-consumer identities. Both new sends and
new receipts are required. The conservative delivered lower bound subtracts
the maximum stock already outstanding on the belt at the first observation,
and it needs attributable delivery increases at three later boundaries.
Those boundaries must also advance the native positive-sample counters and tick.
Downstream
measurements require new route flow plus increasing output at an allowlisted
recipe. Old counters, an alias of one consumer, a stale route, changed flow epoch,
rewritten receipt, replacement entity or lost paid prefix cannot establish these
measurements. These checks do not replace isolated native recovery experiments.
The target's `products_finished` counter may not reset at either observation
boundary while claiming downstream production.

## Timing and matched comparison

Completed-iteration timing is published one record late. Prior cycles attached to
the first two records are validated but excluded when their scope crosses the
first after-state boundary. The final unpublished cycle is not inferred. All later
records require consecutive, complete returned timing samples; failed iterations
are an integrity issue. Wall and process CPU,
exclusive phase counts/costs, logical native call/byte/failure counts, intentional
sleep, and other inter-iteration gap costs remain separate. Nested inclusive
phases are never added to the iteration total. Missing phases remain explicitly
unobserved; opaque helper elapsed time is not labeled CPU or network latency.
These summaries do not establish complete wall-window clock reconciliation,
network-packet counts, all persistence byte/write counts, actor hauling counts,
or effective host capacity. Those remain external evidence gates.

For matched analysis, provide both arms' four private files:

```sh
PYTHONPATH=src python -m jev_factorio.integration_evidence \
  --gameplay "$PRIVATE_TREATMENT_GAMEPLAY" --trial "$PRIVATE_TREATMENT_TRIAL" \
  --initial-checkpoint "$PRIVATE_TREATMENT_INITIAL" \
  --final-checkpoint "$PRIVATE_TREATMENT_FINAL" \
  --baseline-gameplay "$PRIVATE_BASELINE_GAMEPLAY" \
  --baseline-trial "$PRIVATE_BASELINE_TRIAL" \
  --baseline-initial-checkpoint "$PRIVATE_BASELINE_INITIAL" \
  --baseline-final-checkpoint "$PRIVATE_BASELINE_FINAL" \
  --output "$PRIVATE_NEW_COMPARISON"
```

All four baseline switches are required together. Both captures are reanalyzed;
previous report JSON is not trusted as evidence. Reports and trials remain
hash-bound even if a trial file changes during comparison. The Python equivalent
is `compare_files(baseline_paths, treatment_paths)`; each mapping has `gameplay`,
`trial`, `initial_checkpoint`, and `final_checkpoint` keys. `compare` is a lower-level
report-composition helper, not an authenticity validator.

Arms must have different invocation and capture hashes, but the same declared
experiment, workload/save, configuration, intent set, model, outcome definitions,
windows and regression limits. The observed native mod sets must also have the same
sanitized `runtime_mods_sha256` binding across arms. Algorithm comparisons hold
capacity profile fixed; capacity comparisons hold source commit and fingerprint
fixed. Actual wall and native tick windows must match within the predeclared
maximum observation gap. This narrow schema
does not compare a legacy no-solid controller with a solid-enabled controller or
permit arbitrary feature-flag changes. `unmatched` production trends are not
accepted as controlled pairs.
The comparison carries an aggregate binding for gameplay, trial and both composed
checkpoints in each arm; raw file-byte hashes remain available in file-based arm
reports. These hashes identify inputs but do not authenticate their origin.

A stalled baseline is valid data if its integrity checks pass: it is not discarded
for failing treatment outcome goals. A zero baseline science rate produces an
absolute rate comparison and a null ratio, never an infinite speedup. P95 ratios
use the existing nearest-rank distribution helper. Ratios are measured summaries,
not causal proof. Paired consistency still leaves native acceptance open.

## Validation and outstanding work

Run the focused analyzer and unchanged acceptance-boundary suites, then the full
repository Linux suite. The tests fabricate API-shaped snapshots, timings, model
calls and receipts; **none is a native Factorio or provider run**. They cover valid
and stalled arms, clock/cutoff/identity drift, counter resets, old-flow exclusion,
receipt rewriting, checkpoint corruption, lost ownership, incomplete timing,
changed comparison controls, private-file safety and the no-backend/no-overwrite
boundary.

Required remaining work includes the paid coal mining/bootstrap capability,
automatic downstream kit acquisition/admission where not implemented, isolated
native construction and crash/restart tests, canonical host-capacity review and
readback, independent exact-head review, verified SSH-signed publication and CI,
an established immutable-runtime handoff, and the actual 30-minute useful-progress
campaign evidence. This analysis command does not complete those tasks.
