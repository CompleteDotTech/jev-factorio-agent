# Retained e759 coherent observer migration

This is a one-time owner operation for a retained e759 native campaign after
its pending placement has been reconciled. It is not part of normal resume and
does not install the connector ledger or any other current Lua module.

The owner must stop dispatch, retain the original server/save and checkpoint,
and verify that no other controller is running. The function acquires the
existing `single-writer.lock` exclusively; it must not be preheld on a second
descriptor. Pass the exact original session ID, actor unit, checkpoint target,
SHA-256 of the private checkpoint and SHA-256 of the original v1 attachment
receipt to `migrate_legacy_observation_v2`. The function locks the same owner
file, validates private evidence and the v1 source-bound native callback chain,
and requires no active plan, reservation, background job or attempt, pending
action, foreground attempt, or transfer recovery. A blocked
checkpoint is allowed; migration never unblocks or edits it. The actor must be
idle at normal speed immediately before the native command.

One Lua `pcall` command replaces only `campaign.observation_snapshot_v2` with
the exact reviewed output-tile observer from PR #161. It installs a version 2
profile containing the original e759 hashes for all retained modules and the
new observer hash. An in-command error restores the original observer and
removes the new manifest. A successful readback must match the same session,
actor, module set, hashes and callback chain. The original v1 receipt is
retained. The profile disables paid connector actions because that capability
was never installed in this campaign.

If the RCON acknowledgment is missing, the result is ambiguous. Do not rerun
the migration command. Inspect the native manifest and callback chain read-only
under the same owner lock, retain all evidence, and repair forward. A disk
checkpoint or controller blocked state must be handled by its existing owner;
this migration never changes them.
