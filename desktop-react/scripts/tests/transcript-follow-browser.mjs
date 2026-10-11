import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = process.env.LOOM_TEST_START_SERVER
  ? await (await import("vite")).createServer({ server: { port: 0 } }) : null;
await server?.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const origin = process.env.LOOM_TEST_ORIGIN || server?.resolvedUrls.local[0] || "http://127.0.0.1:5198/";
  await page.goto(`${origin.replace(/\/$/, "")}/scripts/fixtures/long-conversation.html?motion=1&sections=1`, { waitUntil: "domcontentloaded" });
  const scroller = page.locator(".transcript-scroll");
  const bottom = () => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  await page.waitForFunction(() => {
    const el = document.querySelector(".transcript-scroll");
    return el && el.scrollHeight - el.scrollTop - el.clientHeight < 3;
  });
  // Navigation in a sibling pane must not change transcript follow intent.
  await page.evaluate(() => {
    const sibling = document.createElement("button");
    document.body.append(sibling);
    sibling.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
    sibling.remove();
  });
  assert.equal(await scroller.getAttribute("data-following"), "true");
  await page.evaluate(() => window.longConversation.start());
  await page.waitForTimeout(500);
  // A large asynchronous layout change must sync within a few frames even
  // while the live answer is running (images/tool output can grow this way).
  await page.evaluate(async () => {
    const probe = document.createElement("div");
    probe.id = "follow-probe";
    probe.style.height = "700px";
    document.querySelector(".transcript").append(probe);
    for (let i = 0; i < 4; i++) await new Promise(requestAnimationFrame);
  });
  assert.ok(await bottom() < 3, `live output lagged ${await bottom()}px`);
  // Upward intent pauses follow even within the near-bottom threshold, and
  // always exposes a way to resume instead of silently hiding the control.
  await scroller.evaluate(el => {
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: -40, bubbles: true }));
    el.scrollTop -= 40;
  });
  await page.waitForTimeout(50);
  assert.equal(await scroller.getAttribute("data-following"), "false");
  assert.ok(await page.locator(".transcript-jump-latest").evaluate(el => el.classList.contains("is-visible")));
  const detachedTop = await scroller.evaluate(el => el.scrollTop);
  await page.evaluate(() => document.querySelector("#follow-probe").style.height = "900px");
  await page.waitForTimeout(100);
  assert.ok(Math.abs(await scroller.evaluate(el => el.scrollTop) - detachedTop) < 3);
  // At the physical bottom a downward gesture must resume even if it cannot
  // produce another scroll event.
  await scroller.evaluate(el => el.scrollTop = el.scrollHeight);
  await page.waitForTimeout(50);
  await scroller.evaluate(el => el.dispatchEvent(new WheelEvent("wheel", { deltaY: 40, bubbles: true })));
  assert.equal(await scroller.getAttribute("data-following"), "true");
  await page.evaluate(() => window.longConversation.finish());
  await page.waitForTimeout(1000);
  assert.ok(await bottom() < 3);
  assert.deepEqual(errors, []);
  console.log("PASS: live layout sync, scoped keyboard intent, visible resume control, detached reading and bottom return");
} finally {
  await browser.close();
  await server?.close();
}
