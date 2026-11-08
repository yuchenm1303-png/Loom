/**
 * The phone client's motion, measured frame by frame over the scripted shell: what
 * travels really travels, what leaves really leaves and is then removed, an interrupted
 * move ends in the right place, and reduced motion stops every loop.
 *
 *   npx vite --port 5199 --strictPort
 *   node scripts/tests/mobile-motion.mjs [filmDir]
 */
import fs from "node:fs";
import path from "node:path";

const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const executablePath = process.env.LOOM_CHROMIUM_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const film = process.argv[2] ? path.resolve(process.argv[2]) : "";
if (film) fs.mkdirSync(film, { recursive: true });

const browser = await chromium.launch({ executablePath, headless: true });
const failures = [];
const check = (ok, label, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${detail ? `  ${detail}` : ""}`);
  if (!ok) failures.push(label);
};

async function open(query = "", reducedMotion = "no-preference") {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, hasTouch: true, isMobile: true, colorScheme: "dark", reducedMotion });
  const page = await context.newPage();
  page.setDefaultNavigationTimeout(60000);
  page.on("pageerror", (error) => failures.push(`page error: ${error.message}`));
  await page.goto(`${origin}/scripts/fixtures/mobile.html${query}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  return page;
}

/** Runs `act` in the page and records a value per frame until `frames` have passed. */
const sample = (page, selector, read, act, frames = 40) => page.evaluate(async ({ selector, read, act, frames }) => {
  const value = new Function("element", `return (${read})(element)`);
  const trail = [];
  new Function(`return (${act})()`)();
  for (let frame = 0; frame < frames; frame += 1) {
    await new Promise(requestAnimationFrame);
    const element = document.querySelector(selector);
    trail.push(element ? value(element) : null);
  }
  return trail;
}, { selector, read, act, frames });

const left = "(element) => Math.round(element.getBoundingClientRect().left)";
const top = "(element) => Math.round(element.getBoundingClientRect().top)";
const present = (trail) => trail.filter((value) => value !== null);
const distinct = (trail) => new Set(present(trail)).size;

const page = await open();

// Tabs
const glider = await sample(page, ".m-nav > .m-glider", left, `() => document.querySelectorAll(".m-nav button")[2].click()`);
check(distinct(glider) > 4, "the tab selection travels", `${present(glider)[0]} -> ${present(glider).at(-1)} in ${distinct(glider)} steps`);
await page.waitForTimeout(500);
const landed = await page.locator(".m-nav > .m-glider").evaluate((element) => element.getBoundingClientRect().left);
const tabLeft = await page.locator(".m-nav button").nth(2).evaluate((element) => element.getBoundingClientRect().left);
check(Math.abs(landed - tabLeft) < 1, "and lands on its tab", `${landed} vs ${tabLeft}`);
await page.locator(".m-nav button").nth(0).click();
await page.waitForTimeout(600);

// Pushing a conversation
const pushed = await sample(page, ".m-screen", left, `() => document.querySelector(".m-wait-card").click()`);
check(distinct(pushed) > 5, "a conversation slides in", `from ${present(pushed)[0]} in ${distinct(pushed)} steps`);
await page.waitForTimeout(500);
const flush = await page.locator(".m-screen").evaluate((element) => element.getBoundingClientRect().left);
check(flush === 0, "and lands flush", String(flush));
const covered = await page.locator(".m-shell").evaluate((element) => element.getBoundingClientRect().left);
check(covered < -40, "the list slides back under it", String(covered));
check(await page.locator(".m-shell").evaluate((element) => element.inert), "the covered list takes no touches");
await page.waitForTimeout(1000);

// A sheet comes and goes
const rising = await sample(page, ".m-sheet", top, `() => document.querySelector(".m-chip").click()`);
check(distinct(rising) > 5, "a sheet rises", `in ${distinct(rising)} steps`);
if (film) await page.screenshot({ path: path.join(film, "sheet.png") });
await page.evaluate(() => { window.auditSheet = document.querySelector('.m-sheet'); document.querySelector('.m-scrim').click(); });
await page.waitForTimeout(70);
const reversedSheet = await page.evaluate(async () => {
  const before = document.querySelector('.m-sheet').getBoundingClientRect().top;
  document.querySelector('.m-chip').click();
  await new Promise(requestAnimationFrame);
  return { before, after: document.querySelector('.m-sheet').getBoundingClientRect().top, same: window.auditSheet === document.querySelector('.m-sheet') };
});
check(reversedSheet.same && Math.abs(reversedSheet.before - reversedSheet.after) < 80, "a sheet reverses from its current position", JSON.stringify(reversedSheet));
await page.waitForTimeout(400);
const segments = await sample(page, ".m-reasoning-options button:nth-of-type(3)", "(element) => getComputedStyle(element).color", `() => document.querySelectorAll(".m-segments button")[2].click()`, 30);
check(distinct(segments) > 2, "the reasoning choice fades to its selected state", `in ${distinct(segments)} steps`);
await page.waitForTimeout(300);
const falling = await sample(page, ".m-sheet", top, `() => document.querySelector(".m-scrim").click()`, 45);
check(distinct(falling) > 5, "a sheet falls instead of vanishing", `in ${distinct(falling)} steps`);
await page.waitForTimeout(400);
check(await page.locator(".m-sheet-host").count() === 0, "and is removed after its exit");

