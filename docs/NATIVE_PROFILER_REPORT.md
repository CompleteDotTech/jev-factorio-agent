# Native profiler stages in latency reports

Run the offline report against one source-bound gameplay JSONL stream:

```sh
PYTHONPATH=src python -m jev_factorio.latency_report path/to/gameplay.jsonl > latency-report.json
```

The report now exposes the fixed `campaign_snapshot`, `discovery`, and
`serialize` native profiler stages under
`observation_native_stage:<stage>:nested`. Each distribution contains only
observations with a recognized, bounded native profiler reading. Its
`count`, `median_ns`, `p95_ns`, and `total_ns` use nanoseconds. A measured zero
is a sample; a missing or unrecognized stage is unavailable and contributes no
sample. Per-stage available and unavailable counts add to
`observation_profiles`.

For example, three sanitized observations with campaign profiler readings of
2 ms, unavailable, and unavailable produce a campaign distribution with
`count: 1`, `median_ns: 2000000`, and `total_ns: 2000000`, plus
`observation_native_stage:campaign_snapshot:unavailable: 2`. These native
stages are nested within the inclusive observation RPC. Do not add them to
observation wall or process CPU totals, and do not interpret them as native CPU
or transport latency. Legacy records without profiler fields remain readable;
malformed stage names, values, or contradictory availability are rejected.

This source reporting contract does not establish a native performance gain.
The staged acceptance owner must compare equal observation semantics under
recorded load with exact code, dependency, and configuration provenance.
