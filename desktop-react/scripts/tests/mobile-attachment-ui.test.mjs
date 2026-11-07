import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const bundle = buildSync({
  stdin: {
    contents: `import React from "react";
      import { renderToStaticMarkup } from "react-dom/server";
      import { AttachmentTray } from "./src/mobile/AttachmentTray";
      export const render = props => renderToStaticMarkup(React.createElement(AttachmentTray, props));`,
    resolveDir: fileURLToPath(new URL("../../", import.meta.url)),
  },
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic", loader: { ".css": "empty" },
});
const compiled = { exports: {} };
new Function("module", "exports", "require", bundle.outputFiles[0].text)(compiled, compiled.exports, createRequire(import.meta.url));
const { render } = compiled.exports;
const item = (id, status, name = "报告.pdf") => ({ id, status, file: new File(["content"], name) });
const props = { queue: {}, online: true, disabled: false };

test("mixed upload results show per-file state, removal and a retry action", () => {
  const html = render({ ...props, items: [item(1, "uploading"), { ...item(2, "error", "archive.zip"), error: "传输超时，请重试" }, item(3, "ready", "notes.txt")] });
  assert.match(html, /3 \/ 10 个附件/);
  assert.match(html, /1 个已就绪/);
  assert.match(html, /正在传到电脑/);
  assert.match(html, /传输超时，请重试/);
  assert.match(html, /aria-label="重试上传 archive.zip"/);
  assert.equal((html.match(/aria-label="移除 /g) ?? []).length, 3);
});

test("offline disables retry while files remain removable; sending locks removal", () => {
  const items = [{ ...item(1, "error"), error: "离线" }];
  const offline = render({ ...props, items, online: false });
  assert.match(offline, /disabled=""[^>]*aria-label="重试上传 报告.pdf"/);
  assert.doesNotMatch(offline, /disabled=""[^>]*aria-label="移除/);
  const sending = render({ ...props, items, disabled: true });
  assert.match(sending, /disabled=""[^>]*aria-label="移除 报告.pdf"/);
});

test("images expose a labeled preview and unusual filenames remain escaped text", () => {
  const image = { ...item(1, "ready", '<script>alert("file")</script>.png'), preview: "blob:test" };
  const html = render({ ...props, items: [image] });
  assert.match(html, /aria-label="预览 &lt;script&gt;/);
  assert.match(html, /src="blob:test"/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal(render({ ...props, items: [] }), "");
});
