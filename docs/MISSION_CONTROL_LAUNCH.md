# Mission Control: launch and factory evidence

This is a read-only display update accompanying first-rocket launch readiness.
It does not create a backend, poll the game, request a model, change reservations,
start a launch, reset a world, restart a service or approve deployment.

## Launch panel

Seven independent gates show the captured version/session boundary, landing pad,
owned payload, rocket readiness, cargo/destination capacity, submitted request and
native victory. They are **not** a cumulative completion percentage. In particular:

- A ready rocket without pad/cargo is preparation, not launch readiness.
- One carried fish/satellite is distinguished from exactly one loaded payload.
  The UI does not invent proof of a reservation absent from captured evidence.
- Unexpected/multiple cargo, insufficient destination capacity, automatic launch,
  unresolved launch intent and native observer faults remain visible blockers.
- A session/actor/tick-bound launch receipt is `SUBMITTED`, never victory. A native
  victory requires the recorded native world, victory flag and native launch source.
  A mock/model claim or a completed controller step does not satisfy that label.
- Missing, malformed, wrong-version or mismatched-session/tick evidence is unknown.
  This display does not call or replace authoritative gameplay predicates.

The current native snapshot's tick is shown separately from the completed decision
record's tick. Source/configuration/planning evidence can therefore be older than
an in-flight observation. A newly missing observation clears old readiness.

## Factory and release sections

Expand **Production, automation and planning** for current technology progress,
recorded plan-budget counts, generated/ranked candidate counts, and a small window
of input-route, production-site and successor statuses. At most six roles per
capability are summarized. Cached survey counters are not re-counted as new work;
the original survey tick is shown. An enabled flag is not commissioned production.
Neither throughput, useful consumption, actor idle nor dollar cost is invented.

Expand **Source, release and acceptance boundaries** for the completed record's
controller commit and tick. PR/merge state is not supplied by gameplay. Source
merge does not establish deployment. No native acceptance report is connected by
this change. These states are always separate from observed game progress and
cannot turn green merely because a commit merged. The viewer makes no GitHub,
RCON, model-provider or acceptance-run requests.

The existing **Evidence** dialog and **Export view** contain only the bounded,
redacted display snapshot, not a complete audit or authoritative checkpoint.
Keep exports private; existing redaction is best effort, not external authenticity.

## Freshness and compatibility

The Mission Control panel ages the **last observation**, not the last model/phase
heartbeat. Frozen, stale, disconnected, partial and gapped feeds visibly suppress
current-status coloring while retaining the historical facts for inspection.
Legacy rows with a valid timezone-aware `recorded_at_utc` use that original time;
copying an old log cannot make those facts fresh. Rows without an original time
retain the existing file-mtime fallback and label. No timestamp is invented.

Both legacy completed-decision logs and the optional dashboard event sidecar are
supported. Older sidecars that never captured the new projection show unknown.
Opening/updating the viewer does not require a game/controller restart. A running
controller will only emit the richer sidecar projection on its next separately
authorized invocation; it does not hot-reload a merge.

The industrial theme, screen/camera capture, freeze/export controls, narrow layout,
keyboard inspectors and reduced-motion behavior are retained. The 1920x1080 OBS
studio cutout remains x=277, y=111, width=1342 with a 16:9 aspect ratio. The right
column scrolls; no new panel overlaps or resizes the video cutout. Broadcast mode
continues to hide the diagnostic panels.

## Validation

```sh
PYTHONPATH=src python -m pytest tests/test_dashboard.py tests/test_dashboard_mission.py tests/test_dashboard_integration.py -q
PYTHONPATH=src python -m pytest tests/test_dashboard_browser.py -q
PYTHONPATH=src python -m pytest tests/ -q
```

New fixtures explicitly use synthetic state. They test independent prerequisites,
receipt/victory distinction, missing/mismatched evidence, faults and ambiguous
intent, reservations remaining unknown, bounded/redacted data, legacy/sidecar
parity, observation freshness, freeze, XSS-safe text, studio geometry and mobile
layout. Existing controller-equivalence tests ensure observer attachment adds no
backend/model calls and does not change logs/checkpoints/actions in those fixtures.
Browser tests do not establish native Factorio gameplay or actual OBS acceptance.
