"use strict";

const $ = (id) => document.getElementById(id);
const object = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const array = (value) => Array.isArray(value) ? value : [];
const text = (value, fallback = "—") => typeof value === "string" || typeof value === "number" ? String(value) : fallback;
const number = (value, places = 2) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(places) : "—";
const short = (value) => typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat(undefined, {notation: "compact", maximumFractionDigits: 1}).format(value) : "—";
const set = (id, value) => { $(id).textContent = text(value); };
const el = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = text(content);
  return node;
};
const STAGES = [
  ["Campaign supervision", "SUP / deadline & process state", "A separately supplied supervisor.json reports campaign state. A running dashboard is not evidence of a supervisor lock."],
  ["Hierarchical controller", "CTRL / observe → commit", "Existing validated observations and controller state publications. Opening this viewer never creates an observation or game action."],
  ["Goal dependencies & bootstrap", "GOAL_TREE / verified prerequisites", "stockpile_fuel → bootstrap_mining → target. Completion is read from the controller's verified goal predicates, never inferred from a score."],
  ["Factory dependency planner", "FACTORY / bounded prerequisites", "The deterministic compiler prepares bounded executable prerequisites. Candidate detail is available from the actual model request or committed plan; unobserved alternatives stay unknown."],
  ["JEV candidate judgment", "JEV / request → scores → gates", "Observed question batches, provider answers, duration and the controller's final selection. Raw provider answers are not accepted decisions. Utility is not confidence."],
  ["Native action execution", "NATIVE / dispatch & return", "An existing backend call is in progress or has returned. Return acknowledgment alone does not prove the expected game effect."],
  ["Pending-action verification", "VERIFY / observed postcondition", "Prepared and ambiguous actions remain pending until the original controller verifies their effects. This viewer cannot clear, replay, or reconcile them."],
  ["Automatic Codex repair", "REPAIR / supervisor-only", "Repair state is read from the matching supervisor. The dashboard has no repair, approval, merge, restart, or game-control endpoints."]
];

let userNotice = "";
let latest = null;
let displayed = null;
let connected = false;
let frozen = false;
let receivedAt = 0;
let stream = null;
let mediaGeneration = 0;
let mediaBusy = false;
let sourceName = "NO SOURCE";
let broadcast = new URLSearchParams(location.search).get("overlay") === "1";

function notice(message, persistent = true) {
  if (persistent) userNotice = message || "";
  const combined = persistent ? userNotice : [userNotice, message].filter(Boolean).join(" ");
  $("notice").hidden = !combined;
  set("notice", combined);
}
function inspect(title, description, evidence) {
  set("inspector-title", title);
  set("inspector-description", description);
  set("inspector-content", JSON.stringify(evidence ?? {available: false}, null, 2));
  if (!$("inspector").open) $("inspector").showModal();
}
function stageEvidence(index) {
  const data = object(displayed);
  const v = object(data.view);
  return [data.supervisor, {state: v.state, status: v.status, invocation: v.run_id},
    {goal: v.goal, target: v.target, completed_goals: v.completed_goals}, {plan: v.plan, candidates: object(v.request).candidates},
    {request: v.request, response: v.response, accepted_decision: v.decision, latency_ms: v.model_ms},
    {action: v.action, parameters: v.parameters, pending: v.pending},
    {pending: v.pending, verified: v.verified, outcome: v.outcome, state: v.state}, data.supervisor][index];
}

STAGES.forEach(([title, subtitle, description], index) => {
  const node = el("button", "workflow-node");
  node.id = `stage-${index + 1}`;
  node.append(el("span", "node-index", String(index + 1).padStart(2, "0")));
  const body = el("span", "node-text");
  body.append(el("strong", "", title), el("small", "", subtitle));
  node.append(body);
  node.addEventListener("click", () => inspect(title, description, stageEvidence(index)));
  $("workflow").append(node);
});

function renderGoals(v) {
  const completed = object(v.completed_goals);
  const target = typeof v.target === "string" ? v.target : "rocket_launch";
  const path = target === "bootstrap_mining" ? ["stockpile_fuel", target] : ["stockpile_fuel", "bootstrap_mining", target];
  const nodes = path.map((goal) => {
    const done = Object.hasOwn(completed, goal);
    const active = !done && goal === v.goal;
    const node = el("div", `goal-node${done ? " done" : active ? " current" : ""}`);
    node.append(el("strong", "", goal), el("small", "", done ? `Verified · tick ${text(completed[goal])}` : active ? "Active prerequisite" : "Not yet verified"));
    return node;
  });
  $("goals").replaceChildren(...nodes);
}

