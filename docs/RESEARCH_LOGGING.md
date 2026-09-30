# Research logging core (V1)

This is the first logging increment. `--run-dir` creates an **exclusive new run
for one CLI invocation**. It does not resume or append an earlier research run.
A controller may explicitly resume its existing game/checkpoint into a **new**
research directory. Never delete a checkpoint or reset a world to reuse a log.

## Usage

```sh
python -m jev_factorio --controller hierarchical --backend mock --mock-model \
  --target bootstrap_mining --steps 40 --tick-seconds 0 \
  --run-dir runs/research-001 --log-file runs/research-001/decisions.jsonl
python -m jev_factorio.research_log runs/research-001
python -m jev_factorio.evaluation runs/research-001/decisions.jsonl
```

`JEV_RUN_DIR` is the environment/.env equivalent. Explicit CLI arguments override
it, and exported environment values override `.env`. The directory must not
already exist, even empty. Parent directories may exist and are not chmodded.
The same option works with the flat controller. For fully offline flat runs,
explicitly clear `TYPESAFE_API_KEY` and `CLOUDFLARE_API_TOKEN`.

`--log-file`/`JEV_LOG_FILE` is unchanged and independently optional: it still writes
the original decision JSONL with its original schema, append behavior and
existing evaluator compatibility. It is **not automatically hash-protected**.
The CLI refuses log/checkpoint aliases that overlap internal research files.
Without `--run-dir`, no research files or provenance collection are introduced.

## Scope and files

| File | Purpose |
| --- | --- |
| `manifest.json` | Immutable-by-writer configuration and allowlisted local provenance. |
| `events.jsonl` | Append-only, fsynced, versioned event envelopes. |
| `integrity.json` | Final event count, manifest hash and final-event hash; created only on finalization. |

The CLI emits `run_started`, `controller_initialized`, `controller_stopped` and
`run_finished`. Exceptions omit the normal stopped event and record only the
exception **type**, not its message, in `run_finished`. A sealed lifecycle may
have outcome `returned`, `error`, or `interrupted`: **complete evidence is not
proof of successful gameplay or native victory**. Uncatchable termination may
leave unsealed or torn evidence. The verifier does not interpret a run cutoff as
a verified milestone.

PR 1 intentionally does not emit per-observation/request/action/verification
events, add new game observations, wrap `step()`, alter policy/checkpoints,
change supervisor authorization, or merge streams across repaired revisions.
Those remain subsequent instrumentation/segmentation work. Lifecycle fsync and
provenance collection add overhead; no native timing equivalence is claimed.

`ResearchLog.emit()` is the core API for future instrumenters. Supply only facts
already obtained by the controller. `factorio_tick` and `session_id` may be null;
correlation may contain nonempty string `decision_id`, `model_call_id`, `plan_id`
and `action_id`. Unknown facts must not be filled with synthetic values.
The two-positional-argument `EventSink.emit(event_type, payload)` protocol also
supports causal instrumentation: payload `session_id`, `factorio_tick`, and the
four correlation IDs populate the envelope when corresponding explicit keyword
arguments are absent or null. These facts remain in the payload; `trace_id`,
`observation_id` and `world_kind` remain payload-only. `safe_payload` detaches
causal data, labels nonfinite numbers and opaque values, and redacts credentials;
the canonical writer itself continues to reject non-JSON input.

## V1 envelope and serialization

Events have exactly these fields:

```text
schema = jev-factorio.event.v1; schema_version = 1
run_id = canonical UUID; sequence = positive integer, starting at 1
event_type = lowercase identifier (up to 64 characters)
time = {utc, monotonic_ns, factorio_tick}
session_id = string or null
correlation = object with the optional IDs above
payload = JSON object
prev_hash = sha256:<64 lowercase hex digits>
event_hash = sha256:<64 lowercase hex digits>
```

UTC is timezone-aware ISO 8601 with six fractional digits and `Z`. Wall-clock
adjustments are permitted; monotonic nanoseconds cannot decrease **within this
one process/run**. Neither monotonic time nor sequence is a global clock across
machines, restarts or game sessions. Sequence allocation and writes are protected
by a thread lock; a PID guard rejects sharing the writer after fork. Exclusive
creation prevents a second writer from opening the same run, not two controllers
from opening the same Factorio world. Existing single-controller precautions apply.

V1 canonical bytes are Python JSON with sorted keys, compact separators,
`ensure_ascii=True`, `allow_nan=False`, then ASCII encoding. This is an explicitly
versioned Python encoding, **not RFC 8785**. Only dictionaries with string keys,
lists, strings, finite numbers, booleans and null are accepted. No implicit string
conversion of objects, tuple coercion, duplicate keys or nonfinite numbers.
The stored line is canonical bytes plus one LF. Each document/event is at most
1 MiB including its LF; the verifier streams events with bounded line reads.

