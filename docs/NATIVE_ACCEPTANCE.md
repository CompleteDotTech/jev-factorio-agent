# Phase 4: native acceptance preparation and evidence gates

This phase delivers **acceptance tooling**, not a completed native acceptance run.
No game, controller, VM, service, save, network route, production checkpoint or
feature flag is changed by the offline evaluator/capture tools. The preflight is
an explicitly invoked, guest-local, single read-only RCON query. It does not import
or initialize FLE, call campaign callbacks, bind a character, install handlers,
start gameplay, adopt a session or update controller memory.

The baseline for this work is phase 3 commit
`964b84ad3f58feb1ebadd71c055f474db972449a`. A source merge or successful synthetic
CI job is not evidence that the production campaign can be cut over.

## Why native execution is a separate gate

A running development game and a working TCP proxy do not establish that the
world, actor, ephemeral `jev_fle_runtime`, paid component ownership and controller
checkpoint describe the same campaign. A disk clone/old autosave is not a copy of
live Lua runtime or an application-consistent handoff. The existing FLE resume
path correctly rejects a missing live agent; these tools do not bypass it.

Before running a trial, an authorized operator must establish an isolated
**matching live session and controller boundary**, one writer, a paired save and
checkpoint with documented handoff provenance, and the exact development VM
identity. Do not initialize/reset the clone to make a failed preflight green,
change actor/session IDs, clear pending work, erase failures, or copy an old
candidate save over production. A failed native identity check is an actionable
blocker, not a request to automatically repair those identities.

Actual path distance, actor idle classification, real fault/restart exercises,
independent exact-head review, single-writer isolation and production authorization
remain explicit external gates. Reports always contain:

```json
{"native_acceptance":"not_accepted","deployment_authorized":false}
```

A `measurement_checks_passed` value means only the implemented consistency and
measurement checks passed. It is never an installation, repair or cutover receipt.

## 1. Read-only guest-local preflight

Run on the authorized **development guest**, not the production guest or a hosted
CI runner. Create private configuration and a separate private password file,
owned by the invoking user with mode `0600` or stricter. Do not commit either file.
The UUID examples below are placeholders, not this project's VM identities.

```json
{
  "schema": 1,
  "vm_uuid": "00000000-0000-4000-8000-000000000001",
  "production_vm_uuid": "00000000-0000-4000-8000-000000000002",
  "session_id": "PIN_THE_EXISTING_DEVELOPMENT_SESSION",
  "port": 27015,
  "password_file": "/private/acceptance/rcon-password"
}
```

```sh
python -m jev_factorio.dev_preflight \
  --config /private/acceptance/dev.json \
  --checkpoint /private/acceptance/initial-controller.json \
  --output /private/acceptance/preflight.json
```

The command checks Linux DMI identity before opening a socket, refuses the pinned
production UUID, validates the existing checkpoint with its actual schema loader,
and requires an idle running rocket boundary. It connects only to `127.0.0.1` at
the declared port: no arbitrary host option, SSH command, proxy creation or remote
shell. Its credential is never printed or included in the report. Failure output
contains a generic exception type, not credential-bearing provider/RCON text.

The fixed Lua query reads existing session, actor, surface, force, mods, speed,
pause state and bounded owned entity/route receipts. It does not invoke
`campaign.observe` or `fair.actor`, both of which can have runtime side effects.
Missing runtime, mismatched session/actor, regressed ticks, unbound/disconnected
player, altered speed, incomplete ownership or mismatched retained receipts prevent
readiness. Existing outpost commitments are explicitly unsupported by this initial
preflight rather than silently declared compatible. The socket always closes.

DMI identifies the machine executing the probe, **not a remotely forwarded game
server**. A loopback listener could be a tunnel; verify its process and actual game
ownership separately. A successful point-in-time preflight is not a writer lease,
a guarantee against later VM role changes, or proof of network isolation. The
report must be fresh: the trial evaluator requires a UTC preflight no more than
five minutes before the first record, and native tick/session consistency.

## 2. Predetermine the comparison

