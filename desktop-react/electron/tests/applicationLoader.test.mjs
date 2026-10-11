import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fixture } from "./applicationFixtures.mjs";
import { prepareApplicationRelease, readApplicationPointer, updateApplicationPointer } from "../../dist-electron/applicationUpdateStore.js";
mock.module("node:module", { namedExports: { register() {} } });
mock.module("electron", { namedExports: { app: { isPackaged: true, getVersion: () => "0.1.82", once() {} } } });
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
    assert.equal(await selectApplication(installed, home, false), path.join(root, "1/dist-electron/applicationEntry.js"));
    await updateApplicationPointer(root, value => ({ ...value, session: undefined, booting: { build: 2, pid: 2147483647 } }));
    assert.equal(await selectApplication(installed, home, true), path.join(root, "1/dist-electron/applicationEntry.js"));
    assert.ok((await readApplicationPointer(root)).rejected.includes(2));
    await globalThis.loomApplicationBootstrap.healthy();
    // A damaged payload is never executed.
    await fs.writeFile(path.join(root, "1/dist-electron/main.js"), "corrupt");
    assert.equal(await selectApplication(installed, home, true), path.join(installed, "dist-electron/applicationEntry.js"));
  } finally { delete globalThis.loomApplicationBootstrap; await fs.rm(home, { recursive: true, force: true }); }
});
