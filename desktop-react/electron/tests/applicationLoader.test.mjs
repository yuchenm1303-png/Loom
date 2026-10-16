import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fixture } from "./applicationFixtures.mjs";
import { prepareApplicationRelease, readApplicationPointer, updateApplicationPointer } from "../../dist-electron/applicationUpdateStore.js";
mock.module("node:module", { namedExports: { register() {} } });
let ready = Promise.resolve(), eligibilityCalls = 0;
mock.module("electron", { namedExports: { app: { isPackaged: true, getVersion: () => "0.1.82", once() {}, whenReady: () => ready } } });
let updatesAllowed = true;
mock.module("../../dist-electron/updateEligibility.js", { namedExports: {
  mayReceiveUpdates: async () => { eligibilityCalls++; return updatesAllowed; },
} });
const { selectApplication } = await import("../../dist-electron/applicationLoader.js");
test("Host health commits a pending build; desktop follows the live Host; crashed candidate rolls back", async () => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-loader-"));
  const root = path.join(home, "application-updates"), installed = path.join(home, "installed");
  try {
    for (const build of [1, 2]) {
      const { release, contents } = fixture(build);
      await prepareApplicationRelease(root, release, async file => contents[file.path]);
    }
    await updateApplicationPointer(root, () => ({ ready: 1 }));
    assert.equal(await selectApplication(installed, home, true), path.join(root, "1/dist-electron/applicationEntry.js"));
    assert.equal((await readApplicationPointer(root)).active, undefined);
    await globalThis.loomApplicationBootstrap.healthy();
    assert.equal((await readApplicationPointer(root)).active, 1);
    await updateApplicationPointer(root, value => ({ ...value, ready: 2 }));
    updatesAllowed = false;
    assert.equal(await selectApplication(installed, home, true), path.join(root, "1/dist-electron/applicationEntry.js"));
    assert.equal((await readApplicationPointer(root)).ready, 2, "denied updates remain pending, not activated");
    assert.equal(await selectApplication(installed, home, false), path.join(root, "1/dist-electron/applicationEntry.js"));
    updatesAllowed = true;
    await updateApplicationPointer(root, value => ({ ...value, session: undefined, booting: { build: 2, pid: 2147483647 } }));
    assert.equal(await selectApplication(installed, home, true), path.join(root, "1/dist-electron/applicationEntry.js"));
    assert.ok((await readApplicationPointer(root)).rejected.includes(2));
    await globalThis.loomApplicationBootstrap.healthy();
    // A damaged payload is never executed.
    await fs.writeFile(path.join(root, "1/dist-electron/main.js"), "corrupt");
    assert.equal(await selectApplication(installed, home, true), path.join(installed, "dist-electron/applicationEntry.js"));
  } finally { delete globalThis.loomApplicationBootstrap; await fs.rm(home, { recursive: true, force: true }); }
});

test("pending update waits for credential storage readiness before checking the account", async () => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-ready-"));
  const root = path.join(home, "application-updates"), installed = path.join(home, "installed");
  let finishReady;
  ready = new Promise(resolve => { finishReady = resolve; });
  updatesAllowed = true;
  try {
    const { release, contents } = fixture(3);
    await prepareApplicationRelease(root, release, async file => contents[file.path]);
    await updateApplicationPointer(root, () => ({ ready: 3 }));
    const before = eligibilityCalls;
    let selected = false;
    const selection = selectApplication(installed, home, true).then(entry => { selected = true; return entry; });
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(eligibilityCalls, before, "must not read encrypted credentials before ready");
    assert.equal(selected, false);
    finishReady();
    assert.equal(await selection, path.join(root, "3/dist-electron/applicationEntry.js"));
    assert.equal(eligibilityCalls, before + 1);
    await globalThis.loomApplicationBootstrap.healthy();
    assert.equal((await readApplicationPointer(root)).active, 3);
  } finally {
    finishReady(); ready = Promise.resolve();
    delete globalThis.loomApplicationBootstrap;
    await fs.rm(home, { recursive: true, force: true });
  }
});
