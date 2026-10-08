// `:has()` is expensive for the whole chat surface, not just where it is used.
//
// Whenever React inserts or removes a node, Blink re-evaluates `:has()` on the
// ancestors that some rule uses as an anchor (`.workspace`, `.turn-block`, ...)
// and applies the descendant parts of the document's `:has()` rules to their
// subtrees. A rule such as `.settings-content:has(.x) > .heading > div` therefore
// makes every `div` under `.workspace` restyle on each streamed delta, even
// though the Settings page is not open. Measured on a long live turn this made
// style recalculation grow ~5x with turn length; removing the bare-tag rules
// brought it back to roughly linear.
//
// Keep the rightmost compound of every `:has()` selector keyed on a class, id
// or attribute. State that depends on page or content (`data-settings-page`,
// `.has-capability-list`) should be set by the component instead.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../../src", import.meta.url));

function* cssFiles(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* cssFiles(path);
    else if (name.endsWith(".css")) yield path;
  }
}

/** Every style-rule prelude (selector list) in a stylesheet, descending into at-rules. */
function* preludes(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "{") {
      const prelude = text.slice(start, index).trim();
      if (prelude && !prelude.startsWith("@")) yield prelude;
      start = index + 1;
    } else if (char === "}" || char === ";") {
      start = index + 1;
    }
  }
}

function splitSelectorList(list) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const char of list) {
    if (char === "(" || char === "[") depth += 1;
    if (char === ")" || char === "]") depth -= 1;
    if (char === "," && depth === 0) {
      parts.push(current.trim());
      current = "";
    } else current += char;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

function lastCompound(selector) {
  let depth = 0;
  for (let index = selector.length - 1; index >= 0; index -= 1) {
    const char = selector[index];
    if (char === ")" || char === "]") depth += 1;
    else if (char === "(" || char === "[") depth -= 1;
    else if (depth === 0 && /[\s>+~]/.test(char)) return selector.slice(index + 1).trim();
  }
  return selector.trim();
}

// A type selector or `*`, optionally followed only by pseudo-classes/elements.
const BARE_COMPOUND = /^(?:[a-z][a-z0-9-]*|\*)(?::[a-z-]+(?:\([^)]*\))?)*(?:::[a-z-]+)?$/i;

export function bareTagHasSelectors(css) {
  const found = [];
  for (const prelude of preludes(css)) {
    for (const selector of splitSelectorList(prelude)) {
      if (selector.includes(":has(") && BARE_COMPOUND.test(lastCompound(selector))) found.push(selector);
    }
  }
  return found;
}

test("the detector flags tag-keyed :has() selectors and accepts class-keyed ones", () => {
  assert.deepEqual(bareTagHasSelectors(".page:has(.card) > .heading > div { color: red }"), [".page:has(.card) > .heading > div"]);
  assert.deepEqual(bareTagHasSelectors(".page:has(.card) .heading h2:first-child, .ok { color: red }"), [".page:has(.card) .heading h2:first-child"]);
  assert.deepEqual(bareTagHasSelectors("@media (min-width: 1px) { .row:has(> svg) * { color: red } }"), [".row:has(> svg) *"]);
  assert.deepEqual(bareTagHasSelectors(".row:has(> svg)::before { content: none } .page:has(.card) .heading { top: 0 }"), []);
  assert.deepEqual(bareTagHasSelectors("div > p { color: red } /* .a:has(.b) p { x: y } */"), []);
});

test("no stylesheet has a :has() selector whose rightmost compound is a bare tag", () => {
  const offenders = [];
  for (const file of cssFiles(SRC)) {
    for (const selector of bareTagHasSelectors(readFileSync(file, "utf8"))) {
      offenders.push(`${relative(SRC, file).replaceAll("\\", "/")}: ${selector}`);
    }
  }
  assert.deepEqual(offenders, [], "tag-keyed :has() selectors restyle every matching element under the anchor on each DOM change");
});
