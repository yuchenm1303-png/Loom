// A sent message flies from the field into the conversation (src/sendFlight.ts, components/send-flight.css), on the real
// App over the live-app fixture's scripted host (which answers a send after `latency` ms, as a real Host does).
//   LOOM_PLAYWRIGHT_MODULE=... LOOM_CHROMIUM_PATH=... LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/send-flight-browser.mjs
//
// What is held to: the turn is live from the click (not from the Host's first word); the text leaves from exactly where
// the field drew it; the real entry is held back and the capsule's row is already there; the message lands where the
// entry is with nothing to correct, and then everything is as if it had never flown; the home screen goes out under
// it instead of vanishing; guidance flies too; reduced motion has none of it; a message that cannot fly says why.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
// LOOM_TEST_ONLY=1,3 runs only those scenarios (for checking one thing quickly).
const only = process.env.LOOM_TEST_ONLY ? new Set(process.env.LOOM_TEST_ONLY.split(",").map(Number)) : null;
const run = (n) => !only || only.has(n);

async function open({ theme = "dark", empty = false, osReduced = false, appReduced = false, height = 860, latency = 1200, scenario = "" } = {}) {
  const context = await browser.newContext({ viewport: { width: 1360, height }, deviceScaleFactor: 1.5, reducedMotion: osReduced ? "reduce" : "no-preference" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    window.__flight = [];
    window.addEventListener("loom:send-flight", (event) => window.__flight.push({ at: performance.now(), ...event.detail }));
  });
  const query = `theme=${theme}&lang=en&latency=${latency}${empty ? "&empty=1" : ""}${appReduced ? "&reduced=1" : ""}${scenario ? `&scenario=${scenario}` : ""}`;
  await page.goto(`${origin}/scripts/fixtures/live-app.html?${query}`);
  await page.waitForSelector(".app-shell .composer textarea:not([disabled])", { timeout: 90000 });
  await page.waitForTimeout(1200);
  return { context, page, errors };
}

const events = (page) => page.evaluate(() => window.__flight.map(({ at, ...rest }) => rest));
const phases = async (page) => (await events(page)).map((event) => event.phase);
const flight = (page) => page.waitForFunction(() => window.__flight.some((event) => event.phase === "launch" || event.phase === "skip"), null, { timeout: 5000 });
const landed = (page) => page.waitForFunction(() => window.__flight.some((event) => event.phase === "land"), null, { timeout: 8000 });

/** Hold the flight's long timers (its safety net) while a film of it is taken, and freeze its animations. */
async function freeze(page) {
  return page.evaluate(() => {
    const mine = document.getAnimations().filter((animation) => animation.effect?.target?.closest?.(".send-flight-layer"));
    mine.forEach((animation) => animation.pause());
    window.__held = mine;
    return mine.length;
  });
}
const seek = (page, time) => page.evaluate((time) => window.__held.forEach((animation) => { animation.currentTime = time; }), time);

/** The capsule's parts now: where the text is, the skin's box and the opacity of every layer of the shell. */
const capsuleParts = (page) => page.evaluate(() => {
  const ghost = document.querySelector(".send-flight-ghost");
  const range = document.createRange();
  range.selectNodeContents(ghost.querySelector(".send-flight-text .user-message"));
  const text = range.getBoundingClientRect();
  const part = (selector) => {
    const node = ghost.querySelector(selector);
    const rect = node.getBoundingClientRect();
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, opacity: Number(getComputedStyle(node).opacity) };
  };
  return {
    text: { left: text.left, right: text.right, top: text.top, bottom: text.bottom },
    skin: part(".send-flight-skin"),
    card: part(".send-flight-card"),
    halo: part(".send-flight-halo"),
    // The capsule is never tilted or scaled as a whole: it only goes along its way.
    rotate: getComputedStyle(ghost).rotate,
    scale: getComputedStyle(ghost).scale,
  };
});

/** Where a glyph of the message is drawn in the layer: first or last character of the ghost's text. */
const ghostGlyph = (page, which = "first") => page.evaluate((which) => {
  const root = document.querySelector(".send-flight-text .user-message");
  if (!root) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  if (which === "last") for (let next = node; next; next = walker.nextNode()) node = next;
  const range = document.createRange();
  const text = node.nodeValue;
  const at = which === "first" ? text.search(/\S/) : text.trimEnd().length - 1;
  range.setStart(node, at);
  range.setEnd(node, at + 1);
  const box = range.getBoundingClientRect();
  return { left: box.left, centre: box.top + box.height / 2 };
}, which);

