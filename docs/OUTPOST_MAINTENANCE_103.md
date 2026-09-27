# Outpost demand-aware maintenance follow-up

Related: #98 (science/ready-output preservation), #99 (bounded manual fuel),
#93 (decision-local planning reuse), parent #103. This is **not** a coal mining
and distribution implementation for #101 or native acceptance for #92.

## Fresh reproduction and integration boundary

The outpost mixin retained a separate exclusive fuel override after the input
and output mixins were corrected. A commissioned outpost with stocked ore and
fuel below two coal constructed another planner, then replaced ready science
candidates with a fuel transfer or acquisition. Its inner planner also requested
fuel before collecting already-owned commissioned ore. A commissioned drill used
an isolated target of twenty coal rather than the shared demand/lead-time policy.

The paired controller fixtures change only outpost fuel (0, 1, 2) and carried
coal (0, 50). Both low-fuel values must retain feasible lab delivery; ready science
crafting and collection of sufficient commissioned ore must remain available.
The root planner-construction check measures constructor count, not elapsed-time
speedup. It excludes intentionally distinct speculative workers.

The final integration base is the merge of #119,
`738ce985837df5659a76b80208ea250b68a6730b`. It includes #117's immutable checkpoint
capture and shared carried-input allocation and #118's bound actor capacity and
fresh gather checks, plus #119's sequential-request accounting. Those implementations
are preserved, not replaced by older
unpublished follow-up variants.

## Policy

The controller now preserves the candidate frontier returned by its effective
composed planner, applying the unchanged execution/ownership barriers once.
Required maintenance belongs at the relevant resource dependency in `_need()`;
there is no second root planner or unrelated low-fuel singleton override.

The inner outpost planner collects already-paid output before refueling when the
existing bounded collection/tail threshold is satisfied and the current owned
flow certificate is valid. Uncommissioned stock cannot be drained to manufacture
progress. Required unstocked production still requests fuel; topology, depletion,
construction costs and commissioning waits retain their original boundaries.

Required outpost service uses the existing shared fuel service policy. A second
outpost joins only when it is current, paid, commissioned, non-depleted and its
explicit current ore demand exceeds carried spendable ore and its ready chest
output. Identity alias checks, capacity, failure budgets, distance/time bounds,
lead-time estimates and science/power constraints remain in the shared helper.
No second inventory ledger, receipt protocol, action identifier or reservation
model is introduced. Optional uncommissioned, covered, idle, replaced, exhausted,
full or too-distant outposts do not inflate the coal target.

Only one action is returned. The normal controller must perform a fresh
observation/precondition check, durable preparation, sequential actuation and
receipt verification. Later decisions recompute the due group after partial
transfers or changed stock; the annotation is never future action permission.
A burner that reaches the existing two-coal low-fuel threshold is no longer due,
even if an earlier optional target was five. Held coal cannot be spent.

## Evidence and tests

Run on Linux with the repository's declared dependencies:

```sh
PYTHONPATH=src python -m pytest tests/test_outpost_maintenance_progress.py tests/test_resume_failure_kinds.py -q
PYTHONPATH=src python -m pytest tests/ -q --ignore=tests/test_dashboard_browser.py
PYTHONPATH=src python -m compileall -q src/ tests/
git diff --check
```

The outpost regression file covers paired science delivery/crafting, actual
composed receipt-verified delivery over repeated decisions, ready ore collection,
required bootstrap/refueling, root planner construction, two-outpost acquisition,
partial receipt replanning, held fuel, actor capacity, failed primary/optional
transfers, urgent boiler precedence and retained ownership/failure history under
faults. Lab consumption and all receipts inside the fixtures are synthetic.
The additional resume test varies failure kind (validation, I/O, interruption)
and checks provisional memory is absent before diagnostics can persist it.
It does not alter the merged resume implementation.

No native game/server, live provider, paid network or production-host measurement
is part of these tests. Source CI cannot establish native throughput, production
capacity, deployed SHA/configuration, or the required uninterrupted thirty-minute
useful-progress acceptance window. Independent final-source review, configured
SSH signing, hosted checks, normal publication/merge, deployment and #92 evidence
remain separate gates. A missing browser executable is a missing local browser
qualification, not a browser pass.

## Rollback boundary

The change introduces no native runtime extension or checkpoint schema. Before
publication, discard only the owned isolated worktree or reverse the exact patch
on its exact clean base. After publication, use a reviewed revert through normal
repository controls. For a deployed treatment, follow the established authorized
handoff and preserve the original campaign/checkpoint/pending identities and
cutoff; do not hot-swap source in an immutable treatment or restart a shared host.
A rollback may restore the previous starvation behavior, so retain its evidence
and do not claim operational recovery from merely reverting source.
