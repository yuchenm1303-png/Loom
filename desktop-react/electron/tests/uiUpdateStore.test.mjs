import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { boundedResponse, sha256, UiUpdateStore, validateUiManifest } from "../../dist-electron/uiUpdateStore.js";

const bytes = Buffer.from('<html><script src="assets/app.js"></script></html>');
const contract = "a".repeat(64);
const asset = (name, data) => ({ path: name, size: data.length, sha256: sha256(data),
  url: `https://github.com/yuchenm1303-png/Loom/releases/download/ui-123/${sha256(data)}` });
const manifest = () => ({ schema: 2, build: 123, bootstrap: "1.0.0", contract, files: [asset("index.html", bytes)] });
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-ui-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return new UiUpdateStore(root, "1.0.0", contract);
}
test("only an exact bootstrap and runtime contract may run", () => {
  assert.deepEqual(validateUiManifest(manifest(), "1.0.0", contract), manifest());
  for (const patch of [{ schema: 1 }, { bootstrap: "2.0.0" }, { contract: "b".repeat(64) }, { build: NaN }, { files: [] }]) {
    assert.throws(() => validateUiManifest({ ...manifest(), ...patch }, "1.0.0", contract));
  }
});
test("rejects Windows traversal, aliases, remote code hosts and duplicate paths", () => {
  const supported = manifest();
  supported.files.push(asset("assets/cursor@1.5x.png", bytes), asset("chat-stickers/.source.json", bytes));
  assert.doesNotThrow(() => validateUiManifest(supported, "1.0.0", contract));
  for (const name of ["../index.html", "/index.html", "C:/x", "assets\\x", "assets/a:evil", "CON.js", "assets/lpt1.txt", "assets/a."]) {
    assert.throws(() => validateUiManifest({ ...manifest(), files: [...manifest().files, asset(name, bytes)] }, "1.0.0", contract), name);
  }
  for (const url of ["http://github.com/x", "https://evil.example/file", manifest().files[0].url + "?token=secret"]) {
    const m = manifest(); m.files[0].url = url;
    assert.throws(() => validateUiManifest(m, "1.0.0", contract));
  }
  const m = manifest(); m.files.push(asset("INDEX.html", bytes));
  assert.throws(() => validateUiManifest(m, "1.0.0", contract));
});
test("staging never activates a partial update; health confirmation persists and rollback blocks replay", async t => {
  const store = fixture(t);
  await store.stage(manifest(), async () => bytes);
  assert.equal(store.activeIndex(), null);
  store.confirm(123);
  assert.ok(store.activeIndex().endsWith(path.join("dist", "index.html")));
  assert.equal(new UiUpdateStore(store.root, "1.0.0", contract).activeIndex(), store.activeIndex());
  store.reject(123);
  assert.equal(store.activeIndex(), null);
  assert.equal(store.accepts(123), false);
  assert.equal(store.accepts(122), false);
  assert.equal(store.accepts(124), true);
  assert.throws(() => store.confirm(123));
});
test("corrupt/truncated files and failed downloads preserve the active build", async t => {
  const store = fixture(t);
  await store.stage(manifest(), async () => bytes); store.confirm(123);
  const next = { ...manifest(), build: 124 };
  await assert.rejects(store.stage(next, async () => Buffer.from("broken")));
  assert.equal(fs.existsSync(store.directory(124)), false);
  assert.ok(store.activeIndex());
  assert.deepEqual(fs.readdirSync(store.root).filter(name => name.startsWith("stage-")), []);
  fs.writeFileSync(path.join(store.directory(123), "dist", "index.html"), "tampered");
  assert.equal(store.activeIndex(), null);
  assert.equal(store.accepts(123), false);
});
test("network response size limits apply without trusting Content-Length", async () => {
  assert.equal((await boundedResponse(new Response("ok"), 2)).toString(), "ok");
  await assert.rejects(boundedResponse(new Response("oversized"), 2));
  await assert.rejects(boundedResponse(new Response("missing", { status: 404 }), 100));
});
test("startup cleanup keeps active and recent builds and removes abandoned staging", async t => {
  const store = fixture(t);
  await store.stage(manifest(), async () => bytes); store.confirm(123);
  for (const build of [124, 125, 126]) await store.stage({ ...manifest(), build }, async () => bytes);
  fs.mkdirSync(path.join(store.root, "stage-abandoned"));
  store.prune();
  assert.ok(store.activeIndex());
  assert.equal(fs.existsSync(store.directory(124)), false);
  assert.equal(fs.existsSync(store.directory(125)), true);
  assert.equal(fs.existsSync(store.directory(126)), true);
  assert.equal(fs.existsSync(path.join(store.root, "stage-abandoned")), false);
});
