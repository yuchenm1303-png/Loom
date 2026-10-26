import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";
import ts from "typescript";

// The Loom home page is set in the manner of darioamodei.com and laid out as a full page (docs/portal-home-2026-10-24.md).
// These tests read the sources, like the other portal contracts, and keep what makes it that page: the typeface and
// where it comes from, the palette and the measure of its prose, the frame and grid it is laid on, the way it follows
// the system, and the rules that keep its words readable.

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

test("the page keeps the reference's palette, type and links", () => {
  const dark = rule(".loom-portal-page.loom-editorial");
  assert.match(dark, /--ed-paper:\s*#1f1e1d/);
  assert.match(dark, /--ed-ink:\s*#f0eee6/);
  const light = rule(".loom-portal-page.loom-editorial.is-light");
  assert.match(light, /--ed-paper:\s*#f0eee6/);
  assert.match(light, /--ed-ink:\s*#1f1e1d/);
  assert.match(dark, /font-size:\s*20px/);
  assert.match(dark, /line-height:\s*1\.6/);
  const links = rule(".loom-editorial .editorial-shell a");
  assert.match(links, /text-decoration:\s*underline/);
  assert.match(links, /text-decoration-thickness:\s*\.08em/);
  assert.match(links, /text-underline-offset:\s*\.1em/);
  const pill = rule(".loom-editorial .editorial-theme");
  assert.match(pill, /width:\s*40px/);
  assert.match(pill, /height:\s*20px/);
  assert.match(dialogCss, /--dialog-surface:\s*#262523/, "the sign-in dialog is drawn in the same palette");
});

test("the page is laid on a wide frame and a 12-column grid; its prose keeps the reference's measure", () => {
  const root = rule(".loom-portal-page.loom-editorial");
  assert.match(root, /--ed-wide:\s*clamp\(1280px,\s*58vw,\s*1440px\)/, "a frame of 1280 to 1440px");
  assert.match(root, /--ed-edge:\s*max\(var\(--ed-gutter\),\s*calc\(\(100% - var\(--ed-wide\)\) \/ 2\)\)/, "never closer than the gutter to the window");
  const shell = rule(".loom-portal-page.loom-editorial .editorial-shell");
  assert.match(shell, /width:\s*100%/);
  assert.ok(!/max-width/.test(shell), "the shell spans the window; the frame is the padding of its three parts");
  assert.match(css, /\.editorial-shell > :is\(\.editorial-header, \.editorial-body, \.editorial-footer\)\s*\{[^}]*padding-inline:\s*var\(--ed-edge\)/);
  // The reference's 580px measure is still the width of every paragraph.
  assert.match(rule(".loom-editorial .editorial-intro p"), /max-width:\s*580px/);
  // Two grids of twelve columns: the hero (the words, and the picture beside them) and the sections.
  for (const selector of [".loom-editorial .editorial-hero-grid", ".loom-editorial .editorial-section"]) {
    assert.match(rule(selector), /grid-template-columns:\s*repeat\(12,\s*minmax\(0,\s*1fr\)\)/, selector);
  }
  // The type is one quiet scale: the headline is set to the five columns it sits in (72px holds in 517px, so its second
  // line is as wide as the paragraph under it), a section's heading is two thirds of that or less, an item's title 24px,
  // text 18 to 20px. A clamp is not a rung of the size ladder, so these are read as numbers.
  const title = root.match(/--ed-title:\s*clamp\((\d+)px,\s*([\d.]+)vw,\s*(\d+)px\)/);
  assert.ok(title, "the headline's size is one variable");
  const titleMax = Number(title[3]);
  assert.ok(titleMax >= 60 && titleMax <= 76, `the headline is quiet: at most ${titleMax}px`);
  const headline = rule(".loom-editorial .editorial-hero-copy");
  assert.match(headline, /font-size:\s*var\(--ed-title\)/);
  assert.match(headline, /font-weight:\s*400/);
  assert.match(headline, /line-height:\s*1\.08/, "the cap line is the top of the line box, which the picture's hairline stands on");
  const heading = rule(".loom-editorial .editorial-section h2").match(/font-size:\s*clamp\((\d+)px,\s*([\d.]+)vw,\s*(\d+)px\)/);
  assert.ok(heading && Number(heading[3]) <= titleMax * 0.66 && Number(heading[3]) >= 40, `a section's heading is smaller than the headline: ${heading?.[3]}px`);
  assert.match(rule(".loom-editorial .editorial-list strong"), /font-size:\s*24px/);
  assert.match(rule(".loom-editorial .editorial-steps strong"), /font-size:\s*24px/);
  // It narrows in steps: the picture goes under the words, the columns become one, the nav goes, the phone settles.
  for (const width of [1100, 960, 760, 520]) assert.ok(css.includes(`@media (max-width: ${width}px)`), `${width}px step`);
  assert.match(css, /@media \(max-width: 760px\)\s*\{[^@]*\.editorial-nav[^{]*\{[^}]*display:\s*none/);
});

test("the first screen is laid out from the top, so opening More moves only what is under it", () => {
  const hero = rule(".loom-editorial .editorial-hero");
  assert.ok(!/(?:display:\s*flex|justify-content)/.test(hero), "free space goes below the words, not between them");
  assert.match(hero, /min-height:\s*min\(calc\(100dvh - var\(--ed-head\)\),\s*900px\)/, "the first screen is a screen, up to a limit");
  assert.match(hero, /padding-block:\s*clamp\(40px,\s*19vh,\s*200px\)/, "the group sits where a share of the window's height puts it");
  assert.match(rule(".loom-editorial .editorial-hero-grid"), /align-items:\s*start/);
  assert.match(css, /@media \(max-width: 960px\)\s*\{[^@]*\.editorial-hero\s*\{\s*min-height:\s*0/, "stacked, it is as tall as its words");
});

test("the header is a nav: sections by scrolling, a hairline once the page has moved, the icon last", () => {
  const header = portal.slice(portal.indexOf('<header className="editorial-header'), portal.indexOf("</header>"));
  assert.ok(header.includes('data-stuck={stuck ? "true" : "false"}'));
  assert.ok(header.indexOf("editorial-brand") < header.indexOf("editorial-nav") && header.indexOf("editorial-nav") < header.indexOf("editorial-meta"));
  for (const target of ["loom-capabilities", "loom-start", "loom-download"]) {
    assert.ok(header.includes(`href="#${target}"`) && header.includes(`goToSection(event, "${target}")`), `${target} is a link that scrolls`);
    assert.ok(portal.includes(`id="${target}"`), `${target} exists`);
  }
  assert.ok(portal.includes('document.getElementById(id)?.scrollIntoView({ block: "start" })'), "no hash in the address");
  assert.ok(portal.includes('href={release.download}'), "the download section carries the installer");
  // The page is the scroller, so the hairline follows its scroll, and only when the boolean changes.
  assert.ok(portal.includes('page.addEventListener("scroll", follow, { passive: true })'));
  assert.ok(portal.includes("setStuck(page.scrollTop > 4)"));
  const head = rule(".loom-editorial .editorial-header");
  assert.match(head, /position:\s*sticky/);
  assert.match(head, /top:\s*0/);
  assert.match(head, /border-bottom:\s*1px solid transparent/);
  assert.match(css, /\.editorial-header\[data-stuck="true"\]\s*\{\s*border-bottom-color:\s*var\(--ed-rule\)/);
  // A jump lands under the sticky header, and glides unless the reader asked for less motion.
  assert.match(rule(".loom-portal-page.loom-editorial"), /scroll-padding-top:\s*calc\(var\(--ed-head\) \+ 24px\)/);
  assert.match(css, /@media \(prefers-reduced-motion: no-preference\)\s*\{\s*:root:not\(\[data-loom-reduced-motion="true"\]\) \.loom-portal-page\.loom-editorial\s*\{\s*scroll-behavior:\s*smooth/);
  assert.ok(!portal.includes("prefers-reduced-motion: reduce)\")"), "the stylesheet decides; no second copy of the question (test_motion_scale_contract)");
});

test("the header offers a visible account entry, a language switch and a mobile menu", () => {
  const header = portal.slice(portal.indexOf('<header className="editorial-header'), portal.indexOf("</header>"));
  assert.ok(header.includes('className="editorial-account"') && header.includes("onClick={openEditorialModal}"), "sign-in is a button, not a line of text");
  assert.ok(header.includes('className="editorial-account-avatar"'), "a signed-in visitor gets an avatar and a name");
  assert.ok(header.includes('className="editorial-language"') && header.includes('onClick={toggleLanguage}'), "the page can change language from its header");
  assert.ok(portal.includes('setLanguage(zh ? "en" : "zh-CN")'), "the switch uses the shared language setting");
  assert.ok(header.includes('className="editorial-menu-button"') && header.includes('aria-controls="editorial-mobile-menu"'), "the narrow header has a menu button");
  assert.ok(portal.includes('id="editorial-mobile-menu"') && portal.includes('aria-expanded={menuOpen}'), "the menu panel exists and reports its state");
  assert.ok(portal.includes('window.matchMedia("(min-width: 761px)")'), "the menu closes when the desktop header returns");
  assert.match(rule(".loom-editorial .editorial-account"), /border:\s*1px solid var\(--ed-rule\)/);
  assert.match(rule(".loom-editorial .editorial-menu-button"), /display:\s*none/);
  assert.match(css, /@media \(max-width: 760px\)\s*\{[^@]*\.editorial-menu-button\s*\{\s*display:\s*grid/);
  // The house mark's base rule has three classes; the phone rule must match its specificity, or it never hides.
  assert.match(css, /@media \(max-width: 430px\)\s*\{[^@]*\.loom-editorial \.editorial-shell \.editorial-smirel-mark[^{]*\{[^}]*display:\s*none/);
});

test("the download section names the release, and the footer points at the project", () => {
  const section = portal.slice(portal.indexOf('<section id="loom-download"'), portal.indexOf('<section id="loom-start"'));
  for (const part of ["{home.downloadSection.heading}", "{home.downloadSection.lead}", "{home.downloadSection.platform}", "{release.version", "{release.package", "href={release.download}", "href={release.notes}"]) {
    assert.ok(section.includes(part), part);
  }
  assert.ok(portal.includes('href={`${RELEASES_URL}#readme`}'), "documentation points at the repository readme");
  assert.ok(portal.includes("href={RELEASES_URL}") && portal.includes("{home.links.github}"), "the footer points at the repository");
  assert.ok(portal.includes('id="loom-download"') && portal.includes('goToSection(event, "loom-download")'), "the nav's Download reaches the section");
  const panel = rule(".loom-editorial .editorial-download-panel");
  assert.match(panel, /border-top:\s*1px solid var\(--ed-rule\)/, "a hairline, like every block");
  // The installer is a link, so it needs its own foreground: the global link colour would paint it ink on ink.
  assert.match(rule(".loom-editorial .editorial-download-panel .editorial-primary"), /color:\s*var\(--ed-paper\)/);
});

test("the one picture is a task, start to finish, beside the words and opened by a hairline", () => {
  const grid = portal.slice(portal.indexOf('<div className="editorial-hero-grid">'), portal.indexOf('<section id="loom-capabilities"'));
  // One column of words (the name, the sentence, where to start), then the picture.
  assert.ok(grid.indexOf("editorial-hero-text") < grid.indexOf("<h1 ") && grid.indexOf("<h1 ") < grid.indexOf("editorial-intro") && grid.indexOf("editorial-intro") < grid.indexOf("editorial-actions"));
  assert.ok(grid.indexOf("editorial-release") < grid.indexOf("editorial-session"), "the words first, the picture after");
  for (const part of ["{home.session.heading}", "{home.session.ask}", "{home.session.note}", "data-state={threadState}", "aria-label={home.session.label}"]) {
    assert.ok(grid.includes(part), part);
  }
  assert.ok(grid.includes('index === home.session.rows.length - 1 ? "is-waiting"'), "the last step is the one waiting for you");
  assert.equal((portal.match(/<figure\b/g) ?? []).length, 1);
  assert.equal((grid.match(/<h1\b/g) ?? []).length, 1);
  assert.match(rule(".loom-editorial .editorial-thread li.is-waiting::before"), /background:\s*var\(--ed-accent\)/);
  // No box: like the items below it, the picture is opened by one hairline, and that hairline is level with the headline's cap line.
  const picture = rule(".loom-editorial .editorial-session");
  assert.match(picture, /border-top:\s*1px solid var\(--ed-rule\)/);
  assert.match(picture, /margin:\s*calc\(var\(--ed-title\) \* \.04\) 0 0;/, "level with the cap line: half the leading below the top of the headline's box");
  assert.ok(!/(?:background|border-radius|box-shadow|border:)/.test(picture), "a hairline, not a card");
  assert.match(rule(".loom-editorial .editorial-thread li::before"), /background:\s*var\(--ed-paper\)/, "the knots are cut out of the page, not of a card");
  // The picture is drawn when it is in view, a little after the card itself has faded in.
  assert.match(css, /\.editorial-thread\[data-state="in"\] li,[^{]*\{[^}]*animation-delay:\s*calc\(360ms \+ var\(--i, 0\) \* 180ms\)/);
});

test("below the first screen each block is shown as it comes into view, and every one is shown in the end", () => {
  assert.ok(portal.includes('page.querySelectorAll<HTMLElement>("[data-reveal]:not([data-seen])")'), "blocks already seen are not watched again");
  assert.ok(portal.includes("}, [editorial, typeReady, zh]);"), "a change of language makes new blocks, which are watched");
  assert.ok(portal.includes("(entry.target as HTMLElement).dataset.seen = \"true\";") && portal.includes("observer.unobserve(entry.target)"), "seen once, for good");
  assert.ok(/typeof IntersectionObserver !== "function"\) \{\n\s*blocks\.forEach\(\(block\) => \{ block\.dataset\.seen = "true"; \}\);/.test(portal), "without an observer everything is shown");
  assert.ok(portal.includes("if (!editorial || !typeReady || !page) return;"), "nothing is hidden before the page itself is shown");
  assert.ok((portal.match(/data-reveal/g) ?? []).length >= 4, "headings, items and steps carry the mark");
  // With motion reduced, nothing waits to be seen and nothing is drawn.
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{[^@]*\[data-reveal\]:not\(\[data-seen\]\)\s*\{\s*opacity:\s*1;/);
  assert.match(css, /@keyframes editorial-rule-draw\s*\{\s*from\s*\{\s*transform:\s*scaleX\(0\)/, "the hairline of an item is the only thing drawn");
});

test("the glass cursor can snap to every control, and what it redraws is made of elements", () => {
  const header = portal.slice(portal.indexOf('<header className="editorial-header'), portal.indexOf("</header>"));
  // the house mark, brand, three nav links, language, account, theme switch, menu, icon: the lens samples buttons and links, text, plain fills and borders.
  assert.equal((header.match(/data-liquid-snap="true"/g) ?? []).length, 10);
  assert.equal((header.match(/data-magnetic-hover="true"/g) ?? []).length, 9, "the theme switch stays put");
  // Numbers are elements (a pseudo-element is not redrawn into the lens).
  assert.ok(portal.includes('<span className="editorial-index" aria-hidden="true">{`0${index + 1}`}</span>'));
  assert.ok(portal.includes('<span className="editorial-index" aria-hidden="true">{index + 1}</span>'));
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
    nav: Object.keys(copy.nav).sort().join(),
    downloadSection: Object.keys(copy.downloadSection).sort().join(),
    links: Object.keys(copy.links).sort().join(),
  });
  assert.deepEqual(shape(zh), shape(en));
  for (const [name, copy] of Object.entries({ en, zh })) {
    const words = [];
    const walk = (value) => (typeof value === "string" ? words.push(value) : value && Object.values(value).forEach(walk));
    walk(copy);
    assert.ok(words.every((word) => word.trim().length > 0), `${name}: no empty string`);
    for (const row of copy.session.rows) assert.equal((row.text.match(/\*/g) ?? []).length % 2, 0, `${name}: italics are closed`);
    assert.ok(copy.session.rows.length >= 3 && copy.doing.items.length === 3 && copy.start.steps.length === 3);
    // What the card says was asked is in quotation marks, and says something; the nav names the two sections and the installer.
    assert.match(copy.session.ask, /^[“"].+[”"]$/, `${name}: the request is quoted`);
    assert.ok(copy.start.lead.length > 10, `${name}: getting started has a lead`);
    assert.ok(copy.downloadSection.heading.length > 0 && copy.downloadSection.lead.length > 10, `${name}: the download section has words`);
    for (const key of ["label", "doing", "start", "download"]) assert.ok(copy.nav[key].length > 0, `${name}: nav.${key}`);
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
  // Hiding is only ever conditional: not-yet-ready for the first screen, armed-and-not-yet-seen for the thread, and
  // not-yet-seen for the blocks below it.
  const hides = (css.match(/[^{}]+\{\s*opacity:\s*0;\s*\}/g) ?? []).filter((block) => block.trim().split(/\s*\{/)[0].trim() !== "from");
  assert.ok(hides.length >= 1);
  for (const block of hides) {
    assert.ok(/data-type-ready="false"|data-state="armed"|editorial-more-panel|\[data-reveal\]:not\(\[data-seen\]\)/.test(block), `unconditional hide: ${block.trim().slice(0, 80)}`);
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
  // The ellipsis and More are bound to the last word by no-break spaces, so they never wait alone on a new line.
  assert.ok(portal.includes('<span aria-hidden="true">{"\\u00a0…"}</span>}\n                      {"\\u00a0"}\n'));
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
