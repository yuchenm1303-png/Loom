import { test, mock } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fixture } from "./applicationFixtures.mjs";
const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-app-update-"));
const { release, contents } = fixture();
let channelRelease = release; let requests = 0, corrupt = true, safe = false, restarted = false;
let desktopConnected = true, relaunchArgs;
mock.module("electron", { namedExports: {
  app: { isPackaged: true, getVersion: () => "0.1.82", getPath: () => home, whenReady: () => ({ then() {} }),
    relaunch(options) { restarted = true; relaunchArgs = options.args; }, quit() {} },
  net: { fetch: async url => { requests++;
    if (new URL(url).pathname.endsWith("stable.json")) assert.ok(new URL(url).searchParams.has("check"));
    return new Response(new URL(url).pathname.endsWith("stable.json") ? JSON.stringify(channelRelease)
      : corrupt ? "broken" : contents[release.files.find(file => file.url === url).path]); } },
} });
mock.module("../../dist-electron/hostProcess.js", { namedExports: { isHostProcess: true, hostHasDesktopClients: () => desktopConnected, broadcastHostEvent() {} } });
const { writeUpdatePreferences } = await import("../../dist-electron/updatePreferences.js");
const updater = await import("../../dist-electron/applicationUpdater.js");
test("application updates stage automatically, reject damaged files and respect the active-task restart guard", async () => {
  try {
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "error");
    corrupt = false;
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "downloaded");
    assert.ok(requests > 1);
    channelRelease = { ...release, build: 2 };
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().availableVersion, "0.1.82+2");
    assert.equal(JSON.parse(await fs.readFile(path.join(home, "application-updates/state.json"), "utf8")).ready, 2);
    // An invalid newer release must preserve the verified ready version.
    channelRelease = { ...release, build: 3, files: [] };
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "downloaded");
    assert.equal(updater.applicationUpdateState().availableVersion, "0.1.82+2");
    assert.equal(JSON.parse(await fs.readFile(path.join(home, "application-updates/state.json"), "utf8")).ready, 2);
    channelRelease = { ...release, build: 2 };
    await updater.checkApplicationUpdates();
    assert.equal(updater.applicationUpdateState().phase, "downloaded");
    updater.registerApplicationRestartGuard(() => safe);
    assert.equal(await updater.maybeInstallApplicationUpdate(), false);
    assert.equal(restarted, false);
    safe = true;
    writeUpdatePreferences({ autoRestartAfterUpdate: false });
    assert.equal(await updater.maybeInstallApplicationUpdate(), false);
    assert.equal(restarted, false);
    writeUpdatePreferences({ autoRestartAfterUpdate: true });
    // A connected Desktop must not prevent a safe silent installation.
    assert.equal(desktopConnected, true);
    assert.equal(await updater.maybeInstallApplicationUpdate(), true);
    await new Promise(resolve => setTimeout(resolve, 550));
    assert.equal(restarted, true);
    assert.deepEqual(relaunchArgs, ["--loom-host", "--loom-open-desktop-after-update"]);
    const pointer = JSON.parse(await fs.readFile(path.join(home, "application-updates/state.json"), "utf8"));
    assert.equal(pointer.ready, 2);
    assert.equal(pointer.active, undefined);
  } finally { await fs.rm(home, { recursive: true, force: true }); }
});
