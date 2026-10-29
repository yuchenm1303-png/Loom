import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/liquid-cursor.html`);
    const result = await page.evaluate(async () => {
      const { readingMount, paddedMount } = await import("/src/components/portalLiquidMount.ts");
      const host = document.createElement("div");
      host.style.cssText = "position:fixed;left:10px;top:10px;width:320px;background:#1f1e1d;font:24px Georgia;line-height:1.5";
      host.innerHTML = '<strong style="display:block;width:600px">Create.</strong><p style="width:300px">检查这段中文换行。<em>Read this carefully.</em> Another line of text.</p><div><time>09:14</time><span style="margin-left:30px">Ran the tests: 41 passed.</span></div><p style="width:900px;white-space:nowrap">Found the cause in release.py and fixed it. This is a very long timeline row.</p>';
      document.body.append(host);
      const rectOf = node => { const range = document.createRange(); range.selectNodeContents(node); return range.getClientRects()[0]; };
      const title = host.querySelector("strong"); const tr = rectOf(title);
      const titleMount = readingMount(title, tr.left + 10, tr.top + 10);
      title.dataset.liquidLens = "read";
      const titleFrame = paddedMount(title, titleMount);
      const titleBlank = readingMount(title, tr.left + 400, tr.top + 10);
      const row = host.querySelector("div"); const sr = rectOf(row.querySelector("span"));
      const time = rectOf(row.querySelector("time"));
      const rowMount = readingMount(row, sr.left + 20, sr.top + 10);
      const timeMount = readingMount(row, time.left + 5, time.top + 5);
      const para = host.querySelector("p"); const range = document.createRange(); range.selectNodeContents(para);
      const rects = Array.from(range.getClientRects()); const first = rects[0];
      const lineMount = readingMount(para, first.left + 10, first.top + 5);
      const last = host.querySelector("p:last-child"); const lr = rectOf(last);
      const longMount = readingMount(last, lr.left + 100, lr.top + 5);
      host.remove();
      return { frameWidth: titleFrame.width, frameHeight: titleFrame.height, titleWidth: titleMount?.width, titleBlank, rowWidth: rowMount?.width,
        rowLeft: rowMount?.left, textLeft: sr.left, timeMount, longMount,
        lineHeight: lineMount?.height, blockHeight: para.getBoundingClientRect().height };
    });
    assert.ok(result.titleWidth > 50 && result.titleWidth < 120, "short heading follows glyphs, not the 600px column");
    assert.ok(result.frameWidth < 180 && result.frameHeight >= 64, "short text has rim clearance without filling the column");
    assert.equal(result.titleBlank, null, "column whitespace is not a mount");
    assert.ok(result.rowWidth < 360 && Math.abs(result.rowLeft - result.textLeft) < 1);
    assert.equal(result.timeMount, null, "the timestamp must not mount the entire row");
    assert.equal(result.longMount, null, "long prose keeps the free lens instead of a ribbon");
    assert.ok(result.lineHeight > 10 && result.lineHeight < 40, "wrapped text mounts one painted line");
    console.log(`PASS ${viewport.width}px: heading, whitespace, timestamp, wrapped CJK and long rows`);
    await page.close();
  }
} finally { await browser.close(); }
