import assert from "node:assert/strict";
import { test } from "node:test";
import { ModelUsageService, parseModelUsage, usageEndpoint } from "../../dist-electron/modelUsage.js";

const spec = { model: "MiniMax-M3", baseUrl: "https://api.minimaxi.com/v1", apiKey: "test-secret" };
const quota = { model_remains: [{ model_name: "video", current_interval_remaining_percent: 100 }, {
  model_name: "general", current_interval_remaining_percent: 61, current_weekly_remaining_percent: 88,
  current_interval_total_count: 0, current_interval_usage_count: 0, remains_time: 1000, weekly_remains_time: 2000,
}], base_resp: { status_code: 0 } };

test("MiniMax uses remaining percentages, general text quota and independent reset times", () => {
  const result = parseModelUsage("MiniMax", quota, spec.model, 100);
  assert.deepEqual(result.windows, [
    { label: "5h", remainingPercent: 61, resetsAt: 1100 },
    { label: "Weekly", remainingPercent: 88, resetsAt: 2100 },
  ]);
  assert.throws(() => parseModelUsage("MiniMax", { ...quota, base_resp: { status_code: 2049 } }, spec.model));
  assert.throws(() => parseModelUsage("MiniMax", { model_remains: [{ model_name: "video", current_interval_status: 3, current_interval_remaining_percent: 100 }] }, "video"));
  assert.throws(() => parseModelUsage("MiniMax", quota, "unknown"));
});

test("missing percent is unknown, not zero; count-based plans and epoch resets work", () => {
  const result = parseModelUsage("MiniMax", { model_remains: [{ model_name: "general",
    current_interval_remaining_percent: null, current_interval_total_count: 10, current_interval_usage_count: 3,
    end_time: 1800000000,
  }] }, spec.model);
  assert.deepEqual(result.windows, [{ label: "5h", remainingPercent: 70, resetsAt: 1800000000000 }]);
  assert.throws(() => parseModelUsage("MiniMax", { model_remains: [{ model_name: "general" }] }, spec.model));
});

test("DeepSeek retains money precision and all currencies without inventing a percent", () => {
  const result = parseModelUsage("DeepSeek", { balance_infos: [
    { currency: "CNY", total_balance: "0.00000001", topped_up_balance: "0", granted_balance: "0.00000001" },
    { currency: "USD", total_balance: "0" },
  ] }, "deepseek-chat");
  assert.equal(result.balances[0].total, "0.00000001");
  assert.equal(result.balances[1].total, "0");
  assert.deepEqual(result.windows, []);
  assert.throws(() => parseModelUsage("DeepSeek", {}, "deepseek-chat"));
});

test("only official origins receive keys; account gateway credentials are never sent", async () => {
  for (const url of ["http://api.deepseek.com", "https://api.deepseek.com.evil.test", "https://api.deepseek.com:444", "https://user@api.deepseek.com"]) assert.equal(usageEndpoint(url), null);
  let calls = 0;
  const service = new ModelUsageService(async () => { calls++; throw Error("unexpected"); });
  assert.equal((await service.query({ ...spec, baseUrl: "https://relay.test" })).status, "unsupported");
  assert.equal((await service.query({ ...spec, authMode: "loom-account" })).status, "unsupported");
  assert.equal((await service.query({ ...spec, apiKey: "" })).status, "unconfigured");
  assert.equal(calls, 0);
});

test("cache and concurrent requests deduplicate; refresh and changed keys fetch again", async () => {
  let calls = 0;
  const service = new ModelUsageService(async (url, options) => {
    calls++;
    assert.equal(url, "https://www.minimaxi.com/v1/token_plan/remains");
    assert.equal(options.redirect, "error");
    assert.ok(options.signal);
    await new Promise(resolve => setTimeout(resolve, 5));
    return new Response(JSON.stringify(quota));
  });
  const results = await Promise.all([service.query(spec), service.query(spec, true)]);
  assert.equal(calls, 1);
  assert.deepEqual(results[0], results[1]);
  await service.query(spec);
  await service.query({ ...spec, model: "MiniMax-M2.7" });
  assert.equal(calls, 1);
  await service.query(spec, true);
  await service.query({ ...spec, apiKey: "different-account" });
  assert.equal(calls, 3);
  assert.ok(!JSON.stringify(results).includes(spec.apiKey));
});

test("network failures and rejected credentials never expose upstream secrets", async () => {
  for (const fetcher of [async () => { throw Error(spec.apiKey); }, async () => new Response(spec.apiKey, { status: 401 }), async () => new Response("x".repeat(65537))]) {
    const result = await new ModelUsageService(fetcher).query(spec);
    assert.equal(result.status, "error");
    assert.ok(!JSON.stringify(result).includes(spec.apiKey));
  }
});
