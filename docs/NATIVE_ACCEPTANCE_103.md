# Staged native acceptance for #103 / #92

This runbook prepares acceptance; it does not assert that deployment, an approved
window, paid solid transport, or useful native progress already exists.

## Stop gates

Retain the original campaign, supervisor cutoff, immutable treatment, native save,
checkpoint, receipts, paid prefixes, ownership and failure history. Do not reset,
rename or extend a campaign to obtain another measurement window. Never shut down
shared WSL, restart a shared VM or stop another workload.

Do not stage integration until each prerequisite's implementation is available
and independently reviewed. Issues may stay evidence-pending while source is
merged/staged; they need not be closed before the shared native run. Current source
contains #100's narrow paid straight-corridor foundation and #102's explicit,
kit-funded science-investment policy; neither establishes native acceptance or a
complete production logistics network. #101 now has an experimental paid electric
source/multi-consumer composition, and default-off acquisition of an explicitly
specified immutable coal kit. The CLI and supervisor support the immutable
[complete solid/coal treatment](COMPLETE_TREATMENT.md), including checkpoint and
manifest bindings, and the private v2 capture retains its ownership and native
observations. Treatment v2 also binds the
[coal economic admission flag](COAL_ECONOMIC_ADMISSION_V2.md). Its native economic
witness remains explicitly unqualified: positive autonomous demand/payback
admission still needs qualified operating-energy and acquisition/construction
cost evidence and a reviewed policy. These source capabilities do not establish
native qualification or an activated production campaign.

The read-only development preflight validates composed checkpoints but does not
inspect native solid/coal paid ownership. It reports those families as unsupported,
and the complete capture preserves that limitation. Qualifying the actual
FLE/Factorio session, adding a reviewed native ownership projection, and proving
paid material flow, recovery and useful science progress remain open gates.
Neither a capture nor an analyzer consistency pass grants acceptance or deployment
authority. Check the current issues and reviewed source rather than treating an
older packet's status table as today's implementation state.

The source-only coal experiment in `COAL_SUPPLY_EXPERIMENT.md` adds explicit
paid independent electric-source bundles, explicitly bound mixed downstream routes,
shared kit locks and recovery tests. The complete treatment can bind this
composition for qualification, but its existing-power and engine-accounting
assumptions remain explicit. Paid source/transport fixtures and bounded kit
acquisition do not satisfy the native economic, material-flow or recovery gates.

Before deploying the solid composition, include the first-observation publication
barrier described in `SOLID_INITIAL_OBSERVATION_TRANSACTION.md` and qualify its
prepared/pending/receipt recovery on the actual backend. Local filesystem fixtures
are not a substitute for the native restart tests below.

Use the established authorized deployment/handoff process only. Read back the
actual source and configuration; a merge SHA alone is not deployment evidence.
Verify the remaining original window can contain rollout margin plus the full
1800-second useful-progress window. If not, preserve state and record the missing
authorized handoff/window without changing the cutoff.

## Read-only preflight

```sh
PYTHONPATH=src python -m jev_factorio.native_acceptance_preflight "$PRIVATE_PREFLIGHT_MANIFEST"
```

The manifest schema is illustrated below. Replace illustrative values with actual
operator-verified evidence; do not copy `true` flags to manufacture acceptance.
Each issue in 98,99,100,101,102,93,94,95,96,97 needs its own implementation entry.
The complete API contract and negative examples are in
`tests/test_native_acceptance_preflight.py`.

```json
{
  "schema": 1,
  "useful_progress_window_seconds": 1800,
  "rollout_margin_seconds": 300,
  "original_cutoff_utc": "<original UTC cutoff>",
  "runtime_cutoff_utc": "<same original UTC cutoff>",
  "predeclared_experiment_sha256": "<64 hexadecimal characters>",
  "comparison_kind": "matched_native",
  "baseline_save_sha256": "<64 hexadecimal characters>",
  "treatment_initial_save_sha256": "<same starting save digest for matched arms>",
  "implementation": {
    "98": {
      "implementation_available": false,
      "independent_review_verified": false,
      "final_head_checks_verified": false,
      "ssh_signature_verified": false,
      "integration_state": "not_staged",
      "source_commit": null
    }
  },
  "runtime": {
    "deployed_commit": null,
    "configuration_sha256": null,
    "ownership_checkpoint_sha256": null,
    "rollback_record_sha256": null,
    "readback_verified": false,
    "immutable_treatment_preserved": false
  },
  "established_operational_handoff_verified": false
}
```

