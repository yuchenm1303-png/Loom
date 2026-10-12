import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fixture } from "./applicationFixtures.mjs";
const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-update-"));
const { release, contents } = fixture(); let requests = 0, corrupt = true, safe = false, restarted = false;
mock.module("electron", { namedExports: {
  app: { isPackaged: true, getVersion: () => "0.1.82", getPath: () => home, whenReady: () => ({ then() {} }),
    relaunch() { restarted = true; }, quit() {} },
  net: { fetch: async url => { requests++;
    if (new URL(url).pathname.endsWith("stable.json")) assert.ok(new URL(url).searchParams.has("check"));
    return new Response(new URL(url).pathname.endsWith("stable.json") ? JSON.stringify(release)
      : corrupt ? "broken" : contents[release.files.find(file => file.url === url).path]); } },
} });
mock.module("../../dist-electron/hostProcess.js", { namedExports: { isHostProcess: true, hostHasDesktopClients: () => true, broadcastHostEvent() {} } });
const updater = await import("../../dist-electron/applicationUpdater.js");
test("application updates stage automatically, reject damaged files and respect the active-task restart guard", async () => {
  try {
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "error");
    corrupt = false;
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "downloaded");
    assert.ok(requests > 1);
    updater.registerApplicationRestartGuard(() => safe);
    assert.equal(await updater.installApplicationUpdate(), false);
    assert.equal(restarted, false);
    safe = true;
    assert.equal(await updater.installApplicationUpdate(), true);
    await new Promise(resolve => setTimeout(resolve, 550));
    assert.equal(restarted, true);
    const pointer = JSON.parse(await fs.readFile(path.join(home, "application-updates/state.json"), "utf8"));
    assert.equal(pointer.ready, 1);
    assert.equal(pointer.active, undefined);
  } finally { await fs.rm(home, { recursive: true, force: true }); }
});