The manifest uses schema `jev-factorio.manifest.v1`. Its hash is the first event's
`prev_hash`. New manifests also record treatment settings `factory_scheduling`,
`background_work`, `furnace_output_buffers` and `furnace_input_belts` in
configuration, plus the explicit run-mode fields `until_complete` and
`reconcile_only`. Older V1 manifests may omit additive treatment/run-mode fields;
their historical defaults are serial scheduling, disabled opt-ins, and both run
modes disabled. Core configuration fields remain required and unknown fields are
rejected.
Each event hashes its entire envelope **except `event_hash`**;
`prev_hash` points to the previous event hash. The start payload also names the
manifest hash. Redaction happens **before** hashing and writing. The final seal
uses `jev-factorio.integrity.v1` and binds run ID, manifest hash, event count and
final event hash. Strict validators reject unknown schema versions/fields.

## Durability and incomplete evidence

Files are created with exclusive creation and mode `0600`; newly created run and
parent directories use `0700` (POSIX modes, not a replacement for Windows ACLs).
Writes flush before `os.fsync`. On POSIX, newly created directory entries are
fsynced as well; unsupported/failing fsync is an error, not a silent downgrade.
Windows records `file-fsync-only`, because directory fsync is not exposed here.
Filesystem/hardware guarantees still apply; use a trusted local directory.

Logging initialization and a durable run-start record precede backend creation.
This is not a filesystem/game transaction: a later logging failure cannot undo
backend initialization or a game action. An uncertain append poisons the writer;
it cannot append later events or claim a complete sealed run. Partial artifacts
are retained for inspection, not automatically deleted/repaired/reused.

```sh
# Accept an intact, unsealed prefix as incomplete; never ignore a torn line.
python -m jev_factorio.research_log runs/research-001 --allow-incomplete
# Compare to a separately retained trusted anchor:
python -m jev_factorio.research_log runs/research-001 \
  --expected-final-hash sha256:REPLACE_WITH_64_HEX_DIGITS
```

Default verification requires a matching seal and terminal event. Reordering,
duplicate events, edits, manifest changes, sequence gaps, mixed runs, malformed
JSON and retained-seal suffix deletion fail verification. An unsealed valid
prefix is reported `complete: false`, never silently upgraded. The tool is
read-only and does not initialize a backend, load `.env`, call an API, or modify
checkpoints. Exit status is nonzero on invalid/missing/incomplete evidence unless
an intact incomplete prefix was explicitly allowed.

**Hashes are not signatures.** Removing a valid suffix and the seal is
indistinguishable from an interrupted run without an external anchor. Someone
who can rewrite every artifact can rehash the entire run. Retain the final hash
separately when publishing evidence. Local verification alone does not establish
authenticity, independent execution, or experimental reproducibility.

## Secret-safe provenance

The manifest records explicit non-path CLI settings, Git commit/dirty status
when locally available, Python/system/architecture, and versions of a small
allowlist of relevant distributions. Missing facts remain null. Git provenance is
recorded only when the executing logging module is tracked inside that
worktree; copied site-packages installations inside another checkout stay unknown.
Package versions still identify the installed distributions. Git probes are
local, read-only, timed, and never contact remotes. Capture precedes run-directory
creation to avoid the logger itself changing the dirty flag. A dirty flag is not
a source snapshot or a reproducibility guarantee; code patch capture is deferred.

Credential presence is boolean only. No environment dump, raw argv, remote URL,
account ID, hostname, username, filesystem path, credential value, source diff,
HTTP headers/body, or exception message is automatically captured. Requests for
an alias such as `jev-latest` are preserved as requests, not treated as immutable
resolved model identities. `controller_initialized` records the client's model
setting and mock flag without making another provider call.

Payload redaction is defense in depth: sensitive keys, known secret environment
values, authorization strings and HTTP(S) URLs are removed before serialization.
Secrets shorter than four characters are matched only as whole string values,
because arbitrary substring replacement would destroy ordinary evidence.
Allowlisted provenance, schema keys, event types, lifecycle outcomes and hashes
are structural facts and are not subjected to environment substring replacement.
It does not mutate caller data. Arbitrary secrets without a recognizable key,
encoded secrets, and private natural-language/game/repository content cannot be
guaranteed detectable. Instrumenters must avoid raw dumps and pass explicit safe
fields. Existing legacy decision logs retain their original confidentiality and
durability limitations; protect them separately.

## Tests

```sh
PYTHONPATH=src python -m pytest tests/test_research_log.py tests/test_research_cli.py -q
PYTHONPATH=src python -m pytest tests/ -q
python -m compileall -q src
```

Core tests cover schema/serialization, ordering, clocks, concurrent writers,
redaction, provenance, size limits, corruption, missing seals, partial tails,
write/flush/fsync failures and the offline verifier. Integration tests compare
actual flat/hierarchical mock controllers with/without logging, test legacy
compatibility, path aliases, configuration precedence, startup failure ordering,
exceptions and interruption. These are synthetic/offline checks, not live FLE
acceptance or native performance results.