try {
  // 1. A conversation: the message leaves the field and lands on its entry.
  if (run(1)) {
    const { context, page, errors } = await open();
    const typed = "Add email format validation to the login page and cover it with a unit test.";
    await page.locator(".composer textarea").click();
    await page.keyboard.type(typed);
    await page.waitForTimeout(500);
    const field = await page.evaluate(() => {
      const area = document.querySelector(".composer textarea");
      const rect = area.getBoundingClientRect();
      const style = getComputedStyle(area);
      return { left: rect.left + parseFloat(style.paddingLeft), centre: rect.top + parseFloat(style.paddingTop) + parseFloat(style.lineHeight) / 2 };
    });
    await page.evaluate(() => { window.__setTimeout = window.setTimeout; window.setTimeout = (fn, ms, ...rest) => (ms >= 500 && ms < 5000 ? -1 : window.__setTimeout(fn, ms, ...rest)); });
    await page.keyboard.press("Enter");
    await flight(page);
    const first = await page.evaluate(() => {
      const turns = [...document.querySelectorAll(".transcript > .turn-block")];
      const turn = turns.at(-1);
      const entry = turn.querySelector(":scope > .entry-user_message");
      const capsule = turn.querySelector(".pending-thinking-presence");
      return {
        turns: turns.map((item) => item.classList.contains("is-active") ? "active" : "complete"),
        sending: entry.classList.contains("is-sending"), held: entry.dataset.flight, visibility: getComputedStyle(entry).visibility,
        entryAnimations: entry.getAnimations({ subtree: true }).map((animation) => animation.animationName).filter(Boolean),
        capsuleRow: capsule ? capsule.getBoundingClientRect().height : -1, capsuleOpacity: capsule ? Number(getComputedStyle(capsule).opacity) : -1,
        ghosts: document.querySelectorAll(".send-flight-ghost").length,
        // The capsule: a shell (the shadow, the skin, the bubble's own fill) and the text.
        layers: [...document.querySelectorAll(".send-flight-ghost > *")].map((layer) => layer.className.replace(/ ?turn-block is-active ?/, "")),
        shell: [...document.querySelectorAll(".send-flight-ghost > .send-flight-shell > *")].map((part) => part.className.replace(/ ?turn-block is-active ?/, "")),
        oldText: getComputedStyle(document.querySelector(".composer-mode-crossfade > .crossfade-out .composer textarea")).webkitTextFillColor,
      };
    });
    assert.deepEqual(first.turns, ["complete", "active"], "the turn just sent is live from the click and the one before it is not");
    assert.equal(first.ghosts, 1);
    assert.equal(first.held, "flying", "the real entry is held back while its message is in the air");
    assert.equal(first.visibility, "hidden");
    assert.equal(first.sending, false, "the flight owns the entrance: no second one on the entry");
    assert.deepEqual(first.entryAnimations, [], "nothing animates on the held entry");
    assert.ok(first.capsuleRow > 12, `the capsule's row is already there (${first.capsuleRow})`);
    assert.equal(first.capsuleOpacity, 0, "the capsule waits for the message");
    assert.deepEqual(first.layers, ["send-flight-shell", "send-flight-text"], "the capsule is a shell and the text, in that order (the text on top)");
    assert.deepEqual(first.shell, ["send-flight-halo", "send-flight-skin", "send-flight-card"], "the shell's layers, back to front: the shadow, the skin, the bubble's own fill");
    assert.match(first.oldText, /0, 0, 0, 0|transparent/, "the composer that is going away does not show the text again");

    // At its first moment the text is on the field's text: same left edge, same line.
    assert.ok(await freeze(page) >= 4);
    await seek(page, 0);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const start = await ghostGlyph(page);
    assert.ok(Math.abs(start.left - field.left) < 1.5, `starts on the field's left edge (${start.left} vs ${field.left})`);
    assert.ok(Math.abs(start.centre - field.centre) < 1.5, `starts on the field's line (${start.centre} vs ${field.centre})`);
    const cardOpacity = await page.evaluate(() => Number(getComputedStyle(document.querySelector(".send-flight-card")).opacity));
    assert.equal(cardOpacity, 0, "the card has not come up yet");
    // Halfway the card is coming up and the text is somewhere between the two places.
    await seek(page, 200);
    const middle = await ghostGlyph(page);
    assert.ok(middle.left > start.left + 20 && middle.centre < start.centre - 20, "the text is on its way");

    // The capsule: its shell is around the text the whole way (the text never leaves it), comes up at once, is lifted (it
    // has a shadow) while it is in the air, relaxes into the bubble's own fill as it is set down, and is gone (every part
    // at rest) at the end. It is never tilted or scaled as a whole.
    const launched = (await events(page)).find((event) => event.phase === "launch");
    assert.ok(launched.arrive > 200 && launched.arrive < 420 && launched.duration >= 330 && launched.duration <= 420, `it arrives in a fraction of a second (${launched.arrive} of ${launched.duration} ms)`);
    for (const time of [0, 30, 60, 100, 150, 200, 260, 320, launched.arrive + 20]) {
      await seek(page, time);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const parts = await capsuleParts(page);
      if (time > 0) {
        const { text, skin } = parts;
        // Inside with room to spare (a capsule has padding: the text does not touch its ends), not merely inside.
        assert.ok(skin.left <= text.left - 8 && skin.right >= text.right + 8 && skin.top <= text.top - 3 && skin.bottom >= text.bottom + 3,
          `at ${time} ms the text is inside the capsule's skin: text ${JSON.stringify(text)} skin ${JSON.stringify(skin)}`);
      }
      if (time === 0) assert.equal(parts.skin.opacity, 0, "the skin comes up from nothing, around the text");
      if (time >= 30 && time <= 150) assert.ok(parts.skin.opacity > 0.97, `the skin is there at ${time} ms (${parts.skin.opacity})`);
      if (time <= 100) assert.equal(parts.card.opacity, 0, "the bubble's own fill waits until it is being put down");
      if (time >= 60 && time <= 100) assert.ok(parts.halo.opacity > 0.9, `it is lifted at ${time} ms: its shadow is there (${parts.halo.opacity})`);
      assert.equal(parts.rotate, "none", `it is not tilted at ${time} ms`);
      assert.equal(parts.scale, "none", `and not scaled at ${time} ms`);
      if (time === launched.arrive + 20) assert.ok(parts.card.opacity > 0.98 && parts.skin.opacity < 0.05 && parts.halo.opacity < 0.05, `by arrival it has been put down: ${JSON.stringify([parts.card.opacity, parts.skin.opacity, parts.halo.opacity])}`);
    }
    await seek(page, launched.duration - 2);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const rest = await capsuleParts(page);
    // (Two milliseconds short of the end, a curve may still be a hair off its last value: invisible, and gone at the end.)
    assert.deepEqual([rest.skin, rest.halo].map((layer) => layer.opacity < 0.02), [true, true], "at the end only the bubble's own fill is left");
    assert.ok(rest.card.opacity > 0.999);
    assert.equal(rest.rotate, "none");
    // Its way, measured on the real page (the first glyph, with the flight's own clock sampled at nine moments): Material's
    // arc. Along the field's level first and then up into its place, so the horizontal is always further on than the
    // vertical, every sample is on the quarter circle that the two shares make, and the path bows away from the straight line.
    await seek(page, launched.duration - 0.1);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const end = await ghostGlyph(page);
    const way = Math.hypot(end.left - start.left, end.centre - start.centre);
    assert.ok(way > 300, `a flight with an arc to look at (${way.toFixed(0)} px)`);
    let bow = 0;
    for (const time of [30, 60, 90, 120, 150, 180, 220, 260, 300]) {
      await seek(page, time);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const point = await ghostGlyph(page);
      const sideways = (point.left - start.left) / (end.left - start.left);
      const upwards = (point.centre - start.centre) / (end.centre - start.centre);
      assert.ok(sideways >= upwards - 0.02, `at ${time} ms the horizontal (${sideways.toFixed(2)}) leads the vertical (${upwards.toFixed(2)})`);
      assert.ok(Math.abs(sideways ** 2 + (1 - upwards) ** 2 - 1) < 0.03, `at ${time} ms it is on the quarter circle (${(sideways ** 2 + (1 - upwards) ** 2).toFixed(3)})`);
      bow = Math.max(bow, Math.abs((point.left - start.left) * (end.centre - start.centre) - (point.centre - start.centre) * (end.left - start.left)) / way);
    }
    assert.ok(bow > 0.15 * way && bow < 0.25 * way, `the path bows by ${(bow / way * 100).toFixed(0)}% of its length: a quarter circle bows by a fifth`);
    // The text never scales once it is on its way: the only scale is the type size meeting the field's.
    await page.evaluate(() => { window.setTimeout = window.__setTimeout; window.__held.forEach((animation) => animation.play()); });
    await landed(page);
    const after = await events(page);
    assert.deepEqual(after.map((event) => event.phase), ["launch", "land"], "it lands where the entry is: nothing to correct");
    await page.waitForTimeout(150);
    const done = await page.evaluate(() => {
      const entry = document.querySelector(".transcript > .turn-block:last-child > .entry-user_message");
      const capsule = document.querySelector(".transcript > .turn-block:last-child .pending-thinking-presence");
      return { ghosts: document.querySelectorAll(".send-flight-ghost").length, visibility: getComputedStyle(entry).visibility, landed: entry.dataset.flight,
        capsule: capsule ? Number(getComputedStyle(capsule).opacity) : -1 };
    });
    assert.equal(done.ghosts, 0, "the layer is empty once the message has landed");
    assert.equal(done.visibility, "visible");
    assert.equal(done.landed, "landed");
    await page.waitForFunction(() => !document.querySelector(".transcript > .turn-block:last-child > .entry-user_message").dataset.flight, null, { timeout: 3000 });
    await page.waitForFunction(() => Number(getComputedStyle(document.querySelector(".transcript > .turn-block:last-child .pending-thinking-presence, .transcript > .turn-block:last-child .live-reasoning")).opacity) > 0.9, null, { timeout: 3000 });
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 2. A conversation that scrolls: the message is aimed at where the page will rest, and lands there. The page's own
  //    scroll is still easing for about a hundred milliseconds after the flight is over (the entry is 4 px away from where
  //    it will be at the hand-off, and moving), so the capsule has to follow the entry closely at the end, not the place
  //    where the page will rest: that took a filter that tightens as the capsule comes in, and a single run cannot show
  //    it (one in four needed a correction without it): three do.
  if (run(2)) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const { context, page, errors } = await open({ height: 560 });
      await page.locator(".composer textarea").click();
      await page.keyboard.type("A message in a window that has to scroll to make room");
      await page.waitForTimeout(400);
      await page.keyboard.press("Enter");
      await landed(page);
      assert.deepEqual(await phases(page), ["launch", "land"], `a page that scrolls under the flight does not leave anything to correct (run ${attempt + 1} of 3)`);
      // The page is the size it will end as from the first frame (the capsule's row is reserved, the entry is held in its
      // place): it does not grow a second time under a message that has already landed.
      await page.waitForTimeout(500);
      const launched = (await events(page))[0];
      const height = await page.evaluate(() => document.querySelector(".conversation-stage > .transcript-scroll").scrollHeight);
      assert.ok(Math.abs(height - launched.pageHeight) <= 1, `the page was ${launched.pageHeight} px when the message set off and is ${height} now`);
      assert.deepEqual(errors, []);
      await context.close();
    }
  }

  // 3. The home screen: it goes out under the message instead of vanishing in a frame; the message leaves from the field.
  if (run(3)) {
    const { context, page, errors } = await open({ empty: true });
    await page.locator(".composer textarea").click();
    await page.keyboard.type("Start from the home screen");
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await flight(page);
    const home = await page.evaluate(() => ({
      copy: document.querySelectorAll(".send-flight-layer .send-flight-home").length,
      live: [...document.querySelectorAll(".empty-state")].filter((node) => !node.closest(".send-flight-layer")).length,
      animated: [...document.querySelectorAll(".send-flight-home *")].filter((node) => node.getAnimations().some((animation) => animation.animationName)).length,
      opacity: Number(getComputedStyle(document.querySelector(".send-flight-home")).opacity),
    }));
    assert.equal(home.copy, 1, "the home screen is still there, as a copy, for a moment");
    assert.equal(home.live, 0, "and no longer in the page");
    assert.equal(home.animated, 0, "nothing in the copy plays an entrance again");
    assert.ok(home.opacity > 0.9, `it starts as it was (${home.opacity})`);
    await page.waitForFunction(() => !document.querySelector(".send-flight-home"), null, { timeout: 2000 });
    await landed(page);
    assert.deepEqual(await phases(page), ["launch", "land"]);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 3b. The real home screen, where the conversation does not exist yet: the message waits for thread/start (a Host
  //     needs a second or more for it on a slow route) and is live all the same, it flies at once, and lands long
  //     before the conversation has been created.
  if (run(3)) {
    const { context, page, errors } = await open({ latency: 1500 });
    await page.getByRole("button", { name: /new conversation/i }).first().click();
    await page.waitForSelector(".empty-state");
    await page.locator(".composer textarea").click();
    await page.keyboard.type("A message that starts a conversation");
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await flight(page);
    const live = await page.evaluate(() => {
      const turn = [...document.querySelectorAll(".transcript > .turn-block")].at(-1);
      return { turn: turn?.classList.contains("is-active"), held: turn?.querySelector(":scope > .entry-user_message")?.dataset.flight, home: document.querySelectorAll(".send-flight-home").length };
    });
    assert.deepEqual(live, { turn: true, held: "flying", home: 1 }, "live and flying while thread/start is still out");
    await landed(page);
    assert.deepEqual(await phases(page), ["launch", "land"]);
    await page.waitForTimeout(1700);
    assert.equal(await page.locator(".transcript > .turn-block").count(), 1, "the conversation created behind it does not remount the message");
    assert.equal(await page.locator(".transcript > .turn-block > .entry-user_message").evaluate((entry) => getComputedStyle(entry).visibility), "visible");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 4. A starter card sets the message off from the card; its text comes in on the way.
  if (run(4)) {
    const { context, page, errors } = await open({ empty: true });
    await page.waitForSelector(".starter-card:not([disabled])");
    await page.locator(".starter-card").nth(1).click();
    await flight(page);
    assert.equal((await phases(page))[0], "launch");
    const text = await page.evaluate(() => document.querySelector(".send-flight-text").getAnimations().filter((animation) => animation.effect.getKeyframes().some((frame) => "opacity" in frame)).length);
    assert.ok(text >= 1, "the text comes in as the message sets off");
    await landed(page);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 5. Several lines: the first frame is the field's own text (it is laid out another way in the bubble), which gives
  //    way to the message's layout on the way.
  if (run(5)) {
    const { context, page, errors } = await open();
    await page.locator(".composer textarea").click();
    const lines = ["Please refactor the login form:", "1. move the rules into one module", "2. add tests for the validator"];
    for (let index = 0; index < lines.length; index += 1) { await page.keyboard.type(lines[index]); if (index < lines.length - 1) await page.keyboard.press("Shift+Enter"); }
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await flight(page);
    const field = await page.evaluate(() => { const layer = document.querySelector(".send-flight-field"); return layer ? { text: layer.textContent, lineHeight: parseFloat(getComputedStyle(layer).lineHeight) } : null; });
    assert.ok(field, "the field's text is drawn again for a message that is laid out another way");
    assert.equal(field.text, lines.join("\n"));
    // The capsule is around the text in both of its layouts: the field's own at first, the message's once it has gone
    // over (the skin reshapes meanwhile), and the skin never lags behind the text it holds.
    assert.ok(await freeze(page) >= 6);
    // The field's text is the bottom layer: the skin that comes up round it is a veil it goes out behind.
    const order = await page.evaluate(() => [...document.querySelector(".send-flight-ghost").children].map((node) => node.className.replace(/ ?turn-block is-active ?/, "")));
    assert.deepEqual(order, ["send-flight-field", "send-flight-shell", "send-flight-text"], "the field's text under the shell, the message's text on top");
    const around = (time) => page.evaluate((time) => {
      window.__held.forEach((animation) => { animation.currentTime = time; });
      return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => {
        const ghost = document.querySelector(".send-flight-ghost");
        const rect = (node) => { const range = document.createRange(); range.selectNodeContents(node); const box = range.getBoundingClientRect(); return { left: box.left, right: box.right, top: box.top, bottom: box.bottom }; };
        const skin = ghost.querySelector(".send-flight-skin").getBoundingClientRect();
        resolve({ skin: { left: skin.left, right: skin.right, top: skin.top, bottom: skin.bottom }, field: rect(ghost.querySelector(".send-flight-field")), text: rect(ghost.querySelector(".send-flight-text .user-message")),
          fieldOpacity: Number(getComputedStyle(ghost.querySelector(".send-flight-field")).opacity),
          skinOpacity: Number(getComputedStyle(ghost.querySelector(".send-flight-skin")).opacity),
          textOpacity: Number(getComputedStyle(ghost.querySelector(".send-flight-text")).opacity) });
      })));
    }, time);
    const holds = (skin, text, label) => assert.ok(skin.left <= text.left + 2 && skin.right >= text.right - 2 && skin.top <= text.top + 2 && skin.bottom >= text.bottom - 2, `${label}: ${JSON.stringify(skin)} holds ${JSON.stringify(text)}`);
    const early = await around(0);
    assert.ok(early.fieldOpacity > 0.99 && early.textOpacity < 0.01, "the field's own text is what is shown at first, and only that");
    holds(early.skin, early.field, "at the first moment the capsule is around the field's text");
    // The exchange: behind the skin that comes up, in a few frames, and the two layouts are never both clearly there
    // (what is seen of the old one is what the skin has not covered), nor the text ever nearly gone.
    let both = 0;
    let faintest = 9;
    for (let time = 0; time <= 40; time += 2) {
      const sample = await around(time);
      const old = sample.fieldOpacity * (1 - sample.skinOpacity);
      both = Math.max(both, Math.min(old, sample.textOpacity));
      faintest = Math.min(faintest, old + sample.textOpacity);
    }
    assert.ok(both < 0.36, `the old and the new layout are never both there (${(both * 100).toFixed(0)}% at once)`);
    assert.ok(faintest > 0.5, `and the text is not fainter than ${(faintest * 100).toFixed(0)}% on the way`);
    const swapped = await around(44);
    assert.ok(swapped.fieldOpacity < 0.01 && swapped.textOpacity > 0.99, "the exchange is over in 44 ms");
    const gone = await around(200);
    assert.equal(gone.fieldOpacity, 0, "and the field's layout has given way by 200 ms");
    holds(gone.skin, gone.text, "at 200 ms it is around the message's");
    await page.evaluate(() => { window.__held.forEach((animation) => animation.play()); });
    await landed(page);
    assert.equal(await page.locator(".send-flight-ghost").count(), 0);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 6. Guidance sent while a task runs flies into the live process.
  if (run(6)) {
    const { context, page, errors } = await open({ latency: 0, scenario: "long" });
    await page.locator(".composer textarea").click();
    await page.keyboard.type("Start a long task");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".composer.is-steering textarea", { timeout: 10000 });
    await page.waitForFunction(() => document.querySelector(".composer-mode-crossfade > .crossfade-out") === null, null, { timeout: 3000 });
    await landed(page);
    await page.evaluate(() => { window.__flight.length = 0; });
    await page.locator(".composer.is-steering textarea").click();
    await page.keyboard.type("Also keep the diff small");
    await page.waitForTimeout(400);
    // The field that is emptied shows its placeholder again: not under the text that is leaving it.
    await page.evaluate(() => {
      window.__placeholder = [];
      window.__enterAt = 0;
      document.addEventListener("keydown", (event) => { if (event.key === "Enter") window.__enterAt = performance.now(); }, { capture: true, once: true });
      const started = performance.now();
      const tick = () => {
        const area = document.querySelector(".composer.is-steering textarea");
        if (area) window.__placeholder.push({ at: performance.now(), opacity: Number(getComputedStyle(area, "::placeholder").opacity) });
        if (performance.now() - started < 4000 && !(window.__enterAt && performance.now() - window.__enterAt > 1000)) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    await page.keyboard.press("Enter");
    await flight(page);
    await landed(page);
    assert.deepEqual(await phases(page), ["launch", "land"], "guidance lands where its entry is, in a page that keeps growing");
    await page.waitForFunction(() => window.__enterAt && performance.now() - window.__enterAt > 1000, null, { timeout: 4000 });
    const { placeholder, enterAt } = await page.evaluate(() => ({ placeholder: window.__placeholder, enterAt: window.__enterAt }));
    const early = placeholder.filter((sample) => sample.at > enterAt + 8 && sample.at < enterAt + 150);
    assert.ok(early.length >= 3, `sampled ${early.length} frames while the guidance leaves`);
    assert.ok(early.every((sample) => sample.opacity < 0.05), `the placeholder stays back while the text leaves: ${JSON.stringify(early.map((sample) => sample.opacity))}`);
    assert.ok(placeholder.filter((sample) => sample.at > enterAt + 800).every((sample) => sample.opacity > 0.9), "and it is back by the end");
    assert.equal(await page.locator(".entry-steering-user").count(), 1);
    assert.equal(await page.locator(".entry-steering-user").evaluate((entry) => getComputedStyle(entry).visibility), "visible");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 7. A message too tall for the window does not fly, says why, and is not held back.
  if (run(7)) {
    const { context, page, errors } = await open({ height: 420 });
    await page.locator(".composer textarea").click();
    await page.keyboard.type("A long one. ".repeat(60));
    await page.waitForTimeout(400);
    await page.keyboard.press("Enter");
    await flight(page);
    const result = await events(page);
    assert.equal(result[0].phase, "skip");
    assert.match(result[0].reason, /too tall/);
    assert.equal(await page.locator(".send-flight-ghost").count(), 0);
    assert.equal(await page.locator(".transcript > .turn-block:last-child > .entry-user_message[data-flight]").count(), 0, "a message that does not fly is not held back");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 8. Reduced motion, from the system or from the app's own setting: no flight, no hold, the message is there at once.
  for (const mode of run(8) ? [{ osReduced: true }, { appReduced: true }] : []) {
    const { context, page, errors } = await open({ ...mode, empty: true });
    await page.locator(".composer textarea").click();
    await page.keyboard.type("No motion please");
    await page.waitForTimeout(300);
    await page.keyboard.press("Enter");
    await page.waitForSelector(".turn-block .user-message");
    const state = await page.evaluate(() => {
      const entry = document.querySelector(".turn-block .entry-user_message");
      return { ghosts: document.querySelectorAll(".send-flight-ghost, .send-flight-home").length, held: entry.dataset.flight ?? null,
        visibility: getComputedStyle(entry).visibility, sent: document.querySelector(".composer-mode-crossfade")?.hasAttribute("data-sent") };
    });
    assert.equal(state.ghosts, 0, JSON.stringify(mode));
    assert.equal(state.held, null);
    assert.equal(state.visibility, "visible");
    await page.waitForTimeout(500);
    assert.deepEqual(await events(page), [], "reduced motion makes no flight at all");
    assert.deepEqual(errors, []);
    await context.close();
  }
  // ... and guidance sent into a running task does not fly either.
  for (const mode of run(8) ? [{ osReduced: true }, { appReduced: true }] : []) {
    const { context, page, errors } = await open({ ...mode, latency: 0, scenario: "long" });
    await page.locator(".composer textarea").click();
    await page.keyboard.type("Start a long task");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".composer.is-steering textarea", { timeout: 10000 });
    await page.waitForTimeout(800);
    await page.locator(".composer.is-steering textarea").click();
    await page.keyboard.type("Also keep the diff small");
    await page.waitForTimeout(300);
    await page.keyboard.press("Enter");
    await page.waitForSelector(".entry-steering-user");
    await page.waitForTimeout(500);
    assert.equal(await page.locator(".send-flight-ghost").count(), 0, JSON.stringify(mode));
    assert.deepEqual(await events(page), [], "guidance does not fly under reduced motion");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 9. An entry that goes away mid-flight takes its flight with it: no ghost is left, nothing throws.
  if (run(9)) {
    const { context, page, errors } = await open();
    await page.locator(".composer textarea").click();
    await page.keyboard.type("Gone before it lands");
    await page.waitForTimeout(300);
    await page.keyboard.press("Enter");
    await flight(page);
    await page.evaluate(() => document.querySelector(".transcript > .turn-block:last-child > .entry-user_message")?.remove());
    await page.waitForFunction(() => !document.querySelector(".send-flight-ghost"), null, { timeout: 3000 });
    assert.deepEqual(errors, []);
    await context.close();
  }

  console.log("Send flight: live from the click, exact lift-off, held entry, clean landing, home dissolve, cards, lines, guidance, tall, reduced motion, vanished entry.");
} finally {
  await browser.close();
}
