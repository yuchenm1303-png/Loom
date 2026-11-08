import assert from "node:assert/strict";
import fs from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
const out = process.argv[2] || "../.cache/mobile-reading"; fs.mkdirSync(out, { recursive: true });
try {
  for (const width of [320, 390, 430]) {
    const page = await browser.newPage({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true });
    await page.goto("http://127.0.0.1:5199/scripts/fixtures/mobile.html?readability=1");
    await page.getByText("修复 Host git 挂起", { exact: true }).first().click();
    await page.locator(".turn-final-answer .assistant-message").waitFor();
    const process = page.locator(".turn-process-header"); if (await process.count()) await process.first().click();
    const geometry = await page.evaluate(() => {
      const transcript = document.querySelector('.transcript').getBoundingClientRect();
      const final = document.querySelector('.turn-final-answer .assistant-message');
      const style = getComputedStyle(final), box = final.getBoundingClientRect();
      const content = document.querySelector('.turn-process-content');
      return { width: innerWidth, transcript: transcript.width, final: box.width, left: box.left, font: parseFloat(style.fontSize), line: parseFloat(style.lineHeight), indent: content ? parseFloat(getComputedStyle(content).paddingLeft) + parseFloat(getComputedStyle(content).marginLeft) : 0, stage: document.querySelector('.conversation-stage').getBoundingClientRect().height, overflow: document.documentElement.scrollWidth - innerWidth };
    });
    assert.ok(geometry.transcript >= width - 34, JSON.stringify(geometry));
    assert.ok(geometry.final >= width - 40, JSON.stringify(geometry));
    assert.equal(geometry.font, 15);
    assert.ok(Math.abs(geometry.line / geometry.font - 1.6) < .01);
    assert.ok(geometry.indent <= 4, "process nesting must not steal reading width");
    assert.ok(geometry.stage >= 550, JSON.stringify(geometry));
    assert.equal(geometry.overflow, 0);
    await page.locator('.transcript-scroll').evaluate((element) => { element.scrollTop = 0; });
    await page.screenshot({ path: `${out}/reading-${width}.png`, animations: "disabled" });
    await page.setViewportSize({ width, height: 480 });
    await page.getByRole('textbox', { name: '发消息', exact: true }).focus();
    assert.ok(await page.locator('.conversation-stage').evaluate((element) => element.getBoundingClientRect().height) >= 220, "keyboard leaves room for messages");
    await page.screenshot({ path: `${out}/keyboard-${width}.png`, animations: "disabled" });
    await page.close();
  }
  console.log("Phone reading: full-width text, compact 15px body, 1.6 leading, flattened process indentation and keyboard space passed at 320/390/430px");
} finally { await browser.close(); }