Use the same instrumented commit for baseline and treatment where possible, with
only `ore_side_successors` disabled versus enabled. Keep model, policy, save,
checkpoint, active goal, runtime/mods, and all other flags matched. Instrumentation
adds consumption-counter reads inside the existing observation; its cost must be
present in both arms. No extra RCON observation is introduced, and diagnostic-only
`consumed`/`acceptance_runtime` fields are removed from the model-facing copy.

Create one `trial.json` **before the run**. The coordinator must preserve its
original digest/timestamp outside the capture: a self-supplied JSON file cannot
prove preregistration or external authenticity. Each trial has a unique `trial_id`
which must equal its gameplay `run_id`; preserve the existing supervisor's run
provenance instead of rewriting an archived log to fit the descriptor.

```json
{
  "schema": 1,
  "experiment_id": "successor-acceptance-01",
  "trial_id": "EXISTING_RUN_ID_FOR_THIS_TRIAL",
  "pair_id": "pair-01",
  "arm": "treatment",
  "expected_commit": "REPLACE_WITH_EXACT_40_HEX_COMMIT",
  "expected_policy": "hybrid",
  "expected_model": "PIN_THE_SAME_PROVIDER_MODEL",
  "initial_save_sha256": "REPLACE_WITH_64_HEX_SAVE_HASH",
  "initial_checkpoint_sha256": "REPLACE_WITH_64_HEX_CHECKPOINT_HASH",
  "vm_uuid": "00000000-0000-4000-8000-000000000001",
  "production_vm_uuid": "00000000-0000-4000-8000-000000000002",
  "goal": "research:automation-2",
  "configuration": {
    "factory_scheduling": "ready-work",
    "background_work": true,
    "furnace_output_buffers": true,
    "furnace_input_belts": true,
    "mining_outposts": false,
    "ore_side_successors": true
  }
}
```

Replace the non-hex placeholders: validation deliberately rejects them. The other
arms are `baseline` and `soak`. Goals are `research:<technology>`,
`milestone:<controller-goal>` or `rocket:launch`. Choose an unfinished goal and a
useful output/consumption item before inspecting results. Deterministic runs use
`expected_model: "none"`; they must not be pooled with model-based runs.

Baseline/treatment captures require at least 108,000 simulation ticks or a newly
observed predeclared goal. The soak requires both 432,000 simulation ticks and
7,200 wall-clock seconds. Do not change game speed, equate stream FPS with game
progress, or advance synthetic clocks and call that native evidence. A successor
trial may need a longer window than the minimum to finish construction and its
36,000-tick qualification.

## 3. Capture complete operational evidence privately

After an authorized stopped/reconciled run boundary, use stable copied inputs:

```sh
python -m jev_factorio.acceptance_capture \
  --gameplay /private/trial/gameplay.jsonl \
  --initial-checkpoint /private/trial/initial-controller.json \
  --final-checkpoint /private/trial/final-controller.json \
  --save /private/trial/initial-save.zip \
  --trial /private/trial/trial.json \
  --preflight /private/trial/preflight.json \
  --output /private/captures/trial-01
```

The output is a new mode-0700 directory with mode-0600 files. Existing output is
never merged or overwritten. `SHA256SUMS` is written last as a completion marker.
The bundle contains the descriptor, preflight, complete validated initial/final
checkpoint projections, gzip gameplay and a versioned manifest. It hashes the
supplied initial save without copying it into the bundle. Save hashing is **not**
a ZIP-integrity test, game-load test, or proof that the live world came from it.

Operational projection retains native producer/recipe/inventory/craft/flow state,
successor ownership and witnesses, failed-attempt budgets, background/capital
state, structured candidate diagnostics, per-call usage, timings and fair-action
counters. New gameplay records explicitly state all capability flags, including
false values. Native actor/mod/speed metadata and separate force production and
consumption counters are read in the existing observation.

Model questions/answers, full decision state, prompts and provider request/response
bodies are not copied. Existing secret/URL redaction provides defense in depth;
the manifest counts omitted fields and redacted strings. Unrecognized or encoded
secrets are not guaranteed detectable, and full checkpoint history is sensitive:
**this is a private diagnostic package, not a publish-to-GitHub export**. Redaction
of an integrity-critical field can legitimately make the capture ineligible.

