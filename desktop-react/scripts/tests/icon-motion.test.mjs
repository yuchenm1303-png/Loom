import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";

/*
 * icon-motion.css moves parts of icons by name. Nothing in the toolchain notices when a
 * name drifts (a glyph is renamed, a part is dropped, lucide spells a class differently
 * from what a stylesheet guessed), and the result is a gesture that silently never plays.
 * These tests read the sources and fail on every such drift.
 */
const root = new URL("../../src/", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8").replace(/\r\n/g, "\n");
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

function stylesheets(dir = new URL("./", root), found = []) {
  for (const name of readdirSync(dir)) {
    const url = new URL(name + (statSync(new URL(name, dir)).isDirectory() ? "/" : ""), dir);
    if (name === "node_modules") continue;
    if (name.endsWith("/") || statSync(url).isDirectory()) stylesheets(url, found);
    else if (name.endsWith(".css")) found.push({ name: url.pathname.split("/src/")[1], text: strip(readFileSync(url, "utf8")) });
  }
  return found;
}

const glyphSource = read("components/icons/glyphs.tsx");
const motion = strip(read("components/icon-motion.css"));

/** name -> the JSX text of that glyph */
const glyphs = new Map();
{
  const starts = [...glyphSource.matchAll(/createIcon\("([a-z0-9-]+)"/g)];
  starts.forEach((match, index) => {
    glyphs.set(match[1], glyphSource.slice(match.index, starts[index + 1]?.index ?? glyphSource.length));
  });
}

/** [{ icon, parts: [...], selector }] for every selector in icon-motion.css that names a glyph */
function topLevelCommas(list) {
  const parts = [];
  let depth = 0, current = "";
  for (const ch of list) {
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (ch === "," && depth === 0) { parts.push(current); current = ""; } else current += ch;
  }
  parts.push(current);
  return parts;
}

function motionTargets() {
  const out = [];
  for (const [, selectorList] of motion.matchAll(/([^{}@][^{}]*)\{/g)) {
    for (const selector of topLevelCommas(selectorList)) {
      const parts = [...selector.matchAll(/\.(li-[a-z0-9-]+)/g)].map((m) => m[1]);
      for (const [, icon] of selector.matchAll(/\[data-icon="([a-z0-9-]+)"\]/g)) {
        out.push({ icon, parts, selector: selector.trim() });
      }
    }
  }
  return out;
}

test("every glyph the motion layer names exists, with the parts it names", () => {
  assert.ok(glyphs.size > 40, "glyphs.tsx should define the redrawn set");
  for (const { icon, parts, selector } of motionTargets()) {
    assert.ok(glyphs.has(icon), `icon-motion.css targets [data-icon="${icon}"], which no glyph defines: ${selector}`);
    for (const part of parts) {
      assert.match(glyphs.get(icon), new RegExp(`\\b${part}\\b`), `[data-icon="${icon}"] has no ${part}: ${selector}`);
    }
  }
});

test("every named part in a glyph is moved by something", () => {
  const moved = new Map();
  for (const { icon, parts } of motionTargets()) moved.set(icon, new Set([...(moved.get(icon) ?? []), ...parts]));
  for (const [icon, text] of glyphs) {
    for (const part of new Set([...text.matchAll(/\bli-[a-z0-9-]+\b/g)].map((m) => m[0]))) {
      assert.ok(moved.get(icon)?.has(part), `${icon}: ${part} is drawn but nothing in icon-motion.css moves it`);
    }
  }
});

test("only icon-motion.css owns the part names, the dials and the shared keyframes", () => {
  for (const sheet of stylesheets()) {
    if (sheet.name.endsWith("components/icon-motion.css")) continue;
    assert.doesNotMatch(sheet.text, /\.li-[a-z]/, `${sheet.name} styles an icon part (.li-*)`);
    const dials = sheet.text.match(/--ic-(?!t-spin\b)[a-z-]+/g);
    assert.equal(dials, null, `${sheet.name} reads or sets an icon dial: ${dials}`);
    const keyframes = [...sheet.text.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]).filter((name) => /spin$/.test(name));
    assert.deepEqual(keyframes, [], `${sheet.name} defines its own spinner keyframes; use li-spin`);
  }
});

test("a loader or a ring spinner turns at the one shared pace", () => {
  for (const sheet of stylesheets()) {
    if (sheet.name.endsWith("components/icon-motion.css")) continue;
    for (const [, value] of sheet.text.matchAll(/animation:\s*([^;]*\bspin\b[^;]*);/g)) {
      assert.match(value, /^li-spin var\(--ic-t-spin\) linear infinite$/, `${sheet.name}: ${value}`);
    }
  }
});

test("lucide class selectors name classes lucide really emits", () => {
  const require = createRequire(import.meta.url);
  const React = require("react");
  const { renderToStaticMarkup } = require("react-dom/server");
  const lucide = require("lucide-react");
  const index = read("components/icons/index.ts");
  const passthrough = [...index.matchAll(/export \{([^}]*)\} from "lucide-react"/g)]
    .flatMap((m) => m[1].split(",").map((name) => name.trim()).filter(Boolean));
  assert.ok(passthrough.length > 40);
  const emitted = new Set();
  for (const name of passthrough) {
    assert.ok(lucide[name], `icons/index.ts re-exports ${name}, which lucide-react does not have`);
    for (const cls of /class="([^"]*)"/.exec(renderToStaticMarkup(React.createElement(lucide[name])))[1].split(" ")) emitted.add(cls);
  }
  for (const sheet of stylesheets()) {
    for (const [, cls] of sheet.text.matchAll(/\.(lucide-[a-z0-9-]+)/g)) {
      assert.ok(emitted.has(cls), `${sheet.name} selects .${cls}, which no lucide icon in use emits (redrawn glyphs carry data-icon instead)`);
    }
  }
});

test("every icon imported anywhere comes from the icon module", () => {
  const files = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const url = new URL(name, dir);
      if (statSync(url).isDirectory()) { if (name !== "icons") walk(new URL(name + "/", dir)); } else if (/\.tsx?$/.test(name)) files.push({ name, text: readFileSync(url, "utf8") });
    }
  })(new URL("./", root));
  // The work log and the tool marks are drawn by weave.css and tool-identity.css, which own their motion.
  const stillLucide = new Set(["WeaveFlow.tsx", "ToolIdentity.tsx"]);
  for (const file of files) {
    if (stillLucide.has(file.name)) continue;
    assert.doesNotMatch(file.text, /from "lucide-react"/, `${file.name} imports lucide-react directly; import from ./icons`);
  }
});

test("a glyph that spins or draws on its own says so in its name", () => {
  assert.match(glyphSource, /createIcon\("loader"/);
  assert.match(motion, /\[data-icon="loader"\],\s*\[data-spinning="true"\]\s*\{\s*animation: li-spin/);
});