function renderCandidates(v) {
  const decision = object(v.decision);
  const request = object(v.request);
  const candidates = object(request.candidates);
  const answers = object(decision.answers);
  const rawAnswers = object(object(v.response).answers);
  const accepted = Object.keys(answers).length > 0;
  const utilities = object(decision.utilities);
  const plan = object(v.plan);
  let entries = Object.entries(candidates).filter(([, value]) => value && typeof value === "object");
  if (!entries.length && typeof plan.id === "string") entries = [[plan.id, plan]];
  set("candidate-count", `${entries.length} ${Object.keys(candidates).length ? "MODEL CANDIDATES" : "KNOWN PLANS"}`);
  const rows = entries.slice(0, 16).map(([id, candidate]) => {
    candidate = object(candidate);
    const selected = id === decision.plan_id || id === plan.id;
    const row = el("tr", selected ? "selected" : "");
    const title = el("td");
    const button = el("button");
    button.append(el("span", "candidate-title", text(candidate.description, id)), el("span", "candidate-id", id));
    button.addEventListener("click", () => inspect(id, "Captured candidate; quantities and parameters are evidence, not dashboard controls.", candidate));
    title.append(button);
    const a = accepted ? answers : rawAnswers;
    const status = selected ? "COMMITTED" : Object.hasOwn(utilities, id) ? "ELIGIBLE" : accepted ? "NOT ELIGIBLE" : "NOT ACCEPTED";
    row.append(title, el("td", "", number(object(a[`${id}/benefit`]).score)),
      el("td", "", number(object(a[`${id}/disruption`]).score)),
      el("td", "", number(object(a[`${id}/needs_observation`]).noul)),
      el("td", "", number(object(object(a.candidate).probabilities)[id])),
      el("td", "", `${number(object(a[`${id}/benefit`]).confidence)} / ${number(object(a[`${id}/disruption`]).confidence)}`),
      el("td", "utility", number(utilities[id], 3)));
    const state = el("td"); state.append(el("span", "tag", status)); row.append(state);
    return row;
  });
  if (!rows.length) {
    const row = el("tr");
    const cell = el("td", "empty", "No candidate batch in this cycle. Deterministic alternatives are not invented.");
    cell.colSpan = 8; row.append(cell); rows.push(row);
  }
  $("candidates").replaceChildren(...rows);
  set("selected-plan", text(plan.id, text(decision.plan_id, "No current committed plan")));
  set("selection-source", `${text(decision.source, "No new selection")} · ${Array.isArray(plan.steps) ? plan.steps.length + " bounded steps" : "no active plan"}${decision.reason ? " · " + text(decision.reason) : ""}`);
}

function renderLog(data) {
  const filter = $("event-filter").value;
  const events = array(data.events);
  const selected = events.filter((event) => filter === "all" || String(event.stage) === filter).slice(-120).reverse();
  const rows = selected.map((event) => {
    const kind = text(event.kind, "unknown");
    const row = el("div", `event-row${kind.endsWith("failed") ? " failed" : ""}`);
    const date = new Date(Number(event.time) * 1000);
    const timestamp = data.source?.mode === "legacy" ? "captured row" : Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString([], {hour12: false});
    row.append(el("span", "event-time", timestamp), el("span", "event-stage", `0${event.stage} / ${["", "SUP", "CONTROLLER", "GOALS", "PLANNER", "JEV", "NATIVE", "VERIFY", "REPAIR"][event.stage] || "UNKNOWN"}`), el("span", "event-kind", kind.replaceAll("_", " ") + (event.action ? ` · ${text(event.action)}` : "")), el("span", "event-duration", typeof event.duration_ms === "number" ? `${number(event.duration_ms, 1)} ms` : ""));
    return row;
  });
  const scroll = $("event-log").scrollTop;
  $("event-log").replaceChildren(...(rows.length ? rows : [el("p", "empty", "No matching captured events. No simulated activity is displayed.")]));
  $("event-log").scrollTop = scroll;
  set("event-count", `${events.length} recent events`);
}

