// The changed-files card under a finished turn, in the real transcript: the live-app fixture with ?changes=1 holds five
// completed turns that changed files (one created file, a few with a page to preview, eleven, a deletion, two files of one
// name in long folders, three created files), with the diffs in the runtime's own shape, and a live run (?scenario=code) ends in the card unfolding.
//
//   LOOM_PLAYWRIGHT_MODULE=file:///.../playwright-core/index.mjs LOOM_CHROMIUM_PATH=.../chrome.exe \
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/turn-changes-browser.mjs
//   (LOOM_TURN_CHANGES_SCREENSHOTS=<dir> keeps a picture of every card in both themes)
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const screenshots = process.env.LOOM_TURN_CHANGES_SCREENSHOTS;
if (screenshots) await mkdir(screenshots, { recursive: true });

const channel = (rgb) => rgb.match(/[\d.]+/g).slice(0, 3).map(Number)
  .map((value) => value / 255).map((value) => (value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4))
  .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
const contrast = (a, b) => (Math.max(channel(a), channel(b)) + .05) / (Math.min(channel(a), channel(b)) + .05);
const plain = (value) => value.replace(/\u200e/g, "").replace(/\s+/g, " ").trim();

async function open(url, size = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport: size, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    // What the card asks of the app, caught before the app hears it (the review pane and the artifact dock are not under test).
    window.__asked = [];
    for (const name of ["loom:review-open", "loom:artifact-preview-open"]) {
      window.addEventListener(name, (event) => { window.__asked.push({ name, detail: event.detail }); event.stopImmediatePropagation(); }, true);
    }
  });
  await page.goto(`${origin}/scripts/fixtures/live-app.html?${url}`);
  return { page, context, errors };
}

const card = (page, turn) => page.locator(`section.turn-block[data-turn-id="turn-change-${turn}"] .tc`);
const asked = (page) => page.evaluate(() => window.__asked.splice(0));
async function reveal(page, turn) {
  const target = card(page, turn);
  await target.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await page.mouse.move(2, 2);
  await page.waitForTimeout(220);
  return target;
}
async function ready(page) {
  await page.locator('section.turn-block[data-turn-id="turn-change-6"] .tc').waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
}

