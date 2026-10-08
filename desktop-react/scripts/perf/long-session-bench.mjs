// Main-thread cost of a long conversation, measured on the real App with the
// in-memory host of scripts/fixtures/perf-session.tsx (no model, no Host).
//
//   LOOM_PLAYWRIGHT_MODULE=file:///…/playwright-core/index.mjs \
//   LOOM_CHROMIUM_PATH="C:/Program Files/Google/Chrome/Application/chrome.exe" \
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 \
//   node scripts/perf/long-session-bench.mjs [following|detached|flush ...]
//
// Numbers come from CDP `Performance.getMetrics` deltas (script / layout / style
// recalculation / total main-thread task). Run it against a Vite dev server for
// relative comparisons, or against a production build for absolute ones; React
// development builds make script time several times larger.
//
// LOOM_BENCH_HEADED=1       real GPU compositing and display refresh (opens a window)
// LOOM_BENCH_MAX_GROWTH=3   fail `detached` when late style recalculation exceeds
//                           this multiple of the early cost (a long live turn must
//                           stay roughly linear; see css-has-invalidation.test.mjs)
//
// Scenarios
//   following  stream 10 tool cycles at the bottom of a 30-turn history.
//   detached   scroll up first, so nothing folds away, then stream a long edit-heavy
//              turn and report cost per 25 cycles. This is where the `:has()`
//              invalidation made style recalculation grow with turn length.
//   flush      200 tiny deltas into a history message: the per-update App cost.
import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const headed = process.env.LOOM_BENCH_HEADED === "1";
const requested = process.argv.slice(2);
const scenarios = requested.length ? requested : ["following", "detached", "flush"];

async function session(query, viewport = { width: 1707, height: 1000 }) {
  const browser = await chromium.launch({
    executablePath: process.env.LOOM_CHROMIUM_PATH,
    headless: !headed,
    args: headed ? ["--window-position=2400,0", "--disable-features=CalculateNativeWinOcclusion"] : [],
  });
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const client = await page.context().newCDPSession(page);
  await client.send("Performance.enable");
  await page.goto(`${origin}/scripts/fixtures/perf-session.html?${query}`);
  await page.locator(".turn-block").nth(2).waitFor({ timeout: 60000 });
  await page.waitForTimeout(1500);
  const metrics = async () => Object.fromEntries((await client.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
  return { browser, page, errors, metrics };
}

const spent = (before, after) => ({
  taskMs: Math.round((after.TaskDuration - before.TaskDuration) * 1000),
  scriptMs: Math.round((after.ScriptDuration - before.ScriptDuration) * 1000),
  layoutMs: Math.round((after.LayoutDuration - before.LayoutDuration) * 1000),
  recalcMs: Math.round((after.RecalcStyleDuration - before.RecalcStyleDuration) * 1000),
  recalcs: after.RecalcStyleCount - before.RecalcStyleCount,
});
const line = (label, result, wall) => console.log(`${label.padEnd(26)} wall=${String(wall).padStart(6)}ms task=${String(result.taskMs).padStart(6)}ms (${Math.round(100 * result.taskMs / wall)}%) script=${String(result.scriptMs).padStart(5)} layout=${String(result.layoutMs).padStart(4)} recalc=${String(result.recalcMs).padStart(6)} (${result.recalcs})`);

async function following() {
  const { browser, page, errors, metrics } = await session("turns=30&tools=30&motion=1");
  try {
    const before = await metrics();
    const started = Date.now();
    await page.evaluate((options) => window.perfFixture.startLive(options), { tools: 10, deltaMs: 24, toolMs: 160, finalChars: 1500 });
    await page.waitForTimeout(800);
    line("following, 10 cycles", spent(before, await metrics()), Date.now() - started);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

async function detached() {
  const cycles = 50, bucket = 25;
  const { browser, page, errors, metrics } = await session("turns=10&tools=20&edits=0.25&editLines=30&motion=1");
  try {
    await page.evaluate((options) => { void window.perfFixture.startLive(options); }, { tools: cycles, deltaMs: 12, toolMs: 60, finalChars: 1500 });
    await page.waitForTimeout(1500);
    const box = await page.locator(".transcript-scroll").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, -600); // user reads earlier output: follow detaches, no folding
    await page.waitForTimeout(300);
    const recalc = [];
    let last = await metrics();
    let lastAt = Date.now();
    for (let from = 0; from < cycles; from += bucket) {
      await page.waitForFunction((target) => window.perfFixture.progress() >= target, Math.min(cycles - 1, from + bucket), { timeout: 600000, polling: 200 });
      const now = await metrics();
      const result = spent(last, now);
      recalc.push(result.recalcMs);
      line(`detached, cycles ${from}-${Math.min(cycles - 1, from + bucket)}`, result, Date.now() - lastAt);
      last = now;
      lastAt = Date.now();
    }
    const growth = recalc.at(-1) / Math.max(1, recalc[0]);
    console.log(`style recalculation, late/early: ${growth.toFixed(2)}x`);
    if (process.env.LOOM_BENCH_MAX_GROWTH) {
      assert.ok(growth <= Number(process.env.LOOM_BENCH_MAX_GROWTH), `style recalculation grew ${growth.toFixed(2)}x while one live turn lengthened`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

async function flush() {
  const { browser, page, errors, metrics } = await session("turns=30&tools=60&edits=0.3&editLines=40&motion=0");
  try {
    const id = await page.evaluate(() => [...document.querySelectorAll(".turn-block")].at(-1).querySelector("[data-message-id]").dataset.messageId);
    const before = await metrics();
    const started = Date.now();
    await page.evaluate(async (itemId) => {
      for (let index = 0; index < 200; index++) {
        window.perfFixture.emit("item/delta", { threadId: "perf-0", itemId, delta: { text: "·" } });
        await new Promise((resolve) => setTimeout(resolve, 30));
      }
    }, id);
    await page.waitForTimeout(300);
    const result = spent(before, await metrics());
    line("flush, 200 updates", result, Date.now() - started);
    console.log(`per update: script ${(result.scriptMs / 200).toFixed(1)}ms, recalc ${(result.recalcMs / 200).toFixed(1)}ms`);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
}

const runners = { following, detached, flush };
for (const name of scenarios) {
  assert.ok(runners[name], `unknown scenario ${name}`);
  await runners[name]();
}
