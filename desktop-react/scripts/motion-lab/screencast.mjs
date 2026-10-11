// Motion lab: record what the screen actually showed, frame by frame.
//   node screencast.mjs <url> <outDir> [width=935] [height=731] [dsf=1] [--send] [--after <ms>] [--for <ms>] [--every 1]
// CDP Page.startScreencast hands over each frame the compositor produced, with its timestamp. With --send the live-app
// prompt is sent first; frames are kept from --after ms (default 0) after the send for --for ms (default until the turn
// ends). Writes frame-<ms since send>.jpg and prints how many frames came, and the longest gap between two of them (a
// frozen screen shows up here even when the page's own timers look healthy).
import fs from "node:fs";
import { launch } from "./launch.mjs";
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const valued = new Set(["--after", "--for", "--every"]);
const positional = argv.filter((arg, index) => !arg.startsWith("--") && !valued.has(argv[index - 1]));
const [url, outDir, width = "935", height = "731", dsf = "1"] = positional;
fs.mkdirSync(outDir, { recursive: true });
const browser = await launch();
try {
  const page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: Number(dsf) });
  await page.goto(url);
  await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
  await page.waitForTimeout(1000);
  const client = await page.context().newCDPSession(page);
  const frames = [];
  client.on("Page.screencastFrame", async (event) => {
    frames.push({ at: event.metadata.timestamp * 1000, data: event.data });
    await client.send("Page.screencastFrameAck", { sessionId: event.sessionId }).catch(() => {});
  });
  await client.send("Page.startScreencast", { format: "jpeg", quality: 80, everyNthFrame: Number(option("--every", "1")) });
  const sentAt = Date.now();
  if (flag("--send")) await page.evaluate(() => window.liveFixture.send());
  const forMs = Number(option("--for", "0"));
  if (forMs) await page.waitForTimeout(forMs);
  else if (flag("--send")) {
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length > 0, null, { timeout: 30000 });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 300000 });
    await page.waitForTimeout(1800);
  } else await page.waitForTimeout(2000);
  await client.send("Page.stopScreencast");
  const first = frames[0]?.at ?? 0;
  const after = Number(option("--after", "0"));
  const kept = frames.filter((frame) => frame.at - first >= after);
  let worstGap = 0;
  for (let index = 1; index < kept.length; index += 1) worstGap = Math.max(worstGap, kept[index].at - kept[index - 1].at);
  for (const frame of kept) fs.writeFileSync(`${outDir}/frame-${String(Math.round(frame.at - first)).padStart(6, "0")}.jpg`, Buffer.from(frame.data, "base64"));
  console.log(`${kept.length} frames over ${Math.round((kept.at(-1)?.at ?? 0) - (kept[0]?.at ?? 0))}ms, longest gap ${Math.round(worstGap)}ms (sent at ${Date.now() - sentAt}ms ago)`);
} finally { await browser.close(); }
