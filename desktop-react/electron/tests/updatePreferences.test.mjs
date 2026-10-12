import assert from "node:assert/strict";
import { mock, test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const root = fs.mkdtempSync(path.join(os.tmpdir(), "loom-update-prefs-"));
mock.module("electron", { namedExports: { app: { getPath: () => root } } });
const { readUpdatePreferences, writeUpdatePreferences } = await import("../../dist-electron/updatePreferences.js");
test("update preferences default on, persist independently, and reject invalid IPC input", () => {
  try {
    assert.deepEqual(readUpdatePreferences(), { automaticUpdates: true, silentUpdates: true, autoRestartAfterUpdate: true });
    writeUpdatePreferences({ automaticUpdates: false });
    assert.deepEqual(readUpdatePreferences(), { automaticUpdates: false, silentUpdates: true, autoRestartAfterUpdate: true });
    writeUpdatePreferences({ silentUpdates: false, autoRestartAfterUpdate: true });
    assert.deepEqual(readUpdatePreferences(), { automaticUpdates: false, silentUpdates: false, autoRestartAfterUpdate: true });
    writeUpdatePreferences({ autoRestartAfterUpdate: false });
    assert.equal(readUpdatePreferences().autoRestartAfterUpdate, false);
    assert.equal(readUpdatePreferences().silentUpdates, false);
    assert.throws(() => writeUpdatePreferences({ automaticUpdates: "false" }));
    assert.throws(() => writeUpdatePreferences({ unknown: true }));
    assert.equal(readUpdatePreferences().automaticUpdates, false);
    fs.writeFileSync(path.join(root, "update-preferences.json"), "broken");
    assert.deepEqual(readUpdatePreferences(), { automaticUpdates: true, silentUpdates: true, autoRestartAfterUpdate: true });
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
