import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const load = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const x64 = load("../build-windows-runtime.mjs");
const x86 = load("../build-windows-x86-local-runtime.mjs");
const verify = load("../../../win32-local/verify-local-host.ps1");
const builder64 = JSON.parse(load("../../package.json"));
const builder86 = load("../../build/win32-local.config.cjs");

test("x86 bundles the same release-time GitHub Web OAuth configuration as x64", () => {
  for (const token of [
    "connector_release_config_generated.py",
    "LOOM_GITHUB_CLIENT_ID", "LOOM_GITHUB_CLIENT_SECRET",
    "LOOM_GITHUB_OAUTH_SCOPES", "LOOM_GITHUB_CALLBACK_PATH",
    "prepareGeneratedConnectorConfig()",
  ]) {
    assert.ok(x64.includes(token), "x64 release contract missing " + token);
    assert.ok(x86.includes(token), "x86 release contract missing " + token);
  }
  assert.ok(x86.includes('process.on("exit", cleanupGeneratedConnectorConfig)'),
    "temporary generated OAuth credentials must be removed");
  assert.ok(x86.includes('"--collect-submodules", "app"'),
    "frozen Python must collect the generated app connector module");
});

test("x86 offline installed Agent checks GitHub auth with all CI credentials removed", () => {
  assert.ok(verify.includes("LOOM_EXPECT_PACKAGED_GITHUB_OAUTH"));
  assert.ok(verify.includes("WebOAuthConnectorManager"));
  assert.ok(verify.includes('SetEnvironmentVariable($variable, $null, "Process")'));
  assert.ok(verify.includes('SetEnvironmentVariable($variable, $oauthOriginal[$variable], "Process")'));
  assert.ok(verify.includes('webOAuthAvailable'));
});

test("x86 and x64 contain the same real Loom Web renderer and native local Host", () => {
  assert.equal(builder64.build.files.includes("dist/**/*"), true);
  assert.equal(builder64.build.files.includes("dist-electron/**/*"), true);
  assert.ok(builder86.includes("...base"));
  assert.ok(builder86.includes('electronVersion: "43.8.6"'));
  assert.ok(builder86.includes('"runtime-dist-x86/python"'));
});

test("an x86 installer must never contain or install x64 MXC, chromium, or updates", () => {
  assert.ok(x86.includes('LOOM_RUNTIME_ARCH: "ia32"'));
  assert.ok(builder86.includes("publish: null"));
  assert.ok(builder86.includes("afterPack: undefined"));
  assert.ok(!builder86.includes("wxc-exec.exe"));
  assert.ok(verify.includes("Get-PeMachine"));
  assert.ok(verify.includes('Get-ChildItem -Path (Join-Path $InstallDir "resources")'));
});
