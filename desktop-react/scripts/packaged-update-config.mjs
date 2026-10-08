import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { getAppUpdatePublishConfiguration } = require("app-builder-lib/out/publish/PublishManager.js");
const { serializeToYaml } = require("builder-util");
const { load } = require("js-yaml");

// --dir has no NSIS target, so electron-builder's default afterPack handler
// skips this file. --prepackaged then skips afterPack entirely. Generate it
// during the directory build, using the same resolver as electron-builder.
export default async function writePackagedUpdateConfig(context) {
  if (context.electronPlatformName !== "win32") return;
  const config = await getAppUpdatePublishConfiguration(context.packager, null, context.arch, true);
  assert.ok(config, "Windows package has no update publish configuration");
  const resources = context.packager.getResourcesDir(context.appOutDir);
  await fs.writeFile(path.join(resources, "app-update.yml"), serializeToYaml(config));
}

export async function verifyPackagedUpdateConfig(resources, packageJson) {
  const config = load(await fs.readFile(path.join(resources, "app-update.yml"), "utf8"));
  const publish = packageJson.build.publish;
  const expected = Array.isArray(publish) ? publish[0] : publish;
  assert.ok(config && expected, "Packaged updater configuration is empty");
  for (const field of ["provider", "owner", "repo"]) {
    assert.equal(config[field], expected[field], `Packaged updater ${field} differs from build.publish`);
  }
  assert.ok(typeof config.updaterCacheDirName === "string" && config.updaterCacheDirName.length > 0,
    "Packaged updater cache directory is missing");
  return config;
}
