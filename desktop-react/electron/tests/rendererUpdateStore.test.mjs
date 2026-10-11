import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { validateRendererRelease, prepareRendererRelease, rendererHash, verifyRendererRelease, readRendererRelease } from "../../dist-electron/rendererUpdateStore.js";

const contents = { "index.html": Buffer.from("<html>healthy</html>"), "assets/app.js": Buffer.from("console.log('healthy')") };
function manifest(build = 1) {
  return { schema: 1, build, bootstrap: "0.1.82", files: Object.entries(contents).map(([name, bytes]) => {
    const sha256 = rendererHash(bytes);
    return { path: name, size: bytes.length, sha256, url: `https://github.com/yuchenm1303-png/Loom/releases/download/ui-1/${sha256}` };
  }) };
}
test("rejects incompatible, malicious, oversized and ambiguous manifests", () => {
  const good = manifest();
  assert.equal(validateRendererRelease(good, "0.1.82"), good);
  for (const change of [
    release => release.bootstrap = "0.1.1",
    release => release.build = -1,
    release => release.files[0].path = "../index.html",
    release => release.files[0].path = "C:/index.html",
    release => release.files[0].path = "con.html",
    release => release.files[0].url = "https://evil.test/payload",
    release => release.files[0].size = 129 * 1024 * 1024,
    release => release.files.push({ ...release.files[1], path: "ASSETS/APP.JS" }),
    release => release.files.shift(),
  ]) {
    const release = structuredClone(good); change(release);
    assert.throws(() => validateRendererRelease(release, "0.1.82"));
  }
});
test("only changed files are downloaded; corrupt cache is repaired", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "loom-ui-store-"));
  try {
    let requests = 0;
    const fetch = async file => { requests++; return contents[file.path]; };
    const first = await prepareRendererRelease(root, manifest(), fetch);
    await verifyRendererRelease(first, manifest());
    assert.equal(requests, 2);
    await prepareRendererRelease(root, manifest(2), fetch);
    assert.equal(requests, 2);
    await writeFile(path.join(root, "objects", manifest().files[0].sha256), "corrupt");
    await prepareRendererRelease(root, manifest(3), fetch);
    assert.equal(requests, 3);
    assert.equal((await readRendererRelease(first, "0.1.82")).build, 1);
    assert.equal(await readRendererRelease(first, "0.1.83"), null);
    await writeFile(path.join(first, "assets/app.js"), "corrupt");
    await assert.rejects(verifyRendererRelease(first, manifest()));
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("failed preparation preserves the active bundle and never promotes partial data", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "loom-ui-failure-"));
  try {
    const first = await prepareRendererRelease(root, manifest(), async file => contents[file.path]);
    const changed = manifest(2);
    const broken = Buffer.from("changed");
    changed.files[1] = { ...changed.files[1], size: broken.length, sha256: rendererHash(broken) };
    await assert.rejects(prepareRendererRelease(root, changed, async () => Buffer.from("bad")));
    await verifyRendererRelease(first, manifest());
    assert.ok(!(await readdir(root)).some(name => name === "2" || name.startsWith("staging-")));
  } finally { await rm(root, { recursive: true, force: true }); }
});
