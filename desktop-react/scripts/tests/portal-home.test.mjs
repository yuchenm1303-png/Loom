import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import ts from "typescript";

// The Loom home page is set in the manner of darioamodei.com (docs/portal-home-2026-10-24.md). These tests read the
// sources, like the other portal contracts, and keep what makes it that page: the typeface and where it comes from,
// the palette and the measure, the way it follows the system, and the rules that keep its words readable.

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const portal = read("../../src/components/WebPortal.tsx");
const css = read("../../src/components/portal-editorial.css");
const dialogCss = read("../../src/components/portal-auth-dialog.css");
const fontDir = new URL("../../src/assets/fonts/newsreader/", import.meta.url);

const copySource = read("../../src/components/portalHomeCopy.ts");
const compiled = ts.transpileModule(copySource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { PORTAL_HOME_COPY } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function rule(selector) {
  const start = css.indexOf(`${selector} {`);
  assert.ok(start >= 0, `missing rule: ${selector}`);
  return css.slice(start, css.indexOf("}", start));
}

test("the home page is set in Newsreader, which ships with the app under its licence", () => {
  const faces = css.match(/@font-face\s*\{[^}]*\}/g) ?? [];
  assert.equal(faces.length, 2, "one upright and one italic face");
  for (const [style, file] of [["normal", "newsreader-latin-opsz-normal.woff2"], ["italic", "newsreader-latin-opsz-italic.woff2"]]) {
    const face = faces.find((block) => block.includes(`font-style: ${style}`));
    assert.ok(face, `${style} face is declared`);
    assert.ok(face.includes('font-family: "Newsreader"'));
    assert.ok(face.includes(`url("../assets/fonts/newsreader/${file}")`), "bundled through the build, so it is hashed and cached for good");
    assert.ok(face.includes("font-weight: 400 650"), "the weights the file carries");
    assert.ok(face.includes("font-display: swap"));
    const path = new URL(file, fontDir);
    assert.ok(existsSync(path), `${file} exists`);
    const bytes = readFileSync(path);
    assert.equal(bytes.subarray(0, 4).toString("latin1"), "wOF2", `${file} is a real WOFF2`);
    assert.ok(statSync(path).size < 90_000, `${file} stays a subset (${statSync(path).size} bytes)`);
  }
  assert.match(css, /--ed-serif:\s*"Newsreader",/, "the stack starts with the typeface");
  assert.match(css, /font-optical-sizing:\s*auto/, "the 24 pt and Display cuts are one font, picked by size");
  assert.match(read("../../src/assets/fonts/newsreader/OFL.txt"), /SIL OPEN FONT LICENSE Version 1\.1/);
  assert.ok(existsSync(new URL("README.md", fontDir)), "where the files come from is written down");
});

