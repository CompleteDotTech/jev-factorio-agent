# Explicit isolated acceptance window

The read-only `jev_factorio.native_acceptance_preflight` command accepts two
operator-manifest versions. Neither verifies authority, starts a campaign,
changes a deadline, or proves native acceptance.

Version 1 retains the original timed-campaign contract: both UTC cutoff fields
must name the same original deadline, and the remaining window must accommodate
the useful-progress measurement plus rollout margin. Use it for existing timed
production campaigns.

Version 2 represents an explicitly authorized isolated run that has no cutoff.
Keep all version 1 implementation, runtime, save, experiment and handoff evidence;
replace only these fields:

```json
{
  "schema": 2,
  "original_cutoff_utc": null,
  "runtime_cutoff_utc": null,
  "window_policy": {
    "mode": "until_complete",
    "scope": "isolated",
    "authorization_sha256": "<SHA-256 of the retained private authorization record>"
  }
}
```

This is a partial example, not a ready manifest. Both null cutoff fields must be
explicitly present. A non-null original or runtime cutoff is rejected; v2 cannot
be used to remove a timed campaign's deadline. Keep a separate isolated identity,
save and owner, and independently verify that the authorization record actually
permits this scope. A syntactically valid digest is an operator claim, not proof.

The v2 report uses `remaining_original_window_seconds: null`, includes the
explicit isolated policy and leaves `authority_verified: false`. It does not
invent a distant deadline or report infinity. The minimum 1,800-second useful
science/research window, source checks, paid ownership, recovery evidence,
immutable treatment and native acceptance requirements all remain in force.
Preserve checkpoints, pending receipts and failure history between stages.
