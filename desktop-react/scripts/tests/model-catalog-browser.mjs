import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/model-catalog.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.getByRole("button", { name: /Choose model/ }).click();
  await page.getByRole("button", { name: /OpenCode Go/ }).first().click();
  await page.getByText(/catalog could not be validated/).waitFor();
  assert.match(await page.getByRole("status").first().innerText(), /Last successful sync:/);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByText("Model directory received.", { exact: false }).waitFor();
  await page.getByRole("textbox", { name: /Search OpenCode Go/ }).fill("beam");
  const beam = page.getByRole("button", { name: /Beam Free/ });
  await beam.waitFor();
  assert.equal(await beam.isEnabled(), true);
  await beam.click();
  assert.equal(await page.evaluate(() => window.selectedCatalogModel), "builtin:opencode-go:beam-free");
  assert.deepEqual(errors, []);
  console.log("model catalog picker: stale status, timestamp, refresh, search and Beam selection passed");
} finally { await browser.close(); }
