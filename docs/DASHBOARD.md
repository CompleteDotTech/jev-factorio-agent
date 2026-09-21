# JEV Mission Control

A working, local web interface for the eight stages in
[`00-complete-workflow.mmd`](workflows/mmd/00-complete-workflow.mmd), with a large
in-browser game preview. This is executable HTML/CSS/JavaScript and a Python
server, not an image mockup. It has no frontend build step or CDN dependencies.

## Open it without touching an existing campaign

From an environment with this revision installed:

```sh
python -m pip install -e .
python -m jev_factorio.dashboard --log-file /path/to/existing/gameplay.jsonl
```

Open **http://127.0.0.1:8765** in a desktop browser. An absent file is permitted;
the viewer waits for it. The server only reads the explicitly selected file.
The `jev-factorio-dashboard` installed command is equivalent. `--port 8766`
selects another local port.

Legacy logs expose completed decisions, not the inside of an in-flight model
call. The interface labels this limitation. It does not animate invented model
activity or fill in missing candidates, token counts, or confidence values.

To include the supervisor's reported phase, original cutoff, repair-required
flag and attempt count:

```sh
python -m jev_factorio.dashboard --log-file /path/to/gameplay.jsonl --supervisor-state /path/to/supervisor.json
```

Supervisor data is applied only when its session matches the observed session.
File contents do not establish current lock ownership or independently prove
process health. No supervisor file, checkpoint or world is changed.

## Enable in-flight telemetry on an authorized controller invocation

Add `--dashboard-events /path/to/dashboard.jsonl` to the **existing hierarchical
controller command**, retaining its target, policy, model, checkpoint, resume
flags and all other campaign parameters. Alternatively set
`JEV_DASHBOARD_EVENTS`. This flag is opt-in and hierarchical-only.

Start the separate viewer in another terminal:

```sh
python -m jev_factorio.dashboard --events /path/to/dashboard.jsonl
```

For a harmless, explicitly synthetic smoke run, use two terminals:

```sh
# Terminal 1: the viewer can start before the file exists.
python -m jev_factorio.dashboard --events runs/dashboard.jsonl

# Terminal 2: mock world and mock model, not live Factorio.
python -m jev_factorio --controller hierarchical --backend mock --mock-model --target bootstrap_mining --steps 40 --tick-seconds 0.75 --dashboard-events runs/dashboard.jsonl
```

The mock run is labelled **MOCK WORLD** in the UI and can finish quickly. No
synthetic game video, synthetic telemetry playback loop, API credentials or
paid provider call is bundled into normal viewer startup.

Do not restart/reset/resume a live world merely to open this viewer. A running
Python controller does not hot-reload a merged revision. Use the legacy reader
until your next separately authorized deployment/invocation. Merging this PR
is not an action against an active campaign.

Output paths must be different from the legacy log and checkpoint, including
hard-link aliases. Existing non-dashboard files and incomplete tails are
rejected before backend initialization. A complete dashboard file may be
appended on a subsequent invocation; each writer gets a new invocation ID and
the viewer resets its projection at the boundary. Use one writer per file;
concurrent multi-process writing is not a supported mode. Rotate/archive these
files operationally; the producer does not impose a disk retention limit.

## Put the game in the window

**Direct game capture:** click **Choose game window** or **Game window**, then
select the Factorio window in the browser's capture picker. The browser, not
this server, supplies the video. Permission is explicit and cannot be silently
preapproved. The browser and the selected game window must be on the same
capture-capable machine.

**OBS:** start OBS Studio's **Virtual Camera**, click **Camera / OBS**, grant
video-device permission, and select **OBS Virtual Camera** in the device list.
The initial camera request can select the system default camera; the button is
explicitly a camera request. Switch the dropdown to OBS after devices are
identified. No microphone is requested. The camera button is also usable for
other explicitly selected video devices.

**Stop preview** releases all media tracks; it does not stop the agent or the
game. Changing sources keeps the old source if the new request is cancelled.
Leaving the page releases the capture. Fullscreen affects only the game pane.

Video remains in the browser: there is no video upload, recording service,
RTMP ingestion, WebRTC relay, or server-side game capture. It is a real video
preview, not an iframe claiming to embed the native executable. Telemetry and
video are independent; exact game-tick/frame synchronization is not claimed.
Remote access to a telemetry file does not grant access to the remote desktop.

