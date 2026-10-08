import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  if (process.env.LOOM_LONG_CONVERSATION_BASELINE) {
    const { readFileSync } = await import("node:fs");
    const { default: ts } = await import("typescript");
    const body = ts.transpileModule(readFileSync(process.env.LOOM_LONG_CONVERSATION_BASELINE, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    await page.route("**/src/customScrollbars.ts*", route => route.fulfill({ contentType: "application/javascript", body }));
  }
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/long-conversation.html?motion=1`);
  await page.locator(".turn-final-answer").nth(19).waitFor();
  await page.waitForTimeout(1200);
  const initial = await page.evaluate(() => ({ nodes: document.querySelectorAll("*").length,
    codeBlocks: document.querySelectorAll(".markdown-code-block pre").length,
    bottom: document.querySelector(".transcript-scroll").scrollHeight - document.querySelector(".transcript-scroll").scrollTop
      - document.querySelector(".transcript-scroll").clientHeight }));
  assert.ok(initial.bottom < 5, `opening long history must pin to bottom: ${initial.bottom}`);
  const metrics = await page.evaluate(async () => {
    const original = Element.prototype.getBoundingClientRect;
    let codeMeasurements = 0;
    Element.prototype.getBoundingClientRect = function(...args) {
      if (this.matches(".markdown-code-block pre")) codeMeasurements++;
      return original.apply(this, args);
    };
    const scroller = document.querySelector(".transcript-scroll");
    scroller.dispatchEvent(new WheelEvent("wheel", { deltaY: -100, bubbles: true }));
    const started = performance.now();
    try {
      for (let index = 0; index < 20; index++) {
        scroller.scrollTop -= 60;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      return { codeMeasurements, elapsedMs: performance.now() - started };
    } finally { Element.prototype.getBoundingClientRect = original; }
  });
  console.log(JSON.stringify({ initial, metrics }));
  if (!process.env.LOOM_LONG_CONVERSATION_BASELINE) assert.ok(metrics.codeMeasurements < 200,
    `scrolling measured historical code ${metrics.codeMeasurements} times`);
  const interaction = await page.evaluate(async () => {
    const original = Element.prototype.getBoundingClientRect;
    let codeMeasurements = 0;
    Element.prototype.getBoundingClientRect = function(...args) {
      if (this.matches(".markdown-code-block pre")) codeMeasurements++;
      return original.apply(this, args);
    };
    try {
      const button = document.querySelector(".turn-block:last-child .message-action-button");
      for (let index = 0; index < 20; index++) {
        button.dispatchEvent(new TransitionEvent("transitionend", { bubbles: true, propertyName: "color" }));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }
      return { codeMeasurements };
    } finally { Element.prototype.getBoundingClientRect = original; }
  });
  console.log(JSON.stringify({ interaction }));
  if (!process.env.LOOM_LONG_CONVERSATION_BASELINE) assert.ok(interaction.codeMeasurements < 10,
    `button transitions measured historical code ${interaction.codeMeasurements} times`);
  const client = await page.context().newCDPSession(page);
  await client.send("Performance.enable");
  // Reproduce the previous root-tone rules at their original specificity;
  // otherwise the new theme default would win and the root benchmark would
  // measure selector matching without actually changing inherited resources.
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.id = "legacy-root-cursor-benchmark";
    style.textContent = ':root[data-loom-pointer-tone="white"] { --loom-native-cursor: var(--loom-cursor-white); } :root[data-loom-pointer-tone="black"] { --loom-native-cursor: var(--loom-cursor-black); }';
    document.head.append(style);
  });
  const styleTime = async () => (await client.send("Performance.getMetrics")).metrics.find(metric => metric.name === "RecalcStyleDuration").value;
  const cursorCost = async local => {
    const before = await styleTime();
    await page.evaluate(async local => {
      const owner = local ? document.querySelector("textarea") : document.documentElement;
      for (let index = 0; index < 20; index++) {
        owner.dataset.loomPointerTone = index % 2 ? "black" : "white";
        await new Promise(requestAnimationFrame);
      }
      delete owner.dataset.loomPointerTone;
      await new Promise(requestAnimationFrame);
    }, local);
    return (await styleTime() - before) * 1000;
  };
  const rootCursorMs = await cursorCost(false);
  const localCursorMs = await cursorCost(true);
  await page.locator("#legacy-root-cursor-benchmark").evaluate(el => el.remove());
  assert.ok(localCursorMs < rootCursorMs / 3, `cursor contrast restyled history: root=${rootCursorMs}ms local=${localCursorMs}ms`);
  console.log(JSON.stringify({ rootCursorMs, localCursorMs }));
  await page.evaluate(() => {
    document.querySelector(".transcript-jump-latest").click();
  });
  await page.waitForTimeout(500);
  const feedback = page.locator(".turn-block:last-child .message-action-button[aria-pressed]").first();
  await feedback.hover();
  await feedback.click();
  assert.equal(await feedback.getAttribute("aria-pressed"), "true");
  await feedback.click();
  assert.equal(await feedback.getAttribute("aria-pressed"), "false");
  assert.equal(await page.evaluate(() => document.documentElement.hasAttribute("data-loom-pointer-tone")), false,
    "real hover must not change inherited root cursor resources");
  await page.locator('textarea[aria-label="Message"]').fill("长对话中仍能输入");
  assert.equal(await page.locator('textarea[aria-label="Message"]').inputValue(), "长对话中仍能输入");
  await page.evaluate(() => window.longConversation.start());
  await page.waitForFunction(() => document.querySelector(".turn-block.is-active"));
  const stream = await page.evaluate(async () => {
    const original = Element.prototype.getBoundingClientRect;
    let historicalCodeMeasurements = 0;
    let offscreenCodeMeasurements = 0;
    Element.prototype.getBoundingClientRect = function(...args) {
      const rect = original.apply(this, args);
      if (this.matches(".markdown-code-block pre") && !this.closest(".is-active")) {
        historicalCodeMeasurements++;
        if (rect.bottom < 0 || rect.top > innerHeight) offscreenCodeMeasurements++;
      }
      return rect;
    };
    try {
      for (let index = 0; index < 20; index++) {
        window.longConversation.delta(index);
        await new Promise(resolve => setTimeout(resolve, 35));
      }
      return { historicalCodeMeasurements, offscreenCodeMeasurements };
    } finally {
      Element.prototype.getBoundingClientRect = original;
      window.longConversation.finish();
    }
  });
  console.log(JSON.stringify({ stream }));
  if (!process.env.LOOM_LONG_CONVERSATION_BASELINE) {
    assert.ok(stream.historicalCodeMeasurements < initial.codeBlocks * 2,
      `streaming scanned all historical code: ${stream.historicalCodeMeasurements} measurements`);
    assert.ok(stream.offscreenCodeMeasurements < 10,
      `streaming measured offscreen code ${stream.offscreenCodeMeasurements} times`);
  }
  await page.waitForFunction(() => document.querySelector(".turn-block:last-child .markdown-body")?.textContent.includes("流式更新 19"));
  await page.waitForTimeout(1000);
  assert.ok(await page.locator(".transcript-scroll").evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 5));
  await page.evaluate(() => window.longConversation.small());
  await page.locator(".markdown-body").filter({ hasText: "Small conversation" }).waitFor();
  await page.evaluate(() => window.longConversation.restore());
  await page.locator(".turn-final-answer").nth(19).waitFor();
  await page.waitForTimeout(1000);
  assert.ok(await page.locator(".transcript-scroll").evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight < 5));
  assert.deepEqual(errors, []);
  console.log("PASS: long rich history, bounded hover/scroll work, local cursor contrast, streaming completion and thread switches");
} finally { await browser.close(); }
