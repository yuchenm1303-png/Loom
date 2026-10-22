// The feel probe's shared parts (feel.mjs in headless Chrome, feel-electron.mjs in a real Electron window): the
// in-page sampler, the interactions (declarative, so both drivers play them with real input at real coordinates),
// and the analysis. docs/motion-feel-2026-10-21.md is what it found.
//
// For every animation frame the sampler records the rect, effective opacity and scale of a few "subjects" (the
// popover, the dock, the composer the dock pushes...) and the timestamps of the input events. Per subject and channel:
//   first  ms from the first input event to the first frame that differs (the answer)
//   t50    ms until half of the change is done (what the eye calls "how fast")
//   t90    ms until 90% is done
//   jump   the largest one-frame step as % of the whole change (a pop is close to 100%)
// For round trips (the second input lands while the first is still moving) it reports the range, the largest step
// and how many times the direction turned: a smooth reversal is one turn and small steps.

export const SAMPLER = `(() => {
  if (window.__feel) return;
  const feel = { on: false, subjects: [], frames: [], inputs: [] };
  window.__feel = feel;
  for (const type of ["pointerdown", "pointerup", "keydown", "contextmenu", "click"]) {
    window.addEventListener(type, (event) => { if (feel.on) feel.inputs.push({ type, t: event.timeStamp, key: event.key || "" }); }, { capture: true });
  }
  const effectiveOpacity = (el) => {
    let o = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === "none") return 0;
      if (n === el && cs.visibility === "hidden") return 0;
      o *= Number(cs.opacity);
    }
    return o;
  };
  const scaleOf = (tf) => {
    if (!tf || tf === "none") return 1;
    const m = tf.match(/matrix(3d)?\\(([^)]+)\\)/);
    if (!m) return 1;
    const v = m[2].split(",").map(Number);
    return m[1] ? Math.hypot(v[0], v[1], v[2]) : Math.hypot(v[0], v[1]);
  };
  // "selector", "selector|text" (the first element whose text includes text) or "selector@n" (the nth match);
  // a bare selector is its last match.
  feel.resolve = (spec) => {
    const at = spec.match(/@(\\d+)$/);
    if (at) spec = spec.slice(0, at.index);
    const [sel, text] = spec.split("|");
    const list = [...document.querySelectorAll(sel)];
    if (at) return list[Number(at[1])] || null;
    if (text) return list.find((el) => (el.textContent || "").includes(text)) || null;
    return list[list.length - 1] || null;
  };
  feel.point = (spec, dx, dy) => {
    const el = feel.resolve(spec);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(dx == null ? r.left + r.width / 2 : r.left + dx), y: Math.round(dy == null ? r.top + r.height / 2 : r.top + dy) };
  };
  const tick = (now) => {
    if (feel.on) {
      const values = {};
      for (const spec of feel.subjects) {
        const el = feel.resolve(spec);
        if (!el) { values[spec] = null; continue; }
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        const sc = cs.scale && cs.scale !== "none" ? Number(cs.scale.split(" ")[0]) : 1;
        values[spec] = {
          x: Math.round(r.x * 10) / 10, y: Math.round(r.y * 10) / 10, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
          o: Math.round(effectiveOpacity(el) * 1000) / 1000, s: Math.round(scaleOf(cs.transform) * sc * 10000) / 10000,
        };
      }
      feel.frames.push({ t: now, values });
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})()`;

const tool = (id) => [["click", `.loom-tool-button[data-tool-id="${id}"]`]];
// The toolbar's toggle (one button in both states); the second selector is the card's / the header's own toggle of
// builds before SidebarControls, so two builds can be compared.
const sidebarClose = [["click", ".sidebar-controls .sidebar-toggle, .codex-sidebar-topbar .sidebar-toggle"]];
const sidebarOpen = [["click", ".sidebar-controls .sidebar-toggle, .thread-header .sidebar-toggle"]];

