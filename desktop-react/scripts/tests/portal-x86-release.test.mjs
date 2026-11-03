import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/portalX86Release.ts", import.meta.url), "utf8")
  .replace(/import \{ RELEASES_URL \} from "\.\/portalRelease";/, 'const RELEASES_URL = "https://github.com/yuchenm1303-png/Loom/releases";');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const x86 = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const tag = "win32-v0.1.168-r1";
const url = `${x86.X86_RELEASES_URL.replace(/\?q=.*/, "")}/download/${tag}/Loom-Setup-x86.exe`;
const preview = {
  tag_name: tag, prerelease: true, draft: false,
  assets: [{ name: "Loom-Setup-x86.exe", size: 138500000, browser_download_url: url }],
};

test("independent x86 preview is resolved only from a validated x86 release asset", () => {
  const result = x86.parsePortalX86Release([preview]);
  assert.equal(result.version, "v0.1.168-r1");
  assert.equal(result.download, url);
  assert.equal(result.package, "132.1 MiB");
  assert.equal(result.notes, `${x86.X86_RELEASES_URL.split("?")[0]}/tag/${tag}`);
});

test("stable x64 releases, remote companions, drafts, missing assets and external URLs are not x86 downloads", () => {
  const x64 = { tag_name: "v0.1.168", prerelease: false, draft: false,
    assets: [{ name: "Loom-Setup-x64.exe", size: 138500000, browser_download_url: "https://example.com/file.exe" }] };
  for (const bad of [
    x64, { ...preview, prerelease: false }, { ...preview, draft: true },
    { ...preview, tag_name: "v0.1.168" },
    { ...preview, tag_name: "win32-v0.1.168-r0" },
    { ...preview, tag_name: "win32-v0.1.168-r1/../../" },
    { ...preview, assets: [] },
    { ...preview, assets: [{ ...preview.assets[0], name: "Loom-Remote-Setup-x86.exe" }] },
    { ...preview, assets: [{ ...preview.assets[0], size: 1 }] },
    { ...preview, assets: [{ ...preview.assets[0], browser_download_url: "https://example.com/spoof.exe" }] },
  ]) assert.equal(x86.parsePortalX86Release([bad]), null);
  assert.equal(x86.parsePortalX86Release([x64, preview])?.download, url);
  assert.throws(() => x86.parsePortalX86Release({}), /release list/i);
});

test("x86 API uses its independent release listing; failure must not invent an installer URL", async () => {
  const previous = globalThis.fetch;
  try {
    const signal = new AbortController().signal;
    globalThis.fetch = async (request, options) => {
      assert.equal(request, x86.X86_RELEASE_API_URL);
      assert.equal(options.signal, signal);
      assert.equal(options.cache, "no-store");
      return { ok: true, json: async () => [preview] };
    };
    assert.equal((await x86.fetchPortalX86Release(signal))?.download, url);
    globalThis.fetch = async () => ({ ok: true, json: async () => [] });
    assert.equal(await x86.fetchPortalX86Release(signal), null);
    globalThis.fetch = async () => ({ ok: false, status: 403 });
    await assert.rejects(x86.fetchPortalX86Release(signal));
  } finally { globalThis.fetch = previous; }
});

test("homepage keeps the original stable x64 links and adds a separate x86 release route", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const stable = readFileSync(new URL("../../src/portalRelease.ts", import.meta.url), "utf8");
  assert.match(stable, /latest\/download\/Loom-Setup-x64\.exe/);
  assert.match(stable, /Loom-Setup-x64\.exe/);
  assert.ok(portal.includes('href={release.download}'));
  assert.ok(portal.includes('href={x86Release?.download ?? X86_RELEASES_URL}'));
  assert.ok(portal.includes('fetchPortalX86Release(controller.signal)'));
  assert.ok(portal.includes('Windows 10 · x86'));
  assert.ok(portal.includes('Preview · not yet released'));
});
