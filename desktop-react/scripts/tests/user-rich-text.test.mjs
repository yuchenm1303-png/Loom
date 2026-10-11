import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const result = buildSync({
  stdin: {
    contents: `import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import { UserRichText } from "./src/components/UserRichText";
      export const render = text => renderToStaticMarkup(React.createElement(UserRichText, {text}));`,
    resolveDir: fileURLToPath(new URL("../../", import.meta.url)),
  },
  bundle: true, write: false, format: "cjs", platform: "node",
  loader: { ".css": "empty" }, jsx: "automatic",
});
const compiled = { exports: {} };
new Function("module", "exports", "require", result.outputFiles[0].text)(compiled, compiled.exports, createRequire(import.meta.url));
const { render } = compiled.exports;

test("a URL joined to Chinese instructions preserves the whole readable message", () => {
  const text = "https://github.com/morluto/rea帮我看一下这个仓库吧";
  const html = render(text);
  assert.ok(html.includes(text));
  assert.ok(!html.includes("user-link-card"));
  assert.ok(!html.includes("href="));
  assert.ok(!html.includes("%E5"));
});

test("standalone URLs remain cards and surrounding prose remains visible", () => {
  const html = render("https://github.com/morluto/rea\n\n帮我看一下这个仓库吧");
  assert.ok(html.includes('class="user-link-card"'));
  assert.ok(html.includes('href="https://github.com/morluto/rea"'));
  assert.ok(html.includes("帮我看一下这个仓库吧"));
});

test("removing an earlier card keeps Markdown source offsets aligned", () => {
  const text = "https://example.com\n\nhttps://github.com/morluto/rea帮我看一下这个仓库吧";
  const html = render(text);
  assert.ok(html.includes("rea帮我看一下这个仓库吧"));
  assert.equal((html.match(/href=/g) || []).length, 1);
});

test("explicit Unicode Markdown links and delimited autolinks remain clickable", () => {
  for (const text of ["[中文页面](https://example.com/中文)", "<https://example.com/中文>"]) {
    assert.ok(render(text).includes("href="));
  }
  assert.ok(render("https://example.com/%E4%B8%AD%E6%96%87").includes('class="user-link-card"'));
});
