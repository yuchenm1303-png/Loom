// A run produces a main-thread frame per vsync, and every animation that is still going makes the browser resolve
// its element's style again on each of them, whether or not the animation runs on the compositor (measured: ten
// transform/opacity loops on an idle page cost nothing; the same ten on a page that renders every frame cost a style
// recalculation of ten elements per frame). Eight always-on ornaments in the sidebar mark, two ambient glows and the
// closed inspector's three loops used to take half of the main thread while a long answer streamed.
//
// This runs a live turn, traces the middle of it, and holds the number of animated elements the browser re-resolves
// per frame, and the style time that costs, to a budget. A new infinite animation that is not paused while a run is
// live (html[data-loom-live]) shows up here.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = process.env.LOOM_TEST_START_SERVER
  ? await (await import("vite")).createServer({ server: { port: 0 } }) : null;
await server?.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const origin = (process.env.LOOM_TEST_ORIGIN || server?.resolvedUrls.local[0] || "http://127.0.0.1:5198/").replace(/\/$/, "");
  const page = await browser.newPage({ viewport: { width: 935, height: 731 }, deviceScaleFactor: 1.5 });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=long&cadence=real`);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(800);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.loomLive), undefined, "no run, no live flag");

  const client = await page.context().newCDPSession(page);
  const events = [];
  client.on("Tracing.dataCollected", (chunk) => events.push(...chunk.value));
  await page.evaluate(() => window.liveFixture.send());
  await page.waitForFunction(() => document.documentElement.dataset.loomLive === "true", null, { timeout: 10000 });

  // The answer starts typing after the reasoning pause; trace a stretch of it.
  await page.waitForFunction(() => document.querySelector(".turn-block.is-active .markdown-body.is-receiving"), null, { timeout: 30000 });
  await page.waitForTimeout(1500);
  await client.send("Tracing.start", { traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline.invalidationTracking"] }, transferMode: "ReportEvents" });
  const seconds = 3;
  await page.waitForTimeout(seconds * 1000);
  await client.send("Tracing.end");
  await new Promise((resolve) => client.once("Tracing.tracingComplete", resolve));

  const recalcs = events.filter((event) => event.name === "UpdateLayoutTree" && (event.ph === "X" || event.ph === "B"));
  const styleMs = recalcs.reduce((sum, event) => sum + (event.dur ?? 0), 0) / 1000;
  const animated = new Map();
  for (const event of events) {
    if (event.name !== "StyleRecalcInvalidationTracking" || event.args?.data?.reason !== "Animation") continue;
    const node = String(event.args.data.nodeName ?? "");
    animated.set(node, (animated.get(node) ?? 0) + 1);
  }
  const frames = Math.max(1, recalcs.length);
  const perFrame = [...animated.values()].reduce((sum, count) => sum + count, 0) / frames;
  const worst = [...animated.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([node, count]) => `${count}x ${node.slice(0, 60)}`).join("; ");
  // Budget: the running step's node, the thinking glyph, the sidebar's running dot, the stream tail and a block
  // being formed are legitimate. Ornament is not. (Before: 13 ornaments per frame, 425 ms of style per second.)
  assert.ok(perFrame <= 5, `${perFrame.toFixed(1)} animated elements are re-resolved per frame during a run; the busiest: ${worst}`);
  assert.ok(styleMs / seconds < 160, `style recalculation took ${(styleMs / seconds).toFixed(0)} ms per second of a streaming answer`);

  await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 120000 });
  await page.waitForTimeout(600);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.loomLive), undefined, "the live flag leaves with the run");
  assert.deepEqual(errors, []);
  console.log(`PASS: during a streaming run ${perFrame.toFixed(1)} animated elements are re-resolved per frame and style takes ${(styleMs / seconds).toFixed(0)} ms/s`);
} finally {
  await browser.close();
  await server?.close();
}
