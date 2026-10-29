import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, colorScheme: "dark" });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(origin);
  const demo = page.locator(".home-demo");
  await demo.waitFor();
  await page.waitForFunction(() => document.querySelector(".home-demo")?.dataset.playing === "true");
  await demo.getByRole("button", { name: "Pause", exact: true }).click();
  const paused = await demo.getAttribute("data-step");
  await page.waitForTimeout(3400);
  assert.equal(await demo.getAttribute("data-step"), paused, "pause stops progression");
  await demo.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".home-demo")?.dataset.step === "3", null, { timeout: 15000 });
  assert.equal(await demo.getAttribute("data-playing"), "false", "autoplay stops for approval");
  await demo.getByRole("button", { name: "Review changes", exact: true }).click();
  assert.match(await demo.locator(".home-demo-diff").innerText(), /return version >= current/);
  await demo.getByRole("button", { name: "Conversation", exact: true }).click();
  await demo.getByRole("button", { name: "Approve demo", exact: true }).click();
  assert.equal(await demo.getAttribute("data-step"), "4");
  assert.match(await demo.locator(".home-demo-complete").innerText(), /origin\/main/);
  await demo.getByRole("button", { name: "Replay demo", exact: true }).click();
  assert.equal(await demo.getAttribute("data-step"), "0");
  await demo.getByRole("button", { name: "02 Edit code", exact: true }).click();
  assert.equal(await demo.getAttribute("data-playing"), "false", "manual step selection takes over");
  if (process.env.LOOM_DEMO_SCREENSHOTS) {
    await page.mouse.move(10, 1000);
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${process.env.LOOM_DEMO_SCREENSHOTS}/home-demo-dark.png` });
  }

  // Leaving the viewport freezes autoplay; re-entry resumes it.
  await demo.getByRole("button", { name: "Replay demo", exact: true }).click();
  await page.locator("#loom-start").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector(".home-demo")?.dataset.playing === "false");
  await page.waitForTimeout(3400);
  assert.equal(await demo.getAttribute("data-step"), "0");
  await demo.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector(".home-demo")?.dataset.playing === "true");
  assert.match(await demo.locator(".home-demo-sidebar").innerText(), /Projects[\s\S]*Fix release test[\s\S]*Archive/, "sidebar mirrors projects and conversations, with playback outside the app");
  assert.equal(await demo.locator(".home-demo-sidebar .home-demo-step-icon").count(), 0);
  await demo.getByRole("button", { name: "View terminal", exact: true }).click();
  assert.equal(await demo.getAttribute("data-step"), "2");
  assert.match(await demo.locator(".home-demo-terminal").innerText(), /41 passed/);
  await demo.getByRole("button", { name: "View code changes", exact: true }).click();
  assert.equal(await demo.getByRole("button", { name: "View code changes", exact: true }).getAttribute("aria-pressed"), "true");
  assert.match(await demo.locator(".home-demo-diff").innerText(), /return version >= current/);

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce", colorScheme: "light" });
  await mobile.addInitScript(() => localStorage.setItem("loom.settings.language", "zh-CN"));
  await mobile.goto(origin);
  const mobileDemo = mobile.locator(".home-demo");
  await mobileDemo.scrollIntoViewIfNeeded();
  await page.waitForTimeout(3400);
  assert.equal(await mobileDemo.getAttribute("data-step"), "0", "reduced motion disables autoplay");
  assert.equal(await mobileDemo.getAttribute("data-playing"), "false");
  await mobileDemo.getByRole("button", { name: "下一步", exact: true }).click();
  assert.equal(await mobileDemo.getAttribute("data-step"), "1");
  const bounds = await mobileDemo.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 391, "mobile preview fits viewport");
  assert.equal(await mobileDemo.evaluate(node => node.scrollWidth <= node.clientWidth), true, "no preview overflow");
  await mobileDemo.getByRole("button", { name: "04 等待批准", exact: true }).click();
  await mobileDemo.getByRole("button", { name: "批准演示", exact: true }).click();
  assert.equal(await mobileDemo.getAttribute("data-step"), "4");
  if (process.env.LOOM_DEMO_SCREENSHOTS) await mobileDemo.screenshot({ path: `${process.env.LOOM_DEMO_SCREENSHOTS}/home-demo-mobile.png` });
  assert.deepEqual(errors, []);
  console.log("Home demo: autoplay, pause, approval, review, replay, manual navigation, viewport suspension, Chinese, mobile and reduced motion passed.");
} finally {
  await browser.close();
}
