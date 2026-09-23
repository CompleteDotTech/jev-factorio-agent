# Current campaign log review

Run: `autonomous-20260922T0240Z`; session: `3562a7a347b54be5823716bd06be5af1`.
Campaign start: 2026-09-22T02:16:21+00:00.
Capture: 2026-09-23T02:46:23.568226+00:00 through 2026-09-23T02:58:59.419886+00:00.
Review branch code base: `6c651b8c3da3fd7a29342a1141fdebc4f841da12`; historical records may have other revisions.

Start with `gameplay-recent-100.jsonl`, `gameplay.log`, `supervisor.json`,
`state.json`, and `events.jsonl`. Full gameplay and research history,
repair reports, and audit recovery evidence are included recursively.
Large files and the full latest-100 records are gzip-compressed.
From this snapshot directory, use `gzip -cd gameplay.jsonl.gz | less`.

## Boundaries

Elapsed campaign time includes pauses and repairs, not just active play.
Files are independently bounded snapshots, not an atomic checkpoint.
Incomplete trailing JSONL bytes are omitted and counted in `manifest.json`.
Credentials and endpoint URLs are redacted; environment files, saves,
media and symlinks are excluded. See the manifest for exact exclusions.
Embedded original integrity hashes are not checksums of redacted copies.
Export SHA-256 checksums and byte sizes are recorded in the manifest.
This is review evidence, not a resumable checkpoint or proof of recovery.
The live campaign, world, stream, deadline and source logs were not changed.
