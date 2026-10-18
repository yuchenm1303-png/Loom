import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/live-app.html?scenario=quick`, { waitUntil: "domcontentloaded" });
  await page.locator(".composer textarea").waitFor();
  await page.evaluate(async () => {
    const bridge = window.loom;
    const original = bridge.call.bind(bridge);
    const listed = await original("thread/list");
    const id = listed.threads[0].id;
    const baseline = await original("thread/read", { threadId: id });
    window.recoveryFixture = { id, status: "running", reads: 0 };
    bridge.call = async (method, args) => {
      if (method !== "thread/read" || args?.threadId !== id) return original(method, args);
      window.recoveryFixture.reads += 1;
      const status = window.recoveryFixture.status;
      return { ...baseline, thread: { ...baseline.thread, currentTurnId: "recovery", status },
        turns: [{ id: "recovery", threadId: id, status, finalItemId: "recovery-answer", items: [] }] };
    };
    window.liveFixture.emit("turn/started", { threadId: id, turn: { id: "recovery", threadId: id, status: "running", items: [] } });
    window.liveFixture.emit("item/completed", { item: { id: "recovery-answer", threadId: id, turnId: "recovery", type: "assistant_message", status: "completed", phase: "commentary", text: "The requested work is done." } });
  });
  await page.locator(".inline-thinking").waitFor();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForFunction(() => window.recoveryFixture.reads > 0);
  assert.equal(await page.locator(".inline-thinking").count(), 1, "completed prose and a running Host read do not end a turn");
  await page.evaluate(() => { window.recoveryFixture.status = "completed"; });
  // No completion notification or focus event: the periodic Host read must
  // discover the terminal state itself within one reconciliation interval.
  await page.waitForFunction(() => !document.documentElement.dataset.loomLive);
  assert.equal(await page.locator(".inline-thinking").count(), 0, "authoritative read recovers a lost terminal notification");
  await page.locator(".turn-final-answer").getByText("The requested work is done.", { exact: true }).waitFor();
  await page.evaluate(() => window.liveFixture.emit("thread/updated", { thread: {
    id: window.recoveryFixture.id, currentTurnId: "recovery", status: "running", title: "Stale snapshot",
  } }));
  assert.equal(await page.locator(".inline-thinking").count(), 0, "a stale running snapshot cannot resurrect a completed turn");
  await page.evaluate(() => window.liveFixture.emit("turn/started", { threadId: window.recoveryFixture.id,
    turn: { id: "recovery", threadId: window.recoveryFixture.id, status: "running", items: [] } }));
  assert.equal(await page.locator(".inline-thinking").count(), 0, "a replayed start cannot restart the same completed turn");
  await page.evaluate(() => {
    const threadId = window.recoveryFixture.id;
    window.liveFixture.emit("turn/started", { threadId, turn: { id: "next", threadId, status: "running", items: [] } });
    window.liveFixture.emit("item/completed", { item: { id: "next-user", threadId, turnId: "next", type: "user_message", status: "completed", text: "Continue with the next task" } });
    window.liveFixture.emit("turn/completed", { threadId, turn: { id: "recovery", threadId, status: "completed", items: [] } });
  });
  await page.locator(".inline-thinking").waitFor();
  assert.equal(await page.locator(".inline-thinking").count(), 1, "a delayed previous completion cannot stop the next turn");
  const previousReads = await page.evaluate(() => window.recoveryFixture.reads);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForFunction(count => window.recoveryFixture.reads > count, previousReads);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.ok(await page.evaluate(() => Boolean(document.documentElement.dataset.loomLive)), "an old Host read cannot end the next turn either");
  assert.deepEqual(errors, []);
  console.log("Real App verifies missing completion recovery, stale snapshot protection, and next-turn isolation.");
} finally {
  await browser.close();
}
