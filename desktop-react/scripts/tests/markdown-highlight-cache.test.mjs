import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import rehypeHighlight from "rehype-highlight";
import { VFile } from "vfile";

function moduleUrl(source) {
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
}
const budget = moduleUrl(readFileSync(new URL("../../src/state/retainedBudgetMap.ts", import.meta.url), "utf8"));
const source = readFileSync(new URL("../../src/components/markdownHighlight.ts", import.meta.url), "utf8")
  .replace('"rehype-highlight"', JSON.stringify(import.meta.resolve("rehype-highlight")))
  .replace('"../state/retainedBudgetMap"', JSON.stringify(budget));
const { createCachedHighlighter } = await import(moduleUrl(source));

const tree = (value = "const value = 1;", language = "javascript", offset = 0) => ({ type: "root", children: [
  { type: "element", tagName: "pre", properties: {}, children: [
    { type: "element", tagName: "code", properties: { className: [`language-${language}`] },
      position: { start: { line: 1, column: 1, offset }, end: { line: 1, column: value.length + 1, offset: offset + value.length } },
      children: [{ type: "text", value }] },
  ] },
] });
const code = root => root.children[0].children[0];

test("streaming reuses unchanged code, isolates mutations and preserves source positions", () => {
  let calls = 0;
  const original = rehypeHighlight({ detect: false });
  const highlight = createCachedHighlighter((root, file) => { calls++; original(root, file); });
  const first = tree();
  highlight(first, new VFile());
  const expected = structuredClone(code(first).children);
  code(first).children[0].properties.className.push("mutated");
  const second = tree(undefined, undefined, 100);
  highlight(second, new VFile());
  assert.equal(calls, 1);
  assert.equal(code(second).position.start.offset, 100);
  assert.deepEqual(code(second).children, expected);
  code(second).children.length = 0;
  const third = tree(); highlight(third, new VFile());
  assert.deepEqual(code(third).children, expected);
  highlight(tree("const value = 2;"), new VFile());
  highlight(tree(undefined, "python"), new VFile());
  assert.equal(calls, 3, "changed text and language must not reuse stale tokens");
});

test("highlighting matches the original, including unknown languages and diagnostics", () => {
  const original = rehypeHighlight({ detect: false });
  const highlight = createCachedHighlighter(original);
  for (const language of ["javascript", "python", "no-highlight", "unknown-loom-language", ""]) {
    for (let pass = 0; pass < 2; pass++) {
      const expected = tree("const value = 1;\n", language);
      const actual = structuredClone(expected);
      const before = new VFile(), after = new VFile();
      original(expected, before); highlight(actual, after);
      assert.deepEqual(actual, expected);
      assert.deepEqual(after.messages.map(message => message.reason), before.messages.map(message => message.reason));
    }
  }
});

test("entry count is bounded and recently used code survives eviction", () => {
  let calls = 0;
  const original = rehypeHighlight({ detect: false });
  const highlight = createCachedHighlighter((root, file) => { calls++; original(root, file); });
  for (let index = 0; index < 128; index++) highlight(tree(`const value = ${index};`), new VFile());
  highlight(tree("const value = 0;"), new VFile());
  highlight(tree("const value = 128;"), new VFile());
  highlight(tree("const value = 0;"), new VFile());
  assert.equal(calls, 129);
  highlight(tree("const value = 1;"), new VFile());
  assert.equal(calls, 130);
});

test("large output is not retained and cannot evict small cached code", () => {
  let calls = 0;
  const highlight = createCachedHighlighter(root => {
    calls++;
    const node = code(root);
    node.children = [{ type: "text", value: node.children[0].value === "large" ? "x".repeat(150000) : "small" }];
  });
  highlight(tree("small"), new VFile());
  highlight(tree("large"), new VFile());
  highlight(tree("large"), new VFile());
  highlight(tree("small"), new VFile());
  assert.equal(calls, 3);
});

test("the byte budget evicts token-heavy code before the entry count limit", () => {
  let calls = 0;
  const highlight = createCachedHighlighter(root => {
    calls++;
    code(root).children = Array.from({ length: 100 }, () => ({ type: "element", tagName: "span",
      properties: { className: ["hljs-keyword"] }, children: [{ type: "text", value: "x".repeat(200) }] }));
  });
  for (let index = 0; index < 60; index++) highlight(tree(String(index)), new VFile());
  highlight(tree("59"), new VFile());
  assert.equal(calls, 60, "the newest entry fits and remains cached");
  highlight(tree("0"), new VFile());
  assert.equal(calls, 61, "old entries must be evicted below the 128-entry limit");
});

test("twenty streamed updates highlight 32 unchanged blocks only once each", () => {
  let calls = 0;
  const original = rehypeHighlight({ detect: false });
  const highlight = createCachedHighlighter((root, file) => { calls++; original(root, file); });
  for (let update = 0; update < 20; update++) {
    const root = { type: "root", children: Array.from({ length: 32 }, (_, index) => tree(`const value = ${index};`).children[0]) };
    highlight(root, new VFile());
    assert.ok(code(root).children.some(node => node.type === "element"), "all passes retain highlighted output");
  }
  assert.equal(calls, 32, "640 visits must not repeat unchanged syntax highlighting");
});
