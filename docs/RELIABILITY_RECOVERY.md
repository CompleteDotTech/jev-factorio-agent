# Operational runbook — reviewed rollout required

**Not a production deployment transcript. Commands are for the explicitly selected environment after review. Do not run a generic restart to test these fixes. Do not reset a world, campaign timer, pending action, receipt, reservation, ambiguity, or failure budget. Never shut down/terminate WSL.**

## 1. Establish current state without touching the game adapter

Read the domain AGENTS.md, DOMAIN_CONTEXT.md and docs/VALIDATION.md again. Discover the active pointer, real checkout/revision, exact campaign, checkpoint, supervisor process identity and fixed start/cutoff from current runtime files. Check service/process state and existing fresh gameplay evidence without importing or attaching FLE/FairActions. Do not use historical success ticks or recorded paths as proof of present health. If a QEMU guest-exec request times out, inspect its saved process/artifacts before any retry.

Compare consecutive owned-action receipts and successful verified outcomes, not PID presence or native tick advancement alone. Use a connected OBS control route to read scene/stream/audio; do not infer them from an operator rule. If the game/controller is confirmed down or stopped for repair, select the existing maintenance scene while keeping the broadcast connected. Read back scene and streaming state and confirm audio/overlay unchanged. Normal production/crafting/travel waits are not incidents.

## 2. Review and prepare away from production

Apply both patches in isolated worktrees using APPLY.md. Run the full suite with supported Python, FLE and browser dependencies, then independent exact-head review and hosted checks. Preserve the existing three-level delegation/signing/merge/publication gates. Stage binaries and rollback artifacts before pausing healthy play. Do not change the active pointer or use a new checkout as proof of an accepted deployment: the existing launcher enforces source/manifest and accepted-repair provenance.

The deployment helper is additive. Its service-script hash must come from review of the exact installed service script. Do not supply a newly computed hash merely to bypass review. Keep the domains changes scoped to this domain. The new units add only `RestartPreventExitStatus=2`; exit 2 stays an observable failure, not a fake successful run.

## 3. Planned maintenance after the new handshake is installed

Use the configured wrapper and the discovered controller Python/environment. The basic non-disruptive commands are:

```sh
python -m jev_factorio.operations --checkpoint "$CHECKPOINT" request --timeout 120
python -m jev_factorio.operations --checkpoint "$CHECKPOINT" verify "$REQUEST_ID"
```

These commands do not stop a process. The running new controller closes admission, reconciles existing work, accounts for tracked background jobs and native controls, and publishes a fresh acknowledgement bound to the request/session/checkpoint. A timeout leaves the request intact and does not authorize a stop. Explicitly release an abandoned request only after confirming that no stop/deployment command is still running:

```sh
python -m jev_factorio.operations --checkpoint "$CHECKPOINT" release "$REQUEST_ID"
```

For an integrated planned stop, run the new `factorio-maintenance-gate.py` inside the existing target wrapper with the reviewed controller on PYTHONPATH:

```sh
python factorio-maintenance-gate.py prepare production   --service-script "$REVIEWED_SERVICE_SCRIPT" --service-sha256 "$REVIEWED_SHA256" --timeout 120
python factorio-maintenance-gate.py verify production   --service-script "$REVIEWED_SERVICE_SCRIPT" --service-sha256 "$REVIEWED_SHA256" --request-id "$REQUEST_ID"
```

After quiescence, switch OBS to maintenance through its connected control interface and obtain a fresh readback. The helper's `--obs-evidence` file must contain the fields below from real observations, not invented values or this example. Hash canonical audio configuration before/after the scene change; confirm that the hashes match. Keep credentials and full OBS authentication payloads out of the record.

```json
{
  "schema": 1,
  "target": "production",
  "session_id": "ACTUAL_CURRENT_NATIVE_SESSION",
  "at": 0,
  "current_scene": "JEV - Maintenance",
  "streaming": true,
  "audio_before_sha256": "ACTUAL_64_HEX_DIGEST",
  "audio_after_sha256": "SAME_64_HEX_DIGEST"
}
```