// A stalled window produces no frames; a closing surface must not wait for one forever.
await page.evaluate(() => document.querySelector(".m-chip").click());
await page.waitForTimeout(500);
await page.evaluate(() => {
  window.__raf = window.requestAnimationFrame;
  window.requestAnimationFrame = () => 0;
  document.querySelector(".m-scrim").click();
});
await page.waitForTimeout(1600);
const stuck = await page.locator(".m-sheet-host").count();
await page.evaluate(() => { window.requestAnimationFrame = window.__raf; });
check(stuck === 0, "a surface still closes when no frame ever comes");

// The offline banner makes its own room
await page.evaluate(() => window.__shell.state({ socket: "closed", online: false }));
await page.waitForTimeout(800);
check(await page.locator(".m-thread > .m-fold").count() === 0, "a short blip shows no banner");
await page.evaluate(() => window.__shell.state({ socket: "open", online: true }));
await page.waitForTimeout(100);
await page.evaluate(() => window.__shell.state({ socket: "closed", online: false }));
await page.waitForTimeout(2850);
const banner = await sample(page, ".m-thread > .m-fold", "(element) => Math.round(element.getBoundingClientRect().height)", "() => {}", 90);
check(distinct(banner) > 4, "the offline banner folds open", `${present(banner)[0]} -> ${present(banner).at(-1)}`);
const closing = await sample(page, ".m-thread > .m-fold", "(element) => Math.round(element.getBoundingClientRect().height)", `() => window.__shell.state({ socket: "open", online: true })`, 30);
check(distinct(closing) > 4, "and folds shut", `${present(closing)[0]} -> ${present(closing).at(-1)}`);
await page.waitForTimeout(350);
check(await page.locator(".m-thread > .m-fold").count() === 0, "and is removed");

// Composer
await page.locator(".m-composer textarea").fill("一行");
await page.waitForTimeout(300);
const growing = await sample(page, ".m-composer textarea", "(element) => Math.round(element.getBoundingClientRect().height)", `() => {
  const area = document.querySelector(".m-composer textarea");
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  set.call(area, "第一行\\n第二行\\n第三行");
  area.dispatchEvent(new Event("input", { bubbles: true }));
}`, 20);
check(distinct(growing) > 3, "the field grows instead of jumping", `${present(growing)[0]} -> ${present(growing).at(-1)}`);
await page.locator(".m-composer textarea").fill("");

// Going back
const leaving = await sample(page, ".m-screen", left, `() => document.querySelector(".m-icon-button").click()`, 40);
check(distinct(leaving) > 5, "a conversation slides away", `in ${distinct(leaving)} steps`);
await page.waitForTimeout(400);
check(await page.locator(".m-screen").count() === 0, "and is removed after its exit");
check(await page.locator(".m-shell").evaluate((element) => element.getBoundingClientRect().left) === 0, "the list comes back flush");

// Interrupted moves end where the last touch says
await page.evaluate(() => document.querySelector(".m-wait-card").click());
await page.waitForTimeout(90);
await page.evaluate(() => document.querySelector(".m-icon-button").click());
await page.waitForTimeout(700);
check(await page.locator(".m-screen").count() === 0, "back during the push ends on the list");
await page.evaluate(() => document.querySelector(".m-wait-card").click());
await page.waitForTimeout(450);
await page.evaluate(() => document.querySelector(".m-icon-button").click());
await page.waitForTimeout(90);
await page.evaluate(() => document.querySelector(".m-wait-card").click());
await page.waitForTimeout(700);
const reopened = await page.locator(".m-screen").evaluate((element) => [element.getBoundingClientRect().left, element.dataset.phase]);
check(reopened[0] === 0 && reopened[1] === "entered", "reopening during the exit ends on the conversation", reopened.join(" "));

// Android's Back key
const consumed = await page.evaluate(() => !window.dispatchEvent(new CustomEvent("loom:mobile-back", { cancelable: true })));
check(consumed, "Back closes the conversation");
await page.waitForTimeout(600);
const idle = await page.evaluate(() => !window.dispatchEvent(new CustomEvent("loom:mobile-back", { cancelable: true })));
check(!idle, "Back on the list is left to the system");

const loops = await page.evaluate(() => document.getAnimations().filter((animation) => animation.effect?.getTiming().iterations === Infinity).length);
check(loops > 0, "live states breathe", `${loops} loops`);
await page.context().close();

const still = await open("", "reduce");
const stillLoops = await still.evaluate(() => document.getAnimations().filter((animation) => animation.effect?.getTiming().iterations === Infinity).length);
check(stillLoops === 0, "reduced motion stops every loop", String(stillLoops));
await still.evaluate(() => document.querySelector(".m-wait-card").click());
await still.waitForTimeout(500);
check(await still.locator(".m-screen").evaluate((element) => element.getBoundingClientRect().left) === 0, "reduced motion still arrives");
await still.evaluate(() => document.querySelector(".m-icon-button").click());
await still.waitForTimeout(200);
check(await still.locator(".m-screen").count() === 0, "reduced motion leaves at once");
await still.context().close();

await browser.close();
if (failures.length) console.log(`\n${failures.length} failed:\n${failures.join("\n")}`);
process.exitCode = failures.length ? 1 : 0;
