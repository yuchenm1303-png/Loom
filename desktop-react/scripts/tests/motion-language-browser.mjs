// The motion language, read back from the running renderer.
//
// tests/test_motion_scale_contract.py reads the sources; this reads what the browser really runs after the cascade
// (document.getAnimations(), see motion-lab/spec-lib.mjs) while the most common interactions are made in both themes,
// and in a session that asks for reduced motion:
//   - every duration is a step of the scale (a mark's travel is the one thing that may be longer than flow), every
//     curve is one of the scale's;
//   - a popover or a menu comes in on `fast` from a readable .72 and leaves on `fast-out` on the exit curve, and
//     never moves;
//   - what carries words does not move at all: in these interactions the only things that travel are the selection
//     pills and the bars of a chart, and a dock or the sidebar by a few pixels of depth while it comes and goes;
//   - folds take a fold's time, in-place surfaces take `base` in and `base-out` out;
//   - a dock (and the sidebar) is readable on its first frame, settles on the panel step and leaves on fast-out;
//   - with reduced motion nothing takes time.
import assert from "node:assert/strict";
import { open, runStep, readRows, scenarios, describeRow, interesting } from "../motion-lab/spec-lib.mjs";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const executablePath = process.env.LOOM_CHROMIUM_PATH;

const SCALE = new Set([90, 120, 150, 200, 240, 260, 320, 390, 520]);
const CURVES = new Set([
  "ease", "linear",
  "cubic-bezier(0.16, 0.78, 0.18, 1)", // out
  "cubic-bezier(0.22, 0.7, 0.18, 1)", // move
  "cubic-bezier(0.3, 0.3, 0.55, 1)", // exit
  "cubic-bezier(0.4, 0, 0.2, 1)", // inout
  "cubic-bezier(0.2, 0, 0, 1)", // glide
]);
const OUT = "cubic-bezier(0.16, 0.78, 0.18, 1)";
const MOVE = "cubic-bezier(0.22, 0.7, 0.18, 1)";
const EXIT = "cubic-bezier(0.3, 0.3, 0.55, 1)";
const INOUT = "cubic-bezier(0.4, 0, 0.2, 1)";

// What may travel while these interactions are made: the selection pills (text-free, scripted, long-distance), the round
// "jump to latest" button (an arrow in a disc), a chart's bars, the quick-tool dock (a capsule of icon tiles that slides
// in from its edge, its glider and the name tip that follows it). Icons and pseudo-element rings are left out by
// describeRow.
const MARKS = (row) => /^(span\.thread-selection-pill|i\.settings-nav-pip|button\.transcript-jump-latest(\.is-visible)?|div\.loom-tool-(glider|tip|dock|position))/.test(row.target) || /^profile-(bar|hour)-grow$/.test(row.name);
// The docks: the inspector (a grid column), the portal docks, and the sidebar on the other side.
const DOCK = (row) => /^(aside|div)\.(inspector|review-workspace|sub-agent-dock|artifact-preview-dock|project-details-panel|sidebar)(\.|$)/.test(row.target);

async function tour(theme, reduced) {
  const { browser, page, errors } = await open(chromium, { origin, executablePath, theme, reducedMotion: reduced ? "reduce" : "no-preference" });
  const failures = [];
  try {
    for (const [name, action, settle] of scenarios()) {
      const failure = await runStep(page, name, () => action(page), settle);
      if (failure) failures.push(`${name}: ${failure}`);
    }
    return { rows: await readRows(page), failures, errors };
  } finally {
    await browser.close();
  }
}

const inStep = (rows, step) => rows.filter((row) => row.step === step);
const find = (rows, step, predicate, what) => {
  const hit = inStep(rows, step).find(predicate);
  assert.ok(hit, `${step}: ${what} was not recorded`);
  return hit;
};

