# Current campaign review snapshot

Snapshot of `autonomous-20260922T0240Z`, started September 22, 2026 at
02:16:21 UTC and captured around 10:18 UTC (about eight hours of elapsed run
history, including downtime). The live campaign was not stopped or modified.

## Review entry points

- `gameplay-recent-100.jsonl`: compact summaries of the latest 100 decisions.
- `gameplay-recent-100-full.jsonl.gz`: their full redacted records.
- `gameplay.jsonl.gz`: complete captured gameplay history.
- `gameplay.log`: readable controller activity and diagnostics.
- `events.jsonl`, `supervisor.json`, `state.json`: audit history and current state.
- `research/`: recorded research invocation evidence, including large compressed logs.
- `repair*`, `independent-review*`, `audit-reconciliation*`: repair, acceptance,
  and audit recovery evidence.
- `manifest.json`: capture boundaries, file sizes, redaction counts, and SHA-256
  checksums of exported artifacts.

Code baseline: `cb77cde65a7b031c1ce3f92fc74047e70b16d4e4`.
Historical events may have been produced by older code revisions.

Read compressed records with `gzip -cd review-logs/gameplay.jsonl.gz | less`.

## Limits and safety

Files were captured independently while the campaign continued, not as an atomic
cross-file checkpoint. Incomplete final JSONL records are excluded and counted.
Known credentials, sensitive fields, credential patterns, and endpoint URLs are
redacted. Environment files, game saves, images, and unrelated runs are excluded.
This is a review copy, not a resumable checkpoint or original forensic evidence.
Embedded original integrity hashes refer to original evidence; use the export
manifest for checksums of these redacted copies. No merge or deployment is implied.

Verify exported checksums from the repository root:

```python
import hashlib
import json
from pathlib import Path

root = Path("review-logs")
for item in json.loads((root / "manifest.json").read_text())["files"]:
    assert hashlib.sha256((root / item["file"]).read_bytes()).hexdigest() == item["sha256"]
print("All snapshot checksums match")
```
