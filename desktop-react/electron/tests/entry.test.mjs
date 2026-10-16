import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

test("shortcut starts installed application and ignores pending or active cached releases", async () => {
  const previousRoot = process.env.LOOM_HOST_DATA_DIR;
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-installed-entry-"));
  process.env.LOOM_HOST_DATA_DIR = home;
  let userData = home, quits = 0;
  const paths = [];
  const app = {
    getPath: () => userData,
    setPath: (_name, value) => { userData = value; paths.push(value); },
    setName() {}, requestSingleInstanceLock: () => true, quit() { quits++; },
  };
  mock.module("electron", { namedExports: { app, protocol: { registerSchemesAsPrivileged() {} } } });
  mock.module("../../dist-electron/applicationEntry.js", { namedExports: {} });
  try {
    for (const channel of ["application-updates", "ui-updates"]) {
      const root = path.join(home, channel);
      await fs.mkdir(path.join(root, "999/dist-electron"), { recursive: true });
      await fs.writeFile(path.join(root, "state.json"), JSON.stringify({ active: 999, ready: 999, session: {build:999,pid:process.pid} }));
      await fs.writeFile(path.join(root, "999/dist-electron/applicationEntry.js"), "throw new Error('Cached release must never load')");
    }
    await import("../../dist-electron/entry.js");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(process.env.LOOM_HOST_DATA_DIR, home);
    assert.deepEqual(paths, [path.join(home, "desktop-ui")]);
    assert.equal(quits, 0);
    assert.equal((await fs.readdir(home)).length, 2, "startup must not write component pointers");
  } finally {
    if (previousRoot === undefined) delete process.env.LOOM_HOST_DATA_DIR;
    else process.env.LOOM_HOST_DATA_DIR = previousRoot;
    await fs.rm(home, { recursive: true, force: true });
  }
});