Prefer a supported Chromium desktop browser on localhost (or an appropriately
secured deployment). Capture support varies by browser and OS. See the primary
references: [MDN screen capture](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia),
[MDN video-device capture](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia),
and [OBS Virtual Camera Guide](https://obsproject.com/kb/virtual-camera-guide).

### OBS overlay composition

Use **Broadcast layout**, or add an OBS Browser Source at
`http://127.0.0.1:8765/?overlay=1`, above an ordinary OBS Game Capture source.
This mode renders only a transparent goal/status HUD; it intentionally hides
the page's video pane and controls. Match the OBS Browser Source dimensions to
your scene. Press **B** or **Escape** to leave broadcast mode in a browser, or
navigate back to `/`.

For the full dashboard composition, capture the dashboard browser window in
OBS instead. Do not feed that same complete OBS scene back into its own virtual
camera preview, or capture this dashboard with its own window picker: that
creates a recursive hall of mirrors.

## What the interface shows

| Workflow area | Evidence and interaction |
| --- | --- |
| Campaign supervision | Matching supervisor phase/cutoff; unavailable otherwise. |
| Hierarchical controller | Existing validated observations, plan/pending publications, status and invocation boundary. |
| Goal dependencies/bootstrap | Active prerequisite and recorded verified goal ticks, not estimated completion percentages. |
| Factory planner | Planning boundary and the actual committed plan; model-request candidates when available. Deterministic alternatives not captured remain unknown. |
| JEV judgment | In-flight request indicator, measured last model duration, bounded questions/responses, reported token usage, choice probabilities, benefit/disruption scores and their separate confidences, recorded utility and selection source. |
| Native execution | Existing dispatch start, return or failure; original action parameters available for inspection. |
| Pending verification | Prepared/returned/ambiguous state, pending polls and the controller's recorded postcondition result. Return acknowledgement is not verified success. |
| Automatic repair | Matching supervisor's reported repair status, not an invented standby/healthy assertion. No repair controls. |

Stage highlighting means **last observed boundary**, not that every previous
stage is globally completed. The workflow is cyclic and the supervisor is
independent. Model wave animation is enabled only by a captured in-flight call
with a fresh feed. A gap, stale source, partial line, ended invocation,
unsupported row, disconnected feed or mismatched supervisor remains visible.

Click workflow stages, plan names, or **Inspect** to view captured JSON.
**Freeze display** (or Space outside controls) freezes the evidence view, not
video/gameplay. **Export view** downloads a redacted, bounded display snapshot;
it is explicitly not a complete audit. The event selector filters up to 120
recent boundary summaries. The interface supports narrow screens, keyboard
focus and reduced-motion preferences.

## Observer and transport contract

`dashboard.attach()` decorates one already-created hierarchical loop instance,
not global classes or another process. It delegates existing calls exactly
once, preserves returned objects/errors, and adds no backend observations,
provider calls, predicate evaluations, sleeps or checkpoint writes. Native
Lua, planning, confidence gates, dispatch semantics and repair policy are
unchanged. Existing stdout and `--log-file` formats remain separate.

The new optional sidecar emits `schema: "jev.dashboard.v1"`, a per-invocation
`run_id`, sequential `seq`, UTC `at`, epoch `time`, `kind`, workflow `stage`, and
bounded `data`. Model/backend durations use `perf_counter_ns` around existing
operations, excluding the observer's before/after event writes. Timings around
larger controller operations can include nested observer overhead. Synchronous
serialization and append add unmeasured wall-clock overhead and can affect the
next state of an independently advancing native world. Mock call equivalence
is not a native trajectory-equivalence or speed claim.

This is **best-effort display telemetry**, not the research hash-chain core,
causal replay contract, provenance segmentation or evaluator proposed in other
PRs. It does not claim compatibility with unmerged research schemas. It does
not fsync, authenticate events, guarantee replay completeness or authorize
retries. After a write/projection failure the optional observer is disabled or
omits the display event; core controller exceptions still propagate unchanged.
The existing authoritative checkpoint/logging paths are not weakened.

The viewer shares one incremental reader among clients, samples files every
250 ms, and sends reconnectable full SSE snapshots every 500 ms. These are
nominal display intervals, not real-time performance guarantees. Reconnects
receive the latest bounded snapshot rather than replaying commands. Reading
starts within the last 2 MiB of large files, processes at most 256 KiB per poll,
and rejects oversized/invalid rows. Incomplete final lines are held until
completed. Truncation or replacement resets the projection. This intentionally
cannot reconstruct a complete historical run from a truncated window.

## Security and deployment boundary

The server binds **127.0.0.1 only**, has no `--host` public-binding flag, rejects
unexpected Host/Origin/cross-site requests, and serves an exact asset/API route
allowlist. It cannot browse arbitrary paths, fetch captured URLs, send game
commands, modify checkpoints, approve tools, execute repairs or merge code.
There are no write/control API endpoints. CSP, no-store, MIME sniffing
protection and bounded SSE connections are applied.

No JavaScript framework, fonts, scripts or icons are downloaded from a CDN.
Untrusted strings use DOM text nodes rather than HTML injection. Known
environment secrets, credential-shaped text, sensitive field names and URLs
are redacted, and large/deep values visibly truncated. This is best-effort
redaction, **not a guarantee that arbitrary free text contains no confidential
information**. Keep logs and exports private. There is no multi-user
authentication/authorization service; do not expose the server directly to a
LAN or public network. A dedicated authenticated deployment is separate work.

## Tests

```sh
python -m pip install -e '.[test,dashboard-test]'
python -m playwright install chromium
python -m pytest tests/test_dashboard.py tests/test_dashboard_integration.py tests/test_dashboard_browser.py -q
```

The existing Python matrix exercises the real controller integration tests;
a separate browser job installs the built package plus Playwright and runs
actual HTTP/SSE Chromium tests. Tests inject clearly synthetic media only to
verify capture plumbing; they are not native Factorio or actual OBS device
validation. `CHROMIUM_PATH` may select an already installed supported Chromium.
`DASHBOARD_SCREENSHOT` optionally saves the browser test's labelled synthetic
preview. No generated image assets are required by the application.
