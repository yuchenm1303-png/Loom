import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  // Keep an asynchronous wallpaper download out of the idle measurements.
  await page.route("https://smirel.com/**", route => route.fulfill({
    contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><path fill="#eee" d="M0 0h4v4H0z"/></svg>',
  }));
  await page.addInitScript(() => {
    localStorage.setItem("loom.language", "zh-CN");
    window.effectDraws = 0;
    const draw = WebGLRenderingContext.prototype.drawArrays;
    WebGLRenderingContext.prototype.drawArrays = function(...args) {
      window.effectDraws++;
      return draw.apply(this, args);
    };
  });
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/runtime-effects.html`);
  await page.waitForFunction(() => window.effectDraws > 0);
  await page.locator('.language-settings-options button').nth(1).evaluate(el => el.click());
  await page.waitForFunction(() => document.querySelector("#dynamic-setting")?.textContent === "常规");
  const translation = await page.evaluate(async () => {
    const original = document.createTreeWalker;
    let visited = 0;
    document.createTreeWalker = function(...args) {
      const walker = original.apply(this, args);
      const next = walker.nextNode.bind(walker);
      walker.nextNode = () => { visited++; return next(); };
      return walker;
    };
    try {
      const node = document.querySelector("#dynamic-setting");
      for (let index = 0; index < 10; index++) {
        node.firstChild.nodeValue = index % 2 ? "General" : "Settings";
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      const added = document.createElement("div");
      added.innerHTML = '<span title="General">Settings</span><input placeholder="Search settings">';
      node.append(added);
      await new Promise(resolve => setTimeout(resolve, 0));
      return { visited, text: node.firstChild.nodeValue, added: added.textContent,
        title: added.querySelector("span").title, placeholder: added.querySelector("input").placeholder };
    } finally { document.createTreeWalker = original; }
  });
  assert.ok(translation.visited < 30, `settings updates walked ${translation.visited} nodes`);
  assert.equal(translation.text, "常规");
  assert.equal(translation.added, "设置");
  assert.equal(translation.title, "常规");
  assert.equal(translation.placeholder, "搜索设置");
  const settle = async () => {
    await page.evaluate(() => { window.effectIdleSample = { count: window.effectDraws, since: performance.now() }; });
    await page.waitForFunction(() => {
      const sample = window.effectIdleSample;
      if (sample.count !== window.effectDraws) {
        sample.count = window.effectDraws;
        sample.since = performance.now();
      }
      return performance.now() - sample.since > 500;
    }, undefined, { timeout: 8000 });
    const before = await page.evaluate(() => window.effectDraws);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.effectDraws), before, "stationary cursor must stop drawing");
    return before;
  };
  await page.mouse.move(600, 400);
  const idle = await settle();
  await page.mouse.move(220, 210);
  await page.waitForFunction(before => window.effectDraws > before, idle);
  const hovered = await settle();
  assert.notEqual(await page.locator(".loom-portal-page button").evaluate(el => el.style.translate), "",
    "hover must exercise a magnetic snap target");
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForFunction(before => window.effectDraws > before, hovered);
  const pressed = await settle();
  await page.locator(".loom-portal-page button").evaluate(el => { el.textContent = "Continue"; });
  await page.waitForFunction(before => window.effectDraws > before, pressed);
  await settle();
  await page.locator('.language-settings-options button').first().evaluate(el => el.click());
  await page.waitForFunction(() => document.querySelector("#dynamic-setting")?.firstChild.nodeValue === "General");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ translation, idle, hovered, pressed }));
  console.log("PASS: incremental settings translation and cursor idle/wake behavior");
} finally { await browser.close(); }
