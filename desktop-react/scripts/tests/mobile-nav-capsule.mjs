import assert from "node:assert/strict";
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const out = process.argv[2] || "../.cache/mobile-nav-capsule";
fs.mkdirSync(out, { recursive: true });
try {
  for (const reducedMotion of ["no-preference", "reduce"]) {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion });
    await page.goto("http://127.0.0.1:5199/scripts/fixtures/mobile.html");
    await page.locator(".m-nav button").nth(2).click();
    await page.locator(".m-nav button").nth(3).hover();
    const surfaces = await page.locator(".m-nav").evaluate((nav) => {
      const icons = [...nav.querySelectorAll("button > i")];
      return icons.map((icon) => ({ background: getComputedStyle(icon).backgroundColor, before: getComputedStyle(icon, "::before").content, after: getComputedStyle(icon, "::after").content }));
    });
    assert.ok(surfaces.every((item) => item.background === "rgba(0, 0, 0, 0)"), JSON.stringify(surfaces));
    assert.equal(await page.locator(".m-nav-pill").count(), 1);
    const scales = await page.evaluate(async () => {
      document.querySelectorAll(".m-nav button")[4].click();
      const values = [];
      for (let i = 0; i < 36; i++) { await new Promise(requestAnimationFrame); values.push(getComputedStyle(document.querySelector(".m-nav-pill")).transform); }
      return values;
    });
    if (reducedMotion === "reduce") assert.equal(new Set(scales).size, 1);
    else assert.ok(new Set(scales).size > 5, "capsule deforms across frames");
    await page.locator(".m-nav button").nth(2).click();
    await page.locator(".m-nav button").nth(1).click();
    await page.locator(".m-nav-pill").evaluate(async (pill) => { await Promise.allSettled(pill.getAnimations().map((a) => a.finished)); });
    await page.screenshot({ path: `${out}/${reducedMotion}.png`, animations: "disabled" });
    await page.close();
  }
  console.log("Single capsule, hover without duplicate surface, elastic motion, interrupted taps and reduced motion passed");
} finally { await browser.close(); }
