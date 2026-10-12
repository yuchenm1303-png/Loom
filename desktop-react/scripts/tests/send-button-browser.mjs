// The send button on the real composer (the live-app fixture: the production stylesheets over a scripted host):
// a disc with a paper plane, where the plane rests in each state, what plays when a message is sent, and reduced motion.
//   LOOM_PLAYWRIGHT_MODULE=... LOOM_CHROMIUM_PATH=... LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/send-button-browser.mjs
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";

async function open({ theme = "light", appReduced = false, osReduced = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 860 }, deviceScaleFactor: 1.5, reducedMotion: osReduced ? "reduce" : "no-preference" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?theme=${theme}&lang=en&empty=1${appReduced ? "&reduced=1" : ""}`);
  await page.waitForSelector(".app-shell .composer .send-button", { timeout: 90000 });
  await page.waitForTimeout(1200);
  return { context, page, errors };
}

/** The first send button on the page: its shape, its glyph and where the plane rests (CSS px from the disc's centre). */
const read = (page, selector = ".composer .send-button") => page.evaluate((selector) => {
  const button = document.querySelector(selector);
  const svg = button.querySelector("svg");
  const box = button.getBoundingClientRect();
  const [x, y] = getComputedStyle(svg).translate.split(" ").map(parseFloat);
  const lean = new DOMMatrix(getComputedStyle(svg.querySelector(".li-all")).transform);
  return { icon: svg.dataset.icon, width: box.width, height: box.height, radius: getComputedStyle(button).borderRadius,
    fill: getComputedStyle(button).backgroundColor, disabled: button.disabled, x, y, leanX: lean.e, leanY: lean.f };
}, selector);

/** Names of the icon animations the moment the outgoing composer appears: what the swap plays, and on which button. */
function watchSwap(page) {
  return page.evaluate(() => {
    window.__swap = null;
    new MutationObserver((_, observer) => {
      if (window.__swap || !document.querySelector(".composer-mode-crossfade > .crossfade-out .send-button")) return;
      window.__swap = document.getAnimations().filter((animation) => animation.animationName?.startsWith("li-")).map((animation) => {
        const svg = animation.effect.target;
        const layer = svg.closest(".composer-mode-crossfade > *")?.classList.contains("crossfade-out") ? "out" : "in";
        return `${animation.animationName}:${svg.closest(".send-button").classList.contains("stop") ? "stop" : "send"}:${layer}`;
      });
      window.__swapOverflow = getComputedStyle(document.querySelector(".composer-mode-crossfade > .crossfade-out .send-button")).overflow;
      observer.disconnect();
    }).observe(document.body, { childList: true, subtree: true });
  });
}
const swapped = async (page) => { await page.waitForFunction(() => window.__swap, null, { timeout: 5000 }); return page.evaluate(() => window.__swap); };

const centre = async (page, selector) => { const box = await page.locator(selector).first().boundingBox(); return { x: box.x + box.width / 2, y: box.y + box.height / 2 }; };
const near = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 0.05, `${message}: ${actual} is not ${expected}`);

try {
  // 1. A disc with a paper plane, in both themes. Idle, the plane is centred by its weight; with something to send it
  //    has moved one stroke width along its heading; pointed at, it leans further; deleted, it goes back.
  for (const theme of ["light", "dark"]) {
    const { context, page, errors } = await open({ theme });
    const idle = await read(page);
    assert.equal(idle.icon, "send", `${theme}: the send button holds a paper plane`);
    assert.equal(idle.width, idle.height);
    assert.equal(idle.radius, "50%", `${theme}: the send button is a disc`);
    assert.equal(idle.disabled, true);
    near(idle.x, -1.3, `${theme}: idle plane x`); near(idle.y, 1.3, `${theme}: idle plane y`);

    await page.locator(".composer textarea").first().click();
    await page.keyboard.type("hello");
    await page.mouse.move(2, 2);
    await page.waitForTimeout(1000);
    const ready = await read(page);
    assert.equal(ready.disabled, false);
    assert.notEqual(ready.fill, idle.fill, `${theme}: the disc fills once there is something to send`);
    near(ready.x, -0.5, `${theme}: armed plane x`); near(ready.y, 0.5, `${theme}: armed plane y`);
    near(ready.leanX, 0, `${theme}: not leaning at rest`);

    const at = await centre(page, ".composer .send-button");
    await page.mouse.move(at.x, at.y);
    await page.waitForTimeout(900);
    const pointed = await read(page);
    assert.ok(pointed.leanX > 1 && pointed.leanY < -1, `${theme}: pointed at, the plane leans toward its heading (${pointed.leanX}, ${pointed.leanY})`);

    await page.mouse.move(2, 2);
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.waitForTimeout(1000);
    const again = await read(page);
    assert.equal(again.disabled, true);
    near(again.x, -1.3, `${theme}: the plane goes back when the text is deleted`);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 2. Sending: the composer hands over to the guidance composer. The plane in the send button takes off through the
  //    rim of the disc, a new one lands in the next, the stop square grows. When the run ends nothing takes off.
  {
    const { context, page, errors } = await open();
    await page.locator(".composer textarea").first().click();
    await page.keyboard.type("hello");
    await page.waitForTimeout(900);
    await watchSwap(page);
    await page.keyboard.press("Enter");
    const toSteering = await swapped(page);
    for (const wanted of ["li-takeoff:send:out", "li-land:send:in", "li-grow:stop:in"]) assert.ok(toSteering.includes(wanted), `sending plays ${wanted} (got ${toSteering})`);
    assert.equal(await page.evaluate(() => window.__swapOverflow), "hidden", "the plane leaves through the rim of the disc");

    // The guidance composer has the same disc and plane, idle while its draft is empty.
    await page.waitForSelector(".composer.is-steering .send-button:not(.stop)");
    await page.waitForTimeout(700);
    const guidance = await read(page, ".composer.is-steering .send-button:not(.stop)");
    assert.equal(guidance.icon, "send");
    assert.equal(guidance.radius, "50%");
    assert.equal(guidance.disabled, true);
    assert.equal(await page.locator(".composer.is-steering .send-button.is-idle:not(.stop)").count(), 1, "the guidance send wears the same idle look");

    // 3. Guidance: the button stays, so the plane takes off from it and a new one comes in from behind, every time.
    await page.locator(".composer.is-steering textarea").first().click();
    await page.keyboard.type("more");
    await page.waitForTimeout(900);
    assert.equal(await page.locator(".composer.is-steering .send-button.is-ready:not(.stop)").count(), 1);
    await page.evaluate(() => { window.__plane = document.querySelector(".composer.is-steering .send-button:not(.stop) svg"); });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(60);
    const first = await page.evaluate(() => {
      const svg = document.querySelector(".composer.is-steering .send-button:not(.stop) svg");
      return { fresh: svg !== window.__plane, sent: document.querySelector(".composer.is-steering .send-button:not(.stop)").hasAttribute("data-sent"),
        names: svg.getAnimations().map((animation) => animation.animationName).filter(Boolean) };
    });
    assert.deepEqual(first, { fresh: true, sent: true, names: ["li-relaunch"] }, "a sent guidance relaunches the plane");
    await page.waitForTimeout(900);
    await page.locator(".composer.is-steering textarea").first().click();
    await page.keyboard.type("and this");
    await page.evaluate(() => { window.__plane = document.querySelector(".composer.is-steering .send-button:not(.stop) svg"); });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(60);
    const second = await page.evaluate(() => {
      const svg = document.querySelector(".composer.is-steering .send-button:not(.stop) svg");
      const animation = svg.getAnimations().find((item) => item.animationName === "li-relaunch");
      return { fresh: svg !== window.__plane, restarted: Boolean(animation) && animation.currentTime < 300 };
    });
    assert.deepEqual(second, { fresh: true, restarted: true }, "the next guidance takes off again");

    // Stopping hands back to the idle composer: the stop square shrinks, a plane lands, nothing takes off.
    await watchSwap(page);
    await page.locator(".composer.is-steering .send-button.stop").click();
    const toIdle = await swapped(page);
    assert.ok(toIdle.includes("li-shrink:stop:out") && toIdle.includes("li-land:send:in"), `stopping plays shrink and land (got ${toIdle})`);
    assert.ok(!toIdle.some((name) => name.startsWith("li-takeoff")), `nothing takes off when a run ends (got ${toIdle})`);
    assert.deepEqual(errors, []);
    await context.close();
  }

  // 4. Reduced motion, from the system or from the app's own setting: the plane still takes its poses (that is state),
  //    but nothing takes off, lands, grows or relaunches.
  for (const mode of [{ osReduced: true }, { appReduced: true }]) {
    const { context, page, errors } = await open(mode);
    await page.locator(".composer textarea").first().click();
    await page.keyboard.type("hello");
    await page.waitForTimeout(500);
    const ready = await read(page);
    near(ready.x, -0.5, `${JSON.stringify(mode)}: armed plane x`); near(ready.y, 0.5, `${JSON.stringify(mode)}: armed plane y`);
    await page.keyboard.press("Enter");
    await page.waitForSelector(".composer.is-steering .send-button:not(.stop)");
    await page.locator(".composer.is-steering textarea").first().click();
    await page.keyboard.type("more");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(80);
    const names = await page.evaluate(() => document.getAnimations().map((animation) => animation.animationName).filter((name) => name?.startsWith("li-") && name !== "li-spin"));
    assert.deepEqual(names, [], `${JSON.stringify(mode)}: no flight under reduced motion`);
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log("Send button: disc and plane, poses, takeoff, landing, relaunch and reduced motion passed.");
} finally {
  await browser.close();
}
