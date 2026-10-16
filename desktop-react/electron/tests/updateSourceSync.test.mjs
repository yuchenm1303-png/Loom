import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
const object = Buffer.from("historical reusable object"), installer = Buffer.from("verified installer");
const sha = createHash("sha256").update(object).digest("hex");
const sha512 = createHash("sha512").update(installer).digest("base64");
const realRead = fsp.readFile.bind(fsp);
mock.module("node:fs/promises", { defaultExport: { ...fsp, readFile: async (file, ...args) => {
  if (String(file).match(/(?:application-runtime|ui-runtime|host-runtime)\/stable\.json$/)) {
    return Buffer.from(JSON.stringify({ files: [{ url: `https://github.com/yuchenm1303-png/Loom/releases/download/app-1001/${sha}`, sha256: sha, size: object.length }] }));
  }
  return realRead(file, ...args);
} } });
const uploads = [];
let breakUpload = false;
let rangeSupported = true;
mock.module("node:child_process", { namedExports: { execFileSync(tool, args) {
  if (tool === "aws") {
    if (breakUpload) throw new Error("upload unavailable");
    uploads.push({ key: args[3], cache: args.at(-1), bytes: fs.readFileSync(args[2], "utf8") });
    return "";
  }
  assert.equal(tool, "gh");
  if (args[1] === "view") return JSON.stringify({ tagName: "v0.1.92" });
  assert.equal(args[1], "download");
  const directory = args[args.indexOf("--dir") + 1];
  for (let index = 0; index < args.length; index++) if (args[index] === "--pattern") {
    const name = args[++index];
    const bytes = name === "latest.yml" ? `version: 0.1.92\npath: Loom-Setup-x64.exe\nfiles:\n  - url: Loom-Setup-x64.exe\n    size: ${installer.length}\n    sha512: ${sha512}\n` : name.endsWith(".exe") ? installer : name === sha ? object : Buffer.from("blockmap");
    fs.writeFileSync(path.join(directory, name), bytes);
  }
  return "";
} } });
test("sync verifies historical objects and installer before publishing mutable channels", async () => {
  const env = { ...process.env };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async url => {
    assert.match(url, /^https:\/\/cdn\.example\.test\/loom\/releases\/v0\.1\.92\//);
    return new Response("x", { status: rangeSupported ? 206 : 200, headers: { "content-range": `bytes 0-0/${installer.length}` } });
  };
  process.env.LOOM_UPDATE_SOURCE_URL = "https://cdn.example.test/loom";
  process.env.LOOM_UPDATE_BUCKET = "update-test-bucket";
  try {
    await import("../../scripts/sync-update-source.mjs?test=success");
    assert.ok(uploads.some(item => item.key.endsWith(`/releases/app-1001/${sha}`)));
    const firstChannel = uploads.findIndex(item => item.key.includes("/channels/"));
    assert.ok(firstChannel > 0);
    assert.ok(uploads.slice(0, firstChannel).every(item => item.key.includes("/releases/")));
    assert.ok(uploads.at(-1).key.endsWith("/installer/latest.yml"));
    assert.match(uploads.at(-1).bytes, /\.\.\/releases\/v0\.1\.92\/Loom-Setup-x64.exe/);
    uploads.length = 0; breakUpload = true;
    await assert.rejects(import("../../scripts/sync-update-source.mjs?test=failure"), /upload unavailable/);
    assert.equal(uploads.length, 0, "failed asset upload must not publish any channel");
    breakUpload = false; rangeSupported = false;
    await assert.rejects(import("../../scripts/sync-update-source.mjs?test=no-range"), /byte-range/);
    assert.ok(uploads.every(item => item.key.includes("/releases/")), "unsupported public CDN must not publish channels");
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of ["LOOM_UPDATE_SOURCE_URL", "LOOM_UPDATE_BUCKET"]) {
      if (env[key] === undefined) delete process.env[key]; else process.env[key] = env[key];
    }
  }
});
