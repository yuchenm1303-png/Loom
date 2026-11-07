/**
 * Every phone screen at phone size, over the scripted shell. Writes one PNG per
 * screen and fails on sideways scroll, text under the floor or a console error.
 *
 *   npx vite --port 5199 --strictPort
 *   node scripts/tests/mobile-screens.mjs <outDir>
 */
import fs from "node:fs";
import path from "node:path";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const out = path.resolve(process.argv[2] || "mobile-screens");
const executablePath = process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ executablePath, headless: true });
const failures = [];

async function check(page, name) {
  const report = await page.evaluate(() => {
    const root = document.documentElement;
    const small = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const host = node.parentElement;
      if (!host || !node.textContent.trim() || !host.getClientRects().length) continue;
      const size = parseFloat(getComputedStyle(host).fontSize);
      if (size < 11.5) small.push(`${size}px ${node.textContent.trim().slice(0, 24)}`);
    }
    const tiny = [...document.querySelectorAll("button, a, input, select, textarea")]
      .filter((element) => element.getClientRects().length && !element.closest(".transcript-scroll"))
      .map((element) => [element, element.getBoundingClientRect()])
      .filter(([, box]) => box.height < 44 && box.width > 0)
      .map(([element, box]) => `${Math.round(box.width)}x${Math.round(box.height)} ${element.textContent.trim().slice(0, 20) || element.getAttribute("aria-label")}`);
    return { overflow: root.scrollWidth - innerWidth, small, tiny };
  });
  if (report.overflow > 0) failures.push(`${name}: page scrolls sideways by ${report.overflow}px`);
  for (const text of report.small) failures.push(`${name}: under the floor: ${text}`);
  for (const target of report.tiny) failures.push(`${name}: touch target: ${target}`);
}

async function shot(page, name) {
  await page.waitForTimeout(450);
  await check(page, name);
  await page.screenshot({ path: path.join(out, `${name}.png`) });
}

async function open(query = "") {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true, colorScheme: "dark" });
  const page = await context.newPage();
  page.on("pageerror", (error) => failures.push(`page error: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !/404/.test(message.text())) failures.push(`console: ${message.text().slice(0, 160)}`);
  });
  await page.goto(`${origin}/scripts/fixtures/mobile.html${query}`);
  await page.waitForTimeout(query.includes("unpaired") ? 900 : 1400);
  return page;
}

const home = await open();
await shot(home, "01-now");
await home.locator(".m-nav button").nth(1).click();
await shot(home, "02-chats");
await home.locator(".m-nav button").nth(2).click();
await shot(home, "03-build");
await home.locator(".m-new button").click();
await shot(home, "04-project-sheet");
await home.locator(".m-choice").first().click();
await shot(home, "14-new-thread");
await home.locator(".m-icon-button").click();
await home.locator(".m-nav button").nth(3).click();
await shot(home, "05-more");
await home.locator(".m-nav button").nth(0).click();
await home.locator(".m-wait-card").click();
await home.waitForTimeout(1200);
await shot(home, "06-thread-end");
await home.locator(".transcript-scroll").evaluate((element) => { element.scrollTop = 0; });
await shot(home, "07-thread-top");
await home.locator(".m-chip").click();
await shot(home, "08-controls");
await home.locator(".m-scrim").click();
await home.locator(".m-composer textarea").fill("先别推，再跑一遍测试，然后把结果告诉我。这段话长一点，用来看输入框增高之后的样子。");
await shot(home, "09-composing");
await home.context().close();

const offline = await open("?offline=1");
await shot(offline, "10-offline");
await offline.context().close();

const pair = await open("?unpaired=1");
await shot(pair, "11-pair");
await pair.evaluate(() => window.__shell.pairing({ state: "claimed", confirmCode: "482913", deviceName: "书房台式机" }));
await shot(pair, "12-pair-confirm");
await pair.evaluate(() => window.__shell.pairing({ state: "failed", message: "这不是 Loom 的配对二维码" }));
await shot(pair, "13-pair-failed");
await pair.context().close();

await browser.close();
console.log(failures.length ? failures.join("\n") : "all screens clean");
process.exitCode = failures.length ? 1 : 0;
