import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
// A row's actions come up when the row is hovered (or focused), as a person reaches them.
async function rowAction(root, name) {
  const button = root.getByRole("button", { name, exact: true });
  await button.locator("xpath=ancestor::div[contains(@class, 'bh-row')][1]").hover();
  await button.click();
}
try {
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/build-history.html?theme=${theme}`);
    const root = page.locator(".bh-root");
    await root.locator(".bh-row").first().waitFor();
    assert.equal(await root.locator(":scope > .bh-list .bh-row").count(), 5);
    assert.match(await root.locator('[aria-current="page"]').textContent(), /Build task 70/);
    assert.equal(await root.locator(":scope > .bh-list").getByText("Other app", { exact: true }).count(), 0);
    if (process.env.LOOM_HISTORY_SCREENSHOT && theme === "dark") await page.screenshot({ path: process.env.LOOM_HISTORY_SCREENSHOT });
    // Rename in place: Enter saves, Escape leaves the title as it was.
    await rowAction(root, "Rename Build task 0");
    const field = root.getByRole("textbox", { name: "Rename" });
    await field.fill("Ship the parser");
    await field.press("Escape");
    assert.equal(await page.evaluate(() => window.renamed), undefined);
    await rowAction(root, "Rename Build task 0");
    await field.fill("Ship the parser");
    await field.press("Enter");
    await root.getByText("Ship the parser").waitFor();
    assert.deepEqual(await page.evaluate(() => window.renamed), ["t0", "Ship the parser"]);
    // The current conversation and one still replying can't be archived or deleted.
    assert.equal(await root.getByRole("button", { name: "Archive Build task 70", exact: true }).isDisabled(), true);
    assert.equal(await root.getByRole("button", { name: "Delete Build task 70", exact: true }).isDisabled(), true);
    assert.equal(await root.getByRole("button", { name: "Delete Build task 2", exact: true }).isDisabled(), true);
    // Delete takes a second click; a failure says so and keeps the row.
    await page.evaluate(() => { window.failAction = true; });
    await rowAction(root, "Delete Build task 4");
    await rowAction(root, "Click again to delete: Build task 4");
    await root.getByRole("status").filter({ hasText: /didn't work/ }).waitFor();
    await page.evaluate(() => { window.failAction = false; });
    await rowAction(root, "Delete Build task 4");
    await rowAction(root, "Click again to delete: Build task 4");
    await root.getByText("Build task 4", { exact: true }).waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => window.deleted), "t4");
    await rowAction(root, "Archive Build task 6");
    await root.getByText("Build task 6", { exact: true }).waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => window.archived), "t6");
    assert.equal(await root.locator(":scope > .bh-list .bh-row").count(), 5);
    // Search lights the words it found; Escape in the field clears it.
    await root.getByRole("searchbox").fill("task 8");
    assert.ok(await root.locator(":scope > .bh-list mark").count() > 0);
    await root.getByRole("searchbox").press("Escape");
    assert.equal(await root.getByRole("searchbox").inputValue(), "");
    await root.getByRole("searchbox").fill("Unique oldest");
    assert.match(await root.locator(":scope > .bh-list").textContent(), /Build task 84/);
    await root.getByRole("searchbox").fill("Other app");
    assert.match(await root.locator(":scope > .bh-list").textContent(), /No matching/);
    await root.getByRole("button", { name: "All projects", exact: true }).click();
    assert.equal(await root.locator(":scope > .bh-list .bh-row").count(), 5);
    await root.getByRole("searchbox").fill("");
    await root.getByRole("button", { name: /View all/ }).click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor();
    assert.equal(await dialog.locator(".bh-row").count(), 83);
    await dialog.getByRole("searchbox").fill("Unique oldest");
    assert.equal(await dialog.locator(".bh-row").count(), 1);
    await page.evaluate(() => { window.failOpen = true; });
    await dialog.locator(".bh-row").click();
    await dialog.getByRole("status").waitFor();
    assert.match(await dialog.getByRole("status").textContent(), /Could not open/);
    await page.evaluate(() => { window.failOpen = false; });
    await dialog.locator(".bh-row").click();
    await dialog.waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => window.opened), "t84");
    await root.getByRole("button", { name: /View all/ }).click();
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    await root.getByRole("button", { name: "New conversation in this project", exact: true }).click();
    assert.equal(await page.evaluate(() => window.created), true);
    await page.setViewportSize({ width: 390, height: 600 });
    await root.getByRole("button", { name: /View all/ }).click();
    const box = await dialog.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 600);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Build history: project scope, all-project search beyond 30, pinned current, task states, rename/archive/two-click delete with failure, search marks + Escape, open failure/retry, new, Escape and narrow dialog passed in both themes");
} finally { await browser.close(); }