for (const theme of (process.env.LOOM_THEMES || "dark,light").split(",")) {
  const { rows, failures, errors } = await tour(theme, false);
  assert.deepEqual(failures, [], `${theme}: every step of the tour could be made`);
  assert.deepEqual(errors, [], `${theme}: no page errors`);
  const shown = interesting(rows);

  // ---- the scale ---------------------------------------------------------------------------------------------
  const offScale = shown.filter((row) => {
    const d = describeRow(row);
    if (d.reversed) return false; // a transition cut short by its own reversal
    if (row.duration === 0) return false; // a hold, not a step: something hidden once its exit is over
    if (row.kind === "script" && row.name !== "loom-layout-anchor" && row.duration >= 320 && row.duration <= 390) return false; // a mark's travel
    return !SCALE.has(row.duration);
  });
  assert.deepEqual(offScale.map((row) => `${row.step}: ${row.target} ${row.name} ${row.duration}ms`), [], `${theme}: every duration is a step of the scale`);

  const offCurve = shown.filter((row) => !CURVES.has(row.easing) && !/^linear\(/.test(row.easing));
  // Only the sidebar snapshots (its card and the two toolbar controls it shares with the header); no other step does.
  assert.deepEqual(shown.filter((row) => describeRow(row).viewTransition && !/^sidebar-/.test(row.step)).map((row) => `${row.step}: ${row.target}`), [], `${theme}: nothing but the sidebar is snapshotted`);
  for (const step of ["sidebar-open", "sidebar-close"]) {
    const snapshots = inStep(rows, step).filter((row) => describeRow(row).viewTransition);
    assert.ok(snapshots.length && snapshots.every((row) => /loom-sidebar(-toggle|-compose)?\)/.test(row.target)), `${theme}: ${step} snapshots only the card and its shared controls (${snapshots.map((row) => row.target).join(", ")})`);
  }
  assert.deepEqual(offCurve.map((row) => `${row.step}: ${row.target} ${row.name} ${row.easing}`), [], `${theme}: every curve is the scale's`);

  const distinct = new Set(shown.filter((row) => !describeRow(row).reversed && !describeRow(row).layoutAnchor)
    .map((row) => `${row.duration}|${row.easing}`));
  assert.ok(distinct.size <= 30, `${theme}: ${distinct.size} distinct duration+curve pairs in the whole tour (it was 54 on a shorter one)`);

  // ---- popovers and menus: readable at once, fast in, fast-out, no travel ------------------------------------------
  for (const [open_, close_, target] of [
    ["permission-popover-open", "permission-popover-close", "div.composer-popover.permission-popover"],
    ["model-popover-open", "model-popover-close", "div.composer-popover.model-popover"],
    ["message-menu-open", "message-menu-close", "div.loom-context-menu.is-positioned"],
  ]) {
    const arrive = find(rows, open_, (row) => row.target === target && row.name === "opacity", "the opacity transition");
    assert.equal(arrive.duration, 150, `${theme}: ${open_} comes in on fast`);
    assert.equal(arrive.easing, OUT, `${theme}: ${open_} comes in on the out curve`);
    assert.equal(arrive.first.opacity, "0.72", `${theme}: ${open_} is readable on its first frame`);
    const leave = find(rows, close_, (row) => row.target === target && row.name === "opacity", "the exit transition");
    assert.equal(leave.duration, 120, `${theme}: ${close_} leaves on fast-out`);
    assert.equal(leave.easing, EXIT, `${theme}: ${close_} leaves on the exit curve`);
    for (const step of [open_, close_]) {
      assert.deepEqual(inStep(rows, step).filter((row) => row.target.startsWith(target.split(".").slice(0, 2).join(".")) && describeRow(row).moves.length).map((row) => row.name), [], `${theme}: ${step} does not move`);
    }
  }
// The conversation menu is a transition (a keyframe entrance cannot be turned around by a second press).
  const menuIn = find(rows, "thread-menu-open", (row) => /^div\.thread-context-menu/.test(row.target) && row.name === "opacity", "the thread menu's entrance");
  assert.equal(menuIn.kind, "transition");
  assert.equal(menuIn.duration, 150);
  assert.equal(menuIn.first.opacity, "0.72");
  const menuOut = find(rows, "thread-menu-close", (row) => /^div\.thread-context-menu/.test(row.target) && row.name === "opacity", "the thread menu's exit");
  assert.deepEqual([menuOut.duration, menuOut.easing], [120, EXIT]);

  // ---- what carries words does not move ----------------------------------------------------------------------------
  const travelling = shown.filter((row) => {
    const d = describeRow(row);
    return d.moves.length && !d.icon && !d.pseudo && !d.viewTransition && !d.layoutAnchor && !d.reversed && !MARKS(row)
      && !(DOCK(row) && /^(dock|sidebar)-/.test(row.step)); // a dock's own depth, read below
  });
  assert.deepEqual(travelling.map((row) => `${row.step}: ${row.target} ${row.name} ${d_moves(row)}`), [], `${theme}: nothing that carries words travels`);
  for (const step of ["thread-switch", "settings-page", "settings-page-far"]) {
    const movers = new Set(inStep(shown, step).filter((row) => describeRow(row).moves.length && !describeRow(row).icon && !describeRow(row).pseudo).map((row) => row.target));
    assert.ok(movers.size <= 1, `${theme}: ${step}: one mark moves (${[...movers].join(", ")})`);
  }

  // ---- in place: base in, base-out out ------------------------------------------------------------------------------
  for (const [step, target, name, duration, easing] of [
    ["settings-open", "aside.settings-sidebar", "opacity", 240, OUT],
    ["settings-open", "main.settings-main", "opacity", 240, OUT],
    ["settings-close", "aside.settings-sidebar", "opacity", 200, EXIT],
    ["settings-close", "main.settings-main", "opacity", 200, EXIT],
    ["profile-open", "section.profile-insights-page", "opacity", 240, OUT],
    ["profile-close", "section.profile-insights-page", "opacity", 200, EXIT],
    ["background-open", "section.background-task-panel.pl-float", "opacity", 240, OUT],
    ["background-close", "section.background-task-panel.pl-float", "opacity", 200, EXIT],
  ]) {
    const hit = find(rows, step, (row) => row.target === target && row.name === name, `${target} ${name}`);
    assert.equal(hit.duration, duration, `${theme}: ${step} ${target}`);
    assert.equal(hit.easing, easing, `${theme}: ${step} ${target} curve`);
  }
  assert.equal(find(rows, "settings-open", (row) => row.target === "main.settings-main").delay, 40, `${theme}: the page follows the sidebar by one stagger`);

  // ---- switching: the conversation is there at once, the pill glides, the title fades ---------------------------------
  // The replacing conversation arrives at full opacity, settled at its live edge (1ea15edb): the stage has no entrance.
  assert.deepEqual(inStep(rows, "thread-switch").filter((row) => /^loom-thread-surface/.test(row.name)).map((row) => `${row.target} ${row.name}`), [], `${theme}: the conversation arrives at full opacity`);
  const pill = find(rows, "thread-switch", (row) => row.target === "span.thread-selection-pill" && /translate/.test(Object.keys(row.last).join()), "the selection pill");
  assert.ok(pill.duration >= 320 && pill.duration <= 390, `${theme}: the pill travels in ${pill.duration}ms`);
  assert.equal(pill.easing, "cubic-bezier(0.2, 0, 0, 1)");
  assert.equal(find(rows, "thread-switch", (row) => row.target === "span.animated-thread-title", "the title").duration, 150);
  const homeIn = inStep(rows, "thread-switch").filter((row) => row.name === "loom-home-in");
  assert.ok(homeIn.length >= 2 && homeIn.every((row) => Object.keys(row.first).join() === "opacity" && row.duration === 240), `${theme}: the home arrives by opacity alone`);
  assert.deepEqual(homeIn.map((row) => row.delay).sort((a, b) => a - b).slice(0, 3), [0, 40, 80].slice(0, Math.min(3, homeIn.length)), `${theme}: one stagger apart`);
  const pages = inStep(rows, "settings-page").filter((row) => row.name === "settings-block-fade");
  assert.ok(pages.length >= 2 && pages.every((row) => row.duration === 240 && Object.keys(row.first).join() === "opacity"), `${theme}: a settings page fades in on base`);

  // ---- folds --------------------------------------------------------------------------------------------------------
  for (const [step, target, duration, easing] of [
    ["process-expand", "div.turn-process-grid", 320, MOVE],
    ["process-collapse", "div.turn-process-grid", 320, INOUT],
    ["project-unfold", "div.project-thread-list-shell.open", 320, MOVE],
    ["project-fold", "div.project-thread-list-shell", 320, MOVE],
    ["sidebar-search-open", "div.compact-search-shell.open", 240, MOVE],
    ["sidebar-search-close", "div.compact-search-shell", 240, MOVE],
  ]) {
    const hit = find(rows, step, (row) => row.target === target && row.name === "grid-template-rows", `${target} rows`);
    assert.equal(hit.duration, duration, `${theme}: ${step}`);
    assert.equal(hit.easing, easing, `${theme}: ${step} curve`);
  }

  // ---- the docks: readable at once, depth on the panel step, gone on fast-out ---------------------------------
  // A dock is a live surface (no View Transition: a snapshot costs a frozen frame before anything moves). It arrives
  // readable (its fade starts a fifth of the way in, where the out curve is at .72) with a few pixels of depth settling
  // on the panel step, and leaves on fast-out on the exit curve, seen until it has gone. The workspace follows on the
  // panel step either way. (The sidebar keeps its snapshot for the toolbar controls it shares with the header; it is
  // read above and by sidebar-toolbar-motion-browser.mjs.)
  for (const [open_, close_] of [["dock-open", "dock-close"]]) {
    const drift = find(rows, open_, (row) => DOCK(row) && row.name === "transform", "the dock's drift in");
    assert.deepEqual([drift.duration, drift.easing], [390, OUT], `${theme}: ${open_}: the depth settles on the panel step`);
    const px = Math.abs(Number(/translate(?:3d)?\((-?[\d.]+)px/.exec(drift.first.transform || "")?.[1]));
    assert.ok(px > 0 && px <= 24, `${theme}: ${open_}: a few pixels of depth, not a slide (${drift.first.transform})`);
    const fadeIn = find(rows, open_, (row) => DOCK(row) && row.name === "opacity", "the dock's fade in");
    assert.deepEqual([fadeIn.duration, fadeIn.easing, fadeIn.delay], [150, OUT, -30], `${theme}: ${open_}: readable at once`);
    const out = find(rows, close_, (row) => DOCK(row) && row.name === "opacity", "the dock's fade out");
    assert.deepEqual([out.duration, out.easing], [120, EXIT], `${theme}: ${close_}: gone on fast-out`);
    const driftOut = find(rows, close_, (row) => DOCK(row) && row.name === "transform", "the dock's drift out");
    assert.deepEqual([driftOut.duration, driftOut.easing], [120, EXIT], `${theme}: ${close_}: drifts out on fast-out`);
    const hold = find(rows, close_, (row) => DOCK(row) && row.name === "visibility", "the hold before the dock is hidden");
    assert.equal(hold.delay, 120, `${theme}: ${close_}: hidden when its exit is over, not before`);
    for (const step of [open_, close_]) {
      const anchors = inStep(rows, step).filter((row) => row.name === "loom-layout-anchor");
      assert.ok(anchors.length >= 2 && anchors.every((row) => row.duration === 390 && row.easing === MOVE), `${theme}: ${step}: the workspace follows the dock on the panel step (${anchors.map((row) => row.target + " " + row.duration + " " + row.easing).join("; ")})`);
    }
  }
}

function d_moves(row) {
  return describeRow(row).moves.join("+");
}

// ---- reduced motion: nothing takes time ---------------------------------------------------------------------------------
{
  const { rows, failures } = await tour("dark", true);
  assert.deepEqual(failures, [], "reduced motion: every step of the tour could be made");
  const slow = interesting(rows).filter((row) => {
    const d = describeRow(row);
    return !d.reversed && !d.layoutAnchor && row.duration > 80;
  });
  assert.deepEqual(slow.map((row) => `${row.step}: ${row.target} ${row.name} ${row.duration}ms`), [], "reduced motion: whatever is written on the scale takes no time");
}

console.log("motion-language-browser: ok");
