// The composer's panels are one family (src/components/composerPanel.tsx, composer-panels.css,
// docs/composer-panels-2026-10-22.md): tools & skills, permissions, the model card, balance & quota and the draft
// preview wear the same material and head, come and go the same way, open one at a time, give the focus back, walk
// with the arrow keys, and only marks travel inside them. Both themes, on the live-app fixture.
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/composer-panels-browser.mjs
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

const PANELS = [
  ["tools", ".composer-tools-trigger"],
  ["permission", ".permission-chip"],
  ["model", ".model-chip"],
  ["usage", ".model-usage-button"],
  ["preview", '[aria-label="Preview message"]'],
];

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1333, height: 853 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en&models=rich&usage=1`);
    await page.locator(".composer textarea").first().waitFor();
    await page.waitForTimeout(700);
    await page.locator(".composer textarea").first().fill("Check the preview of this draft.");

    const looks = [];
    for (const [id, trigger] of PANELS) {
      const sample = await page.evaluate(async ({ id, trigger }) => {
        const frame = () => new Promise(requestAnimationFrame);
        document.querySelector(trigger).click();
        await frame();
        const el = document.querySelector(`.cp[data-composer-panel="${id}"]`);
        if (!el) throw new Error(`${id}: no panel on the first frame`);
        const fade = el.getAnimations().find((animation) => animation.transitionProperty === "opacity");
        const first = { opacity: Number(getComputedStyle(el).opacity), transform: getComputedStyle(el).transform, inert: el.inert, phase: el.dataset.motionPhase,
          fade: fade ? `${fade.effect.getTiming().duration} ${fade.effect.getTiming().easing}` : "none" };
        await new Promise((resolve) => setTimeout(resolve, 320));
        const style = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const anchor = document.querySelector(trigger).getBoundingClientRect();
        const title = el.querySelector(".cp-title, .cp-identity-name");
        const titleStyle = getComputedStyle(title);
        return {
          first,
          material: [style.borderTopLeftRadius, style.borderTopWidth, style.borderTopColor, style.backgroundColor, style.boxShadow].join(" | "),
          head: `${titleStyle.fontSize}/${titleStyle.fontWeight}`,
          // Where the first thing in the head starts, from the inside of the border (the model card leads with its mark).
          inset: Math.round((el.querySelector(".cp-identity > :first-child") ?? title).getBoundingClientRect().left - box.left - parseFloat(style.borderLeftWidth)),
          box: { left: box.left, right: box.right, top: box.top, bottom: box.bottom },
          gap: Math.round(anchor.top - box.bottom),
          open: document.querySelectorAll('.cp[data-motion-phase="entered"]').length,
          expanded: document.querySelector(trigger).getAttribute("aria-expanded"),
        };
      }, { id, trigger });
      // Readable on its first frame, still, and taking input.
      assert.ok(sample.first.opacity >= 0.7 && sample.first.opacity < 0.99, `${theme}/${id}: readable on the first frame, and fading in (${sample.first.opacity})`);
      assert.equal(sample.first.fade, "150 cubic-bezier(0.16, 0.78, 0.18, 1)", `${theme}/${id}: comes in on the fast step and the out curve`);
      assert.equal(sample.first.transform, "none", `${theme}/${id}: the panel does not travel`);
      assert.equal(sample.first.inert, false, `${theme}/${id}: takes input at once`);
      assert.equal(sample.open, 1, `${theme}/${id}: one panel at a time`);
      assert.equal(sample.expanded, "true", `${theme}/${id}: the trigger says it is open`);
      assert.ok(sample.box.left >= 0 && sample.box.right <= 1333 && sample.box.top >= 0, `${theme}/${id}: inside the window ${JSON.stringify(sample.box)}`);
      assert.equal(sample.gap, 10, `${theme}/${id}: hangs 10px above its trigger`);
      looks.push({ id, ...sample });

      // Escape: it leaves (inert, on its exit), then goes, and the focus is back on the trigger.
      const exit = await page.evaluate(async ({ id, trigger }) => {
        document.activeElement?.blur();
        document.querySelector(`.cp[data-composer-panel="${id}"]`).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        await new Promise(requestAnimationFrame);
        const el = document.querySelector(`.cp[data-composer-panel="${id}"]`);
        const leaving = el ? { phase: el.dataset.motionPhase, inert: el.inert } : null;
        const started = performance.now();
        while (document.querySelector(`.cp[data-composer-panel="${id}"]`) && performance.now() - started < 1000) await new Promise(requestAnimationFrame);
        return { leaving, goneAfter: Math.round(performance.now() - started), focus: document.activeElement === document.querySelector(trigger) };
      }, { id, trigger });
      assert.deepEqual(exit.leaving, { phase: "exiting", inert: true }, `${theme}/${id}: leaves on its exit without taking input`);
      assert.ok(exit.goneAfter < 400, `${theme}/${id}: gone after its exit (${exit.goneAfter}ms)`);
      assert.ok(exit.focus, `${theme}/${id}: Escape gives the focus back to the trigger`);
    }

    // One material, one head, one inset.
    for (const key of ["material", "head", "inset"]) {
      const values = new Set(looks.map((look) => look[key]));
      assert.equal(values.size, 1, `${theme}: the panels differ in ${key}: ${JSON.stringify(looks.map((look) => [look.id, look[key]]))}`);
    }
    assert.equal(looks[0].head, "13px/600", `${theme}: a panel's title is 13/600`);
    assert.equal(looks[0].inset, 16, `${theme}: a panel's head starts 16px in`);

    // Opening another panel closes the open one, whichever component either belongs to (each pair of neighbours in
    // the chain lives in a different component, so a panel with a host of its own would stay open beside the next).
    // The triggers are clicked without a pointer, as the keyboard does: a press outside would close the panel anyway.
    const chain = [...PANELS, PANELS[0], PANELS[2]];
    for (let index = 1; index < chain.length; index += 1) {
      const [previous] = chain[index - 1];
      const [id, trigger] = chain[index];
      if (index === 1) {
        await page.evaluate((selector) => document.querySelector(selector).click(), chain[0][1]);
        await page.locator(`.cp[data-composer-panel="${previous}"][data-motion-phase="entered"]`).waitFor();
      }
      await page.evaluate((selector) => document.querySelector(selector).click(), trigger);
      await page.locator(`.cp[data-composer-panel="${id}"][data-motion-phase="entered"]`).waitFor();
      await page.locator(`.cp[data-composer-panel="${previous}"]`).waitFor({ state: "detached" });
      assert.equal(await page.locator('.cp[data-motion-phase="entered"]').count(), 1, `${theme}: ${id} opened beside ${previous}`);
    }
    assert.equal(await page.locator('.cp[data-composer-panel="model"]').count(), 1);

    // An open trigger wears the reading ink, not the accent.
    const ink = await page.evaluate(() => {
      const chip = document.querySelector(".model-chip");
      return { chip: getComputedStyle(chip).color, glyph: getComputedStyle(chip.querySelector("svg")).color };
    });
    assert.equal(ink.glyph, ink.chip, `${theme}: the open trigger's glyph is in the chip's ink`);

    // The reasoning needle is a mark, the one thing that travels: to the chosen level on the settle spring (flow), with the
    // window that reveals the lit ticks sliding beside it. They are `transform` transitions, which the compositor runs; the
    // ticks themselves stand still (a custom property carrying the needle's place once restyled every tick every frame).
    const travel = await page.evaluate(async () => {
      const frame = () => new Promise(requestAnimationFrame);
      const stops = [...document.querySelectorAll(".cp-step")];
      stops[3].click();
      await frame();
      const running = document.getAnimations().filter((animation) => animation.effect?.target?.closest?.(".cp-steps"));
      const transforms = running.filter((animation) => animation.transitionProperty === "transform");
      const parts = transforms.map((animation) => ({ part: animation.effect.target.classList[0], duration: animation.effect.getTiming().duration, easing: String(animation.effect.getTiming().easing) }));
      const ticksAnimated = running.filter((animation) => animation.effect.target.closest(".cp-ticks")).length;
      // Pause and seek: the lit ticks end where the needle is, at every moment of the travel.
      const gaps = [];
      for (const ms of [40, 120, 200]) {
        for (const animation of transforms) { animation.pause(); animation.currentTime = ms; }
        await frame();
        const needle = document.querySelector(".cp-needle").getBoundingClientRect();
        gaps.push(Math.abs(document.querySelector(".cp-lit").getBoundingClientRect().right - (needle.left + needle.width / 2)));
      }
      for (const animation of transforms) animation.finish();
      return { checked: stops.map((stop) => stop.getAttribute("aria-checked")), parts, ticksAnimated, gaps };
    });
    assert.deepEqual(travel.checked, ["false", "false", "false", "true", "false"], `${theme}: the fourth level is chosen`);
    assert.deepEqual(travel.parts.map((part) => part.part).sort(), ["cp-lit", "cp-lit-ticks", "cp-needle", "cp-pin", "cp-slide"], `${theme}: the needle, its pin and the lit window travel`);
    for (const part of travel.parts) {
      assert.equal(part.duration, 320, `${theme}: ${part.part} on the flow step`);
      assert.match(part.easing, /^linear\(/, `${theme}: ${part.part} on the spring`);
    }
    assert.equal(travel.ticksAnimated, 0, `${theme}: the ticks stand still while the needle travels`);
    assert.ok(travel.gaps.every((gap) => gap < 0.75), `${theme}: the lit ticks end where the needle is (${travel.gaps.map((gap) => gap.toFixed(2))})`);

    // What is drawn: the ticks up to the needle are the accent, the ones after it are not (a screenshot of the scale, read pixel by pixel:
    // the window that reveals the lit copy has to clip it, which no rectangle can show).
    const scale = await page.evaluate(() => {
      const box = document.querySelector(".cp-scale").getBoundingClientRect();
      const needle = document.querySelector(".cp-needle").getBoundingClientRect();
      return {
        clip: { x: Math.floor(box.left) - 8, y: Math.floor(box.top), width: Math.ceil(box.width) + 16, height: Math.ceil(box.height) },
        needleX: needle.left + needle.width / 2,
        ticks: [...document.querySelectorAll(".cp-scale > .cp-ticks > span")].map((tick) => { const r = tick.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }),
        overflow: getComputedStyle(document.querySelector(".cp-lit")).overflowX,
      };
    });
    assert.equal(scale.overflow, "clip", `${theme}: the lit copy is clipped by its window`);
    const shot = (await page.screenshot({ clip: scale.clip })).toString("base64");
    const chroma = await page.evaluate(async ({ shot, scale }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${shot}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext("2d");
      context.drawImage(image, 0, 0);
      const out = { left: [], right: [] };
      for (const tick of scale.ticks) {
        const side = tick.x < scale.needleX - 8 ? "left" : tick.x > scale.needleX + 8 ? "right" : null;
        if (!side) continue;
        let best = 0;
        for (const dx of [-1, 0, 1]) {
          const [r, g, b] = context.getImageData(Math.round(tick.x - scale.clip.x) + dx, Math.round(tick.y - scale.clip.y), 1, 1).data;
          best = Math.max(best, Math.max(r, g, b) - Math.min(r, g, b));
        }
        out[side].push(best);
      }
      return out;
    }, { shot, scale });
    assert.ok(chroma.left.length > 10 && chroma.right.length > 3, `${theme}: ticks on both sides of the needle`);
    assert.ok(chroma.left.every((value) => value > 70), `${theme}: every tick before the needle is the accent (${chroma.left.join(",")})`);
    assert.ok(chroma.right.every((value) => value < 40), `${theme}: no tick after the needle is (${chroma.right.join(",")})`);

    // A press, a drag and the keys choose a level; the Host hears of it once, when the pointer lets go (never mid-drag).
    await page.evaluate(() => {
      window.__reasoningCalls = [];
      const original = window.loom.setReasoning.bind(window.loom);
      window.loom.setReasoning = (...args) => { window.__reasoningCalls.push(args[args.length - 1]); return original(...args); };
    });
    const read = () => page.evaluate(() => {
      const steps = document.querySelector(".cp-steps");
      const needle = document.querySelector(".cp-needle").getBoundingClientRect();
      return {
        checked: [...document.querySelectorAll(".cp-step")].findIndex((stop) => stop.getAttribute("aria-checked") === "true"),
        dragging: steps.classList.contains("is-dragging"),
        held: steps.classList.contains("is-held"),
        needleX: needle.left + needle.width / 2,
        calls: window.__reasoningCalls.slice(),
        caption: document.querySelector(".cp-reasoning .cp-caption")?.textContent ?? "",
        selected: window.getSelection()?.toString() ?? "",
        pinScale: getComputedStyle(document.querySelector(".cp-pin-dot")).scale,
      };
    });
    const geometry = await page.evaluate(() => ({
      stops: [...document.querySelectorAll(".cp-step")].map((stop) => { const box = stop.getBoundingClientRect(); return box.left + box.width / 2; }),
      scaleBottom: document.querySelector(".cp-scale").getBoundingClientRect().bottom,
      labelY: document.querySelector(".cp-step").getBoundingClientRect().top + 12,
    }));
    const rampY = geometry.scaleBottom - 12;
    await page.mouse.move(40, 40);
    // Hovering a level (on the scale, not only on its word) reads it before it is chosen and lifts its tick.
    await page.mouse.move(geometry.stops[1], rampY);
    await page.waitForTimeout(250);
    const hover = await page.evaluate(() => ({
      peekedTicks: document.querySelectorAll(".cp-ticks > .is-peek").length,
      peekedWord: [...document.querySelectorAll(".cp-step")].findIndex((stop) => stop.classList.contains("is-peek")),
      lifted: [...document.querySelectorAll(".cp-ticks > .is-peek")].every((tick) => getComputedStyle(tick).transform !== "none"),
    }));
    assert.deepEqual(hover, { peekedTicks: 2, peekedWord: 1, lifted: true }, `${theme}: the hovered level is lifted (its tick in both layers) and its word brightens`);
    await page.mouse.move(40, 40);
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => document.querySelectorAll(".cp-ticks > .is-peek").length), 0, `${theme}: nothing stays lifted`);
    // A drag carries the needle under the pointer, level by level the word that is chosen follows, and nothing is sent.
    await page.mouse.move(geometry.stops[3], rampY);
    await page.mouse.down();
    await page.waitForTimeout(160);
    const held = await read();
    assert.ok(held.held && !held.dragging, `${theme}: a press takes hold of the needle`);
    assert.equal(held.pinScale, "1.3", `${theme}: and the pin swells under the hand`);
    const between = (geometry.stops[1] + geometry.stops[2]) / 2 + 6;
    await page.mouse.move(between, rampY, { steps: 8 });
    await page.waitForTimeout(60);
    const dragged = await read();
    assert.ok(dragged.dragging, `${theme}: dragging`);
    assert.ok(Math.abs(dragged.needleX - between) < 1.5, `${theme}: the needle is under the pointer (${dragged.needleX} vs ${between})`);
    assert.equal(dragged.checked, 2, `${theme}: the word under the needle reads as chosen`);
    assert.equal(dragged.selected, "", `${theme}: dragging across the words selects none of them`);
    assert.deepEqual(dragged.calls, [], `${theme}: nothing is sent while dragging`);
    await page.mouse.move(geometry.stops[4] + 40, rampY, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(100);
    const dropped = await read();
    assert.deepEqual(dropped.calls, ["xhigh"], `${theme}: letting go past the end chooses the last level, once`);
    assert.ok(!dropped.dragging && !dropped.held, `${theme}: let go`);
    // Out and back to where it was: nothing to tell the Host.
    await page.evaluate(() => { window.__reasoningCalls.length = 0; });
    await page.mouse.move(geometry.stops[4], rampY);
    await page.mouse.down();
    await page.mouse.move(geometry.stops[4] - 80, rampY, { steps: 6 });
    await page.mouse.move(geometry.stops[4], rampY, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(100);
    assert.deepEqual(await read().then((state) => [state.checked, state.calls]), [4, []], `${theme}: dragging out and back changes nothing`);
    // A press on the scale and on a word each choose once (the click that follows a captured press chooses nothing more).
    await page.mouse.click(geometry.stops[2], rampY - 8);
    await page.waitForTimeout(100);
    await page.mouse.click(geometry.stops[1], geometry.labelY);
    await page.waitForTimeout(100);
    const clicked = await read();
    assert.deepEqual([clicked.checked, clicked.calls], [1, ["medium", "low"]], `${theme}: a press on the scale and on a word choose one level each`);
    // The keys: arrows, Home and End, one call each; Enter on the chosen level says nothing.
    await page.evaluate(() => { window.__reasoningCalls.length = 0; document.querySelector('.cp-step[aria-checked="true"]').focus(); });
    // (The Host answers between presses, so Enter finds the level already chosen.)
    for (const key of ["ArrowRight", "End", "Home", "Enter"]) {
      await page.keyboard.press(key);
      await page.waitForTimeout(80);
    }
    assert.deepEqual((await read()).calls, ["medium", "xhigh", "minimal"], `${theme}: the keys choose level by level (Enter on the chosen one sends nothing)`);
    // A press that is cancelled gives the needle back.
    await page.evaluate(() => { window.__reasoningCalls.length = 0; });
    await page.mouse.move(geometry.stops[3], rampY);
    await page.mouse.down();
    const pressed = await read();
    await page.evaluate(() => document.querySelector(".cp-steps").dispatchEvent(new PointerEvent("pointercancel", { pointerId: 1, bubbles: true })));
    await page.waitForTimeout(60);
    const cancelled = await read();
    assert.deepEqual([pressed.checked, cancelled.checked, cancelled.calls, cancelled.held], [3, 0, [], false], `${theme}: cancelling the press puts the needle back`);
    await page.mouse.up();
    await page.keyboard.press("Escape");
    await page.locator(".cp").waitFor({ state: "detached" });

    // From the keyboard: the panel takes the focus to its current row and the arrows walk the rows.
    await page.locator(".permission-chip").focus();
    await page.keyboard.press("Enter");
    await page.locator('.cp[data-composer-panel="permission"]').waitFor();
    await page.waitForTimeout(80);
    const walk = await page.evaluate(() => document.activeElement?.textContent ?? "");
    assert.match(walk, /^Full access/, `${theme}: the current row has the focus (${walk})`);
    await page.keyboard.press("ArrowUp");
    assert.match(await page.evaluate(() => document.activeElement?.textContent ?? ""), /^Workspace/, `${theme}: ArrowUp walks up`);
    await page.keyboard.press("Home");
    assert.match(await page.evaluate(() => document.activeElement?.textContent ?? ""), /^Read only/, `${theme}: Home goes to the first row`);
    await page.keyboard.press("Escape");
    await page.locator(".cp").waitFor({ state: "detached" });
    assert.ok(await page.evaluate(() => document.activeElement?.classList.contains("permission-chip")), `${theme}: the focus is back on the chip`);

    assert.deepEqual(errors, [], `${theme}: page errors`);
    console.log(JSON.stringify({ theme, material: looks[0].material, head: looks[0].head, inset: looks[0].inset, gaps: looks.map((look) => `${look.id}:${look.gap}`) }));
    await page.close();
  }
  // The reasoning scale at other lengths, in Chinese, and locked while a turn runs (dark only: the look does not change).
  const modelCard = async (query) => {
    const page = await browser.newPage({ viewport: { width: 1333, height: 853 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=dark&models=rich&${query}`);
    await page.locator(".composer textarea").first().waitFor();
    await page.waitForTimeout(600);
    return { page, errors };
  };
  // A tick every ~7px whatever the number of levels, a longer one on each level, both layers the same, each level's tick
  // under its word (the first and the last at the ends of the scale), and the needle on the chosen level.
  for (const { ladder, levels, ticks, dense } of [
    { ladder: "2", levels: 2, ticks: 25, dense: false },
    { ladder: "3", levels: 3, ticks: 35, dense: false },
    { ladder: "5", levels: 5, ticks: 41, dense: false },
    { ladder: "6", levels: 6, ticks: 41, dense: true },
    { ladder: "8", levels: 8, ticks: 43, dense: true },
  ]) {
    const { page, errors } = await modelCard(`lang=en&ladder=${ladder}`);
    await page.locator(".model-chip").first().click();
    await page.locator(".cp-reasoning .cp-step").first().waitFor();
    await page.waitForTimeout(500);
    const drawn = await page.evaluate(() => {
      const center = (el) => { const box = el.getBoundingClientRect(); return box.left + box.width / 2; };
      const base = [...document.querySelectorAll(".cp-scale > .cp-ticks > span")];
      const lit = [...document.querySelectorAll(".cp-lit .cp-ticks > span")];
      const majors = base.filter((tick) => tick.classList.contains("is-major"));
      const words = [...document.querySelectorAll(".cp-step")];
      const needle = document.querySelector(".cp-needle").getBoundingClientRect();
      return {
        base: base.length, lit: lit.length, majors: majors.length, steps: words.length,
        dense: document.querySelector(".cp-steps").classList.contains("is-dense"),
        offsets: majors.map((tick, index) => Math.abs(center(tick) - center(words[index]))),
        needleOnFirst: Math.abs(needle.left + needle.width / 2 - center(words[0])),
        taller: majors.every((tick) => Number.parseFloat(tick.style.height) > Number.parseFloat(tick.previousElementSibling?.style.height || "0") - 0.1),
      };
    });
    assert.deepEqual({ base: drawn.base, lit: drawn.lit, majors: drawn.majors, steps: drawn.steps, dense: drawn.dense }, { base: ticks, lit: ticks, majors: levels, steps: levels, dense }, `ladder ${ladder}: ticks, levels and density`);
    assert.ok(drawn.offsets.every((offset) => offset < 1), `ladder ${ladder}: each level's tick is under its word (${drawn.offsets.map((offset) => offset.toFixed(2))})`);
    assert.ok(drawn.needleOnFirst < 1.5, `ladder ${ladder}: the needle starts on the first level (${drawn.needleOnFirst})`);
    assert.ok(drawn.taller, `ladder ${ladder}: a level's tick is longer than the one before it`);
    assert.deepEqual(errors, [], `ladder ${ladder}: page errors`);
    await page.close();
  }
  {
    // A model that decides for itself: the same ticks, nothing lit, no needle, nothing to choose.
    const { page, errors } = await modelCard("lang=en&ladder=0");
    await page.locator(".model-chip").first().click();
    await page.locator(".cp-reasoning").first().waitFor();
    await page.waitForTimeout(400);
    const automatic = await page.evaluate(() => ({
      ticks: document.querySelectorAll(".cp-auto .cp-ticks > span").length, needle: document.querySelectorAll(".cp-needle, .cp-lit").length,
      choices: document.querySelectorAll(".cp-step").length, text: document.querySelector(".cp-auto")?.textContent, caption: document.querySelector(".cp-reasoning .cp-caption")?.textContent,
    }));
    assert.deepEqual(automatic, { ticks: 41, needle: 0, choices: 0, text: "Automatic", caption: "This model decides how deeply to reason." }, "automatic reasoning is the scale at rest");
    assert.deepEqual(errors, [], "automatic: page errors");
    await page.close();
  }
  {
    // The Chinese interface names the levels and says what they do in Chinese.
    const { page, errors } = await modelCard("effort=medium");
    await page.locator(".model-chip").first().click();
    await page.locator(".cp-reasoning .cp-step").first().waitFor();
    await page.waitForTimeout(400);
    const chinese = await page.evaluate(() => ({
      title: document.querySelector(".cp-reasoning .cp-label")?.textContent, words: [...document.querySelectorAll(".cp-step")].map((stop) => stop.textContent),
      caption: document.querySelector(".cp-reasoning .cp-caption")?.textContent, group: document.querySelector('.cp-steps').getAttribute("aria-label"),
    }));
    assert.deepEqual(chinese.words, ["最少", "低", "中", "高", "超高"], "the levels are named in Chinese");
    assert.match(chinese.title ?? "", /^推理/, "the section is titled in Chinese");
    assert.match(chinese.caption ?? "", /延迟与推理深度之间取得平衡/, "what the chosen level does is said in Chinese");
    assert.equal(chinese.group, "推理强度");
    assert.deepEqual(errors, [], "chinese: page errors");
    await page.close();
  }
  {
    // While a turn runs the level cannot change: the scale dims, says why, and a press on it does nothing.
    const { page, errors } = await modelCard("lang=en&effort=medium&autosend=1&speed=0.15");
    await page.locator(".composer-toolbar-steering .model-chip").waitFor({ timeout: 20000 });
    await page.locator(".composer-toolbar-steering .model-chip").click();
    await page.locator(".cp-reasoning.is-locked").waitFor();
    await page.mouse.move(40, 40);
    await page.waitForTimeout(500);
    const locked = await page.evaluate(() => ({
      opacity: getComputedStyle(document.querySelector(".cp-steps")).opacity, caption: document.querySelector(".cp-reasoning .cp-caption")?.textContent,
      disabled: [...document.querySelectorAll(".cp-step")].map((stop) => stop.getAttribute("aria-disabled")),
    }));
    assert.equal(locked.opacity, "0.5", "the locked scale is dimmed");
    assert.equal(locked.caption, "Stop the active turn to change reasoning.");
    assert.deepEqual(locked.disabled, ["true", "true", "true", "true", "true"], "no level can be chosen");
    const last = await page.locator(".cp-step").nth(4).boundingBox();
    await page.mouse.click(last.x + last.width / 2, last.y - 14);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => [...document.querySelectorAll(".cp-step")].findIndex((stop) => stop.getAttribute("aria-checked") === "true")), 2, "a press on a locked scale chooses nothing");
    assert.deepEqual(errors, [], "locked: page errors");
    await page.close();
  }
  console.log("PASS: the five composer panels are one family in both themes (material, head, entrance, exit, focus, one at a time, keyboard) and the reasoning scale (a needle on the spring with the lit ticks on it, press, drag, keys, hover, cancel, other lengths, Chinese, locked)");
} finally {
  await browser.close();
}
