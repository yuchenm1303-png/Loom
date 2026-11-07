import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
try {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const open = async (query = "") => {
    await page.goto(`${origin}/scripts/fixtures/project-decision.html${query}`);
    await page.locator(".decision-card").waitFor();
  };
  const option = page.getByRole("radio", { name: "先做高 SNR LLR 校准" });
  await open();
  assert.equal(await page.locator(".pf-reply pre").count(), 0);
  await page.evaluate(() => { window.fail = true; });
  await option.click();
  await page.getByText("Send failed", { exact: true }).waitFor();
  assert.equal(await option.isEnabled(), true, "failed submission remains retryable");
  await page.evaluate(() => { window.fail = false; });
  await option.click();
  await page.locator(".decision-inline-receipt").waitFor();
  const [text, attachments, tokens] = await page.evaluate(() => window.sent);
  assert.match(text, /\[\[LOOM_DECISION_RESPONSE:v1\]\].*calibration/);
  assert.deepEqual(attachments, []);
  assert.ok(tokens > 0);
  await page.evaluate(() => window.decisionFixture.restore());
  await page.locator(".decision-inline-receipt").waitFor();
  assert.equal(await page.locator(".decision-card").count(), 0, "restored submission stays resolved");

  await open("?multiple=1");
  await page.getByRole("checkbox").first().click();
  await page.locator(".decision-custom-toggle").click();
  await page.locator(".decision-custom textarea").fill("同时保持现有实验参数");
  await page.locator(".decision-submit").click();
  await page.locator(".decision-inline-receipt").waitFor();
  assert.match((await page.evaluate(() => window.sent))[0], /同时保持现有实验参数/);

  await open();
  await page.evaluate(() => window.decisionFixture.set('```loom-decision\n{"title":', true));
  await page.waitForFunction(() => !document.querySelector(".decision-card"));
  assert.equal(await page.locator("pre").count(), 0, "streaming protocol stays hidden");
  await page.evaluate(() => window.decisionFixture.set(window.decisionFixture.content, true));
  assert.equal(await page.locator(".decision-card").count(), 0, "complete protocol waits for turn completion");
  await page.evaluate(() => window.decisionFixture.set(window.decisionFixture.content));
  await page.locator(".decision-card").waitFor();
  await page.evaluate(() => window.decisionFixture.set('```loom-decision\n{"title":'));
  await page.locator(".decision-recovery").waitFor();
  await page.locator(".decision-submit").click();
  await page.waitForFunction(() => /选项没有生成完整|previous options were incomplete/i.test(window.sent?.[0] || ""));
  assert.deepEqual(errors, []);
  console.log("Build decisions: rendering, retry, receipts, custom input, streaming and recovery passed.");
} finally { await browser.close(); }
