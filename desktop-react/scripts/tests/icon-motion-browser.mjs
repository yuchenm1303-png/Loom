import assert from "node:assert/strict";

// Real hover, real focus, real reduced-motion, on the icon lab (the production stylesheet
// order over every redrawn glyph). Run with the motion-fixture Vite server:
//   LOOM_PLAYWRIGHT_MODULE=... LOOM_CHROMIUM_PATH=... LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/icon-motion-browser.mjs
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";

// Glyphs that do not answer a hover on purpose: marks that only draw when they appear (the tick,
// the slash of a hidden eye), the stop square (it answers a press), and the loader (always turning).
const QUIET = new Set(["check", "circle-check", "eye-off", "square", "loader"]);
// Glyphs that answer by replaying an animation rather than by taking a pose.
const REPLAYS = new Set(["shield-check", "activity", "pulse", "terminal", "bot", "expression"]);

async function openLab(options = {}) {
  const context = await browser.newContext({ viewport: { width: 900, height: 1200 }, deviceScaleFactor: 1.5, ...options });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/icon-lab.html?theme=dark&size=24`);
  await page.waitForFunction(() => window.iconLab && document.querySelector("[data-lab-cell]"));
  await page.waitForTimeout(700);
  return { context, page, errors };
}

/** What a cell shows right now: every part's pose and opacity, and the animations running in it. */
const snapshot = (page, name) => page.evaluate((name) => {
  const cell = window.iconLab.cell(name, "live");
  const parts = [...cell.querySelectorAll("svg *")].map((node) => {
    const style = getComputedStyle(node);
    return `${style.transform}|${style.opacity}|${style.strokeDashoffset}`;
  });
  const animations = cell.getAnimations({ subtree: true }).filter((animation) => animation.animationName).map((animation) => animation.animationName);
  return { parts: parts.join(";"), animations: animations.sort().join(",") };
}, name);

async function hover(page, name) {
  const box = await page.evaluate((name) => { const cell = window.iconLab.cell(name, "live"); cell.scrollIntoView({ block: "center" }); const r = cell.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }, name);
  await page.mouse.move(box.x, box.y);
}

try {
  // 1. Every glyph answers a real hover (or stays quiet by design), and puts itself back.
  {
    const { context, page, errors } = await openLab();
    const names = await page.evaluate(() => window.iconLab.names);
    assert.ok(names.length > 40, "the lab lists the redrawn glyphs");
    const problems = [];
    for (const name of names) {
      await page.mouse.move(2, 1190);
      await page.waitForTimeout(450);
      const rest = await snapshot(page, name);
      await hover(page, name);
      await page.waitForTimeout(REPLAYS.has(name) ? 90 : 700);
      const engaged = await snapshot(page, name);
      const changed = engaged.parts !== rest.parts || engaged.animations !== rest.animations;
      if (QUIET.has(name) && changed) problems.push(`${name} should stay quiet on hover`);
      if (!QUIET.has(name) && !changed) problems.push(`${name} did not answer a hover`);
      await page.mouse.move(2, 1190);
      await page.waitForTimeout(700);
      const after = await snapshot(page, name);
      if (after.parts !== rest.parts) problems.push(`${name} did not return to rest`);
    }
    assert.deepEqual(problems, []);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 2. Keyboard focus answers like the pointer does.
  {
    const { context, page } = await openLab();
    const rest = await snapshot(page, "trash");
    await page.evaluate(() => window.iconLab.cell("trash", "live").focus());
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await page.waitForTimeout(700);
    assert.notEqual((await snapshot(page, "trash")).parts, rest.parts, "a focused control's glyph answers");
    await context.close();
  }

  // 3. Expanded state turns a chevron by exactly its quarter / half turn, once, with or without motion.
  for (const reducedMotion of ["no-preference", "reduce"]) {
    const { context, page } = await openLab({ reducedMotion });
    const turned = await page.evaluate(() => {
      const angle = (name, column) => {
        const path = window.iconLab.cell(name, column).querySelector("path");
        const m = path.getScreenCTM();
        return Math.round(Math.atan2(m.b, m.a) * 180 / Math.PI);
      };
      return { right: angle("chevron-right", "open"), down: angle("chevron-down", "open"), restRight: angle("chevron-right", "rest") };
    });
    assert.deepEqual(turned, { right: 90, down: 180, restRight: 0 }, `chevrons (${reducedMotion})`);
    await context.close();
  }

  // 4. Reduced motion: no glyph takes a pose.
  {
    const { context, page } = await openLab({ reducedMotion: "reduce" });
    for (const name of ["trash", "copy", "settings", "quill", "chevron-right", "plus"]) {
      const rest = await snapshot(page, name);
      await hover(page, name);
      await page.waitForTimeout(500);
      assert.equal((await snapshot(page, name)).parts, rest.parts, `${name} must not move under reduced motion`);
      await page.mouse.move(2, 1190);
    }
    await context.close();
  }

  // 5. A list row keeps its glyph still and still leans an arrow; a tool answers fully.
  {
    const { context, page } = await openLab();
    const result = await page.evaluate(async () => {
      const copy = (name) => window.iconLab.cell(name, "rest").querySelector("svg").cloneNode(true);
      const host = document.createElement("div");
      host.style.cssText = "position:fixed;left:520px;top:30px;display:grid;gap:14px";
      host.innerHTML = `<div data-ic-row><button id="row" style="width:60px;height:34px"></button></div><button id="tool" style="width:60px;height:34px"></button>`;
      document.body.appendChild(host);
      const row = host.querySelector("#row");
      row.append(copy("trash"), copy("chevron-right"));
      host.querySelector("#tool").append(copy("trash"));
      const rect = (el) => { const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; };
      return { row: rect(row), tool: rect(host.querySelector("#tool")) };
    });
    const pose = (selector, part) => page.evaluate(({ selector, part }) => getComputedStyle(document.querySelector(selector).querySelector(part)).transform, { selector, part });
    await page.mouse.move(result.row.x, result.row.y);
    await page.waitForTimeout(700);
    assert.equal(await pose("#row", '[data-icon="trash"] .li-lid'), "matrix(1, 0, 0, 1, 0, 0)", "a row's trash lid stays shut");
    assert.notEqual(await pose("#row", '[data-icon="chevron-right"] .li-all'), "matrix(1, 0, 0, 1, 0, 0)", "a row's chevron still leans");
    await page.mouse.move(result.tool.x, result.tool.y);
    await page.waitForTimeout(700);
    assert.notEqual(await pose("#tool", '[data-icon="trash"] .li-lid'), "matrix(1, 0, 0, 1, 0, 0)", "a tool's trash lid opens");
    await context.close();
  }

  // 6. A loader, or a glyph told it is spinning, turns at the shared pace.
  {
    const { context, page } = await openLab();
    const spin = await page.evaluate(() => {
      const loader = window.iconLab.cell("loader", "rest").querySelector("svg");
      const refresh = window.iconLab.cell("refresh", "rest").querySelector("svg").cloneNode(true);
      refresh.setAttribute("data-spinning", "true");
      document.body.appendChild(refresh);
      const read = (el) => ({ name: getComputedStyle(el).animationName, duration: getComputedStyle(el).animationDuration, count: getComputedStyle(el).animationIterationCount });
      return { loader: read(loader), refresh: read(refresh) };
    });
    assert.deepEqual(spin.loader, { name: "li-spin", duration: "0.9s", count: "infinite" });
    assert.deepEqual(spin.refresh, spin.loader);
    await context.close();
  }

  // 7. Pressing gives the glyph, not the control, a little.
  {
    const { context, page } = await openLab();
    const box = await page.evaluate(() => { const r = window.iconLab.cell("copy", "live").getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await page.mouse.move(box.x, box.y);
    await page.mouse.down();
    await page.waitForTimeout(260);
    const pressed = await page.evaluate(() => { const cell = window.iconLab.cell("copy", "live"); return { glyph: getComputedStyle(cell.querySelector("svg")).scale, control: getComputedStyle(cell).transform }; });
    await page.mouse.up();
    assert.equal(pressed.glyph, "0.88");
    assert.equal(pressed.control, "none", "the control itself does not move");
    await context.close();
  }

  // 8. The quill stands on its nib: engaged it leans in to write about that point, the barb a beat behind; pressed it
  //    gives toward the nib; a stroke (`data-stroke` on the row, for a new conversation) flicks the pen up off the page
  //    and back while the barb is drawn into its root and struck again, ending at rest, again for the next stroke;
  //    under reduced motion none of it happens.
  {
    const { context, page } = await openLab();
    const cell = "window.iconLab.cell('quill', 'live')";
    const turn = (part) => page.evaluate((part) => {
      const m = new DOMMatrix(getComputedStyle(window.iconLab.cell("quill", "live").querySelector(part)).transform);
      return Math.round((Math.atan2(m.b, m.a) * 1800) / Math.PI) / 10;
    }, part);
    const playing = () => page.evaluate(`${cell}.getAnimations({ subtree: true }).map((a) => a.animationName).filter(Boolean).sort().join(",")`);
    const at = (time) => page.evaluate((time) => {
      const node = window.iconLab.cell("quill", "live");
      for (const a of node.getAnimations({ subtree: true })) if (a.animationName) { a.pause(); a.currentTime = time; }
      const scribe = new DOMMatrix(getComputedStyle(node.querySelector(".li-scribe")).transform);
      return {
        turn: Math.round((Math.atan2(scribe.b, scribe.a) * 1800) / Math.PI) / 10,
        barb: parseFloat(getComputedStyle(node.querySelector(".li-barb")).strokeDashoffset),
        outline: Number(getComputedStyle(node.querySelector(".quill-outline")).opacity),
      };
    }, time);

    // Lean: into the page about the nib, the barb a little further, both back at rest after the pointer leaves.
    assert.equal(await turn(".li-quill"), 0);
    await hover(page, "quill");
    await page.waitForTimeout(800);
    assert.equal(await turn(".li-quill"), 6);
    assert.equal(await turn(".li-barb"), 2);
    assert.equal(await playing(), "", "hovering plays nothing of its own");
    const origin = await page.evaluate(() => {
      const svg = window.iconLab.cell("quill", "live").querySelector("svg");
      const box = svg.getBoundingClientRect();
      const [x, y] = getComputedStyle(svg).transformOrigin.split(" ").map(parseFloat);
      return { x: x / box.width, y: y / box.height };
    });
    assert.ok(origin.x < 0.2 && origin.y > 0.85, `a press gives toward the nib, not the middle (${JSON.stringify(origin)})`);
    await page.mouse.move(2, 1190);
    await page.waitForTimeout(700);
    assert.equal(await turn(".li-quill"), 0);
    assert.equal(await turn(".li-barb"), 0);

    // A stroke keeps the feather whole, flicks the pen up and back, strikes the barb again, and holds nothing.
    assert.equal(await playing(), "");
    await page.evaluate(`${cell}.setAttribute("data-stroke", "odd")`);
    assert.equal(await playing(), "li-strike-odd,li-stroke-odd");
    const drawn = await at(92);
    assert.ok(drawn.barb > 1, `the barb is drawn back into its root as the pen lifts (${drawn.barb})`);
    const lift = await at(110);
    assert.ok(lift.turn < -7, `the pen flicks up off the page (${lift.turn}°)`);
    assert.equal(lift.outline, 1, "the feather stays whole");
    const down = await at(290);
    assert.ok(down.turn > 0 && down.turn < 2.5, `it comes back down a hair past where it stood (${down.turn}°)`);
    assert.ok(down.barb < .05, `and the barb is struck again (${down.barb})`);
    await page.evaluate(`${cell}.getAnimations({ subtree: true }).forEach((a) => a.play())`);
    await page.waitForTimeout(700);
    assert.equal(await playing(), "", "the stroke ends and holds nothing");
    assert.equal(await turn(".li-scribe"), 0);
    // the next stroke plays again, even though the first is still on the row
    await page.evaluate(`${cell}.setAttribute("data-stroke", "even")`);
    assert.equal(await playing(), "li-strike-even,li-stroke-even");
    await context.close();
  }
  {
    // What the cascade plays for a stroke: the animation names of the pen and of the barb. (A collapsed tempo would hide
    // a running animation from getAnimations within a millisecond; a name does not go away.)
    const played = (page) => page.evaluate(() => {
      const cell = window.iconLab.cell("quill", "live");
      cell.setAttribute("data-stroke", "odd");
      return ["li-scribe", "li-barb"].map((part) => getComputedStyle(cell.querySelector("." + part)).animationName).join(",");
    });
    const system = await openLab({ reducedMotion: "reduce" });
    assert.equal(await played(system.page), "none,none", "a stroke must not animate under reduced motion");
    await system.context.close();
    // The app's own setting does the same without the system's.
    const app = await openLab();
    await app.page.evaluate("document.documentElement.setAttribute('data-loom-reduced-motion', 'true')");
    assert.equal(await played(app.page), "none,none", "a stroke must not animate when the app's reduced-motion setting is on");
    await hover(app.page, "quill");
    await app.page.waitForTimeout(500);
    const turned = await app.page.evaluate("getComputedStyle(window.iconLab.cell('quill', 'live').querySelector('.li-quill')).transform");
    assert.equal(turned, "matrix(1, 0, 0, 1, 0, 0)", "the quill must not lean when the app's reduced-motion setting is on");
    await app.context.close();
    // And the same stroke does animate when nothing asks for less.
    const normal = await openLab();
    assert.equal(await played(normal.page), "li-stroke-odd,li-strike-odd");
    await normal.context.close();
  }
  console.log("icon motion: ok");
} finally {
  await browser.close();
}
