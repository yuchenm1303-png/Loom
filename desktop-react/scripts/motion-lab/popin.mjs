// Motion lab: what appears or vanishes in a live run with nothing to carry it.
//   node popin.mjs <url-of-live-app.html> [scenario,scenario,...] [--theme dark]
// Plays the live-app scenarios (default code,approval,error,decision) with the pop-in probe installed and lists every
// sizeable block that was added without an animation, or removed without an exit. tour.mjs --popin does the same for
// the rest of the interface.
import { launch } from "./launch.mjs";
import { popinProbe, tally, report } from "./popin-probe.mjs";

const argv = process.argv.slice(2);
const positional = argv.filter((arg, index) => !arg.startsWith("--") && argv[index - 1] !== "--theme");
const url = positional[0];
const scenarios = (positional[1] || "code,approval,error,decision").split(",");
const theme = argv.includes("--theme") ? argv[argv.indexOf("--theme") + 1] : "dark";
const maps = { popins: new Map(), popouts: new Map() };
const browser = await launch();
try {
  for (const scenario of scenarios) {
    const page = await browser.newPage({ viewport: { width: 1180, height: 780 }, deviceScaleFactor: 1 });
    await page.addInitScript(popinProbe);
    await page.goto(`${url}${url.includes("?") ? "&" : "?"}scenario=${scenario}&theme=${theme}&speed=1.4`);
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
    await page.waitForTimeout(1200);
    await page.evaluate(() => { window.__popin.on = true; window.liveFixture.send(); });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length > 0, null, { timeout: 30000 });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 120000 }).catch(() => {});
    await page.waitForTimeout(1800);
    tally(maps, await page.evaluate(() => window.__popin.take()), scenario);
    await page.close();
  }
  report(maps);
} finally { await browser.close(); }
