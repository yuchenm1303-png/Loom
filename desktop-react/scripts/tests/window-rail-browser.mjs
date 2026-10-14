// The window rail: the desktop window has no strip of chrome of its own. The app fills the window,
// the window controls float over its top right corner, and the first --loom-rail-height of the
// window is the part you drag. This test opens every surface that can reach that corner and checks
//   - the app really starts at the top of the window and the controls take no room of their own;
//   - nothing you can click ends up under the controls (a header that forgot to make room);
//   - everything you can click on the rail is cut out of the drag region, because Chromium hands
//     the operating system every pixel left in it: a button that forgot its cut-out would move the
//     window instead of acting.
// Needs the Vite fixture server (LOOM_TEST_ORIGIN, see loom-browser-tests-setup).
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });

const INTERACTIVE = "button, a[href], input, select, textarea, summary, label, [role=button], [role=tab], [role=menuitem], [role=switch], [role=slider], [tabindex]:not([tabindex='-1'])";

// Runs in the page: what is clickable on the rail or under the controls, and what is wrong with it.
function scan(selector) {
  const controls = document.querySelector(".loom-window-controls").getBoundingClientRect();
  const rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--loom-rail-height"));
  const titlebar = document.querySelector(".loom-titlebar");
  const previous = titlebar.style.display;
  titlebar.style.display = "none"; // so that elementFromPoint sees the app, not the controls
  const problems = [], seen = [];
  const classes = (el) => String(el.className || "").trim().split(/\s+/).filter(Boolean).slice(0, 3).join(".");
  const describe = (el) => `${el.tagName.toLowerCase()}.${classes(el) || `(in ${el.parentElement ? classes(el.parentElement) : ""})`}${el.getAttribute("aria-label") ? ` [${el.getAttribute("aria-label")}]` : ""}`;
  for (const el of document.querySelectorAll(selector)) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.top >= rail) continue;
    const x0 = Math.max(r.left, 0), x1 = Math.min(r.right, innerWidth), y0 = Math.max(r.top, 0), y1 = Math.min(r.bottom, rail);
    if (x1 - x0 < 1 || y1 - y0 < 1) continue;
    // Is it really there (not clipped, hidden or covered)? Ask the page at the centre of the visible part.
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || !el.contains(hit)) continue;
    seen.push(describe(el));
    if (x1 > controls.left - 2) problems.push(`under the window controls: ${describe(el)} ${Math.round(r.left)}..${Math.round(r.right)}`);
    if (getComputedStyle(el).getPropertyValue("-webkit-app-region") !== "no-drag") problems.push(`inside the drag region: ${describe(el)} at ${Math.round(r.left)},${Math.round(r.top)}`);
  }
  titlebar.style.display = previous;
  return { problems, seen };
}

const surfaces = {
  base: async () => {},
  "sidebar closed": async (page) => { await page.locator(".thread-sidebar-triangle").click(); },
  // The field opens under the first row and its top edge is still on the rail.
  "sidebar search": async (page) => { await page.locator(".compact-sidebar-actions button").click(); },
  inspector: async (page) => { await page.locator(".thread-inspector-button").click(); },
  // These three entries are hidden until the conversation has something for them, so click them directly.
  agents: async (page) => { await page.locator(".thread-agent-button").evaluate((el) => el.click()); },
  review: async (page) => { await page.locator(".thread-review-check-button").evaluate((el) => el.click()); },
  artifacts: async (page) => { await page.locator(".thread-artifact-button").evaluate((el) => el.click()); },
  project: async (page) => { await page.locator(".project-group-main").first().click(); },
  profile: async (page) => { await page.locator(".thread-profile-button").click(); },
  settings: async (page) => { await page.locator(".thread-settings-button").click(); },
};

// What must be on the rail in each state (the scan reports only what it finds).
const expected = {
  base: ["sidebar-new-conversation", "thread-settings-button", "thread-inspector-button"],
  "sidebar closed": ["thread-sidebar-triangle", "thread-settings-button"],
  "sidebar search": ["compact-search"],
  inspector: ["thread-inspector-button"],
  agents: ["sub-agent-workspace-close"],
  review: ["review-close"],
  artifacts: ["artifact-preview-actions"],
  project: ["project-details-close"],
  profile: ["profile-back-button"],
  settings: [],
};

