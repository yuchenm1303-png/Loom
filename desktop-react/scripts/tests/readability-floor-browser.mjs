// The readability floor, on what is really painted. typography-scale.css says no text is below 11.5px; this walks the
// screens of the live-app fixture (the conversation, every settings page, the profile, the account, both composer
// popovers and the docks) and fails for any text that is, except what is exempt on purpose: digit badges, which are
// pills pinned to fixed geometry (--loom-font-badge), and chart axes, which sit on a geometry of their own.
// (tests/test_design_scale_contract.py counts the declarations; this one looks at the screen.)
//
//   LOOM_PLAYWRIGHT_MODULE=file:///.../playwright-core/index.mjs LOOM_CHROMIUM_PATH=.../chrome.exe \
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/readability-floor-browser.mjs
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const FLOOR = 11.5;
const EXEMPT = [
  ".runtime-tab-count", ".sub-agent-filter button > span", ".project-memory-tabs button b",
  ".profile-heatmap-months", ".profile-heatmap-weekdays", ".profile-hours-axis",
];

const dock = (id) => async (page) => { await page.locator(`.loom-tool-button[data-tool-id="${id}"]`).evaluate((element) => element.click()); };
const settingsPage = (index) => async (page) => {
  await dock("settings")(page);
  await page.locator(".settings-nav button").first().waitFor({ timeout: 10000 });
  await page.waitForTimeout(600);
  await page.locator(".settings-nav button").nth(index).click();
};
const screens = [
  ["conversation", "&autosend=1&changes=1", 24000, null],
  ["home", "&empty=1", 1500, null],
  ["permission popover", "", 1000, async (page) => { await page.locator(".permission-chip").first().click(); }],
  ["model popover", "", 1000, async (page) => { await page.locator(".model-chip").first().click(); }],
  ["inspector", "", 1000, dock("inspector")],
  ["review", "", 1000, dock("review")],
  ["agents", "&agents=1", 1000, dock("agents")],
  ["project", "", 1000, async (page) => { await page.locator(".project-group-main").first().click(); }],
  ["profile", "", 1800, dock("profile")],
  ["account", "", 1500, dock("account")],
  ["settings general", "", 1500, dock("settings")],
];
for (let index = 1; index < 17; index += 1) screens.push([`settings page ${index}`, "", 900, settingsPage(index)]);

const scan = ({ floor, exempt }) => {
  const found = [];
  for (const element of document.querySelectorAll("body *")) {
    const box = element.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    const style = getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) continue;
    let own = "";
    for (const node of element.childNodes) if (node.nodeType === 3) own += node.textContent;
    own = own.replace(/\s+/g, " ").trim();
    if (!own) continue;
    const size = parseFloat(style.fontSize);
    if (size >= floor) continue;
    if (exempt.some((selector) => element.matches(selector) || element.closest(selector))) continue;
    const name = typeof element.className === "string" && element.className ? element.className.split(/\s+/)[0] : element.tagName.toLowerCase();
    found.push(`${name} ${size.toFixed(2)}px "${own.slice(0, 24)}"`);
  }
  return found;
};

try {
  const violations = [];
  let painted = 0;
  for (const [name, query, wait, open] of screens) {
    const context = await browser.newContext({ viewport: { width: 1333, height: 853 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const scenario = query.includes("scenario=") ? "" : "scenario=code";
    await page.goto(`${origin}/scripts/fixtures/live-app.html?${scenario}&theme=dark&lang=en${query}`);
    await page.locator(".thread-header").waitFor({ timeout: 20000 });
    await page.waitForTimeout(600);
    if (open) await open(page);
    await page.waitForTimeout(wait);
    const found = await page.evaluate(scan, { floor: FLOOR, exempt: EXEMPT });
    const texts = await page.evaluate(() => [...document.querySelectorAll("body *")].filter((element) => [...element.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim())).length);
    painted += texts;
    for (const line of found) violations.push(`${name}: ${line}`);
    assert.deepEqual(errors, [], `${name} raised page errors`);
    await context.close();
  }
  assert.ok(painted > 1500, `the screens were really painted (${painted} text elements)`);
  assert.deepEqual(violations, [], `text painted below ${FLOOR}px (raise it, or exempt it here with a reason):\n  ${violations.join("\n  ")}`);
  console.log(`PASS: nothing is painted below ${FLOOR}px on ${screens.length} screens (${painted} text elements), digit badges and chart axes aside`);
} finally {
  await browser.close();
}
