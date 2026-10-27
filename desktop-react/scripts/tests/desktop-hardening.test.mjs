import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

const read = (relative) => readFileSync(new URL(`../../${relative}`, import.meta.url), "utf8");

test("the renderer page runs no inline script, so the desktop Content-Security-Policy can forbid them", () => {
  const html = read("index.html");
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.ok(scripts.length >= 2, "index.html lost its scripts");
  for (const [tag, attributes, body] of scripts) {
    assert.match(attributes, /\ssrc="|^src="/, `inline script in index.html: ${tag.slice(0, 80)}`);
    assert.equal(body.trim(), "", `inline script in index.html: ${tag.slice(0, 80)}`);
  }
  assert.match(html, /<script src="\/loom-boot\.js"><\/script>/, "the theme and boot-failure script must load before the bundle");
  assert.ok(existsSync(new URL("../../public/loom-boot.js", import.meta.url)));
  // The same page is Loom Web's entry (smirel.com styles, blob: artifacts): its policy is the desktop's header.
  assert.doesNotMatch(html, /http-equiv="Content-Security-Policy"/i);
});

test("the packaged executable is locked to Loom's own archive", () => {
  const { build } = JSON.parse(read("package.json"));
  assert.deepEqual(build.electronFuses, {
    runAsNode: false,
    // One-way: switching it off again would leave every embedded-browser cookie unreadable.
    enableCookieEncryption: true,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    enableEmbeddedAsarIntegrityValidation: true,
    onlyLoadAppFromAsar: true,
  });
  assert.equal(build.asar, true, "asar-only loading and integrity checks need the app archive");
});
