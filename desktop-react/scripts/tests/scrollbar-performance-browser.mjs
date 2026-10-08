import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 750 } });
  if (process.env.LOOM_SCROLLBAR_BASELINE) {
    const { readFileSync } = await import("node:fs");
    const { default: ts } = await import("typescript");
    const body = ts.transpileModule(readFileSync(process.env.LOOM_SCROLLBAR_BASELINE, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    await page.route("**/src/customScrollbars.ts*", route => route.fulfill({ contentType: "application/javascript", body }));
  }
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/window-chrome.html`);
  await page.locator(".loom-titlebar").waitFor();
  await page.evaluate(() => {
    const root = document.createElement("section");
    root.id = "scrollbar-benchmark";
    root.style.cssText = "position:absolute;top:300px;height:100px;width:300px;overflow:hidden";
    const fragment = document.createDocumentFragment();
    for (let index = 0; index < 10000; index++) fragment.append(document.createElement("div"));
    root.append(fragment);
    document.body.append(root);
  });
  await page.waitForTimeout(300);
  const client = await page.context().newCDPSession(page);
  await client.send("Performance.enable");
  const metrics = async () => Object.fromEntries((await client.send("Performance.getMetrics")).metrics.map(metric => [metric.name, metric.value]));
  const before = await metrics();
  const result = await page.evaluate(async () => {
    const root = document.getElementById("scrollbar-benchmark");
    const original = window.getComputedStyle;
    let reads = 0;
    window.getComputedStyle = function(element, ...args) {
      if (root.contains(element)) reads++;
      return original.call(this, element, ...args);
    };
    const started = performance.now();
    try {
      for (let index = 0; index < 20; index++) {
        root.style.transform = `translateX(${index % 2}px)`;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      return { reads, elapsedMs: performance.now() - started };
    } finally { window.getComputedStyle = original; }
  });
  const after = await metrics();
  for (const key of ["TaskDuration", "ScriptDuration", "RecalcStyleDuration", "LayoutDuration"]) result[`${key}Ms`] = (after[key] - before[key]) * 1000;
  console.log(JSON.stringify(result));
  if (!process.env.LOOM_SCROLLBAR_BASELINE) assert.ok(result.reads < 100, `animation rescanned history: ${result.reads} style reads`);
  const glow = await page.evaluate(async () => {
    const root = document.getElementById("scrollbar-benchmark");
    const original = window.getComputedStyle;
    let reads = 0;
    window.getComputedStyle = function(element, ...args) {
      if (root.contains(element)) reads++;
      return original.call(this, element, ...args);
    };
    try {
      for (let index = 0; index < 20; index++) {
        root.style.setProperty("--lens-x", `${index}px`);
        root.style.setProperty("--lens-y", `${index}px`);
        root.style.setProperty("--starter-x", `${index}%`);
        root.style.setProperty("--starter-y", `${index}%`);
        root.style.transform = `translateX(${index % 2}px)`;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      for (const name of ["--lens-x", "--lens-y", "--starter-x", "--starter-y"]) root.style.removeProperty(name);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { reads };
    } finally { window.getComputedStyle = original; }
  });
  console.log(JSON.stringify({ glow }));
  if (!process.env.LOOM_SCROLLBAR_BASELINE) assert.ok(glow.reads < 100, `pointer glow rescanned history: ${glow.reads} style reads`);
  // Class and inherited-variable changes may enable descendant scrollports.
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.textContent = ".enable-overflow .dynamic-scroll { overflow-y:auto } .variable-scroll { overflow-y:var(--test-overflow,hidden) }";
    document.head.append(style);
    const root = document.getElementById("scrollbar-benchmark");
    root.replaceChildren();
    const child = document.createElement("div");
    child.className = "dynamic-scroll";
    child.style.cssText = "height:50px;width:100px";
    const content = document.createElement("div"); content.style.height = "500px";
    child.append(content); root.append(child);
  });
  await page.waitForTimeout(80);
  await page.locator("#scrollbar-benchmark").evaluate(el => el.classList.add("enable-overflow"));
  await page.locator(".dynamic-scroll.loom-custom-scrollable").waitFor();
  await page.locator(".dynamic-scroll").evaluate(el => {
    const replacement = el.cloneNode(true);
    replacement.className = "variable-scroll";
    el.replaceWith(replacement);
  });
  await page.waitForTimeout(80);
  await page.locator("#scrollbar-benchmark").evaluate(el => el.style.setProperty("--test-overflow", "auto"));
  await page.locator(".variable-scroll.loom-custom-scrollable").waitFor();
  await page.locator("#scrollbar-benchmark").evaluate(el => el.remove());
  console.log("PASS: bounded animation discovery and dynamic descendant overflow");
} finally { await browser.close(); }
