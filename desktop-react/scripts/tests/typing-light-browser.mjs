// The edge of a streaming answer glows (useTypingLight): three highlight bands over the last text, no element in the
// text, put out one after another when nothing new arrives, gone with the message, absent under reduced motion.
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = process.env.LOOM_TEST_START_SERVER
  ? await (await import("vite")).createServer({ server: { port: 0 } }) : null;
await server?.listen();
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });
try {
  const origin = (process.env.LOOM_TEST_ORIGIN || server?.resolvedUrls.local[0] || "http://127.0.0.1:5198/").replace(/\/$/, "");
  const errors = [];
  const open = async (query) => {
    const page = await browser.newPage({ viewport: { width: 935, height: 731 } });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/live-app.html?${query}`);
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
    await page.waitForTimeout(800);
    return page;
  };
  const state = (page) => page.evaluate(() => {
    const body = document.querySelector(".turn-block.is-active .markdown-body.is-receiving");
    const read = (name) => {
      const highlight = CSS.highlights.get(name);
      const range = highlight ? [...highlight][0] : null;
      return range ? { text: range.toString(), connected: range.startContainer.isConnected } : null;
    };
    return {
      bands: [read("loom-type-0"), read("loom-type-1"), read("loom-type-2")],
      tail: document.querySelectorAll(".stream-text-tail").length,
      body: body ? body.textContent : "",
    };
  });

  const page = await open("scenario=long&cadence=real&speed=2");
  await page.evaluate(() => window.liveFixture.send());
  await page.waitForFunction(() => document.querySelector(".turn-block.is-active .markdown-body.is-receiving"), null, { timeout: 30000 });
  await page.waitForTimeout(2500);
  // While it is typing: the bands are the newest letters, newest first, and the text is plain text.
  let sampled = 0;
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline && sampled < 10) {
    const now = await state(page);
    if (!now.bands[0]) { await page.waitForTimeout(30); continue; }
    sampled += 1;
    const [edge, middle, rear] = now.bands;
    assert.ok(edge.connected && edge.text.length >= 1 && edge.text.length <= 2 * 2, `the edge is the newest letters: "${edge.text}"`);
    if (middle) assert.ok(middle.text.length >= 1 && middle.text.length <= 3 * 2, `then the next ones: "${middle.text}"`);
    if (rear) assert.ok(rear.text.length <= 4 * 2);
    const text = now.body.replace(/s+/g, "");
    const together = `${rear?.text ?? ""}${middle?.text ?? ""}${edge.text}`.replace(/s+/g, "");
    assert.ok(text.includes(together), "the bands lie over the end of the text");
    assert.equal(now.tail, 0, "no element is put into the text to make the effect");
    await page.waitForTimeout(60);
  }
  assert.ok(sampled >= 5, `the edge was lit while typing (${sampled} samples)`);

  // When the answer stops (the turn is over), the glow cools in steps and is gone.
  await page.evaluate(() => window.liveFixture.interrupt());
  await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 20000 });
  await page.waitForTimeout(900);
  const after = await state(page);
  assert.deepEqual(after.bands, [null, null, null], "every band is out once the message is over");
  await page.close();

  // Reduced motion: nothing glows.
  const reduced = await open("scenario=long&cadence=real&speed=2&reduced=1");
  await reduced.evaluate(() => window.liveFixture.send());
  await reduced.waitForFunction(() => document.querySelector(".turn-block.is-active .markdown-body.is-receiving"), null, { timeout: 30000 });
  await reduced.waitForTimeout(2500);
  assert.deepEqual((await state(reduced)).bands, [null, null, null], "reduced motion draws no glow");
  await reduced.close();

  assert.deepEqual(errors, []);
  console.log("PASS: the typing edge glows in three bands over plain text, cools when the message ends, and stays dark under reduced motion");
} finally {
  await browser.close();
  await server?.close();
}