try {
  // -- The cards, in the language of the app and in both themes ---------------------------------------------------
  for (const theme of ["dark", "light"]) {
    const { page, context, errors } = await open(`changes=1&theme=${theme}`);
    await ready(page);
    const composer = await page.locator(".composer").boundingBox();

    // One file: a single row of header, not a card of three rows.
    const one = await reveal(page, 1);
    assert.equal(await one.getAttribute("data-single"), "true");
    assert.equal(await one.locator(".tc-body, .tc-file").count(), 0, `one file has no list (${theme})`);
    const oneBox = await one.boundingBox();
    assert.ok(oneBox.height <= 84, `one edited file takes one row, not three (${oneBox.height}px, ${theme})`);
    assert.ok(Math.abs(oneBox.x - composer.x) <= 1 && Math.abs(oneBox.width - composer.width) <= 1, "the card shares the composer's edges");
    assert.equal(plain(await one.locator(".tc-line").innerText()), "已创建 anew.js", "a file the turn made is created, not edited");
    assert.equal(plain(await one.locator(".tc-dir").innerText()), ".loom/tmp", "the folder says where, the file is not named twice");
    assert.equal(plain(await one.locator(".tc-figures").innerText()), "+79");
    assert.equal(await one.locator(".tc-del").count(), 0, "a side with nothing in it is not drawn");
    assert.equal(await one.locator(".tc-preview").count(), 1);
    assert.equal(plain(await one.locator(".tc-preview").innerText()), "预览", "a lone file is not named again on its button");
    assert.equal(plain(await one.locator(".tc-review").innerText()), "审查");
    assert.equal(await one.locator("[title]").count(), 1, "only the folder carries a tooltip, the header and the buttons do not");

    // Several files: the biggest change first, totals, the page to preview.
    const several = await reveal(page, 2);
    assert.equal(await several.getAttribute("data-single"), null);
    assert.equal(plain(await several.locator(".tc-line").innerText()), "已修改 5 个文件");
    assert.deepEqual(await several.locator(".tc-file-name").allInnerTexts(), ["preview.html", "Register.tsx", "validators.ts", "Login.tsx", "package.json"]);
    assert.deepEqual((await several.locator(".tc-file-dir").allInnerTexts()).map(plain), ["docs", "src/pages", "src/utils", "src/pages", ""]);
    assert.equal(plain(await several.locator(".tc-figures").innerText()), "+56 -11");
    // Every file has its own numbers: the runtime's diff has no `diff --git` line, and splitting on one put them all on the first file.
    assert.deepEqual((await several.locator(".tc-file .tc-stats").allInnerTexts()).map(plain), ["+42", "+4 -9", "+6", "+3 -1", "+1 -1"]);
    assert.equal(plain(await several.locator(".tc-preview").innerText()), "预览 preview.html", "which file a preview opens is said when there is a choice");
    // Every fact is drawn once: the figures are the size of the change, so there is no bar repeating them, no tile
    // repeating the verb and no glyph repeating a name, and the two actions are words without an outline.
    assert.equal(await several.locator(".tc-bar, .tc-bar-fill, .tc-mark, .tc-file-glyph").count(), 0, `no bars, tile or row glyphs (${theme})`);
    const actionBorders = await several.locator(".tc-action").evaluateAll((buttons) => buttons.map((button) => getComputedStyle(button).borderTopWidth));
    assert.deepEqual([...new Set(actionBorders)], ["0px"], `the actions have no outline (${theme})`);
    const rows = await several.locator(".tc-file").evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect().height));
    assert.ok(rows.every((height) => height >= 32), "rows are a comfortable target");
    const title = await several.locator(".tc-title").boundingBox();
    const name = await several.locator(".tc-file-name").first().boundingBox();
    assert.ok(Math.abs(title.x - name.x) <= 1, "file names start where the title does");
    const review = await several.locator(".tc-review").boundingBox();
    const figure = await several.locator(".tc-file .tc-stats").first().boundingBox();
    assert.ok(Math.abs((review.x + review.width - 10) - (figure.x + figure.width)) <= 2, `the review's words end where the rows' figures end (${review.x + review.width - 10} vs ${figure.x + figure.width})`);

    // Eleven files: six of the biggest, the rest behind one row that unfolds in place.
    const long = await reveal(page, 3);
    const before = (await long.boundingBox()).height;
    assert.equal(await long.locator(".tc-body > .tc-files .tc-file").count(), 6);
    assert.equal(await long.locator(".tc-more .tc-file").count(), 5);
    assert.equal(await long.locator(".tc-more").evaluate((element) => element.inert), true, "what is folded is out of reach of the keyboard and screen readers");
    const toggle = long.locator(".tc-toggle");
    assert.equal(plain(await toggle.innerText()), "展开其余 5 个文件");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    await toggle.click();
    await page.waitForTimeout(500);
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    assert.equal(plain(await toggle.innerText()), "收起");
    assert.equal(await long.locator(".tc-more").evaluate((element) => element.inert), false);
    const after = (await long.boundingBox()).height;
    assert.ok(after > before + 5 * 30, `the rest unfolds in place (${before} -> ${after})`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await toggle.click();
    await page.waitForTimeout(500);
    assert.equal(await long.locator(".tc-more").evaluate((element) => element.inert), true);
    assert.ok(Math.abs((await long.boundingBox()).height - before) <= 1, "and folds back to where it was");

    // A deletion with nothing to preview: red only, no preview button.
    const gone = await reveal(page, 4);
    assert.equal(await gone.locator(".tc-preview").count(), 0, "a file that cannot be rendered offers no preview");
    assert.equal(plain(await gone.locator(".tc-figures").innerText()), "-140");
    assert.equal(plain(await gone.locator(".tc-line").innerText()), "已删除 parser.cfg");
    assert.equal(await gone.locator(".tc-add").count(), 0);

    // Files the turn only made: "Created 3 files", the last of them to preview.
    const made = await reveal(page, 6);
    assert.equal(plain(await made.locator(".tc-line").innerText()), "已创建 3 个文件");
    assert.deepEqual(await made.locator(".tc-file-name").allInnerTexts(), ["dxf.ts", "dxf.test.ts", "index.ts"]);
    assert.deepEqual((await made.locator(".tc-file-dir").allInnerTexts()).map(plain), ["src/features/export", "src/features/export", "src/features/export"]);
    assert.equal(plain(await made.locator(".tc-figures").innerText()), "+98");
    assert.equal(plain(await made.locator(".tc-preview").innerText()), "预览 index.ts");

    // Two files of one name: the folder is cut from its left, so what tells them apart survives.
    const twins = await reveal(page, 5);
    const dirs = twins.locator(".tc-file-dir");
    assert.equal(await dirs.first().evaluate((element) => getComputedStyle(element).direction), "rtl");
    assert.equal(await dirs.first().evaluate((element) => getComputedStyle(element).textOverflow), "ellipsis");
    assert.deepEqual((await dirs.allInnerTexts()).map(plain), [
      "services/account-gateway/config/environments/production",
      "services/account-gateway/config/environments/staging",
    ]);

    // Type floor and contrast, on the surface the card really sits on.
    for (const turn of [1, 2, 3, 4, 5, 6]) {
      const target = await reveal(page, turn);
      const found = await target.evaluate((element) => {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let smallest = Infinity;
        let where = "";
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const parent = node.parentElement;
          if (!node.textContent.trim() || !parent.getClientRects().length) continue;
          const size = parseFloat(getComputedStyle(parent).fontSize);
          if (size < smallest) { smallest = size; where = `${parent.className}: ${node.textContent.trim().slice(0, 24)}`; }
        }
        return { smallest, where };
      });
      assert.ok(found.smallest >= 11.5, `nothing in the card is below the type floor (${found.smallest}px, ${found.where})`);
    }
    const colors = await several.evaluate((element) => {
      const color = (selector) => getComputedStyle(element.querySelector(selector)).color;
      return { surface: getComputedStyle(element).backgroundColor, name: color(".tc-file-name"), folder: color(".tc-file-dir"),
        lead: color(".tc-lead"), subject: color(".tc-subject"), action: color(".tc-review") };
    });
    for (const [part, value] of Object.entries(colors)) {
      if (part === "surface") continue;
      assert.ok(contrast(value, colors.surface) >= 4.5, `${part} reads on the card (${theme}): ${value} on ${colors.surface}`);
    }

    if (screenshots) for (const turn of [1, 2, 3, 4, 5]) await (await reveal(page, turn)).screenshot({ path: `${screenshots}/turn-changes-${theme}-${turn}.png` });
    assert.deepEqual(errors, []);
    await context.close();
  }

  // -- What a click asks of the app ------------------------------------------------------------------------------
  {
    const { page, context, errors } = await open("changes=1&theme=dark");
    await ready(page);
    const several = await reveal(page, 2);
    // Anywhere on the header opens the review, the figures and the empty part included (the button is only its left half).
    const figures = await several.locator(".tc-figures").boundingBox();
    await page.mouse.click(figures.x + figures.width / 2, figures.y + figures.height / 2);
    assert.deepEqual(await asked(page), [{ name: "loom:review-open", detail: { path: "docs/preview.html" } }]);
    await several.locator(".tc-review").click();
    assert.deepEqual(await asked(page), [{ name: "loom:review-open", detail: { path: "docs/preview.html" } }]);
    await several.locator(".tc-preview").click();
    assert.deepEqual(await asked(page), [{ name: "loom:artifact-preview-open", detail: { path: "docs/preview.html", workspace: "C:/Workspace/Loom" } }]);
    await several.locator(".tc-file").nth(1).click();
    assert.deepEqual(await asked(page), [{ name: "loom:review-open", detail: { path: "src/pages/Register.tsx" } }]);
    // The keyboard reaches the same places, and says where it is.
    await several.locator(".tc-open").focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    assert.equal(await several.locator(".tc-open").evaluate((element) => getComputedStyle(element, "::after").outlineStyle), "solid", "the focus ring surrounds the whole header");
    await page.keyboard.press("Enter");
    assert.deepEqual(await asked(page), [{ name: "loom:review-open", detail: { path: "docs/preview.html" } }]);
    // The header answers a pointer anywhere on it; the buttons answer for themselves.
    await page.mouse.move(figures.x + figures.width / 2, figures.y + figures.height / 2);
    await page.waitForTimeout(260);
    assert.notEqual(await several.locator(".tc-open").evaluate((element) => getComputedStyle(element, "::after").backgroundColor), "rgba(0, 0, 0, 0)");
    const long = await reveal(page, 3);
    await long.locator(".tc-toggle").click();
    assert.deepEqual(await asked(page), [], "unfolding the list asks nothing of the app");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // -- Narrow: the review pane opens beside the conversation, and the card is measured, not the window -------------
  {
    const { page, context, errors } = await open("changes=1&theme=dark");
    await ready(page);
    // The card is measured inside its 1px border: a slot of 642px is a 640px card, the first that fits one row.
    for (const width of [860, 642, 641, 462, 461, 375, 300]) {
      const inner = width - 2;
      await page.evaluate((value) => document.querySelectorAll(".turn-artifacts-slot").forEach((slot) => { slot.style.width = `${value}px`; }), width);
      await page.waitForTimeout(250);
      for (const turn of [1, 2, 3, 5, 6]) {
        const target = await reveal(page, turn);
        const box = await target.boundingBox();
        const label = `turn ${turn} at ${width}px`;
        assert.ok(Math.abs(box.width - width) <= 1.5, `${label}: the card takes the width it is given (${box.width})`);
        assert.equal(await target.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), true, `${label}: nothing runs past the card`);
        const parts = {};
        for (const part of ["open", "figures", "actions"]) parts[part] = await target.locator(`.tc-head > .tc-${part}`).boundingBox();
        const apart = (a, b) => a.x + a.width <= b.x + .5 || b.x + b.width <= a.x + .5 || a.y + a.height <= b.y + .5 || b.y + b.height <= a.y + .5;
        assert.ok(apart(parts.open, parts.figures), `${label}: the title and the figures do not overlap`);
        assert.ok(apart(parts.figures, parts.actions), `${label}: the figures and the actions do not overlap`);
        assert.ok(apart(parts.open, parts.actions), `${label}: the title and the actions do not overlap`);
        for (const part of Object.values(parts)) assert.ok(part.x >= box.x - .5 && part.x + part.width <= box.x + box.width + .5, `${label}: the header stays inside the card`);
        if (inner >= 640) assert.ok(parts.actions.y < parts.open.y + parts.open.height, `${label}: one row`);
        else assert.ok(parts.actions.y >= parts.open.y + parts.open.height - 1, `${label}: the actions drop under the title`);
        if (turn !== 1) {
          const rowDir = await target.locator(".tc-file-dir").first().evaluate((element) => getComputedStyle(element).display);
          assert.equal(rowDir === "none", inner < 460, `${label}: the rows give up their folder first`);
        }
        for (const row of await target.locator(".tc-file").evaluateAll((buttons) => buttons.map((button) => [button.scrollWidth, button.clientWidth]))) {
          assert.ok(row[0] <= row[1] + 1, `${label}: a row does not run past itself`);
        }
      }
    }
    assert.deepEqual(errors, []);
    await context.close();
  }

  // -- English -----------------------------------------------------------------------------------------------------
  {
    const { page, context, errors } = await open("changes=1&theme=dark&lang=en");
    await ready(page);
    const one = await reveal(page, 1);
    assert.equal(plain(await one.locator(".tc-line").innerText()), "Created anew.js");
    assert.equal(plain(await one.locator(".tc-preview").innerText()), "Preview");
    assert.equal(plain(await one.locator(".tc-review").innerText()), "Review");
    const long = await reveal(page, 3);
    assert.equal(plain(await long.locator(".tc-line").innerText()), "Changed 11 files");
    assert.equal(plain(await long.locator(".tc-toggle").innerText()), "Show 5 more files");
    assert.equal(plain(await long.locator(".tc-preview").innerText()), "Preview icon-motion.css");
    assert.equal(plain(await (await reveal(page, 4)).locator(".tc-line").innerText()), "Deleted parser.cfg");
    assert.equal(plain(await (await reveal(page, 6)).locator(".tc-line").innerText()), "Created 3 files");
    assert.equal(plain(await (await reveal(page, 2)).locator(".tc-line").innerText()), "Changed 5 files");
    assert.deepEqual(errors, []);
    await context.close();
  }

  // -- A live turn completes: the card unfolds into the room the fold gives it ---------------------------------------
  {
    const { page, context, errors } = await open("theme=dark&scenario=code&speed=4");
    await page.locator(".composer textarea:not([disabled])").waitFor({ timeout: 20000 });
    await page.evaluate(() => window.liveFixture.send());
    await page.locator('section.turn-block[data-turn-id^="turn-live"] .tc').waitFor({ timeout: 90000 });
    await page.waitForFunction(() => !document.querySelector('section.turn-block[data-turn-id^="turn-live"].is-settling'), null, { timeout: 5000 });
    const live = page.locator('section.turn-block[data-turn-id^="turn-live"] .tc');
    assert.deepEqual(await live.locator(".tc-file-name").allInnerTexts(), ["Login.tsx", "validators.ts"]);
    assert.equal(await live.locator(".tc-bar").count(), 0, "a card that has just unfolded is the same card as one read from history");
    assert.deepEqual(errors, []);
    await context.close();
  }

  console.log("The changed-files card: one row for one file, the biggest change first, every fact drawn once, a long list folded in place, both themes and languages, narrow widths beside the review pane, clicks and keyboard, and the unfold at completion passed.");
} finally { await browser.close(); }