test("the page keeps the reference's palette and measure", () => {
  const dark = rule(".loom-portal-page.loom-editorial");
  assert.match(dark, /--ed-paper:\s*#1f1e1d/);
  assert.match(dark, /--ed-ink:\s*#f0eee6/);
  const light = rule(".loom-portal-page.loom-editorial.is-light");
  assert.match(light, /--ed-paper:\s*#f0eee6/);
  assert.match(light, /--ed-ink:\s*#1f1e1d/);
  assert.match(dark, /font-size:\s*20px/);
  assert.match(dark, /line-height:\s*1\.6/);
  assert.match(rule(".loom-portal-page.loom-editorial .editorial-shell"), /max-width:\s*620px/);
  const links = rule(".loom-editorial .editorial-shell a");
  assert.match(links, /text-decoration:\s*underline/);
  assert.match(links, /text-decoration-thickness:\s*\.08em/);
  assert.match(links, /text-underline-offset:\s*\.1em/);
  const pill = rule(".loom-editorial .editorial-theme");
  assert.match(pill, /width:\s*40px/);
  assert.match(pill, /height:\s*20px/);
  assert.match(dialogCss, /--dialog-surface:\s*#262523/, "the sign-in dialog is drawn in the same palette");
});

test("the page follows the system's light or dark setting until the visitor chooses", () => {
  assert.ok(portal.includes('window.matchMedia("(prefers-color-scheme: light)")'));
  assert.ok(portal.includes("storedEditorialTheme() ?? (prefersLightScheme()"), "a stored choice wins over the system");
  assert.ok(portal.includes("if (!storedEditorialTheme()) setLightEditorial(event.matches)"), "the system is followed while nothing is stored");
  assert.ok(portal.includes("window.localStorage.setItem(EDITORIAL_THEME_KEY"), "a choice is kept");
  assert.ok(portal.includes("aria-checked={!lightEditorial}"), "the switch is on when the page is dark, as on the reference");
  const knob = rule(".loom-editorial .editorial-theme::after");
  assert.match(knob, /transform:\s*translateX\(20px\)/, "dark: the knob is on the right");
  assert.match(css, /\.loom-editorial\.is-light \.editorial-theme::after\s*\{\s*transform:\s*translateX\(0\)/);
  // Native parts (scrollbars, fields, a phone's address bar) wear the page's colours too.
  assert.match(rule(".loom-portal-page.loom-editorial"), /color-scheme:\s*dark/);
  assert.match(rule(".loom-portal-page.loom-editorial.is-light"), /color-scheme:\s*light/);
  assert.ok(portal.includes('meta.name = "theme-color"'));
  assert.ok(portal.includes('lightEditorial ? "#f0eee6" : "#1f1e1d"'));
});

test("both languages tell the same page", () => {
  const { en, zh } = PORTAL_HOME_COPY;
  const shape = (copy) => ({
    title: copy.title.length,
    more: copy.moreParagraphs.length,
    doing: copy.doing.items.length,
    session: copy.session.rows.length,
    steps: copy.start.steps.length,
  });
  assert.deepEqual(shape(zh), shape(en));
  for (const [name, copy] of Object.entries({ en, zh })) {
    const words = [];
    const walk = (value) => (typeof value === "string" ? words.push(value) : value && Object.values(value).forEach(walk));
    walk(copy);
    assert.ok(words.every((word) => word.trim().length > 0), `${name}: no empty string`);
    for (const row of copy.session.rows) assert.equal((row.text.match(/\*/g) ?? []).length % 2, 0, `${name}: italics are closed`);
    assert.ok(copy.session.rows.length >= 3 && copy.doing.items.length === 3 && copy.start.steps.length === 3);
  }
  assert.equal(en.title.join(" "), "A quieter way to get things done.");
  assert.match(zh.title.join(""), /[一-鿿]/);
  assert.ok(en.doing.items.map((item) => item.lead).join(" ").startsWith("Explore."), "the three words of the home page stay");
  assert.ok(portal.includes("PORTAL_HOME_COPY[zh ? \"zh\" : \"en\"]"));
});

test("the page is shown once its typeface is in, and is never hidden for good", () => {
  assert.ok(portal.includes('data-type-ready={typeReady ? "true" : "false"}'));
  assert.ok(portal.includes("document.fonts.load('400 20px \"Newsreader\"')"));
  assert.ok(portal.includes("window.setTimeout(show, 1200)"), "a slow network shows the page anyway");
  // Hiding is only ever conditional: not-yet-ready for the blocks, armed-and-not-yet-seen for the thread.
  const hides = (css.match(/[^{}]+\{\s*opacity:\s*0;\s*\}/g) ?? []).filter((block) => block.trim().split(/\s*\{/)[0].trim() !== "from");
  assert.ok(hides.length >= 1);
  for (const block of hides) {
    assert.ok(/data-type-ready="false"|data-state="armed"|editorial-more-panel/.test(block), `unconditional hide: ${block.trim().slice(0, 80)}`);
  }
  assert.ok(portal.includes('typeof IntersectionObserver === "function" ? "armed" : "in"'), "without an observer the thread is simply shown");
});

test("what carries words only fades; the thread's line is the one thing drawn", () => {
  const fade = css.match(/@keyframes editorial-fade\s*\{([^]*?)\}\s*\}/)?.[0] ?? css.match(/@keyframes editorial-fade[^\n]*/)[0];
  assert.ok(!/transform|translate|scale|rotate|filter/.test(fade), "editorial-fade is opacity only");
  assert.match(css, /@keyframes editorial-thread-draw\s*\{\s*from\s*\{\s*transform:\s*scaleY\(0\)/);
  assert.ok(!/animation[^;]*(cubic-bezier|linear\()/.test(css), "curves come from the scale");
  assert.ok(!/(?:transition|animation)[^;]*\d(?:ms|s)\b(?!\s*\))/.test(css.replace(/animation-delay:[^;]*;/g, "")), "durations come from the scale");
});

test("More opens a fold that assistive technology can use", () => {
  assert.ok(portal.includes('aria-expanded={moreOpen} aria-controls="editorial-more"'));
  assert.ok(portal.includes('id="editorial-more"'));
  assert.ok(portal.includes("inert={!moreOpen}"), "closed text is out of the tab order and the reading order");
  assert.match(rule(".loom-editorial .editorial-more-panel"), /grid-template-rows:\s*0fr/);
  assert.match(css, /\.editorial-more-panel\[data-open="true"\]\s*\{\s*grid-template-rows:\s*1fr/);
  assert.match(css, /grid-template-rows var\(--loom-dur-flow\)/, "a fold takes a fold's time");
});

test("the home page stays an introduction: workspace entry, download, no composer", () => {
  const home = portal.slice(portal.indexOf('<main className="editorial-body">'), portal.indexOf('<footer className="editorial-footer'));
  assert.ok(home.includes("onClick={openWorkspace}"));
  assert.ok(home.includes("href={release.download}"));
  assert.ok(home.includes("href={release.notes}"));
  assert.ok(!/<(?:input|textarea|form)\b/.test(home));
  assert.ok(home.includes("<h1 "));
  assert.equal((home.match(/<h1\b/g) ?? []).length, 1, "one h1");
});
