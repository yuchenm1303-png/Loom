import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fixture } from "./applicationFixtures.mjs";
import { prepareApplicationRelease, verifyApplicationRelease, validateApplicationRelease } from "../../dist-electron/applicationUpdateStore.js";
test("application manifest requires compatible bootstrap, complete entrypoints and trusted immutable files", () => {
  const { release } = fixture();
  assert.equal(validateApplicationRelease(release, "0.1.82"), release);
  for (const mutate of [r => r.bootstrap = "0.1.1", r => r.files.shift(), r => r.files[1].path = "dist/../main.js",
    r => r.files[1].url = "https://evil.test/entry", r => r.files.push({ ...r.files[1], path: "DIST/INDEX.HTML" }),
    r => r.files[1].path = "dist/aux.txt", r => r.sourceTime = NaN, r => r.kind = "ui"]) {
    const changed = structuredClone(release); mutate(changed);
    assert.throws(() => validateApplicationRelease(changed, "0.1.82"));
  }
});
test("cached application files are reused and a failed download cannot replace a verified release", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-store-"));
  try {
    const { release, contents } = fixture(); let requests = 0;
    const fetch = async file => { requests++; return contents[file.path]; };
    const active = await prepareApplicationRelease(root, release, fetch);
    await prepareApplicationRelease(root, { ...release, build: 2 }, fetch);
    assert.equal(requests, release.files.length);
    const broken = fixture(3);
    await assert.rejects(prepareApplicationRelease(root, broken.release, async () => Buffer.from("partial")));
    await verifyApplicationRelease(active, release);
    assert.equal((await fs.readdir(root)).includes("3"), false);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
