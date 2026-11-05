import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const open = async query => {
    await page.goto(`${origin}/scripts/fixtures/project-folder.html${query}`);
    await page.locator(".pf-switch").first().waitFor();
  };
  // The switch opens a menu: existing projects, then "choose another folder" (the last option).
  const browse = async () => {
    await page.locator(".pf-switch").click();
    await page.locator(".pf-switch-browse").click();
  };
  await open("");
  assert.equal(await page.locator(".pf-branch").textContent(), "—");
  await browse();
  await page.getByText("C:\\Projects\\我的新项目", { exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), ["pick", "C:\\Projects\\我的新项目"]);
  await open("");
  await page.evaluate(() => window.folderFixture.cancel());
  await browse();
  assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), ["pick"]);
  await open("?fail=1");
  await browse();
  await page.getByText("Folder unavailable", { exact: true }).waitFor();
  assert.ok(await page.getByText("C:\\Projects\\original", { exact: true }).isVisible());
  await open("?live=1");
  assert.ok(await page.locator(".pf-switch").isDisabled());
  await open("");
  await page.locator(".pf-switch").click();
  await page.locator(".pf-switch-option", { hasText: "已有项目" }).click();
  await page.getByText("C:\\Projects\\已有项目", { exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), ["project:existing"]);
  assert.equal(await page.locator(".pf-switch-menu").count(), 0);
  // Keyboard: arrows move, Enter picks, Escape closes.
  await open("");
  await page.locator(".pf-switch").focus();
  await page.keyboard.press("ArrowDown");
  await page.locator(".pf-switch-menu").waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.locator(".pf-switch-menu").count(), 0);
  for (const [status, action] of [["not_git", "initialize"], ["unborn", "initialize"], ["detached", "branch"]]) {
    await open(`?setup=${status}`);
    assert.ok(await page.locator(".pf-proposal-start").isDisabled());
    assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), []); // never initialize on read
    await page.locator(".pf-workspace-setup summary").click();
    await page.locator(".pf-workspace-setup button").click();
    await page.waitForFunction(() => !document.querySelector(".pf-proposal-start").disabled);
    assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), [`prepare:${action}:true`]);
  }
  await page.goto(`${origin}/scripts/fixtures/project-folder.html?sidebar=1`);
  await page.locator(".project-section .sidebar-section-title button").click();
  assert.deepEqual(await page.evaluate(() => window.folderFixture.calls), ["pick", "C:\\Projects\\我的新项目"]);
  assert.deepEqual(errors, []);
  console.log("Folder choice, cancellation, errors and live-project lock passed");
} finally { await browser.close(); }
