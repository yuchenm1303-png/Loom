import { test, mock } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";

test("shortcut startup selects updates from the original Host profile after Desktop path initialization", async () => {
  const previousRoot = process.env.LOOM_HOST_DATA_DIR;
  delete process.env.LOOM_HOST_DATA_DIR;
  const original = path.join(os.tmpdir(), "loom-entry-profile");
  let userData = original;
  let selection;
  const app = {
    getPath: () => userData,
    setPath: (_name, value) => { userData = value; },
    setName() {},
    requestSingleInstanceLock: () => true,
  };
  mock.module("electron", { namedExports: { app, protocol: { registerSchemesAsPrivileged() {} } } });
  // hostPaths captures the profile before hostProcess changes it during the
  // loader's static imports. Reproduce that ordering without starting a Host.
  const { hostDataPath } = await import("../../dist-electron/hostPaths.js");
  app.setPath("userData", path.join(original, "desktop-ui"));
  mock.module("../../dist-electron/applicationLoader.js", { namedExports: {
    selectApplication: (...args) => { selection = args; return new Promise(() => {}); },
    rejectApplicationAndRestart() { throw new Error("Unexpected rollback"); },
  } });
  try {
    await import("../../dist-electron/entry.js");
    assert.equal(selection[1], hostDataPath);
    assert.equal(selection[1], original);
    assert.equal(process.env.LOOM_HOST_DATA_DIR, original);
    assert.equal(userData, path.join(original, "desktop-ui"));
    assert.equal(selection[2], false);
  } finally {
    if (previousRoot === undefined) delete process.env.LOOM_HOST_DATA_DIR;
    else process.env.LOOM_HOST_DATA_DIR = previousRoot;
  }
});