The reader rejects duplicate JSON keys, nonfinite values, excessive nesting,
symlink endpoints, partial JSONL, oversized records, changing files, malformed
checksums and oversized gzip expansion. Budgets are 16 MiB per JSON record,
256 MiB per gameplay stream, 50,000 records and 16 GiB per hashed save. A larger
run needs a deliberately revised/streaming capture format, not silent tailing or
truncation. The tool does not recapture only convenient successful records.

Hashes establish consistency with supplied bytes, **not authenticity**. Files
sampled sequentially are not declared atomic. Source hashes and redacted projection
hashes are distinct. Original growing source ranges are not independently
reconstructed. Keep supervisor audit, fault-injection evidence, independent review,
full save and application-consistent handoff evidence separately; this bundle is
not a substitute for those artifacts. Earlier historical captures and their
SHA256SUMS remain unchanged and unverified by this new tool.

## 4. Evaluate measured progress, retaining failed trials

```sh
python -m jev_factorio.native_acceptance \
  /private/captures/pair01-baseline /private/captures/pair01-treatment \
  /private/captures/pair02-baseline /private/captures/pair02-treatment \
  /private/captures/pair03-baseline /private/captures/pair03-treatment \
  /private/captures/treatment-soak \
  --useful-item automation-science-pack \
  --output /private/reports/native-measurements.json
```

The evaluator validates hashes/gzip/counts, native rather than mock labels,
checkpoint/session/tick boundaries, exact code/policy/model/configuration, observed
actor/mod continuity, unpaused normal speed, native counter monotonicity, retained
failure budgets, original producer identities and applicable successor flow proofs.
It rejects duplicate records/captures, repeated trial IDs, ambiguous pairs,
incomplete arms, treatment/baseline drift, stale preflight, insufficient horizons
and missing required coverage. Source strings and native-shaped JSON still cannot
prove authenticity; synthetic tests deliberately exercise that limit.

At least three valid matched pairs and a valid treatment soak are required for
the implemented measurement gate. The chosen item's native production **and**
consumption rates must be positive and nonregressing, and the predeclared goal
must not become slower or lose demonstrable progress. Trials must actually
exercise a new qualified successor and a new use witness; an enabled flag with
no exercised lifecycle is not a successful successor trial. The soak must observe
a qualified successor. There is no statistical-significance claim from three pairs.

Production and consumption are reported separately, not collapsed into a fictitious
causally attributable useful-unit total. The chosen item is a proxy: consumer
statistics do not prove that every consumed item advanced the rocket or came from
a particular producer. Phase-3 witnesses remain the narrower source-attributed
receipt evidence. Keep raw paired counts and variability for external review.

Travel between sampled positions is only a lower bound; fair-action counters can
reset on reattachment and do not measure path length. Transfer receipts are
newly observed unique IDs after excluding the initial rolling-history baseline;
unobserved/evicted receipts can still omit work. Missing token totals are unknown;
dollar cost needs actual versioned prices. Inclusive phase timings are deduplicated
by event identity but are **not additive**. Actor idle and actual path distance
remain explicitly unmeasured, not zero. Failed/missing trials remain in reports.

A zero process exit means only that implemented measurement checks passed. Reports
always retain external review gates and `deployment_authorized: false`.

## Native acceptance and cutover still required

Run actual restart/fault cases for source disappearance, depleted/mixed ore, blocked
output, fuel/power loss, full inventory, foreign connections, ambiguous RPC return,
partial paid construction, restart before/after native acceptance, exhausted
budgets and irrelevant changes that must not permit another attempt. Preserve all
paid assets, active plan/pending identity, reservations and failures. Record actual
useful production/goal progress, manual work, idle categories, model costs and
recovery outcomes; a successful mock fixture is never a native test result.

Only after coordinated native trials, complete evidence, conservation/identity
checks, fault/soak review, independent exact-head review and explicit cutover
acceptance may the deployment workflow proceed. Keep one production writer and
preserve the existing world and deadline. Rollback is not a save rewind. None of
these commands starts that workflow or clears a blocked gate.