// [kind, spec, dx, dy]: click | rclick (at the element's centre, or dx/dy from its top left), key, wait.
export const SCENARIOS = [
  { name: "permission-open", subjects: [".permission-popover"], steps: [["click", ".composer button|Full access"]], wait: 600 },
  { name: "permission-close", subjects: [".permission-popover"], steps: [["key", "Escape"]], wait: 600 },
  { name: "model-open", subjects: [".model-popover"], steps: [["click", ".composer .model-chip"]], wait: 700 },
  { name: "model-close", subjects: [".model-popover"], steps: [["key", "Escape"]], wait: 600 },
  { name: "msgmenu-open", subjects: [".loom-context-menu"], steps: [["rclick", ".assistant-message", 40, 12]], wait: 500 },
  { name: "msgmenu-close", subjects: [".loom-context-menu"], steps: [["key", "Escape"]], wait: 500 },
  { name: "unfold-loom", subjects: [".project-thread-list-shell"], steps: [["click", ".project-disclosure-button"]], wait: 700, prep: true },
  { name: "process-expand", subjects: [".turn-process-grid"], steps: [["click", ".turn-process-header"]], wait: 700 },
  { name: "process-collapse", subjects: [".turn-process-grid"], steps: [["click", ".turn-process-header"]], wait: 700 },
  { name: "search-open", subjects: [".compact-search-shell"], steps: [["click", 'button[aria-label="Search conversations"]']], wait: 500 },
  { name: "search-close", subjects: [".compact-search-shell"], steps: [["key", "Escape"]], wait: 500 },
  { name: "sidebar-close", subjects: [".workspace-panels > .sidebar", ".thread-header-copy", ".composer"], steps: sidebarClose, wait: 800 },
  { name: "sidebar-open", subjects: [".workspace-panels > .sidebar", ".thread-header-copy", ".composer"], steps: sidebarOpen, wait: 800 },
  { name: "inspector-open", subjects: [".workspace-panels > .inspector", ".composer"], steps: tool("inspector"), wait: 800 },
  { name: "inspector-close", subjects: [".workspace-panels > .inspector", ".composer"], steps: tool("inspector"), wait: 800 },
  { name: "review-open", subjects: [".review-workspace", ".composer"], steps: tool("review"), wait: 800 },
  { name: "review-close", subjects: [".review-workspace", ".composer"], steps: tool("review"), wait: 800 },
  { name: "agents-open", subjects: [".sub-agent-dock", ".composer"], steps: tool("agents"), wait: 800 },
  { name: "agents-to-review", subjects: [".sub-agent-dock", ".review-workspace", ".composer"], steps: tool("review"), wait: 800 },
  { name: "review-close-2", subjects: [".review-workspace", ".composer"], steps: tool("review"), wait: 800 },
  { name: "background-open", subjects: [".background-task-panel"], steps: tool("background"), wait: 600 },
  { name: "background-close", subjects: [".background-task-panel"], steps: tool("background"), wait: 600 },
  { name: "settings-open", subjects: [".settings-main", ".settings-sidebar"], steps: tool("settings"), wait: 900 },
  { name: "settings-close", subjects: [".settings-main", ".settings-sidebar"], steps: [["click", ".settings-back"]], wait: 700 },
  { name: "rt-sidebar", roundTrip: true, subjects: [".workspace-panels > .sidebar", ".thread-header-copy", ".composer"], steps: [...sidebarClose, ["wait", 120], ...sidebarOpen], wait: 900 },
  { name: "rt-inspector", roundTrip: true, subjects: [".workspace-panels > .inspector", ".composer"], steps: [...tool("inspector"), ["wait", 140], ...tool("inspector")], wait: 900 },
  { name: "rt-review", roundTrip: true, subjects: [".review-workspace", ".composer"], steps: [...tool("review"), ["wait", 140], ...tool("review")], wait: 900 },
  { name: "rt-model", roundTrip: true, subjects: [".model-popover"], steps: [["click", ".composer .model-chip"], ["wait", 70], ["key", "Escape"]], wait: 600 },
  { name: "rt-settings", roundTrip: true, subjects: [".settings-main"], steps: [...tool("settings"), ["wait", 120], ["click", ".settings-back"]], wait: 800 },
];

