/**
 * Regenerates design/websearch-settings-preview.html.
 *
 * The review page has to show SettingsWebSearchPanel against production
 * styling, so the CSS is assembled from the real source files instead of being
 * hand-copied. Three details need care:
 *
 * 1. Load order. Vite emits component CSS in module-evaluation order.
 *    SettingsPage.tsx imports the panel (line 54) *before* its own stylesheets
 *    (line 55+), and main.tsx imports App (line 3) before its own CSS (line 9+),
 *    so the settings family lands before styles.css and theme.css lands last.
 *    The list below reproduces that order; it is what decides equal-specificity
 *    conflicts.
 *
 * 2. Theme scoping. The app puts the theme on <html>. This page shows both
 *    themes side by side, so `html[data-loom-theme="light"]` is rewritten to
 *    `[data-loom-theme="light"]` and matched by the column wrapper instead.
 *
 * 3. Bulk. styles.css and theme.css are the whole app's global layers, so only
 *    the blocks the panel can actually match are kept.
 *
 * Run from desktop-react/:
 *   node design/build-websearch-preview.mjs
 */

import { readFileSync, writeFileSync } from "node:fs";

/** Paths are relative to desktop-react/, not to this script's own directory. */
const here = (path) => new URL(`../${path}`, import.meta.url);
const read = (path) => readFileSync(here(path), "utf8");

const TARGET = here("design/websearch-settings-preview.html");
const BEGIN = "/* ===BEGIN INLINED CSS=== */";
const END = "/* ===END INLINED CSS=== */";

/**
 * Split a stylesheet into top-level blocks. Comments are stripped first so a
 * brace inside a comment cannot unbalance the depth scan.
 */
function blocks(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  let cursor = 0;
  for (;;) {
    const open = clean.indexOf("{", cursor);
    if (open === -1) break;
    let depth = 1;
    let index = open + 1;
    while (index < clean.length && depth > 0) {
      if (clean[index] === "{") depth += 1;
      else if (clean[index] === "}") depth -= 1;
      index += 1;
    }
    const selector = clean.slice(cursor, open).trim();
    if (selector) out.push({ selector, raw: clean.slice(cursor, index).trim() });
    cursor = index;
  }
  return out;
}

/** Keep only the blocks whose selector passes `keep`, preserving order. */
function subset(css, keep, label) {
  const all = blocks(css);
  const kept = all.filter((block) => keep(block.selector));
  const header = `/* ${label} — kept ${kept.length} of ${all.length} top-level blocks */`;
  return `${header}\n${kept.map((block) => block.raw).join("\n\n")}`;
}

/**
 * Token declarations live on `:root` (dark baseline) and on the theme roots.
 * This has to be an exact match: `startsWith('html[data-loom-theme=')` would
 * also swallow every descendant override such as
 * `html[data-loom-theme="light"] .settings-card`, which is most of the file.
 */
const isThemeRoot = (selector) =>
  selector === ":root" || /^html\[data-loom-theme="(?:dark|light)"\]$/.test(selector);

/** Anything the panel can match: its own classes plus the shared ones it reuses. */
const touchesSettings = (selector) => /\.(settings-|mature-|websearch-)/.test(selector);

/** Base element rules from styles.css that the panel inherits from. */
const BASE_SELECTORS = new Set([
  ":root",
  "html, body, #root",
  "body",
  "button",
  "button, input, textarea",
  "button, textarea, input",
  "::selection",
]);

const isBase = (selector) => BASE_SELECTORS.has(selector);

// Assembled in module-evaluation order. See note 1 at the top of this file.
const pieces = [
  ["components/settings-websearch.css", read("src/components/settings-websearch.css")],
  ["components/settings-page.css", read("src/components/settings-page.css")],
  ["components/settings-general-polish.css", read("src/components/settings-general-polish.css")],
  ["components/settings-maturity.css", read("src/components/settings-maturity.css")],
  ["components/settings-appearance.css", read("src/components/settings-appearance.css")],
  ["components/settings-terminal.css", read("src/components/settings-terminal.css")],
  ["styles.css", subset(read("src/styles.css"), isBase, "styles.css (tokens + base resets)")],
  ["typography-scale.css", read("src/typography-scale.css")],
  ["components/settings-simple.css", read("src/components/settings-simple.css")],
  ["components/settings-icon-alignment.css", read("src/components/settings-icon-alignment.css")],
  ["components/semantic-colors.css", read("src/components/semantic-colors.css")],
  [
    "theme.css",
    subset(read("src/theme.css"), (s) => isThemeRoot(s) || touchesSettings(s), "theme.css (tokens + settings rules)"),
  ],
  ["global-motion.css", subset(read("src/global-motion.css"), (s) => s === ":root", "global-motion.css (:root)")],
];

/** The app themes <html>; this page themes a column, so drop the `html` prefix. */
const rescope = (css) =>
  css
    .replaceAll("html[data-loom-theme=", "[data-loom-theme=")
    .replaceAll("html[data-loom-reduced-motion=", "[data-loom-reduced-motion=")
    .replaceAll("html[data-loom-density=", "[data-loom-density=");

const body = pieces
  .map(([name, css]) => `/* ===== ${name} ===== */\n${rescope(css).trim()}`)
  .join("\n\n");

const page = read("design/websearch-settings-preview.html");
const start = page.indexOf(BEGIN);
const end = page.indexOf(END);
if (start === -1 || end === -1) {
  throw new Error(`markers not found in ${TARGET.pathname}`);
}

const next = `${page.slice(0, start + BEGIN.length)}\n${body}\n${page.slice(end)}`;
writeFileSync(TARGET, next);

const kb = (n) => `${(n / 1024).toFixed(1)} kB`;
console.log(`inlined ${pieces.length} stylesheets -> ${kb(Buffer.byteLength(next))} page`);
for (const [name, css] of pieces) {
  console.log(`  ${kb(Buffer.byteLength(css)).padStart(9)}  ${name}`);
}
