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

    // The reasoning knob is a mark: it travels to the chosen stop on the settle spring (flow).
    const knob = await page.evaluate(async () => {
      const stops = [...document.querySelectorAll(".cp-step")];
      stops[3].click();
      await new Promise(requestAnimationFrame);
      const layer = document.querySelector(".cp-steps-knob > i");
      const motion = layer.getAnimations().map((animation) => ({ property: animation.transitionProperty, duration: animation.effect.getTiming().duration, easing: String(animation.effect.getTiming().easing) }));
      return { motion, checked: stops.map((stop) => stop.getAttribute("aria-checked")) };
    });
    assert.deepEqual(knob.checked, ["false", "false", "false", "true", "false"], `${theme}: the fourth level is chosen`);
    assert.equal(knob.motion.length, 1, `${theme}: the knob travels`);
    assert.equal(knob.motion[0].property, "transform");
    assert.equal(knob.motion[0].duration, 320, `${theme}: on the flow step`);
    assert.match(knob.motion[0].easing, /^linear\(/, `${theme}: on the spring`);
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
  console.log("PASS: the five composer panels are one family in both themes (material, head, entrance, exit, focus, one at a time, keyboard, the knob)");
} finally {
  await browser.close();
}
