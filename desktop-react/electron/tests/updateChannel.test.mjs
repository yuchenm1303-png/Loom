import { test } from "node:test";
import assert from "node:assert/strict";
import { readUpdateChannel } from "../../dist-electron/updateChannel.js";
const url = "https://raw.githubusercontent.com/yuchenm1303-png/Loom/main/application-runtime/stable.json";
test("repository API avoids a stale raw channel", async () => {
  const calls = [];
  const result = await readUpdateChannel(url, async endpoint => {
    calls.push(endpoint);
    return Buffer.from(JSON.stringify(endpoint.includes("api.github.com")
      ? { encoding: "base64", content: Buffer.from('{"build":34}').toString("base64") }
      : { build: 33 }));
  }, 1000);
  assert.equal(result.build, 34);
  assert.equal(calls.length, 1);
});
test("rate-limited API falls back to raw with a new cache key", async () => {
  const result = await readUpdateChannel(url, async endpoint => {
    if (endpoint.includes("api.github.com")) throw new Error("HTTP 403");
    assert.ok(new URL(endpoint).searchParams.has("check"));
    return Buffer.from('{"build":34}');
  }, 1000);
  assert.equal(result.build, 34);
});
