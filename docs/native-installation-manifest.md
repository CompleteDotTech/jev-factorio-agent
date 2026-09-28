# Versioned native installation receipt

Fresh native installations now append a version 2 receipt to the live
`jev_fle_runtime` after each exact bundled Lua installer succeeds. The append is
part of the same RCON command as the installer. It records the session ID,
actor unit and SHA-256 of each installed Lua asset. A failed or partial command
cannot create a complete receipt, and a second install may repeat only the
same asset hash.

On resume, a read-only probe checks the original actor, normal game speed,
active callback chain and installed modules. The adapter then requires every
module flag to have one matching receipt hash and requires those hashes to
equal the local source bytes. A missing, altered or partial receipt stops
reattachment before any installer or controller action. Installer commands are
also rejected if a checked attachment is active.

The isolated e759462 campaign predates this receipt. It remains on the
source-bound v1 private-receipt path from PR #154 and can only be reattached
with its exact original Lua bytes. Version 2 does not convert it, infer missing
hashes or grant a source upgrade. The connector ledger introduced with PR #155
is supported only on fresh installations that recorded its asset hash.
