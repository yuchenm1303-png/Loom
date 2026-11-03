import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const x86 = readFileSync(new URL("../../build/win32-local.config.cjs", import.meta.url), "utf8");
const x64 = readFileSync(new URL("../../package.json", import.meta.url), "utf8");
const nsis = readFileSync(new URL("../../build/installer-x86.nsh", import.meta.url), "utf8");
const stable = readFileSync(new URL("../../build/installer.nsh", import.meta.url), "utf8");

test("x86 installer registers the same loom:// protocol as x64", () => {
  assert.ok(x86.includes('include: "build/installer-x86.nsh"'));
  assert.ok(nsis.includes('LOOM_PROTOCOL_SCHEME "loom"'));
  assert.ok(nsis.includes("!macro customInstall"));
  assert.ok(nsis.includes("!macro customUnInstall"));
  assert.ok(stable.includes("!macro customInstall"));
  assert.ok(stable.includes("!macro customUnInstall"));
});

test("x86 installer cannot steal x64 protocol association on a 64-bit host", () => {
  assert.ok(nsis.includes('\${RunningX64}'));
  assert.ok(nsis.includes('Keeping existing Loom protocol association on 64-bit Windows'));
  assert.ok(nsis.includes('ReadRegStr $R0'));
  assert.ok(nsis.includes("StrCmp $R0"));
  assert.ok(x86.includes('appId: "com.loom.agent.ia32"'));
  assert.ok(x64.includes('"appId": "com.loom.agent"'));
});
