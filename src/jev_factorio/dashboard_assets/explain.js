"use strict";

// Viewer-facing wording for recorded controller evidence. Pure functions only:
// nothing here infers progress the record does not state.
const Explain = (() => {
  const words = (value) => String(value).replaceAll("-", " ").replaceAll("_", " ");
  const ITEM = /^[a-z0-9][a-z0-9-]{0,63}$/;

  // Factorio 1.1 items on the rocket path, ordered by first availability.
  // `null` means available without research.
  const CATALOG = [
    [null, ["wood", "coal", "stone", "iron-ore", "copper-ore", "iron-plate", "copper-plate", "stone-brick",
      "iron-gear-wheel", "copper-cable", "electronic-circuit", "automation-science-pack"]],
    ["automation", ["assembling-machine-1", "long-handed-inserter"]],
    ["logistics", ["underground-belt", "splitter"]],
    ["logistic-science-pack", ["logistic-science-pack"]],
    ["steel-processing", ["steel-plate", "steel-chest"]],
    ["electric-energy-distribution-1", ["medium-electric-pole", "big-electric-pole"]],
    ["fast-inserter", ["fast-inserter"]],
    ["advanced-material-processing", ["steel-furnace"]],
    ["automation-2", ["assembling-machine-2"]],
    ["engine", ["engine-unit"]],
    ["fluid-handling", ["storage-tank", "pump"]],
    ["oil-processing", ["pumpjack", "oil-refinery", "chemical-plant", "solid-fuel"]],
    ["plastics", ["plastic-bar"]],
    ["advanced-electronics", ["advanced-circuit"]],
    ["sulfur-processing", ["sulfur"]],
    ["chemical-science-pack", ["chemical-science-pack"]],
    ["battery", ["battery"]],
    ["concrete", ["concrete"]],
    ["advanced-material-processing-2", ["electric-furnace"]],
    ["productivity-module", ["productivity-module"]],
    ["production-science-pack", ["production-science-pack"]],
    ["advanced-electronics-2", ["processing-unit"]],
    ["electric-engine", ["electric-engine-unit"]],
    ["robotics", ["flying-robot-frame"]],
    ["utility-science-pack", ["utility-science-pack"]],
    ["low-density-structure", ["low-density-structure"]],
    ["rocket-fuel", ["rocket-fuel"]],
    ["rocket-control-unit", ["rocket-control-unit"]],
    ["rocket-silo", ["rocket-silo", "rocket-part", "satellite"]],
  ];

  function unlocked(researched) {
    const done = new Set(Array.isArray(researched) ? researched : []);
    const items = [];
    const byTech = {};
    for (const [tech, list] of CATALOG) {
      if (tech !== null && !done.has(tech)) continue;
      items.push(...list);
      if (tech !== null) byTech[tech] = list;
    }
    return {items, byTech};
  }

  const MACHINES = {drill: "a mining drill", inserter: "a burner inserter"};
  const UTILITIES = {lab: "a lab", boiler: "the boiler", engine: "the steam engine", water: "the water pump"};

  function place(kind, parts, extracting, item) {
    if (kind === "input") return MACHINES[parts[0]] || `a ${words(parts[0] || "machine")}`;
    if (kind === "output-arm") return "an output inserter";
    if (kind === "output-chest") return "an output chest";
    if (kind === "stock") return "the stockpile";
    if (kind === "utility") return UTILITIES[parts[0]] || words(parts[0] || "a utility");
    if (kind === "recipe") return parts[0] === item ? "its production line" : `${words(parts[0] || "")} production`;
    return extracting ? "storage" : "a machine";
  }

  const PHRASES = [
    [/^Waiting for the in-flight postcondition/, "Checking whether the last action worked", "pending"],
    [/^Ambiguous dispatch/, "Sent, but the result is unclear. Checking", "pending"],
    [/^Unverified action outcome/, "Result still unconfirmed. Holding before the next change", "pending"],
    [/^Observed expected postcondition/, "Confirmed: the action worked", "done"],
    [/^Plan effects already observed/, "Already done. Nothing to change", "done"],
    [/^Waiting for native production or research/, "Waiting for machines or research", "wait"],
    [/^Yield passive (machine )?wait/, "Doing other work while machines run", "wait"],
    [/^Native input component returned/, "Built an input feed. Checking the flow", "pending"],
    [/^Paid buffer component returned/, "Built a buffer. Checking the flow", "pending"],
    [/^Paid furnace placed/, "Built a furnace at a production site", "done"],
    [/^Input-route evidence invalid/, "Input route changed. Keeping pending work", "wait"],
    [/^Capital investment catalog or capability changed/, "Build options changed. Keeping plans", "wait"],
  ];

  // One short sentence plus the item it concerns, for the event feed.
  function event(record) {
    const action = typeof record?.action === "string" ? record.action : "";
    const raw = typeof record?.outcome === "string" ? record.outcome : "";
    const verified = record?.verified === true;
    let m = raw.match(/^Transferred (\d+) ([a-z0-9-]+) \(\d+:(factory_insert|factory_extract):([a-z-]+):?(.*)\)$/);
    if (m) {
      const [, count, item, kind, target, rest] = m;
      const extracting = kind === "factory_extract";
      const where = place(target, rest.split(":").filter((part) => !/^\d+$/.test(part)), extracting, item);
      return {text: `${extracting ? "Collected" : "Loaded"} ${count} ${words(item)} ${extracting ? "from" : "into"} ${where}`,
        item, state: verified ? "done" : "pending"};
    }
    if ((m = raw.match(/^Harvested (\d+) ([a-z0-9-]+)/))) return {text: `Gathered ${m[1]} ${words(m[2])} by hand`, item: m[2], state: "done"};
    if ((m = raw.match(/^Started native research ([a-z0-9-]+)/))) return {text: `Started researching ${words(m[1])}`, item: null, state: "done"};
    if ((m = raw.match(/^Placed ([a-z0-9-]+) for (?:recipe|utility):([a-z0-9-]+)/))) return {text: `Built ${words(m[1])} for ${words(m[2])}`, item: m[1], state: "done"};
    if ((m = raw.match(/^Constructed ([a-z0-9-]+) connection/))) return {text: `Connected ${words(m[1])}s. Checking the layout`, item: m[1], state: "pending"};
    if ((m = raw.match(/^Configured recipe:([a-z0-9-]+)/))) return {text: `Set a machine to make ${words(m[1])}`, item: m[1], state: "done"};
    if (/^Native craft request returned/.test(raw)) return {text: verified ? "Crafting confirmed" : "Crafting queued. Waiting for the output", item: null, state: verified ? "done" : "pending"};
    for (const [pattern, text, state] of PHRASES) if (pattern.test(raw)) return {text, item: null, state};
    const first = raw.split(";")[0].trim();
    const fallback = first || (action ? words(action) : "Decision recorded");
    return {text: fallback.length > 90 ? fallback.slice(0, 89) + "…" : fallback, item: null, state: verified ? "done" : "wait"};
  }

  const DRILL = {
    working: "Working",
    waiting_for_space_in_destination: "Blocked: output full",
    no_fuel: "Out of fuel",
    no_minable_resources: "Ore patch exhausted",
    no_power: "No power",
    disabled_by_script: "Paused",
    marked_for_deconstruction: "Being removed",
  };
  function drill(status) {
    if (typeof status !== "string" || !status) return "No drill placed";
    return DRILL[status] || words(status).replace(/^./, (c) => c.toUpperCase());
  }

  function item(name) { return typeof name === "string" && ITEM.test(name) ? name : null; }

  return {CATALOG, unlocked, event, drill, item, words};
})();
if (typeof module !== "undefined") module.exports = Explain;