The example intentionally fails freshness/digest validation. Use the current scene name; the default is historical and must be confirmed. Then:

```sh
python factorio-maintenance-gate.py stop production   --service-script "$REVIEWED_SERVICE_SCRIPT" --service-sha256 "$REVIEWED_SHA256"   --request-id "$REQUEST_ID" --obs-evidence "$FRESH_OBS_READBACK"
```

The helper verifies current supervisor/worker identity, fresh acknowledgement, unchanged pointer and supplied OBS readback, writes `cutover-REQUEST_ID.json`, and delegates signalling to the existing guarded stop. It does not control OBS itself. A missing/unknown stop outcome leaves the preparation receipt and refuses another stop under that request. Inspect actual processes/artifacts instead of repeating a timed-out mutation.

**First-install exception:** the previous controller does not implement this handshake. Do not claim that it has drained after writing a new marker. If no already-stopped, reconciled campaign or independently reviewed safe old-release boundary exists, initial rollout remains blocked. An ordinary service stop remains an emergency/unavoidable interruption, not a quiescence proof.

## 4. Deploy and verify without resetting ownership

Publish/merge and synchronize through repository policy; install the reviewed controller/helper/units using the existing domain process, then verify the unit actually preserves exit 2 and the wrapper propagates it. Retain the immutable campaign pointer/start/cutoff and original checkpoint/session. Do not rewrite manifests or invent accepted-repair events. Preserve the pre-cutover source and environment for rollback.

The maintenance marker intentionally remains active after stop. Start the accepted revision with the same checkpoint and session, confirm identity and fresh admission-closed telemetry, then explicitly release that exact marker to permit gameplay. A startup adapter must not attach while owned native work remains unresolved.

Acceptance must cover a destructible-obstacle route, a safe alternate route, genuine no-path handling, multiple new verified gameplay actions, transfer receipts, and background production transitions. Measure actual observation duration and native ticks/receipt IDs. Confirm the original deadline, same character/world and no duplicated effects. Only after fresh successful gameplay, restore the gameplay scene through OBS and read it and stream status back, including audio/overlay checks. Never call a successful mock test native acceptance.

## 5. Provider failure

Inspect the fixed-vocabulary provider circuit and health sidecars. During a cooldown, the controller stays attached and does not treat provider denial as a gameplay-plan failure. Existing owned/background work is reconciled before planning. A successful, schema-valid inference can recover automatically within the current bounded probe budget. A 403 is not presumed transient. Schema failures and prolonged denial remain explicitly exhausted.

After the original provider/account is genuinely restored and evidence reviewed, authorize another bounded generation, without replacing accounts, credentials, provider or model:

```sh
python -m jev_factorio.operations --checkpoint "$CHECKPOINT" authorize-provider-probes   --incident-id "$ACTUAL_PROVIDER_INCIDENT" --evidence "$REVIEWED_RECOVERY_EVIDENCE"
```

This persists the evidence digest and prior circuit history. It is not a repair-Codex account unlock and does not clear campaign failure budgets. Repair-account quota exhaustion is separately retained in supervisor state; do not launch identical repair commands repeatedly.

## 6. Storage attribution and retention

Choose explicit, non-overlapping current roots for campaign evidence, container storage, caches, unrelated workloads and retained incidents. Avoid printing secret filenames/content. Examples below intentionally leave real paths to current discovery:

```sh
python -m jev_factorio.storage_audit --root "controller=$CONTROLLER_ROOT"   --root "containers=$CONTAINER_STORAGE_ROOT" --root "incidents=$INCIDENT_ROOT"   --maximum-entries 200000 --seconds 15 --output "$CAPACITY_DEST/usage-before.json"
# After a measured interval, take another snapshot with identical roots:
python -m jev_factorio.storage_audit --root "controller=$CONTROLLER_ROOT"   --root "containers=$CONTAINER_STORAGE_ROOT" --root "incidents=$INCIDENT_ROOT"   --maximum-entries 200000 --seconds 15 --previous "$CAPACITY_DEST/usage-before.json"   --output "$CAPACITY_DEST/usage-after.json"
```

