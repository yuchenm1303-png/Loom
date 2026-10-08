import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import writeConfig, { verifyPackagedUpdateConfig } from "../packaged-update-config.mjs";
const require = createRequire(import.meta.url);
const { AppInfo } = require("app-builder-lib/out/appInfo.js");
const { Platform } = require("app-builder-lib");
const { resolveFunction } = require("app-builder-lib/out/util/resolve.js");
const { NsisUpdater } = require("electron-updater");

test("directory packages contain a usable updater configuration before NSIS packaging", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "loom-update-config-"));
  try {
    const packageJson = JSON.parse(await fs.readFile(new URL("../../package.json", import.meta.url), "utf8"));
    const info = { config: packageJson.build, metadata: packageJson };
    const appInfo = new AppInfo(info);
    info.appInfo = appInfo;
    const packager = {
      config: packageJson.build, platformSpecificBuildOptions: packageJson.build.win,
      platform: Platform.WINDOWS, appInfo, info,
      expandMacro: value => value,
      getResourcesDir: () => directory,
    };
    await assert.rejects(verifyPackagedUpdateConfig(directory, packageJson), { code: "ENOENT" });
    const root = fileURLToPath(new URL("../../", import.meta.url));
    const hook = await resolveFunction("module", path.join(root, packageJson.build.afterPack), "afterPack", root);
    await hook({ electronPlatformName: "win32", appOutDir: directory, arch: 1, targets: [], packager });
    const config = await verifyPackagedUpdateConfig(directory, packageJson);
    assert.equal(config.updaterCacheDirName, appInfo.updaterCacheDirName);
    const updater = new NsisUpdater(null, { version: packageJson.version,
      appUpdateConfigPath: path.join(directory, "app-update.yml") });
    assert.deepEqual(await updater.configOnDisk.value, config);
    config.repo = "wrong-repository";
    await fs.writeFile(path.join(directory, "app-update.yml"), JSON.stringify(config));
    await assert.rejects(verifyPackagedUpdateConfig(directory, packageJson), /repo differs/);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("non-Windows packaging does not write a Windows updater config", async () => {
  await writeConfig({ electronPlatformName: "linux" });
});
