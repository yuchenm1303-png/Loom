import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { validateUpdateSource, mirroredUpdateUrl, fetchUpdateSource } from "../../dist-electron/updateSource.js";
const originalEnv = process.env.LOOM_UPDATE_SOURCE_URL;
afterEach(() => {
  if (originalEnv === undefined) delete process.env.LOOM_UPDATE_SOURCE_URL;
  else process.env.LOOM_UPDATE_SOURCE_URL = originalEnv;
});
const asset = "https://github.com/yuchenm1303-png/Loom/releases/download/app-1001/abcd";

test("only explicit safe source URLs and known repository paths are mapped", () => {
  assert.equal(validateUpdateSource(""), "");
  for (const bad of ["http://cdn.example.com", "https://user:secret@cdn.example.com", "https://cdn.example.com/?token=x", "https://127.0.0.1", "https://cdn.example.com:8443"]) {
    assert.throws(() => validateUpdateSource(bad));
  }
  assert.equal(mirroredUpdateUrl(asset, "https://cdn.example.com/loom/"), "https://cdn.example.com/loom/releases/app-1001/abcd");
  assert.equal(mirroredUpdateUrl(asset.replace("yuchenm1303-png", "attacker"), "https://cdn.example.com"), null);
  assert.equal(mirroredUpdateUrl("https://account.smirel.com/v1/auth/me", "https://cdn.example.com"), null);
  assert.equal(mirroredUpdateUrl("https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/host-runtime/stable.json", "https://cdn.example.com"), "https://cdn.example.com/channels/host-runtime/stable.json");
});

test("mirror keeps range requests but receives no user authorization headers", async () => {
  process.env.LOOM_UPDATE_SOURCE_URL = "https://cdn.example.com";
  const calls = [];
  const response = await fetchUpdateSource(asset, { headers: { Authorization: "secret", Cookie: "private", Range: "bytes=0-9" } }, async (url, init) => {
    calls.push(url);
    assert.equal(new Headers(init.headers).get("authorization"), null);
    assert.equal(new Headers(init.headers).get("cookie"), null);
    assert.equal(new Headers(init.headers).get("range"), "bytes=0-9");
    return new Response("0123456789", { status: 206 });
  });
  assert.equal(await response.text(), "0123456789");
  assert.equal(calls.length, 1);
});

test("failed source falls back to the canonical origin with original request headers", async () => {
  process.env.LOOM_UPDATE_SOURCE_URL = "https://cdn.example.com";
  const calls = [];
  const result = await fetchUpdateSource(asset, { headers: { Authorization: "origin-only" } }, async (url, init) => {
    calls.push(url);
    if (calls.length === 1) return new Response("missing", { status: 404 });
    assert.equal(new Headers(init.headers).get("authorization"), "origin-only");
    return new Response("origin bytes");
  });
  assert.equal(await result.text(), "origin bytes");
  assert.deepEqual(calls, ["https://cdn.example.com/releases/app-1001/abcd", asset]);
});

test("caller cancellation cannot trigger an unintended fallback request", async () => {
  process.env.LOOM_UPDATE_SOURCE_URL = "https://cdn.example.com";
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(fetchUpdateSource(asset, { signal: controller.signal }, async () => {
    calls++; controller.abort(new Error("cancelled")); throw new Error("cancelled");
  }), /cancelled/);
  assert.equal(calls, 1);
});

test("generic installer blockmaps missing from mirror fall back to their original release", async () => {
  process.env.LOOM_UPDATE_SOURCE_URL = "https://cdn.example.com/loom";
  const calls = [];
  await fetchUpdateSource("https://cdn.example.com/loom/releases/v0.1.90/Loom-Setup-x64.exe.blockmap", { headers: { Authorization: "source-only" } }, async (url, init) => {
    calls.push(url);
    assert.equal(new Headers(init.headers).get("authorization"), null);
    return new Response("map", { status: calls.length === 1 ? 404 : 200 });
  });
  assert.equal(calls[1], "https://github.com/yuchenm1303-png/Loom/releases/download/v0.1.90/Loom-Setup-x64.exe.blockmap");
});