function render(data) {
  displayed = data;
  const v = object(data.view);
  const state = object(v.state);
  const supervision = object(data.supervisor);
  const sup = supervision.session_match ? object(supervision.state) : {};
  set("session", text(state.session_id, text(v.session_id, "Awaiting telemetry")));
  set("world", state.world_kind === "mock" ? "MOCK WORLD · not native gameplay" : text(state.world_kind, "No world observed"));
  set("policy", v.policy);
  set("tick", `tick ${text(state.tick, text(v.tick))}`);
  set("controller-status", text(v.status, "UNKNOWN").toUpperCase());
  set("run-id", `Invocation ${text(v.run_id, "not observed")}`);
  set("overlay-goal", text(v.goal, "Awaiting observation"));
  set("overlay-status", text(v.status, "NO TELEMETRY").toUpperCase() + (state.world_kind === "mock" ? " / MOCK" : ""));
  set("source-mode", frozen ? "DISPLAY FROZEN" : data.source?.mode === "legacy" ? "LEGACY / COMPLETED DECISIONS" : "READ-ONLY / EVENT FEED");
  renderGoals(v);
  const observed = [["Character position", Array.isArray(state.player_position) ? state.player_position.join(", ") : "—"], ["Drill status", text(state.drill_status, "Unknown") || "Unknown"], ["Drill fuel", text(state.drill_fuel)], ["Ore collected", text(state.iron_ore_collected)], ["Output connected", state.drill_output_connected === true ? "Observed" : state.drill_output_connected === false ? "No" : "Unknown"]];
  $("observations").replaceChildren(...observed.map(([key, value]) => { const row = el("div"); row.append(el("dt", "", key), el("dd", "", value)); return row; }));
  const inventory = object(state.inventory);
  const items = Object.entries(inventory).slice(0, 7);
  $("inventory").replaceChildren(...(items.length ? items : [["INVENTORY", null]]).map(([key, value]) => { const item = el("div"); item.append(el("span", "", key.replaceAll("-", " ")), el("strong", "", value === null ? "Awaiting state" : short(value))); return item; }));
  const latency = el("span", "", " ms"); $("latency").replaceChildren(document.createTextNode(number(v.model_ms, 0)), latency);
  const usage = object(v.usage || object(v.response).usage);
  set("tokens", short(usage.total_tokens ?? (typeof usage.input_tokens === "number" && typeof usage.output_tokens === "number" ? usage.input_tokens + usage.output_tokens : null)));
  renderCandidates(v);
  STAGES.forEach((_, index) => {
    const node = $(`stage-${index + 1}`);
    const active = index === 0 ? Boolean(sup.phase) : index === 7 ? sup.repair_required === true || sup.phase === "repair" : v.stage === index + 1 && v.lifecycle !== "returned" && v.lifecycle !== "error";
    node.classList.toggle("active", active);
    node.classList.toggle("seen", array(v.seen).includes(index + 1));
  });
  const pending = object(v.pending);
  const hasPending = Object.keys(pending).length > 0;
  set("verification-status", hasPending ? `Pending · ${text(pending.action, "action")}` : v.verified === true ? "Postcondition verified by controller" : "No verified effect in this record");
  set("verification-detail", hasPending ? `Dispatch: ${text(pending.dispatch)}. Waiting for observed effects; no replay is authorized by this viewer.` : text(v.outcome, "Returned ≠ verified. Only the controller's observed postcondition advances a step."));
  set("pending-polls", `POLL COUNT ${text(pending.polls)}`);
  $("verified-icon").textContent = v.verified === true && !hasPending ? "✓" : "◇";
  $("verified-icon").classList.toggle("good", v.verified === true && !hasPending);
  set("repair-state", sup.phase ? text(sup.phase).toUpperCase() : supervision.available ? "SESSION MISMATCH" : "UNCONNECTED");
  set("repair-detail", sup.phase ? `Reported supervisor phase: ${text(sup.phase)}. Repair required: ${text(sup.repair_required, "unknown")}. Attempt: ${text(sup.attempt)}. Lock ownership is not inferred.` : supervision.available ? "Supervisor session does not match this telemetry. Its repair state and cutoff are not applied." : "No matching supervisor state connected. No repair or process-control actions are available.");
  renderLog(data);
  const warnings = [];
  if (data.source?.mode === "legacy") warnings.push("Legacy log: completed decisions only. In-flight model timing and unseen candidates are unavailable.");
  if (data.source?.invalid) warnings.push(`${data.source.invalid} malformed or unsupported rows rejected.`);
  if (data.source?.partial) warnings.push("Waiting for a complete final JSONL line.");
  if (v.gap) warnings.push("Event history has a gap or was bounded; this is not a complete audit.");
  if (data.source?.status === "unavailable") warnings.push("Telemetry file unavailable; showing retained evidence.");
  if (frozen) warnings.push("Display frozen; gameplay and video continue independently.");
  notice(warnings.join(" "), false);
  refreshStatus();
}

