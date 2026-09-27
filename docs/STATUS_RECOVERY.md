# Status checks, recovery, and the live overlay

A status question about an expected ongoing campaign includes authorization to
repair an unexpected stop. Complete diagnosis, safe recovery, and verification
instead of returning only a stopped status. This does not authorize restarting
an intentionally stopped/completed run, extending its original cutoff, changing
an immutable treatment, or bypassing an unresolved native-action boundary.

## Recover through the existing owner

1. Identify the active campaign, deployed source, supervisor, service owner and
   original cutoff. Check live processes, checkpoint status, event history and
   completed-action timestamps. A running PID or advancing game tick alone does
   not prove useful autonomous progress. Distinguish gameplay from development
   agents and completed issue-writing tasks.
2. Put the existing maintenance/recovery presentation on the live output while
   diagnosing. Confirm the dashboard follows the active campaign and retains
   original evidence timestamps. Do not relabel old observations as fresh.
3. Preserve the world, actor, pending action, receipt identity, ownership,
   reservations, failure history and immutable incident baseline. Inspect native
   controls before backend attachment: binding fair controls can replace the
   previous job's diagnostic state. An absent receipt alone is not proof that an
   action had no effect.
4. Follow [reliability recovery](RELIABILITY_RECOVERY.md),
   [supervisor acceptance](AUTONOMOUS_SUPERVISION.md) and
   [provenance ownership](SUPERVISOR_PROVENANCE.md). Coordinate with the current
   owner and its actual locks. Do not instantiate a second live supervisor for
   verification or remove a blocked gate by editing its JSON. Check maintenance
   ownership, storage admission and the remaining authorized window.
5. Use the applicable reviewed reconciliation contract. A code repair must pass
   its tests, review, publication and deployment gates. An operational repair
   needs genuine native evidence and the supervisor's normal acceptance. A
   status-only checkpoint transition, where that contract allows one, retains
   pending for the resumed controller to verify; it is not proof of success.
6. Resume through the established service owner only after acceptance. Verify the
   original attempt's reconciliation and subsequent autonomous useful actions.
   Restore the gameplay presentation and verify both the visible output and
   current dashboard state. Report any remaining root-cause risk separately from
   a successful operational recovery.

If required evidence or authority is missing, keep the unresolved boundary and
truthful overlay intact, complete independent authorized work, and report the
specific missing requirement. Do not use a shared VM restart as a recovery step.

## OBS Studio Mode: Preview is not Program

In Studio Mode, selecting the Maintenance scene may change only **Preview**.
Viewers continue seeing the scene in **Program** until the transition is made.
Verify the Program title and actual rendered output after each transition.

During recovery, use the existing maintenance scene without restarting OBS,
the stream, the viewer or the game. After fresh gameplay verification, prepare
the intended gameplay scene in Preview and transition it to Program. Read back
streaming state, audio activity/settings, browser-source configuration and the
dashboard's campaign identity, source timestamp and supervisor phase. A local
selection or successful dashboard HTTP request is insufficient visual proof.

## Observed recovery on 2026-09-27 UTC

This is a sanitized incident record, not a reusable command or an automatic
retry policy. The observed deployment was
`0b5242b7a82995e81bed7c8ba02a8d9ec6e35179`.

The controller stopped at approximately 23:55 UTC on September 26 after a native
walking control lease expired. Retained diagnostics showed a failed approach
before the transfer-RPC stage. The original two-copper extraction remained
ambiguous, so the supervisor deliberately blocked further gameplay.

An independently reviewed, single-use operational intervention held the owner
and maintenance locks, retained the original checkpoint and native diagnostics,
and recorded its intent before walking and transferring. Normal walking and
native reach were enforced. Identity, receipt absence, stock, capacity and exact
inventory conservation were checked in the same native invocation as the paid
transfer. Actor copper increased from 8 to 10 and source stock decreased from
585 to 583, with the original receipt recording two items. Durable preparation
prevented blindly rerunning the intervention after interruption.

The supervisor accepted the operational evidence with only the permitted
checkpoint status transition. The resumed controller verified the original
attempt at 01:19:57 UTC; two subsequent autonomous extractions verified at
01:20:56 and 01:21:50 UTC. Source, original cutoff and historical failure counts
were preserved.

The OBS investigation found Maintenance in Preview while Mission Control was
still on Program. Maintenance was transitioned onto Program during recovery,
then Mission Control was restored at approximately 01:22:25 UTC after the
gameplay checks. Screenshots and fresh telemetry verified actual Program output,
active streaming and unchanged audio/browser-source configuration. The dashboard
already followed the correct campaign; no frontend code change was needed.

These measurements establish that recovery episode only. They do not establish
a durable fix for short control-lease expiry under scheduling/transport delays,
nor general automatic reconciliation after a failed approach. The attribution
and capacity work in [#96](https://github.com/CompleteDotTech/jev-factorio-agent/issues/96)
and [#97](https://github.com/CompleteDotTech/jev-factorio-agent/issues/97) remains
separate, as does the broader native acceptance in
[#92](https://github.com/CompleteDotTech/jev-factorio-agent/issues/92).
Private raw evidence and incident-specific operational scripts remain outside
this public repository.
