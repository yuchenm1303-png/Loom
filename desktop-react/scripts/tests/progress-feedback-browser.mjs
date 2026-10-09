import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const screenshots = process.env.LOOM_SURFACE_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });
try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=${theme}`);
    await page.locator(".composer").waitFor();
    // Mock only the Host settings boundary, retaining the real App and settings UI.
    await page.evaluate(() => {
      const original = window.loom.call.bind(window.loom);
      const state = { mode: "balanced", fail: false, unconfirmed: false, writes: 0, hold: false };
      localStorage.setItem("loom.settings.desktop.v2", JSON.stringify({ agent: { progressFeedback: "detailed" } }));
      window.feedbackTest = state;
      const snapshot = () => ({ schemaVersion: 2, capabilities: {}, agent: { progressFeedback: state.mode } });
      window.loom.call = async (method, args) => {
        if (method === "settings/get") return { settings: snapshot() };
        if (method === "settings/set" && args.path === "agent.progressFeedback") {
          state.writes++;
          if (state.hold) await new Promise(resolve => { window.releaseFeedbackSave = resolve; });
          if (state.fail) throw new Error("Host unavailable");
          if (state.unconfirmed) return {};
          state.mode = args.value;
          return { settings: snapshot() };
        }
        return original(method, args);
      };
    });
    const open = async () => {
      await page.locator(".thread-settings-button").click();
      await page.locator('.settings-host[data-motion-phase="entered"]').waitFor();
      const general = page.locator(".settings-nav").getByRole("button", { name: "常规", exact: true });
      if (await general.getAttribute("aria-current") !== "page") await general.click();
      await page.locator('.progress-feedback-control input[value="balanced"]').waitFor();
    };
    const radio = mode => page.locator(`.progress-feedback-control input[value="${mode}"]`);
    await open();
    await radio("balanced").waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.querySelector(".progress-feedback-options").disabled);
    assert.equal(await radio("balanced").isChecked(), true);
    await page.evaluate(() => window.feedbackTest.hold = true);
    await radio("quiet").click();
    assert.equal(await radio("detailed").isDisabled(), true, "saving prevents overlapping edits");
    await page.evaluate(() => { window.feedbackTest.hold = false; window.releaseFeedbackSave(); });
    await page.waitForFunction(() => document.querySelector('.progress-feedback-control input[value="quiet"]').checked);
    assert.equal(await page.evaluate(() => window.feedbackTest.mode), "quiet");
    await page.getByRole("button", { name: "返回应用", exact: true }).click();
    await page.locator(".settings-host").waitFor({ state: "detached" });
    await open();
    await page.waitForFunction(() => document.querySelector('.progress-feedback-control input[value="quiet"]').checked);
    await page.evaluate(() => window.feedbackTest.fail = true);
    await radio("detailed").click();
    await page.getByText(/进度反馈未保存：Host unavailable/).waitFor();
    assert.equal(await radio("quiet").isChecked(), true, "failed saves retain the confirmed mode");
    await page.evaluate(() => { window.feedbackTest.fail = false; window.feedbackTest.unconfirmed = true; });
    await radio("detailed").click();
    await page.getByText(/Host did not confirm this preference/).waitFor();
    assert.equal(await radio("quiet").isChecked(), true, "missing Host confirmation cannot look successful");
    await page.evaluate(() => window.feedbackTest.unconfirmed = false);
    await radio("detailed").click();
    await page.waitForFunction(() => document.querySelector('.progress-feedback-control input[value="detailed"]').checked);
    assert.equal(await page.evaluate(() => window.feedbackTest.writes), 4);
    await page.locator(".progress-feedback-control").scrollIntoViewIfNeeded();
    if (screenshots) await page.screenshot({ path: `${screenshots}/feedback-${theme}.png` });
    await page.setViewportSize({ width: 720, height: 1000 });
    await page.locator(".progress-feedback-control").scrollIntoViewIfNeeded();
    const box = await page.locator(".progress-feedback-options").boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 721, "options fit narrow settings panel");
    if (screenshots) await page.screenshot({ path: `${screenshots}/feedback-${theme}-narrow.png` });
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Progress feedback: Host persistence, failed/unconfirmed saves, both themes and narrow layout passed.");
} finally { await browser.close(); }