try {
  const failures = [];
  for (const theme of ["dark", "light"]) {
    for (const width of [1040, 1280, 1600]) {
      for (const [name, open] of Object.entries(surfaces)) {
        if (theme === "light" && width !== 1280) continue;
        const page = await browser.newPage({ viewport: { width, height: 820 } });
        const errors = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}`);
        await page.locator(".thread-header").waitFor();
        await page.waitForTimeout(500);
        await open(page);
        await page.waitForTimeout(700);
        const label = `${theme} ${width}px ${name}`;

        const geometry = await page.evaluate(() => {
          const box = (selector) => { const r = document.querySelector(selector).getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); };
          const shell = box(".loom-window-shell");
          return { shell, content: box(".loom-window-content"), titlebar: box(".loom-titlebar"), controls: box(".loom-window-controls"),
            rail: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--loom-rail-height")),
            scrolls: document.scrollingElement.scrollHeight > innerHeight || document.scrollingElement.scrollWidth > innerWidth,
            dragRegion: getComputedStyle(document.querySelector(".loom-titlebar-drag")).getPropertyValue("-webkit-app-region"),
            controlsRegion: getComputedStyle(document.querySelector(".loom-window-controls")).getPropertyValue("-webkit-app-region"),
            dragBox: (() => { const r = document.querySelector(".loom-titlebar-drag").getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); })() };
        });
        try {
          assert.deepEqual(geometry.content, [0, 0, width, 820], "the app fills the whole window");
          assert.deepEqual(geometry.titlebar, [0, 0, width, geometry.rail], "the rail is as wide as the window and rail-high");
          assert.equal(geometry.controls[0] + geometry.controls[2], width, "the controls end at the right edge");
          assert.equal(geometry.controls[3], geometry.rail, "the controls are as tall as the rail");
          assert.equal(geometry.dragRegion, "drag");
          assert.equal(geometry.controlsRegion, "no-drag");
          assert.deepEqual(geometry.dragBox, [0, 0, width, geometry.rail], "the whole rail can be dragged");
          assert.ok(!geometry.scrolls, "the window does not scroll");
        } catch (error) { failures.push(`${label}: ${error.message}`); }

        const { problems, seen } = await page.evaluate(scan, INTERACTIVE);
        for (const problem of problems) failures.push(`${label}: ${problem}`);
        // The scan must actually have found the rail's own controls, or it proves nothing.
        for (const needle of expected[name] ?? []) if (!seen.some((entry) => entry.includes(needle))) failures.push(`${label}: the scan never saw ${needle} on the rail (saw ${seen.length} things)`);
        if (errors.length) failures.push(`${label}: page errors ${errors.slice(0, 2).join(" | ")}`);
        await page.close();
      }
    }
  }
  assert.deepEqual(failures, [], `\n${failures.join("\n")}`);

  // The controls themselves: they act, and the maximize button follows the window's state.
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
  await page.goto(`${origin}/scripts/fixtures/window-chrome.html`);
  await page.locator(".loom-titlebar").waitFor();
  await page.getByRole("button", { name: /最小化|Minimize/ }).click();
  await page.getByRole("button", { name: /最大化|Maximize/ }).click();
  await page.getByRole("button", { name: /还原|Restore/ }).click();
  await page.getByRole("button", { name: /关闭|Close/ }).click();
  const actions = await page.evaluate(() => window.windowActions.map((entry) => entry.action));
  for (const action of ["minimize", "maximize", "close"]) assert.ok(actions.includes(action), `${action} reaches the host`);
  // Their hit area is the full height of the rail and the close button runs to the corner.
  const closeBox = await page.locator(".loom-window-close").boundingBox();
  assert.ok(closeBox.x + closeBox.width >= 1280 - 0.5 && closeBox.y === 0 && closeBox.height === 60, "the close button reaches the corner");
  await page.close();
  console.log("PASS: window rail - app fills the window, nothing clickable under the controls or in the drag region");
} finally { await browser.close(); }
