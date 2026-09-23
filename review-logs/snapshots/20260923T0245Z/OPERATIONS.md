# Explicit recovery after server rollback

## Supervisor ownership during repair

The supervisor alone owns the live supervisor.json and events.jsonl.
A repair worker must not construct a second Supervisor against this directory
or call its capture/source_identity/validate_repair methods: those methods write
live state and audit events and launch tracked processes, even for verification.
Use ordinary read-only subprocesses and isolated temporary state for worker tests;
write the repair result and release ownership. The owning supervisor performs
acceptance. Manual acceptance requires stopping the owner, acquiring its actual
lock, and holding that lock throughout verification and state transitions.

Repair 1 previously ran a second verifier concurrently with the original owner,
advancing the audit to sequence 37 while the owner retained pending sequence 22.
All divergent evidence is preserved in audit-reconciliation-20260922 and the
reconciliation receipt. Do not truncate events or repeat that verification pattern.
Consult repair-reacceptance.json for the final acceptance result. The first two
validation runs were stopped because temporary-file fsync was stalled on the
workspace disk; the complete gate was rerun with pytest's basetemp on /tmp and
passed 1334 tests with four skips. Original failed attempts remain in the audit.
A prepared report is not proof of resume.

The user requested fixing the disconnected stream and stopped campaign.
The viewer disconnect triggered an inherited FLE inventory GUI callback crash.
The server automatically loaded autonomous-fresh-20260922T0212Z.zip, losing the
live runtime and campaign progress. Do not describe this as seamless resumption.
The old checkpoint is incompatible and retained unchanged in
runs/autonomous-20260922T0212Z, with its complete logs and incident report.

This is a fresh controller initialization on the reloaded world, seed 22092026,
not replay of the old pending action and not a new 24-hour allowance.
started-at deliberately retains 1790043381; cutoff stays 2026-09-23 02:16:21 UTC.
All four production modes stay enabled. The supervisor owns gameplay restarts.
Do not routinely restart the server or viewer: native receipts are ephemeral.
The video capture service, not the viewer, must restart for a stale stream.

Guest factorio DMI UUID: 20aaee85-ceb7-48a9-ae44-18d4ed95792e.
Viewer user service: jev-factorio-viewer-20260922-0212.service.
Video user service: jev-factorio-video.service.
Server service: jev-factorio-server.
Guest helper: python3 /tmp/factorio-guest-command.py factorio 'SHELL'.
RCON: .venv/bin/python runs/native-rcon.py 'LUA'.
Never print credentials or server process arguments.

Follow docs/FAIR_PLAY.md and docs/AUTONOMOUS_SUPERVISION.md.
Preserve 1x speed, original actor, native movement/mining, paid construction,
pending receipts, background jobs, input commitments, and failure budgets.
No item grants, teleportation, fabricated progress, or silent checkpoint edits.
Local changes include validated supervisor launch flags and inventory callback
quarantine. They are not published; preserve these changes during repair.
For code repair publication, require independent exact-head review, green checks,
guarded merge, and origin/fork synchronization. Never push upstream.

## Stream recovery 2026-09-22
Direct RCON must inspect jev_fle_runtime, not global storage; ScopedRcon aliases local storage to that runtime. The original actor, session and furnace remained intact. PR50 adds reviewed no-effect transfer reconciliation. Preserve the native world and pending receipts. Private browser-test dependencies use the inherited LD_LIBRARY_PATH and FONTCONFIG_FILE/FONTCONFIG_PATH; do not discard them or skip browser tests.
