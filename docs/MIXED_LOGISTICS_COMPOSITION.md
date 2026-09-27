# Bounded coal and downstream composition

Refs #100, #101, #102, #92 and #103. This is experimental source and regression
coverage, not a signed/merged delivery, independent approval, native run, deployment
permission, or tracker completion. No production CLI/supervisor activation is added.

## One immutable treatment and one actor

`coal_loop_type(solid_loop_type(Base))` accepts every exact dedicated coal
intent plus disjoint non-coal ingredient routes in one fixed order whose destination
is `input`. The existing global bound is four routes, not four per capability.
Thus two coal consumers allow zero, one or two ingredient corridors; three coal
consumers allow at most one; four allow none. Endpoints remain unique across all
intents. Extra fuel routes and use of the reserved `coal:` namespace are rejected.
The pure intent validator is shared by the base controller/backend and coal binding.
Python construction, full checkpoint loading, native adapter attachment and Lua
configuration enforce the same composition. Returned configuration copies are detached.

There is no treatment migration. A retained coal-only checkpoint cannot acquire
downstream intents on resume. Reordered/changed intents fail before native attachment.
Native reattachment preserves existing bindings and paid state. Do not reset a
campaign, replace its treatment or extend its cutoff to activate this experiment.

## Costs and project selection

An initial paid coal component commits the entire source bundle. This includes
unplaced chests/drills AND corridors not yet exposed by a paid chest. Downstream
valuation now reserves all that future work, without counting a corridor twice
after its first paid part has its own solid commitment.

For a downstream construction step, its own remaining kit is the bill; other
projects remain reserved. Previously the coal layer reserved that same downstream
project and then charged its next component again. With exactly sufficient combined
stock, continuation could disappear after the first payment or acknowledgment
recovery. The corrected check retains the complete coal bill, the whole remaining
downstream bill and other owners' reservations once each. All fresh checks,
background-job holds, authoritative prepared/returned saves, receipts and actor
sequencing remain in force. A missing component rejects a selected action before
payment. Ordinary unrelated work cannot consume committed source parts.

The single active-project rule in downstream investment applies to input routes,
not paid coal corridors. Coal construction retains its own bounded least-progress
selection; it cannot consume the downstream project slot. The global pending barrier
still covers both. Existing useful production is compiled once and retained, not
replaced by either infrastructure frontier. Construction is not proof of material flow.

## Regression evidence and remaining gates

`tests/test_coal_solid_composition.py` exercises exact combined kit admission,
part-by-part reservation accounting, lost acknowledgment followed by checkpoint
reconstruction without duplicate payment, immutable treatment refusal, altered
ownership/failure-history preservation, fresh kit loss, unchanged useful frontier,
configuration bounds and real Lua extension control flow on a modeled API. The
Lua fixture builds two coal branches and a disjoint downstream corridor but does
not simulate actual engine physics or claim positive downstream flow.

Run with the declared test dependencies (including pinned Lupa):

```sh
PYTHONPATH=src python -m pytest tests/test_coal_solid_composition.py -q
PYTHONPATH=src python -m pytest tests/ --ignore=tests/test_dashboard_browser.py -q
python -m compileall -q src
```

Browser exclusion is explicit, not a passing browser result. API doubles, local
Linux filesystem checks, actual Factorio qualification, provider execution and live
acceptance must remain separate in every report. Suite time is not controller latency.

The coal source still requires an already-carried complete kit and existing owned
power. Automatic coal kit acquisition, coal demand/lead-time/avoided-hauling valuation,
broader topology and exact-engine qualification remain work, independently of the
signing/review/host-access gates. Main's merged #123-#126 downstream kit, queue,
corridor and counter guards are preserved. Distinct later funding-evidence,
capacity or checkpoint follow-ons must be reconciled from their exact source;
the prior merged packets must not be reapplied.

Obtain configured existing-identity SSH signing, independent final-source review,
exact-head hosted CI and normal merges before an authorized native handoff. Retain
source/configuration and ownership compatibility for rollback. No blind downgrade
after paid state, receipt erasure, world reset, shared-host restart or cutoff change
is authorized by this source. The native acceptance runbook still requires the
original authorized useful-progress window and actual measured flow/recovery.


This document describes the reconciled implementation, not the historical
packet tree. The native core retains the mixed whole-kit and pending barriers
from `e80b356f`; the downstream-project filter and regression cases from
`b8bf1585` are included without replacing those native guards. Solid revision 4
and coal revision 3 reject earlier attachments while preserving retained state.
The existing main downstream paid-kit feature remains supported; the missing
automatic-kit capability above concerns coal bundles only.