A partial scan has no trustworthy growth rate. Cross-root hardlinks are counted once with deterministic root ordering; shared filesystems and open-deleted files require separate read-only host inspection. CPU pressure/load samples are observations, not enough by themselves to set a quota. Measure controller/repair/validation contention and game/stream responsiveness before applying narrowly scoped CPU controls.

Archive only an explicit approved manifest of sealed, inactive, non-pending-referenced files. Protect active run, checkpoint, supervisor and incident roots. The tool refuses a same-filesystem destination, symlink traversal, hardlinked source files, protected names and exceeded capacity budget. Copy hashes and durable archive receipts precede any deletion. Keep the approved manifest itself outside the selected prune scope. For archive-only, omit `--prune`:

```sh
python -m jev_factorio.retention "$APPROVED_SEALED_MANIFEST" "$ARCHIVE_DEST"   --maximum-bytes "$ARCHIVE_CAPACITY_BYTES" --protect "$ACTIVE_CAMPAIGN"   --protect "$INCIDENT_ROOT"
```

Deletion requires separate explicit authorization by adding `--prune`, within that exact approved manifest only. Do not prune broad Podman/image/cache directories. Moving files on the same full filesystem is not capacity relief. This does not automatically rotate active research hash chains or configure an archive schedule. The low-space reserve is advisory: if a competing writer exhausts it, preserve prepared/pending action evidence and reconcile the native effect, never simply rerun the command.

## 7. Escalation, cutover failure and rollback

Exit 2 means deliberately blocked; `RestartPreventExitStatus=2` must prevent the wrapper's restart loop while leaving the failure visible. Unknown exits are not code-defect evidence. Preserve incident IDs, audit outboxes, budgets, repair reports and source/effect provenance. Do not edit away `repair_account_blocked`, `incident_repair_attempts`, pending state or reservations as an unlock procedure.

Rollback triggers include wrong session/character/deadline, unverifiable receipt, duplicate effect, broken trace/checkpoint durability, schema mismatch, failed service identity/publication gate, or lost stream/audio invariants. Keep maintenance displayed. Do not automatically restart an older revision across an active/ambiguous action. First reach or evidence a safe boundary, confirm old-version checkpoint compatibility (including new safety sidecars), then restore only the reviewed source/environment and authorized deployment pointer while preserving current gameplay state. If rollback would destroy or reinterpret ownership evidence, stop and escalate instead.

Finish with exact merged/deployed SHA, actual hosted checks and independent review, measured downtime/detection/recovery/blocked durations, and fresh action/OBS evidence. None of those production results is supplied by this offline package.

## Integration review hardening

The integration review added shared operator locking and pointer binding around
maintenance stop/release, including refusal to release an unresolved stop.
Mining approaches now retain the actual native entity, actor, surface, position,
and a single-use token across detours; harvesting cannot silently mine a
replacement resource at the same coordinate. Dashboard event storage is checked
both before live attachment and during runtime admission.

Every provider request now persists an in-flight reservation before dispatch,
including the first request while healthy. A restart with an unresolved first
request records an exhausted `unknown_outcome` incident, not an invented HTTP
status. Unresolved retries retain their charged attempt and original budget;
later error categories cannot widen that budget. Result-write failures preserve
the reservation, and ordinary successful calls do not report a recovery.

When running checkpointed tests inside a container with a small tmpfs, set
TMPDIR to a test-only directory on a filesystem with at least the configured
storage reserve. Do not lower the production reserve merely to make fixtures
advance. Production's world and campaign do not participate in these tests.
