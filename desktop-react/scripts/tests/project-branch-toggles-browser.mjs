import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const inventory = ["repo", "scope", "files", "knowledge", "plan", "tasks", "agents", "loom", "history"];
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/project-flow.html?at=0&draft=1&theme=${theme}`);
    const buttons = page.locator(".pf-branch-toggle");
    const button = id => page.locator(`.pf-branch-toggle[data-branch="${id}"]`);
    await buttons.first().waitFor();
    assert.deepEqual(await buttons.evaluateAll(nodes => nodes.map(node => node.dataset.branch)), inventory);
    await page.evaluate(() => { window.branchNodes = [...document.querySelectorAll(".pf-branch-toggle")]; });
    for (const id of ["scope", "knowledge", "tasks", "agents", "loom"]) {
      assert.equal(await button(id).isVisible(), true);
      assert.equal(await button(id).isDisabled(), true);
      assert.equal(await button(id).getAttribute("aria-pressed"), "false");
      assert.ok(await button(id).getAttribute("aria-description"), "unavailable branch explains why");
    }
    assert.equal(await page.locator('[data-glide="scope"], [data-glide="knowledge"], [data-glide^="agent:"], [data-glide="loom"]').count(), 0,
      "empty branches do not create placeholder cards or fake execution history");
    if (process.env.LOOM_BRANCH_SCREENSHOT && theme === "dark") await page.screenshot({ path: process.env.LOOM_BRANCH_SCREENSHOT });
    for (const id of ["repo", "files", "plan", "history"]) {
      const initial = await button(id).getAttribute("aria-pressed");
      assert.equal(await button(id).isEnabled(), true);
      await button(id).click();
      assert.notEqual(await button(id).getAttribute("aria-pressed"), initial);
      await button(id).click();
      assert.equal(await button(id).getAttribute("aria-pressed"), initial);
    }
    await page.evaluate(() => window.projectControlsFixture.activate());
    for (const id of ["scope", "knowledge", "agents", "loom"]) {
      await page.waitForFunction(id => !document.querySelector(`[data-branch="${id}"]`).disabled, id);
      assert.equal(await button(id).getAttribute("aria-pressed"), "true");
    }
    assert.equal(await page.evaluate(() => window.branchNodes.every((node, index) => document.querySelectorAll(".pf-branch-toggle")[index] === node)), true,
      "branches keep their DOM order and focus targets when data arrives");
    await button("scope").click();
    await page.evaluate(() => window.projectControlsFixture.clear());
    await page.waitForFunction(() => document.querySelector('[data-branch="scope"]').disabled);
    await page.evaluate(() => window.projectControlsFixture.activate());
    await page.waitForFunction(() => !document.querySelector('[data-branch="scope"]').disabled);
    assert.equal(await button("scope").getAttribute("aria-pressed"), "false", "manual folding survives an empty interval");
    for (const phase of ["draft", "live"]) {
      await page.evaluate(phase => window.projectControlsFixture[phase === "draft" ? "clear" : "activate"](), phase);
      for (const width of [900, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(850);
      assert.deepEqual(await buttons.evaluateAll(nodes => nodes.map(node => node.dataset.branch)), inventory);
      const layout = await page.evaluate(() => {
        const hub = document.querySelector(".pf-composer").getBoundingClientRect();
        const header = document.querySelector(".pf-composer-head");
        const branches = header.querySelector(".pf-branch-toggles");
        return { fits: header.scrollWidth <= header.clientWidth + 1, allBranchesVisible: branches.scrollWidth <= branches.clientWidth + 1,
          buttons: [...header.querySelectorAll("button")].map(node => { const box = node.getBoundingClientRect();
            return { branch: node.dataset.branch, fits: box.left >= hub.left && box.right <= hub.right + 1, width: box.width, height: box.height }; }) };
      });
      assert.equal(layout.fits, true, `${phase} header fits at ${width}px`);
      assert.equal(layout.allBranchesVisible, true, `${phase} branch controls do not need sideways scrolling at ${width}px`);
      assert.ok(layout.buttons.every(entry => entry.fits && entry.width >= 20 && entry.height >= 20), JSON.stringify({ phase, width, layout }));
      }
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Nine stable branch controls: explained empty states, fold actions, automatic readiness, preserved folds and narrow/live header bounds passed in both themes");
} finally { await browser.close(); }