function refreshStatus() {
  const data = object(frozen ? displayed : latest);
  const v = object(data.view);
  const now = typeof data.server_time === "number" ? data.server_time + (performance.now() - receivedAt) / 1000 : Date.now() / 1000;
  const age = typeof v.last_event_time === "number" ? Math.max(0, now - v.last_event_time) : null;
  const stale = age === null || age > 15 || data.source?.status === "unavailable";
  const ended = ["returned", "error"].includes(v.lifecycle);
  const active = connected && !stale && !ended;
  if (!active || frozen) for (let i = 2; i <= 7; i++) $(`stage-${i}`).classList.remove("active");
  $("connection-led").className = `led ${active ? "live" : "stale"}`;
  set("connection", !connected ? "Reconnecting" : ended ? "Invocation ended" : stale ? "No recent telemetry" : "Feed connected");
  set("freshness", age === null ? "No recorded events" : `${data.source?.mode === "legacy" ? "File modified" : "Last event"} ${age < 1 ? "just now" : Math.floor(age) + "s ago"}`);
  const thinking = active && !frozen && v.model_busy === true;
  $("signal").classList.toggle("active", thinking);
  set("thinking-status", frozen ? "Display frozen" : thinking ? "JEV is evaluating" : ended ? "Controller invocation ended" : stale ? "Awaiting fresh evidence" : text(v.kind, "Waiting for an agent").replaceAll("_", " "));
  set("model-detail", thinking ? "Provider call in flight · no tokens invented" : v.response ? "Provider response captured · inspect acceptance" : "No model response in this cycle");
  const supervision = object(data.supervisor);
  const cutoff = supervision.session_match ? object(supervision.state).cutoff : null;
  if (typeof cutoff === "number" && Number.isFinite(cutoff)) {
    const remaining = Math.max(0, Math.floor(cutoff - now));
    set("deadline", `${String(Math.floor(remaining / 3600)).padStart(2, "0")}:${String(Math.floor(remaining / 60) % 60).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`);
  } else set("deadline", "Not connected");
}

function setBroadcast(value) {
  broadcast = value;
  document.body.classList.toggle("broadcast", value);
  $("broadcast").setAttribute("aria-pressed", String(value));
}
function stopCapture() {
  mediaGeneration += 1;
  if (stream) stream.getTracks().forEach((track) => track.stop());
  stream = null;
  $("game-video").srcObject = null;
  $("capture-placeholder").hidden = false;
  $("stop-capture").disabled = true;
  $("video-led").className = "led";
  set("video-status", "NO SOURCE");
  set("video-resolution", "Capture permission required");
}
function updateVideoStatus() {
  if (!stream) return;
  const video = $("game-video");
  const track = stream.getVideoTracks()[0];
  const live = track?.readyState === "live" && !track.muted;
  $("video-led").className = `led ${live ? "live" : "stale"}`;
  set("video-status", live ? sourceName : "SOURCE PAUSED");
  set("video-resolution", video.videoWidth ? `${video.videoWidth} × ${video.videoHeight} · browser capture (not synchronized to game ticks)` : "Connecting video source…");
}
async function capture(kind) {
  if (mediaBusy) return;
  if (!navigator.mediaDevices || !window.isSecureContext) { notice("Capture requires a supported browser on localhost or HTTPS."); return; }
  if (kind === "window" && !navigator.mediaDevices.getDisplayMedia) { notice("Window capture is unavailable in this browser. Use Camera / OBS or a supported desktop browser."); return; }
  mediaBusy = true;
  const generation = ++mediaGeneration;
  try {
    const device = $("camera-devices").value;
    const incoming = kind === "window" ? await navigator.mediaDevices.getDisplayMedia({video: true, audio: false}) : await navigator.mediaDevices.getUserMedia({video: device ? {deviceId: {exact: device}} : true, audio: false});
    if (generation !== mediaGeneration) { incoming.getTracks().forEach((track) => track.stop()); return; }
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = incoming;
    sourceName = kind === "window" ? "WINDOW CAPTURE" : "CAMERA / OBS";
    $("game-video").srcObject = stream;
    $("capture-placeholder").hidden = true;
    $("stop-capture").disabled = false;
    const track = stream.getVideoTracks()[0];
    track.addEventListener("ended", () => { if (stream === incoming) stopCapture(); });
    track.addEventListener("mute", updateVideoStatus);
    track.addEventListener("unmute", updateVideoStatus);
    try { await $("game-video").play(); } catch (error) { stopCapture(); throw error; }
    updateVideoStatus();
    notice("");
    if (kind === "camera") {
      const devices = await navigator.mediaDevices.enumerateDevices().catch(() => []);
      const selected = track.getSettings().deviceId;
      $("camera-devices").replaceChildren(...devices.filter((d) => d.kind === "videoinput").map((d, i) => { const option = el("option", "", d.label || `Video device ${i + 1}`); option.value = d.deviceId; option.selected = d.deviceId === selected; return option; }));
      $("camera-devices").hidden = false;
      notice("Choose OBS Virtual Camera in the device list after starting it in OBS. The selected camera is previewed locally.");
    }
  } catch (error) {
    const messages = {NotAllowedError: "Capture was cancelled or denied. No new source was connected.", NotFoundError: "No matching video device was found. Start OBS Virtual Camera, then try again.", NotReadableError: "The selected source could not be read. Check whether another application is using it.", OverconstrainedError: "That video device is unavailable. Select another device."};
    notice(messages[error?.name] || "Video capture could not start. Check browser permissions and source availability.");
  } finally { mediaBusy = false; }
}

