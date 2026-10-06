// Real optimistic -> authoritative reconciliation must retain DOM and animation.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/streaming.html`);
  await page.waitForFunction(() => Boolean(window.renderItems));
  await page.addStyleTag({ url: "/src/components/generation-motion.css?direct" });
  const pending = { id: "pending-user-test", clientMessageId: "pending-user-test", threadId: "thread-1",
    type: "user_message", text: "你好", status: "sending" };
  await page.evaluate(item => {
    window.renderItems([item], true);
    window.sentBubble = document.querySelector(".user-message");
    window.sendEntry = document.querySelector(".entry-user_message");
    window.launch = window.sendEntry.getAnimations().find(a => a.animationName === "loom-user-send-entry");
    window.launch.pause();
    window.launch.currentTime = 300;
  }, pending);
  assert.ok(await page.evaluate(() => Boolean(window.launch)), "optimistic message animates immediately");
  await page.evaluate(async item => {
    const { preservePendingUserIdentity } = await import("/src/pendingUserMessage.ts");
    const confirmed = preservePendingUserIdentity([item], { ...item, id: "server-user", clientMessageId: undefined,
      turnId: "turn-1", status: "completed" });
    window.renderItems([confirmed], true);
  }, pending);
  assert.ok(await page.evaluate(() => window.sentBubble === document.querySelector(".user-message")), "server id retains bubble DOM");
  assert.ok(await page.evaluate(() => window.sendEntry === document.querySelector(".entry-user_message")), "turn id retains entry DOM");
  assert.ok(await page.evaluate(() => window.sendEntry.getAnimations().includes(window.launch)), "confirmation does not restart launch");
  assert.equal(await page.evaluate(() => window.launch.currentTime), 300);
  console.log("Optimistic send starts immediately and confirmation retains its DOM and animation timeline.");
} finally {
  await browser.close();
}
