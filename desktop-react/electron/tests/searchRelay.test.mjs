import assert from "node:assert/strict";
import { test } from "node:test";
import { createSearchRelay } from "../../dist-electron/searchRelay.js";

test("search relay requires local auth, validates input and forwards results", async () => {
  const calls = [];
  const relay = await createSearchRelay(async (query, count) => {
    calls.push({ query, count });
    return { provider: "loom", results: [{ title: "Docs", url: "https://example.com" }] };
  });
  try {
    const url = relay.env.LOOM_SEARCH_RELAY_URL;
    const headers = { Authorization: `Bearer ${relay.env.LOOM_SEARCH_RELAY_TOKEN}`, "Content-Type": "application/json" };
    assert.equal((await fetch(url, { method: "POST", body: "{}" })).status, 401);
    assert.equal((await fetch(url, { method: "POST", headers: { ...headers, Origin: "https://example.com" }, body: "{}" })).status, 401);
    assert.equal((await fetch(url, { method: "POST", headers, body: JSON.stringify({ query: "docs", count: 99 }) })).status, 400);
    const response = await fetch(url, { method: "POST", headers, body: JSON.stringify({ query: "docs", count: 3 }) });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).provider, "loom");
    assert.deepEqual(calls, [{ query: "docs", count: 3 }]);
  } finally {
    await new Promise((resolve) => relay.server.close(resolve));
  }
});
