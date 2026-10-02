import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/portalRelease.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const release = { tag_name: "v0.1.13", assets: [{ name: "Loom-Setup-x64.exe", size: 144284058,
  browser_download_url: `${model.RELEASES_URL}/download/v0.1.13/Loom-Setup-x64.exe` }] };

test("metadata and installer all refer to the same release", () => {
  const result = model.parsePortalRelease(release);
  assert.equal(result.version, "v0.1.13");
  assert.equal(result.package, "137.6 MiB");
  assert.equal(result.download, release.assets[0].browser_download_url);
  assert.equal(result.notes, `${model.RELEASES_URL}/tag/v0.1.13`);
});

test("rejects incomplete, prerelease and unrelated installer URLs", () => {
  for (const payload of [null, {}, { ...release, prerelease: true }, { ...release, assets: [] },
    { ...release, assets: [{ ...release.assets[0], browser_download_url: "https://example.com/setup.exe" }] }]) {
    assert.throws(() => model.parsePortalRelease(payload));
  }
  assert.equal(model.FALLBACK_RELEASE.version, "");
  assert.equal(model.FALLBACK_RELEASE.package, "");
  assert.equal(model.FALLBACK_RELEASE.download, model.LATEST_INSTALLER_URL);
});

test("request skips cache and handles API failure", async () => {
  const original = globalThis.fetch;
  try {
    const signal = new AbortController().signal;
    globalThis.fetch = async (url, options) => {
      assert.equal(url, model.RELEASE_API_URL);
      assert.equal(options.cache, "no-store");
      assert.equal(options.signal, signal);
      return { ok: true, json: async () => release };
    };
    assert.equal((await model.fetchPortalRelease(signal)).version, "v0.1.13");
    globalThis.fetch = async () => ({ ok: false, status: 403 });
    await assert.rejects(model.fetchPortalRelease(signal));
  } finally { globalThis.fetch = original; }
});
