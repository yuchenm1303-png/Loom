// The conversations palette and split view: what the other conversations are doing, seen from the open one, and a
// second conversation beside it. Real React app over the in-memory Host (live-app fixture, `?runs=1`: live-1 runs and
// takes a step every 1.2 s, live-2 waits for an approval, live-3 finishes 3.5 s in). No account, file or model request
// can escape it.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";

async function open(theme) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en&runs=1`);
  await page.locator('.loom-tool-button[data-tool-id="runs"]').waitFor({ timeout: 60_000 });
  return { page, errors };
}

const rows = (page) => page.evaluate(() => [...document.querySelectorAll(".conversation-run")].map((row) => ({
  id: row.dataset.threadId, state: [...row.classList].find((name) => name.startsWith("is-"))?.slice(3),
  step: row.querySelector(".conversation-run-step")?.textContent ?? "",
  time: row.querySelector(".conversation-run-time")?.textContent ?? "",
})));
const box = async (page, selector) => page.locator(selector).boundingBox();
/** A drag by hand: press on the source, travel, let go over the target (Chromium runs HTML drag and drop from it). */
async function carry(page, source, target, dx = 0) {
  const from = await source.boundingBox();
  await page.mouse.move(from.x + 24, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + from.height / 2 + 10, { steps: 4 });
  const to = await target.boundingBox();
  await page.mouse.move(to.x + to.width / 2 + dx, to.y + to.height * 0.75, { steps: 12 });
  await page.mouse.up();
}

try {
  for (const theme of ["dark", "light"]) {
    const { page, errors } = await open(theme);
    const dock = page.locator('.loom-tool-button[data-tool-id="runs"]');

    // The dock says how many other conversations need a look before anything is opened.
    await page.waitForFunction(() => document.querySelector('[data-tool-id="runs"] .loom-tool-count')?.dataset.on === "true");
    assert.equal(await dock.getAttribute("data-ic-live"), "true", "a running conversation makes the tool live");

    await dock.click();
    const panel = page.locator(".conversation-runs-panel");
    await panel.waitFor();
    assert.equal(await dock.getAttribute("aria-pressed"), "true");
    await page.waitForFunction(() => document.querySelector('.conversation-run[data-thread-id="live-3"]')?.classList.contains("is-completed"), null, { timeout: 8000 });

    // Ordered by what needs the reader: approval, running, then finished.
    let list = await rows(page);
    assert.deepEqual(list.map((row) => row.id), ["live-2", "live-1", "live-3"], JSON.stringify(list));
    assert.deepEqual(list.map((row) => row.state), ["approval", "running", "completed"]);
    assert.ok(!list.some((row) => row.id === "live-0"), "the open conversation is not listed");

    // The running one follows its steps live, and its clock is the turn's (seeded from the Host), not Loom's.
    const before = list[1].step;
    await page.waitForFunction((previous) => document.querySelector('.conversation-run[data-thread-id="live-1"] .conversation-run-step')?.textContent !== previous, before, { timeout: 5000 });
    list = await rows(page);
    assert.match(list[1].step, /^Run /, `the latest step is described: ${list[1].step}`);
    assert.match(list[1].time, /^1m/, `a turn that started 95 s ago reads as such: ${list[1].time}`);

    // The palette stays inside the window, beside the rail, and does not cover it.
    const frame = await box(page, ".conversation-runs-panel");
    const rail = await box(page, ".loom-tool-dock");
    assert.ok(frame.x + frame.width <= rail.x, "the palette hangs beside the dock");

    // Dismissing a finished one takes it away; nothing else moves.
    await page.locator('.conversation-run[data-thread-id="live-3"]').hover();
    await page.locator('.conversation-run[data-thread-id="live-3"] .conversation-run-actions button').last().click();
    await page.waitForFunction(() => !document.querySelector('.conversation-run[data-thread-id="live-3"]'));

    // Split from the palette: the pane shows that conversation and follows it.
    await page.locator('.conversation-run[data-thread-id="live-1"]').hover();
    await page.locator('.conversation-run[data-thread-id="live-1"] .conversation-run-actions button').first().click();
    const pane = page.locator(".split-pane");
    await pane.waitFor();
    assert.equal(await pane.getAttribute("data-split-thread"), "live-1");
    await page.waitForFunction(() => document.querySelectorAll(".split-pane .transcript-entry, .split-pane .turn-process").length > 0, null, { timeout: 8000 });
    assert.equal(await page.locator('.conversation-run[data-thread-id="live-1"] .conversation-run-state').textContent(), "In split view");
    await page.keyboard.press("Escape");
    await panel.waitFor({ state: "detached" });

    // The two share the workspace's area: side by side, no overlap, the dock untouched.
    const workspace = await box(page, ".workspace");
    let paneBox = await box(page, ".split-pane");
    assert.ok(Math.abs(workspace.x + workspace.width - paneBox.x) <= 1, "the pane starts where the main column ends");
    assert.ok(paneBox.x + paneBox.width <= (await box(page, ".loom-tool-rail")).x + 1, "and ends at the tool rail");
    assert.ok(await page.evaluate(() => document.querySelector(".app-shell").classList.contains("with-split")));

    // The divider: carried by the pointer, the share is saved once on release.
    const divider = await box(page, ".split-pane-resizer");
    await page.mouse.move(divider.x + 4, 400);
    await page.mouse.down();
    await page.mouse.move(divider.x - 150, 400, { steps: 6 });
    await page.mouse.up();
    paneBox = await box(page, ".split-pane");
    assert.ok(paneBox.width > 600, `the pane widened: ${paneBox.width}`);
    assert.ok(Number(await page.evaluate(() => localStorage.getItem("loom.layout.splitRatio"))) > 0.5);

    // Swap: the split one becomes the main one and the main one moves into the pane.
    await page.locator(".split-pane-promote").click();
    await page.waitForFunction(() => document.querySelector(".split-pane")?.dataset.splitThread === "live-0", null, { timeout: 8000 });

    // Dragging a conversation from the palette onto the right half replaces the split one.
    await dock.click();
    await carry(page, page.locator('.conversation-run[data-thread-id="live-2"] .conversation-run-title'), page.locator(".split-drop-half.is-split"));
    await page.waitForFunction(() => document.querySelector(".split-pane")?.dataset.splitThread === "live-2", null, { timeout: 8000 });
    assert.equal(await page.evaluate(() => document.documentElement.dataset.loomThreadDrag ?? null), null, "the drop zone is put away");
    assert.equal(await page.locator(".split-drop-target").evaluate((node) => getComputedStyle(node).pointerEvents), "none");
    await page.keyboard.press("Escape");

    // From the sidebar onto the left half: it opens as the main conversation, the split one stays.
    await page.locator(".project-group-row", { hasText: "termrelay" }).locator(".project-disclosure-button").click();
    const row = page.locator('.compact-thread-row[data-thread-id="live-4"] .compact-thread-main');
    await row.waitFor();
    await page.waitForTimeout(600);
    await carry(page, row, page.locator(".split-drop-half.is-main"));
    await page.waitForFunction(() => document.querySelector(".compact-thread-row.active")?.dataset.threadId === "live-4", null, { timeout: 8000 });
    assert.equal(await pane.getAttribute("data-split-thread"), "live-2");

    // Closing the pane gives the column back.
    await page.locator(".split-pane-close").click();
    await page.waitForFunction(() => !document.querySelector(".split-pane"), null, { timeout: 5000 });
    assert.ok(!(await page.evaluate(() => document.querySelector(".app-shell").classList.contains("with-split"))));

    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("PASS: conversations palette (order, live steps, seeded clock, dismiss) and split view (open, follow, divider, swap, drag to split, close)");
} finally {
  await browser.close();
}
