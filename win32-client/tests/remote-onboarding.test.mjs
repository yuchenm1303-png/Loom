import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const read = (file) => fs.readFileSync(path.join(repo, file), "utf8");
const shell = read("win32-client/Program.cs");
const bridge = read("desktop-react/src/webBridge.ts");
const gate = read("desktop-react/src/components/WebAppGate.tsx");
const portal = read("desktop-react/src/components/WebPortal.tsx");
const picker = read("desktop-react/src/components/RemoteDevicesPanel.tsx");

test("x86 WebView2 shell opens a dedicated remote-only URL, without injecting host objects", () => {
  assert.match(shell, /https:\/\/loom\.smirel\.com\/\?loom_client=win32-remote/);
  assert.match(shell, /AreHostObjectsAllowed = false/);
  assert.match(shell, /IsWebMessageEnabled = false/);
  assert.doesNotMatch(shell, /Process\.Start\([^\n]*python\.exe/);
});

test("remote-only client mode survives OAuth redirects but does not affect normal browsers", () => {
  assert.match(bridge, /searchParams\.get\(REMOTE_ONLY_CLIENT_QUERY\) === REMOTE_ONLY_CLIENT_VALUE/);
  assert.match(bridge, /sessionStorage\.setItem\(REMOTE_ONLY_CLIENT_SESSION_KEY, "1"\)/);
  assert.match(bridge, /if \(isRemoteOnlyWebClient\(\)\) return "remote"/);
  assert.match(bridge, /if \(isRemoteOnlyWebClient\(\)\) return remoteWebDeviceId\(\)/);
  assert.match(bridge, /export function activateLocalWebDevice\(\): void \{\s*if \(isRemoteOnlyWebClient\(\)\) return/);
  assert.match(bridge, /export function bindLocalWebDevice\(deviceId: string\): boolean \{\s*if \(isRemoteOnlyWebClient\(\)\) return false/);
});

test("portal can choose an online Host before a local Host exists", () => {
  assert.match(gate, /remoteOnly && !selectedWebDeviceId\(\)/);
  assert.match(gate, /<RemoteControlSurface open=\{remotePickerOpen\}/);
  assert.match(gate, /remoteMode=\{remoteOnly\}/);
  assert.match(gate, /onRemote=\{\(\) => setRemotePickerOpen\(true\)\}/);
  assert.match(portal, /remoteMode && hostState !== "online"/);
  assert.match(portal, /onRemote\?\.\(\)/);
  assert.match(portal, /!remoteMode && \(hostState === "unbound" \|\| hostState === "offline"\)/);
  assert.match(picker, /const remoteOnly = isRemoteOnlyWebClient\(\)/);
  assert.match(picker, /!remoteOnly \? <section className="remote-device-section">/);
  assert.match(picker, /mode === "remote" && selectedId/);
});
