// How the quick-tool dock answers: the glider, the name, one tab stop, a reorder by keyboard and by hand, the dots,
// its arrival and departure, and what reduced motion takes away. Real React app over the in-memory Host (live-app
// fixture): no account, file or model request can escape it. `right-tool-dock-browser.mjs` guards the layout and the
// panels beside it; this one guards the behaviour.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const ORDER_KEY = "loom.ui.rightToolDock.order.v1";
const near = (a, b, tolerance, message) => assert.ok(Math.abs(a - b) <= tolerance, `${message}: ${a} vs ${b}`);

async function open(theme, extra = "") {
  const page = await browser.newPage({ viewport: { width: 1333, height: 853 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en&avatar=1&agents=1&changes=1${extra}`);
  await page.locator(".loom-tool-dock").waitFor({ timeout: 60_000 });
  await page.waitForFunction(() => document.querySelector(".loom-tool-rail")?.dataset.motionPhase === "entered");
  await page.waitForTimeout(700);
  return { page, errors };
}

const tools = (page) => page.evaluate(() => [...document.querySelectorAll(".loom-tool-button")].map((b) => ({
  id: b.dataset.toolId, disabled: b.disabled, pressed: b.getAttribute("aria-pressed"), tab: b.tabIndex, title: b.getAttribute("title"),
})));
const order = (page) => page.evaluate(() => [...document.querySelectorAll(".loom-tool-button")].map((b) => b.dataset.toolId));
const box = (page, id) => page.locator(`.loom-tool-button[data-tool-id="${id}"]`).boundingBox();
const centre = async (page, id) => { const b = await box(page, id); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
const glider = (page) => page.evaluate(() => {
  const g = document.querySelector(".loom-tool-glider");
  const r = g.getBoundingClientRect();
  return { on: g.dataset.on, top: r.top, left: r.left, height: r.height, width: r.width };
});
const tip = (page) => page.evaluate(() => {
  const t = document.querySelector(".loom-tool-tip");
  const r = t.getBoundingClientRect();
  return { on: t.dataset.on, text: t.textContent.replace(/\s+/g, " ").trim(), middle: r.top + r.height / 2, right: r.right };
});

try {
  for (const theme of ["dark", "light"]) {
    const { page, errors } = await open(theme);
    const list = await tools(page);
    const enabled = list.filter((tool) => !tool.disabled).map((tool) => tool.id);
    const disabled = list.filter((tool) => tool.disabled).map((tool) => tool.id);
    assert.ok(list.length >= 10, "the dock keeps every tool");

    // --- Semantics: a toolbar with one tab stop, no native title beside the dock's own name -------------------------
    assert.equal(await page.locator(".loom-tool-dock").getAttribute("role"), "toolbar");
    assert.equal(await page.locator(".loom-tool-dock").getAttribute("aria-orientation"), "vertical");
    assert.deepEqual(list.filter((tool) => tool.tab === 0).map((tool) => tool.id), [enabled[0]], "one tab stop: the first tool that can be used");
    assert.ok(list.every((tool) => tool.title === null), "a native title would show a second tooltip over the dock's own");
    assert.equal(await page.locator(".loom-tool-position").count(), list.length);
    assert.equal(await page.locator('.loom-tool-position [data-tool-id], .loom-tool-position[data-tool-id]').count(), list.length, "tool ids live on the buttons only");

    // --- The glider and the name follow the pointer ----------------------------------------------------------------
    assert.equal((await glider(page)).on, "false", "nothing is lit until the pointer is on a tool");
    const first = enabled[1];
    const far = enabled[enabled.length - 2];
    let at = await centre(page, first);
    await page.mouse.move(at.x, at.y, { steps: 4 });
    await page.waitForTimeout(100);
    assert.equal((await tip(page)).on, "false", "the name waits for a pointer that rests");
    assert.equal(await page.evaluate(() => document.querySelector(".loom-tool-glider").getAnimations().filter((a) => a.transitionProperty === "transform").length), 0,
      "the glider appears where the pointer is; it does not slide in from wherever it last was");
    await page.waitForFunction(() => document.querySelector(".loom-tool-tip")?.dataset.on === "true", null, { timeout: 2500 });
    await page.waitForTimeout(450);
    let wash = await glider(page);
    let target = await box(page, first);
    assert.equal(wash.on, "true");
    near(wash.top, target.y, 1, "the glider sits on the tool it was brought to");
    near(wash.left, target.x, 1, "and lines up with its column");
    near(wash.height, target.height, 0.5, "and is as tall as the tool");
    let note = await tip(page);
    assert.match(note.text, /\S/, "the name says what the tool is");
    near(note.middle, target.y + target.height / 2, 1.5, "the name hangs at the tool's middle");
    near(note.right, (await page.locator(".loom-tool-dock").boundingBox()).x, 14, "and just off the dock's edge");
    // Leaving a tool for another: the glider slides (does not blink), the name follows at once.
    at = await centre(page, far);
    await page.mouse.move(at.x, at.y, { steps: 8 });
    await page.waitForTimeout(80);
    assert.equal((await tip(page)).on, "true", "once a name is open the next one follows without a new delay");
    await page.waitForTimeout(500);
    wash = await glider(page);
    target = await box(page, far);
    near(wash.top, target.y, 1, "the glider arrives on the next tool");
    note = await tip(page);
    near(note.middle, target.y + target.height / 2, 1.5, "and so does the name");
    // Out of the dock: both let go.
    await page.mouse.move(40, 400, { steps: 6 });
    await page.waitForTimeout(500);
    assert.equal((await glider(page)).on, "false");
    assert.equal((await tip(page)).on, "false");

    // A tool that cannot be used is not lit, and its name says why.
    if (disabled.length) {
      at = await centre(page, disabled[0]);
      await page.mouse.move(at.x, at.y, { steps: 5 });
      await page.waitForFunction(() => document.querySelector(".loom-tool-tip")?.dataset.on === "true", null, { timeout: 2500 });
      assert.equal((await glider(page)).on, "false", "a disabled tool is not lit");
      assert.ok(await page.locator(".loom-tool-tip-note").count(), "its name carries the reason");
      await page.mouse.move(40, 400, { steps: 4 });
    }

    // --- Dots: a fact, not a number ----------------------------------------------------------------------------------
    const dots = await page.evaluate(() => ({
      agents: document.querySelector('[data-tool-id="agents"] .loom-tool-count')?.dataset.on,
      status: document.querySelector('[data-tool-id="account"] .loom-tool-status')?.dataset.on,
      avatar: !!document.querySelector('[data-tool-id="account"] img.loom-tool-avatar'),
      avatarSize: document.querySelector('[data-tool-id="account"] img.loom-tool-avatar')?.getBoundingClientRect().width,
    }));
    assert.equal(dots.agents, "true", "delegated agents leave a dot on the sub-agent tool");
    assert.equal(dots.status, "true", "a signed-in account shows its dot");
    assert.equal(dots.avatar, true, "the account tool wears the user's picture");
    near(dots.avatarSize, 22, 0.5, "at the size of a dot of ink, not a portrait");

    // --- Keyboard: arrows skip tools that cannot be used, Alt+Arrow moves a tool, and it keeps focus ----------------
    await page.locator(`.loom-tool-button[data-tool-id="${enabled[0]}"]`).focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.toolId), enabled[0]);
    await page.waitForFunction(() => document.querySelector(".loom-tool-glider")?.dataset.on === "true");
    await page.waitForFunction(() => document.querySelector(".loom-tool-tip")?.dataset.on === "true", null, { timeout: 1500 });
    await page.keyboard.press("End");
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.toolId), enabled[enabled.length - 1], "End goes to the last tool that can be used");
    await page.keyboard.press("Home");
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.toolId), enabled[0]);
    await page.keyboard.press("ArrowUp");
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.toolId), enabled[0], "no wrap at the top");
    const before = await order(page);
    for (let step = 0; step < 4; step++) await page.keyboard.press("ArrowDown");
    const walked = await page.evaluate(() => document.activeElement?.dataset?.toolId);
    const expectedWalk = before.filter((id) => !disabled.includes(id))[4];
    assert.equal(walked, expectedWalk, "arrows step over disabled tools");

    await page.locator(`.loom-tool-button[data-tool-id="${enabled[0]}"]`).focus();
    await page.keyboard.press("Alt+ArrowDown");
    await page.waitForTimeout(450);
    const down = await order(page);
    assert.deepEqual(down, (() => { const next = before.slice(); const [moved] = next.splice(0, 1); next.splice(1, 0, moved); return next; })(), "Alt+ArrowDown moves the tool down one place");
    assert.equal(await page.evaluate(() => document.activeElement?.dataset?.toolId), enabled[0], "and it keeps focus");
    assert.match(await page.locator(".loom-tool-sr").textContent(), /position 2 of/, "a screen reader hears where it went");
    assert.equal(JSON.parse(await page.evaluate((key) => localStorage.getItem(key), ORDER_KEY))[1], enabled[0], "the order is saved");
    await page.keyboard.press("Alt+ArrowUp");
    await page.waitForTimeout(450);
    assert.deepEqual(await order(page), before, "Alt+ArrowUp puts it back");
    // Nothing is left animating on a tool that has arrived.
    assert.equal(await page.evaluate(() => document.querySelector(".loom-tool-dock").getAnimations({ subtree: true }).filter((a) => a.playState === "running" && !(a.effect?.target?.closest?.(".loom-tool-glider, .loom-tool-tip"))).length), 0);

    // --- By hand: pick a tool up, carry it two places down, put it down ------------------------------------------------
    const carried = enabled[1];
    const start = await order(page);
    const at0 = await centre(page, carried);
    const pitch = (await box(page, start[start.indexOf(carried) + 1])).y - (await box(page, carried)).y;
    await page.mouse.move(at0.x, at0.y);
    await page.waitForTimeout(100);
    await page.mouse.down();
    for (let step = 1; step <= 10; step++) { await page.mouse.move(at0.x + 1, at0.y + (pitch * 2 * step) / 10); await page.waitForTimeout(20); }
    await page.waitForTimeout(60);
    const carry = await page.evaluate(() => ({
      dragging: document.querySelector(".loom-tool-dock").dataset.dragging,
      lifted: document.querySelector(".loom-tool-position.is-lifted")?.dataset.slotId,
      glider: document.querySelector(".loom-tool-glider").dataset.on,
      shifted: [...document.querySelectorAll(".loom-tool-position")].filter((n) => n.style.getPropertyValue("--shift") && n.style.getPropertyValue("--shift") !== "0px").length,
    }));
    assert.equal(carry.dragging, "true");
    assert.equal(carry.lifted, carried, "the tool under the pointer is the one lifted");
    assert.equal(carry.shifted, 2, "the two tools it passes make room");
    assert.equal((await tip(page)).on, "false", "no name over a tool in hand");
    await page.mouse.up();
    await page.waitForFunction(() => !document.querySelector(".loom-tool-dock").dataset.dragging, null, { timeout: 3000 });
    const landed = await order(page);
    assert.deepEqual(landed, (() => { const next = start.slice(); const [moved] = next.splice(start.indexOf(carried), 1); next.splice(start.indexOf(carried) + 2, 0, moved); return next; })(), "it lands two places down");
    assert.deepEqual((await tools(page)).filter((tool) => tool.pressed === "true"), [], "putting a tool down is not a click on it");
    assert.deepEqual(await page.evaluate(() => ({
      styled: [...document.querySelectorAll(".loom-tool-position")].filter((n) => /--shift|--drag-y/.test(n.getAttribute("style") || "")).length,
      lifted: document.querySelectorAll(".is-lifted").length,
    })), { styled: 0, lifted: 0 }, "nothing of the carry is left behind");
    assert.equal(JSON.parse(await page.evaluate((key) => localStorage.getItem(key), ORDER_KEY)).join(), landed.join(), "and the new order is saved");

    // Escape puts a tool in hand back where it was, and the release that follows is not a click either.
    const second = landed[landed.indexOf(carried) + 1];
    const enabledSecond = enabled.includes(second) ? second : carried;
    const at1 = await centre(page, enabledSecond);
    await page.mouse.move(at1.x, at1.y);
    await page.mouse.down();
    for (let step = 1; step <= 8; step++) { await page.mouse.move(at1.x, at1.y - (pitch * 2 * step) / 8); await page.waitForTimeout(20); }
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector(".loom-tool-dock").dataset.dragging, null, { timeout: 3000 });
    await page.mouse.up();
    await page.waitForTimeout(100);
    assert.deepEqual(await order(page), landed, "Escape leaves the order as it was");
    assert.deepEqual((await tools(page)).filter((tool) => tool.pressed === "true"), [], "and so does letting go afterwards");

    // A press that barely moves is a click.
    const toggle = enabled.find((id) => ["agents", "review", "artifacts", "inspector"].includes(id));
    if (toggle) {
      const press = await centre(page, toggle);
      await page.mouse.move(press.x, press.y);
      await page.mouse.down();
      await page.mouse.move(press.x, press.y + 2);
      await page.mouse.up();
      await page.waitForFunction((id) => document.querySelector(`.loom-tool-button[data-tool-id="${id}"]`)?.getAttribute("aria-pressed") === "true", toggle, { timeout: 5000 });
      await page.mouse.move(40, 400, { steps: 3 });
      await page.locator(`.loom-tool-button[data-tool-id="${toggle}"]`).evaluate((el) => el.click());
      await page.waitForFunction((id) => document.querySelector(`.loom-tool-button[data-tool-id="${id}"]`)?.getAttribute("aria-pressed") === "false", toggle, { timeout: 5000 });
    }

    // --- A window too short for the dock scrolls it, and the light and the name still find the tool -------------------
    await page.setViewportSize({ width: 1333, height: 420 });
    await page.waitForTimeout(300);
    await page.locator(".loom-tool-dock").evaluate((dock) => { dock.scrollTop = dock.scrollHeight; });
    const lastTool = (await order(page)).at(-1);
    const lastAt = await centre(page, lastTool);
    await page.mouse.move(lastAt.x, lastAt.y, { steps: 5 });
    await page.waitForFunction(() => document.querySelector(".loom-tool-tip")?.dataset.on === "true", null, { timeout: 2500 });
    await page.waitForTimeout(450);
    const shortBox = await box(page, lastTool);
    near((await glider(page)).top, shortBox.y, 1.5, "in a short window the glider finds the tool after the dock scrolled");
    near((await tip(page)).middle, shortBox.y + shortBox.height / 2, 2, "and so does the name");
    await page.setViewportSize({ width: 1333, height: 853 });
    await page.mouse.move(40, 400, { steps: 3 });

    // --- Arriving and leaving: settings covers the app, the dock fades with it and comes back with it ----------------
    await page.locator('.loom-tool-button[data-tool-id="settings"]').evaluate((el) => el.click());
    await page.waitForFunction(() => document.querySelector(".loom-tool-rail")?.dataset.motionPhase === "exiting" || !document.querySelector(".loom-tool-rail"), null, { timeout: 2000 });
    await page.waitForFunction(() => !document.querySelector(".loom-tool-rail"), null, { timeout: 2000 });
    await page.locator(".settings-back").click();
    await page.waitForFunction(() => document.querySelector(".loom-tool-rail")?.dataset.motionPhase === "entering"
      || document.querySelector(".loom-tool-rail")?.dataset.motionPhase === "entered", null, { timeout: 3000 });
    await page.waitForFunction(() => document.querySelector(".loom-tool-rail")?.dataset.motionPhase === "entered", null, { timeout: 3000 });
    assert.deepEqual(errors, [], `${theme} dock caused runtime errors`);
    await page.close();
  }

  // --- Reduced motion: it all still works, and nothing glides ----------------------------------------------------------
  {
    const { page, errors } = await open("dark", "&reduced=1");
    const motion = await page.evaluate(() => {
      const durations = (selector) => getComputedStyle(document.querySelector(selector)).transitionDuration.split(",").map((value) => value.trim());
      return {
        glider: durations(".loom-tool-glider"), position: durations(".loom-tool-position"), button: durations(".loom-tool-button"), tip: durations(".loom-tool-tip"),
      };
    });
    for (const [name, values] of Object.entries(motion)) assert.ok(values.every((value) => value === "0s"), `${name} still transitions under reduced motion: ${values}`);
    const list = await tools(page);
    const ids = list.filter((tool) => !tool.disabled).map((tool) => tool.id);
    await page.locator(`.loom-tool-button[data-tool-id="${ids[0]}"]`).focus();
    await page.keyboard.press("Alt+ArrowDown");
    await page.waitForTimeout(60);
    assert.equal(await page.evaluate(() => document.querySelector(".loom-tool-dock").getAnimations({ subtree: true }).length), 0, "no reorder animation under reduced motion");
    assert.equal((await order(page))[1], ids[0], "and the reorder still happens");
    const at = await centre(page, ids[2]);
    await page.mouse.move(at.x, at.y, { steps: 3 });
    await page.waitForFunction(() => document.querySelector(".loom-tool-tip")?.dataset.on === "true", null, { timeout: 2500 });
    assert.equal((await glider(page)).on, "true", "the glider still shows where the pointer is");
    assert.deepEqual(errors, [], "reduced motion caused runtime errors");
    await page.close();
  }
  console.log("PASS: dock glider, name, tab stop, keyboard and pointer reorder, dots, arrival/departure and reduced motion");
} finally {
  await browser.close();
}
