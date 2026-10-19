// The glider and the name of the quick-tool dock, one row per animation frame, while the pointer rests on one tool and
// then travels to another (live-app fixture). Rows are printed when something changes: how the glider slides and settles,
// when the name appears (after its delay), how it follows, and how both let go.
//   node scripts/motion-lab/dock-trace.mjs [from=browser] [to=inspector] [--theme dark] [--dwell 700] [--ms 700] [--dsf 1.5]
const positional = process.argv.slice(2).filter((arg, index, all) => !arg.startsWith("--") && !(all[index - 1] ?? "").startsWith("--"));
const [from = "browser", to = "inspector"] = positional;
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, arg, index, all) => (arg.startsWith("--") ? [...acc, [arg.slice(2), all[index + 1]]] : acc), []));
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await (await browser.newContext({ viewport: { width: 1333, height: 853 }, deviceScaleFactor: Number(args.dsf || 1.5) })).newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${args.theme || "dark"}&lang=en`);
  await page.locator(".loom-tool-dock").waitFor({ timeout: 60000 });
  await page.waitForTimeout(1500);
  const centre = async (id) => {
    const box = await page.locator(`.loom-tool-button[data-tool-id="${id}"]`).boundingBox();
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  };
  await page.evaluate(() => {
    window.__rows = [];
    const read = () => {
      const glider = document.querySelector(".loom-tool-glider");
      const note = document.querySelector(".loom-tool-tip");
      const g = glider.getBoundingClientRect();
      const n = note.getBoundingClientRect();
      window.__rows.push({
        t: performance.now(), top: +g.top.toFixed(2), opacity: +(+getComputedStyle(glider).opacity).toFixed(2),
        on: note.dataset.on, middle: +(n.top + n.height / 2).toFixed(2), right: +n.right.toFixed(2), width: +n.width.toFixed(1),
        noteOpacity: +(+getComputedStyle(note).opacity).toFixed(2), text: note.textContent.replace(/\s+/g, " ").trim().slice(0, 28),
      });
      window.__raf = requestAnimationFrame(read);
    };
    window.__start = read;
    window.__stop = () => cancelAnimationFrame(window.__raf);
  });
  const a = await centre(from);
  const b = await centre(to);
  await page.mouse.move(2, 2);
  await page.waitForTimeout(400);
  await page.evaluate(() => window.__start());
  await page.mouse.move(a.x, a.y, { steps: 3 });
  await page.waitForTimeout(Number(args.dwell || 700));
  await page.evaluate(() => window.__rows.push({ mark: "travel" }));
  await page.mouse.move(b.x, b.y, { steps: 3 });
  await page.waitForTimeout(Number(args.ms || 700));
  await page.evaluate(() => window.__rows.push({ mark: "leave" }));
  await page.mouse.move(2, 2);
  await page.waitForTimeout(500);
  const rows = await page.evaluate(() => { window.__stop(); return window.__rows; });
  const t0 = rows.find((row) => row.t)?.t ?? 0;
  let last = "";
  for (const row of rows) {
    if (row.mark) { console.log(`--- ${row.mark}`); continue; }
    const key = [row.top, row.opacity, row.on, row.middle, row.noteOpacity, row.text].join("|");
    if (key === last) continue;
    last = key;
    console.log(`${String(Math.round(row.t - t0)).padStart(5)} ms  glider top ${row.top} op ${row.opacity}  |  name ${row.on} mid ${row.middle} right ${row.right} w ${row.width} op ${row.noteOpacity}  ${row.text}`);
  }
  if (errors.length) console.log("page errors:", errors);
} finally {
  await browser.close();
}