$("capture").onclick = $("capture-main").onclick = () => capture("window");
$("camera").onclick = () => capture("camera");
$("camera-devices").onchange = () => capture("camera");
$("stop-capture").onclick = stopCapture;
$("game-video").onloadedmetadata = updateVideoStatus;
$("fullscreen").onclick = async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else await $("game-stage").requestFullscreen(); } catch { notice("Fullscreen was not available or permitted."); } };
$("broadcast").onclick = () => setBroadcast(!broadcast);
$("freeze").onclick = () => { frozen = !frozen; document.body.classList.toggle("frozen", frozen); $("freeze").setAttribute("aria-pressed", String(frozen)); set("freeze", frozen ? "▶ Resume display" : "Ⅱ Freeze display"); if (!frozen && latest) render(latest); else if (displayed) render(displayed); };
$("event-filter").onchange = () => { if (displayed) renderLog(displayed); };
$("close-inspector").onclick = () => $("inspector").close();
$("inspect-model").onclick = () => inspect("JEV request & response", STAGES[4][2], stageEvidence(4));
$("inspect-plan").onclick = () => inspect("Committed plan", STAGES[3][2], stageEvidence(3));
$("inspect-pending").onclick = () => inspect("Pending-action evidence", STAGES[6][2], stageEvidence(6));
$("export").onclick = () => {
  if (!displayed) { notice("No captured view is available to export."); return; }
  const payload = {display_only: true, complete_audit: false, exported_at: new Date().toISOString(), snapshot: displayed};
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"}));
  const link = el("a"); link.href = url; link.download = "jev-dashboard-view.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
window.addEventListener("keydown", (event) => {
  if ($("inspector").open || ["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
  if (event.key === "b" || event.key === "Escape" && broadcast) { setBroadcast(!broadcast); return; }
  if (event.target.tagName === "BUTTON") return;
  if (event.code === "Space") { event.preventDefault(); $("freeze").click(); }
});
setBroadcast(broadcast);
renderGoals({});
const events = new EventSource("/api/events");
events.addEventListener("snapshot", (event) => {
  try {
    const parsed = JSON.parse(event.data);
    if (!parsed || typeof parsed !== "object" || !parsed.source) throw new Error("shape");
    latest = parsed; connected = true; receivedAt = performance.now();
    if (!frozen) render(latest);
  } catch { connected = false; notice("An invalid dashboard snapshot was rejected."); }
});
events.onerror = () => { connected = false; refreshStatus(); };
setInterval(refreshStatus, 500);
window.addEventListener("pagehide", () => { stopCapture(); events.close(); });
