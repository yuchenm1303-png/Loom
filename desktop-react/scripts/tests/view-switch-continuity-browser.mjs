// Switching conversations must not make the header's context meter or the dock's counts vanish and come back: while
// the next conversation opens they keep what they showed (the meter dimmed and marked pending, the dock's tools
// disabled) and change in place when it arrives; a meter with nothing to show any more (a new conversation) fades
// out rather than disappearing in one frame; the project line above the composer folds instead of jumping.
// Uses the workspace-visual fixture's held thread reads (navigation=1) and its context report (context=1).
import assert from "node:assert/strict";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=dark&motion=1&navigation=1&context=1`);
  await page.locator(".context-meter").waitFor({ timeout: 30000 });
  await page.waitForTimeout(500);
  const meter = () => page.evaluate(() => {
    const element = document.querySelector(".context-meter");
    return element ? { opacity: Number(getComputedStyle(element).opacity), pending: element.dataset.pending ?? null, phase: element.dataset.motionPhase } : null;
  });
  const counts = () => page.evaluate(() => [...document.querySelectorAll(".loom-tool-count")].map((count) => `${count.closest("[data-tool-id]")?.dataset.toolId}:${count.dataset.on}`).sort().join(" "));
  assert.deepEqual(await meter(), { opacity: 1, pending: null, phase: "entered" });
  const countsBefore = await counts();
  assert.ok(countsBefore.length > 0, "the dock keeps a count dot for each tool that can carry one");

  // Another conversation is opening (its read is held): the meter stays, dimmed and pending; the counts stay.
  await page.evaluate(() => window.navigationFixture.hold("visual-2"));
  await page.evaluate(() => document.querySelector('.compact-thread-row[data-thread-id="visual-2"] .compact-thread-main').click());
  await page.waitForFunction(() => window.navigationFixture.pending.has("visual-2"));
  const first = await meter();
  assert.ok(first, "the meter does not vanish while the next conversation opens");
  assert.equal(first.pending, "true");
  assert.equal(first.phase, "entered", "it is held, not leaving");
  await page.waitForTimeout(400);
  const held = await meter();
  assert.ok(held, "the meter is still there while the conversation opens");
  assert.ok(Math.abs(held.opacity - .45) < .02, `pending, it dims (${held.opacity})`);
  assert.equal(await counts(), countsBefore, "the dock's counts are held, not dropped to none");

  // It arrives: the meter comes back to full in place.
  await page.evaluate(() => window.navigationFixture.release("visual-2"));
  await page.waitForFunction(() => !document.querySelector(".context-meter")?.dataset.pending);
  await page.waitForTimeout(400);
  assert.deepEqual(await meter(), { opacity: 1, pending: null, phase: "entered" });

  // A new conversation has no reading: the meter fades out (kept while it does), then goes.
  await page.locator(".sidebar-controls .sidebar-new-conversation").click();
  const leaving = await page.waitForFunction(() => document.querySelector(".context-meter")?.dataset.motionPhase === "exiting", null, { timeout: 2000 }).then(() => true, () => false);
  assert.equal(leaving, true, "the meter leaves on its exit, not in one frame");
  await page.waitForFunction(() => !document.querySelector(".context-meter"), null, { timeout: 2000 });

  assert.deepEqual(errors, []);
  console.log("View switch continuity passed: the meter and the dock's counts are held (dimmed, pending) while a conversation opens, come back in place, and the meter fades out for a new conversation.");
} finally {
  await browser.close();
}
