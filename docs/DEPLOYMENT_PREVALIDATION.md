# Full-suite validation before maintenance

Run the complete suite in a clean isolated checkout of the **merged commit**
while the existing campaign continues. Use the same interpreter and installed
dependencies that production acceptance uses. The staging checkout must have
no `.env` file. This command never attaches to Factorio, changes campaign state,
requests maintenance, or advances a deployment pointer:

```sh
PYTHONPATH=src /production/venv/bin/python -m jev_factorio.prevalidation run \
  --state-dir /campaign/supervision --ttl-seconds 3600
```

It emits `{"artifact_id":"…"}` after the fixed full suite succeeds. Output,
JUnit, and the content-addressed completion manifest live privately under
`STATE_DIR/prevalidation/ID/`. A separate nonblocking verifier lock serializes
runs without locking gameplay. The suite has a one-hour timeout. Failed runs
retain private incomplete evidence but never publish a reusable ID.

Before requesting maintenance, check the explicit ID:

```sh
PYTHONPATH=src /production/venv/bin/python -m jev_factorio.prevalidation check \
  --state-dir /campaign/supervision --artifact-id ID
```

The code-repair result can include `"prevalidation": "ID"`. Acceptance still
checks GitHub merge/review/checks, configured remotes, and clean exact HEAD before
and after verification. Only the repeated pytest invocation is replaced. Omit
the field to retain ordinary full-suite acceptance; an invalid explicit ID
fails closed. No automatic rollback, pending-action reset, or world migration
is implied.

The artifact binds exact commit/tree, interpreter content/version/ABI,
installed distribution file contents (including pytest/plugins), host/OS,
hashed allowlisted environment values, fixed command, passing JUnit counts,
log/report hashes and a maximum one-hour expiry after completion. Source/runtime
fingerprints must match before and after the suite. The child receives only
OS/path/locale essentials plus an explicit candidate source path and private
temporary directory; provider/RCON variables are not inherited. Import origin
is checked before tests to reject an editable installation from another tree.

This is operational evidence within the existing trusted same-UID operator
model, not protection against an owner who can rewrite code and supervisor
state. Do not import arbitrary evidence directories. Content hashing adds I/O;
schedule staging under resource headroom and measure its cost before promising
a cutover duration. Host load, native gameplay correctness, and changing
external services are not certified by an offline suite. Native session,
checkpoint, quiescence, source and maintenance gates still apply at deployment.