export function analyze(frames, inputs, spec, roundTrip) {
  const t0 = inputs.length ? inputs[0].t : frames[0].t;
  const series = frames.map((f) => ({ t: f.t - t0, v: f.values[spec] }));
  if (!series.some((s) => s.v)) return { spec, note: "never present" };
  const out = { spec, channels: {} };
  if (!series[0].v && series[series.length - 1].v) out.mount = Math.round(series.find((s) => s.v).t);
  if (series[0].v && !series[series.length - 1].v) out.unmount = Math.round(series.find((s, i) => i > 0 && !s.v)?.t ?? -1);
  for (const ch of ["o", "x", "y", "w", "h", "s"]) {
    const vals = series.map((s) => (s.v ? s.v[ch] : (ch === "o" ? 0 : ch === "s" ? 1 : null)));
    const known = vals.filter((v) => v !== null);
    const start = known[0], end = known[known.length - 1];
    const range = roundTrip ? Math.max(...known) - Math.min(...known) : Math.abs(end - start);
    const tiny = ch === "o" ? 0.02 : ch === "s" ? 0.003 : 0.75;
    if (range < tiny) continue;
    let first = null, t50 = null, t90 = null, settle = null, maxStep = 0, maxStepAt = 0, reversals = 0, lastDir = 0;
    for (let i = 1; i < vals.length; i += 1) {
      if (vals[i] === null || vals[i - 1] === null) continue;
      const d = vals[i] - vals[i - 1];
      if (Math.abs(d) > tiny / 4) {
        if (first === null && series[i].t >= 0) first = series[i].t;
        settle = series[i].t;
        const dir = Math.sign(d);
        if (lastDir && dir !== lastDir) reversals += 1;
        lastDir = dir;
      }
      if (Math.abs(d) > maxStep) { maxStep = Math.abs(d); maxStepAt = series[i].t; }
      if (!roundTrip) {
        const p = (vals[i] - start) / (end - start);
        if (t50 === null && p >= 0.5) t50 = series[i].t;
        if (t90 === null && p >= 0.9) t90 = series[i].t;
      }
    }
    const r = (v) => (v === null ? null : Math.round(v));
    out.channels[ch] = { from: start, to: end, range: Math.round(range * 1000) / 1000, first: r(first), t50: r(t50), t90: r(t90), settle: r(settle), jump: Math.round((maxStep / (range || 1)) * 100), jumpAt: r(maxStepAt), maxStep: Math.round(maxStep * 1000) / 1000, reversals };
  }
  return out;
}

export function report(sc, frames, inputs, log = console.log) {
  const rows = sc.subjects.map((spec) => analyze(frames, inputs, spec, sc.roundTrip));
  const gaps = frames.slice(1).map((f, i) => f.t - frames[i].t);
  const sorted = [...gaps].sort((a, b) => a - b);
  const p50 = sorted[Math.floor(sorted.length / 2)] || 0;
  const t0 = inputs.length ? inputs[0].t : 0;
  log(`\n== ${sc.name}  (inputs: ${inputs.map((i) => `${i.type}@${Math.round(i.t - t0)}`).join(" ")}; frame p50 ${p50.toFixed(1)}ms, worst ${Math.round(Math.max(...gaps))}ms)`);
  for (const row of rows) {
    if (row.note) { log(`  ${row.spec}: ${row.note}`); continue; }
    log(`  ${row.spec}${row.mount !== undefined ? ` mount@${row.mount}` : ""}${row.unmount !== undefined ? ` unmount@${row.unmount}` : ""}`);
    const chans = Object.entries(row.channels);
    if (!chans.length) log("     (no change)");
    for (const [ch, c] of chans) {
      log(sc.roundTrip
        ? `     ${ch} range ${c.range} first ${c.first} settle ${c.settle} maxstep ${c.maxStep} (${c.jump}%) @${c.jumpAt} reversals ${c.reversals}`
        : `     ${ch} ${c.from}→${c.to} first ${c.first} t50 ${c.t50} t90 ${c.t90} settle ${c.settle} jump ${c.jump}%@${c.jumpAt}`);
    }
  }
  return { name: sc.name, rows, p50, worst: Math.max(...gaps) };
}
