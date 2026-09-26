"use strict";

// Display only: no timers, requests, media access, game commands or action predicates.
window.MissionControl = (() => {
  const obj = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const str = (value, fallback = "Unknown") => typeof value === "string" || typeof value === "number" ? String(value) : fallback;
  const node = (tag, content, cls = "") => {
    const element = document.createElement(tag);
    element.className = cls;
    element.textContent = content;
    return element;
  };
  const states = new Set(["observed", "pending", "blocked", "unknown", "submitted"]);
  const set = (id, value) => { document.getElementById(id).textContent = value; };
  let current = {};
  function rows(id, values) {
    const list = document.getElementById(id);
    list.replaceChildren(...values.map(([title, value]) => {
      const row = node("div", "");
      row.append(node("dt", title), node("dd", str(value)));
      return row;
    }));
  }
  function render(data, inspect) {
    const view = obj(data.view), state = obj(view.state), mission = obj(state.mission);
    const launch = obj(mission.launch), record = obj(view.mission_record);
    current = {observation: mission, record, world_kind: state.world_kind,
      read_only: true, display_only: true, deployment_authorized: false};
    set("launch-headline", str(launch.headline, "Launch evidence unavailable"));
    set("launch-sample", `Observation tick ${str(launch.tick, "—")}`);
    const gates = Array.isArray(launch.gates) ? launch.gates : [];
    document.getElementById("launch-gates").replaceChildren(...(gates.length ? gates : [
      {key: "unknown", title: "Launch readiness", state: "unknown", detail: "Not captured by this feed"}
    ]).slice(0, 7).map(gate => {
      gate = obj(gate);
      const status = states.has(gate.state) ? gate.state : "unknown";
      const item = node("li", "", `mission-gate ${status}`);
      item.dataset.gate = str(gate.key, "unknown");
      item.append(node("strong", str(gate.title)), node("span", status.toUpperCase(), "mission-status"),
        node("small", str(gate.detail, "Not captured")));
      return item;
    }));
    const observed = gates.filter(gate => obj(gate).state === "observed").length;
    set("launch-summary", gates.length ? `${observed} of ${Math.min(gates.length, 7)} launch gates observed` : "Launch gates not captured by this feed");
    const research = obj(mission.research);
    const progress = typeof research.progress === "number" && research.progress >= 0 && research.progress <= 1
      ? `${(research.progress * 100).toFixed(1)}% of current technology` : "Unknown";
    rows("mission-production", [["Research", research.name], ["Research progress", progress],
      ["Plan-budget failures", record.failure_count], ["Generated / ranked", `${str(record.generated_count, "—")} / ${str(record.ranked_count, "—")}`]]);
    const automation = Array.isArray(mission.automation) ? mission.automation : [];
    document.getElementById("mission-automation").replaceChildren(...(automation.length ? automation.slice(0, 18).map(item => {
      item = obj(item);
      const row = node("li", "", "mission-automation-row");
      row.append(node("strong", `${str(item.family)} / ${str(item.role)}`),
        node("span", `${str(item.state, "Unreported state")} · ${str(item.reason, "No reason captured")}`));
      if (item.survey_tick != null) row.append(node("small", `Survey tick ${str(item.survey_tick)}${item.cached === true ? " · cached, not new work" : ""}`));
      return row;
    }) : [node("li", "Automation telemetry unavailable", "muted")]));
    const flags = obj(record.features);
    rows("mission-features", Object.entries(flags).map(([key, value]) => [key.replaceAll("_", " "), value === true ? "Enabled (recorded)" : value === false ? "Disabled (recorded)" : "Unknown"]));
    rows("mission-release", [["Controller source", record.commit || "Not captured"],
      ["Record tick", record.tick], ["PR / merge", "Not supplied by gameplay"],
      ["Deployment", "Not established by source merge"], ["Native acceptance", "No acceptance report connected"]]);
    set("mission-native-label", state.world_kind === "mock" ? "MOCK · NOT NATIVE ACCEPTANCE" : "RECORDED FACTS · NOT ACCEPTANCE");
    const button = document.getElementById("inspect-mission");
    button.onclick = () => inspect("Launch and factory evidence", "Bounded display summary. Reported receipts are not victory; telemetry is not deployment authorization.", current);
  }
  function freshness({active, frozen, legacy, gap}) {
    const panel = document.getElementById("mission-panel");
    panel.classList.toggle("mission-historical", !active || frozen || gap);
    set("mission-freshness", frozen ? "FROZEN EVIDENCE" : gap ? "FEED GAP · CHECK EVIDENCE" : !active ? "STALE / DISCONNECTED" : legacy ? "RECORDED DECISION" : "LATEST CAPTURED OBSERVATION");
  }
  return {render, freshness};
})();