Preflight reads an operator-supplied manifest, not the remote world. It checks
window arithmetic, matching save bindings, exact-source/readiness assertions and
readback fields. A successful result only means the manifest is ready for operator
review. Deployment authorization and native acceptance are always false, and no
campaign is started. Even a valid matched manifest alone cannot qualify a causal
speed claim; actual controlled observations are still required.

## Baseline and staged experiments

Use the [component and capacity evidence matrix](NATIVE_COMPONENT_MATRIX_92_97_100.md)
to record the native boundary and exact source/configuration binding for each arm.

Before intervention, freeze a reviewable experiment specification: source/config
digests, seed/save binding, action mix, contention class, warmup, window lengths,
useful-output definitions, numerical regression limits, accepted missing-data
rules and rollback triggers. Obtain a fresh baseline from the actual runtime.
Historical issue numbers are not a matched benchmark.

Use isolated native scenarios from owned saved state for component experiments.
Keep deterministic fixtures, native Lua fixtures, isolated Factorio, provider
calls, deployed readback and the live campaign in separate evidence columns.
Test maintenance with ready science and low upstream fuel; test depleted stock
with genuinely required fuel; then grouped manual service with preserved kits
and background locks. Test #100's paid route at each partial-prefix/receipt
boundary before evaluating #101/#102 policy consumers.

Coal acceptance requires a paid/bootstrap-capable source and demonstrated movement
into at least two distinct eligible consumer fuel inventories beyond their initial
bootstrap stock. Count network fuel consumption. Placed belts and increasing stock
without transport attribution are not proof. Downstream acceptance requires an
owned source-to-science-dependency route, correct items and inputs, actual transfer,
backpressure/recovery and measured reduction of actor hauling in matched scenarios.
An unused candidate/helper does not count as planner adoption.

Native recovery tests interrupt before dispatch, after native effects, and around
receipt persistence and partial route construction. Reconcile the same paid
identities without duplicate spending, free items, ownership adoption, lost pending
work or plan-ID budget evasion. Only then stage the integrated production handoff.

## Integrated useful-progress window

Read back deployed SHA/config again. Observe at least 30 consecutive minutes with
science delivered and consumed, advancing research and completion of the stalled
or current feasible milestone. Describe any research-tier transition/bottleneck.
More plates, coal consumption, decisions or a shorter successful probe are not
substitutes. Preserve original runtime/cutoff throughout.

Report median/p95 and sample counts for iteration scope, planning, every observation
boundary, native action, selection and persistence, with wall/process CPU kept
separate. Include raw call/write/byte counts in sanitized aggregate form, resource
quota/pressure deltas, coal trips, actor haul work and science per wall minute.
Never add nested phases, infer CPU/network splits from opaque wall time or report
throttle accounting as a wall-time percentage. Identify uninstrumented costs.

`jev_factorio.latency_report` provides bounded sanitized attribution summaries;
`jev_factorio.campaign_report` can help inspect gameplay privately, but its identity
fields mean its raw output must not be posted publicly without sanitization. Neither
report certifies route-flow/recovery acceptance. Compare controlled algorithm and
capacity arms separately; unmatched production trends must be labeled as such.

## Experimental solid-route evidence analysis

The separate [integration evidence analyzer](INTEGRATION_EVIDENCE.md) reads
retained complete observations and composed checkpoint copies. It checks window,
identity, paid-prefix, receipt, flow, science and timing consistency, and can
compare tightly controlled baseline/treatment captures. It retains stalled
baselines and does not infer native mining from stocked coal chests. The existing
ore-side exporter continues to reject solid-route evidence.

Use only captures produced through an established authorized experimental/native
handoff. No production flag, immutable treatment, campaign identity or cutoff is
changed by this command. A passing consistency result always leaves native
acceptance and deployment authorization false. Missing provider authenticity,
source cleanliness, native crash/recovery, capacity, haul/write metrics and
coal-source provenance still need independent evidence.

## Rollback, publication and closure

Rollback uses the exact predeclared source/config handoff and preserved compatible
state. Do not switch an old reader onto an unsupported newer route/checkpoint
schema; use the reviewed migration/rollback contract. Preserve pending receipts
and stop only the authorized actor at an established safe boundary. Restore approved
infrastructure settings through the infrastructure owner's process and verify active
and persistent readback. Never reset the world or unrelated services.

Publish only sanitized aggregate metrics, source/PR permalinks and evidence hashes.
Keep local paths, private endpoints/topology, session IDs, credentials, raw saves
and logs private. Attach the appropriate evidence to every leaf, then close only
satisfied leaves and finally #92/#103. A signed source merge or green local tests
alone cannot close any outstanding native criterion.
