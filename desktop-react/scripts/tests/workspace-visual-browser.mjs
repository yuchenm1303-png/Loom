import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const screenshots = process.env.LOOM_WORKSPACE_SCREENSHOTS;
const luminance = rgb => rgb.match(/[\d.]+/g).slice(0, 3).map(Number)
  .map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
  .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
if (screenshots) await mkdir(screenshots, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=light`);
  await page.locator(".compact-thread-main").first().click({ position: { x: 20, y: 16 } });
  await page.locator(".decision-card").waitFor();
  await page.locator(".review-summary-card").waitFor();
  await page.locator(".transcript-scroll").evaluate(el => el.scrollTop = 0);
  await page.mouse.move(1400, 990);
  await page.waitForTimeout(500);
  if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-light.png` });
  // Read the actual cascade, not just the intended CSS declarations.
  const geometry = await page.evaluate(() => {
    const rect = selector => {
      const box = document.querySelector(selector).getBoundingClientRect();
      return { x: box.x, width: box.width };
    };
    return { decision: rect(".decision-card"), review: rect(".review-summary-card"), composer: rect(".composer"),
      descriptionSize: parseFloat(getComputedStyle(document.querySelector(".decision-option-copy > span:last-child")).fontSize),
      fade: parseFloat(getComputedStyle(document.querySelector(".conversation-stage"), "::after").height) };
  });
  for (const card of [geometry.decision, geometry.review]) {
    assert.ok(Math.abs(card.x - geometry.composer.x) <= 1, "conversation cards share the composer left edge");
    assert.ok(Math.abs(card.width - geometry.composer.width) <= 1, "conversation cards share the composer width");
  }
  assert.ok(geometry.descriptionSize >= 13, "decision descriptions remain readable");
  assert.ok(geometry.fade <= 16, "scroll fade does not erase a line of text");
  await page.locator(".composer textarea").fill("检查配置");
  assert.equal(await page.locator(".composer .send-button").isEnabled(), true);
  await page.locator(".composer .permission-chip").click();
  await page.locator(".composer-popover").waitFor();
  if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-permission.png` });
  await page.keyboard.press("Escape");
  await page.locator(".composer textarea").fill("");
  await page.locator(".composer textarea").blur();
  await page.mouse.move(1400, 990);
  for (const theme of ["dark", "light"]) {
    await page.evaluate(theme => document.documentElement.dataset.loomTheme = theme, theme);
    await page.waitForTimeout(300);
    const colors = await page.evaluate(() => ({
      text: getComputedStyle(document.querySelector(".decision-option-copy > span:last-child")).color,
      panel: getComputedStyle(document.querySelector(".decision-card")).backgroundColor,
    }));
    const foreground = luminance(colors.text), background = luminance(colors.panel);
    assert.ok((Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05) >= 4.5,
      `${theme} decision descriptions have sufficient text contrast`);
    if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-${theme}.png` });
    for (const width of [1000, 760, 480]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(350);
      for (const selector of [".decision-card", ".review-summary-card", ".composer"]) {
        const box = await page.locator(selector).boundingBox();
        assert.ok(box && box.x >= 0 && box.x + box.width <= width + 1, `${selector} fits ${width}px ${theme}`);
      }
      const card = await page.locator(".decision-card").boundingBox();
      const input = await page.locator(".composer").boundingBox();
      assert.ok(Math.abs(card.x - input.x) <= 1 && Math.abs(card.width - input.width) <= 1,
        `native scrollbar gutters preserve alignment at ${width}px`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (screenshots && width === 480) await page.screenshot({ path: `${screenshots}/workspace-${theme}-narrow.png` });
    }
    // A compact window can give the full width to the conversation via the
    // existing sidebar toggle, without breaking the fixed input alignment.
    await page.locator(".thread-sidebar-triangle").click();
    await page.waitForTimeout(400);
    assert.ok((await page.locator(".decision-card").boundingBox()).width > 400);
    if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-${theme}-compact.png` });
    await page.locator(".thread-sidebar-triangle").click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.waitForTimeout(400);
  }
  // Custom opinions and immediate single choice submission both remain usable.
  await page.locator(".decision-custom-toggle").click();
  await page.locator(".decision-custom textarea").fill("请保留邮件域名说明");
  assert.equal(await page.locator(".decision-submit").isEnabled(), true);
  await page.locator(".decision-option").first().click();
  await page.locator(".decision-inline-receipt").waitFor();
  assert.match(await page.evaluate(() => document.body.dataset.submitted), /guided/);
  assert.match(await page.evaluate(() => document.body.dataset.submitted), /请保留邮件域名说明/);
  for (const theme of ["light", "dark"]) {
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}&multiple=1`);
    await page.locator(".decision-card").waitFor();
    await page.locator(".decision-option").first().click();
    assert.equal(await page.locator('.decision-option[aria-checked="true"]').count(), 1);
    await page.mouse.move(1400, 990);
    if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-${theme}-selected.png` });
    await page.locator(".decision-submit").click();
    await page.locator(".decision-inline-receipt").waitFor();
  }
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=light&empty=1&lang=en`);
  await page.locator(".empty-state").waitFor();
  await page.waitForTimeout(500);
  assert.equal(await page.locator(".boot-error-card").count(), 0, "home remains healthy after async usage data arrives");
  assert.equal(await page.locator(".empty-state").count(), 1);
  if (screenshots) await page.screenshot({ path: `${screenshots}/workspace-home.png` });
  assert.deepEqual(errors, []);
  console.log("Workspace themes, native scrollbar alignment, typography, scroll fade, composer, narrow layouts, decisions and English home passed.");
} finally { await browser.close(); }
