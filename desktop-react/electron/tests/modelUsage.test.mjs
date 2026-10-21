import assert from "node:assert/strict";
import { test } from "node:test";
import { ModelUsageService, parseModelUsage, usageEndpoint, accountUsageEndpoint } from "../../dist-electron/modelUsage.js";

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

test("Go returns remaining percentages and distinct rolling, weekly and monthly resets", () => {
  const result = parseModelUsage("OpenCode Go", { usage: {
    rolling: { percent: 5, resetsAt: "2026-10-15T19:51:43.000Z" },
    weekly: { percent: 0 }, monthly: { percent: 95 },
  } }, "step-5-preview-free");
  assert.deepEqual(result.windows.map(r => r.remainingPercent), [95, 100, 5]);
  assert.equal(result.windows[0].resetsAt, Date.parse("2026-10-15T19:51:43Z"));
  assert.throws(() => parseModelUsage("OpenCode Go", { usage: { weekly: { percent: null } } }, "demo"));
});

test("Relay distinguishes token grants from wallet balances and unlimited token caps", () => {
  const result = parseModelUsage("Muxway Relay", { code: 0, data: { total_available: "0.00001" } }, "demo");
  assert.equal(result.scope, "api-key");
  assert.equal(result.balances[0].total, "0.00001");
  assert.deepEqual(parseModelUsage("Muxway Relay", { data: { unlimited_quota: true } }, "demo").balances, []);
  assert.throws(() => parseModelUsage("Muxway Relay", { success: false, data: { total_available: 99 } }, "demo"));
});

test("every builtin group queries its trusted gateway with account authentication only", async () => {
  const service = new ModelUsageService(async (url, init) => {
    assert.match(String(url), /^https:\/\/account\.smirel\.com\/model\/v1(?:\/[^/]+)?\/usage\?model=demo/);
    assert.equal(init.headers.Authorization, "Bearer loom_model_test");
    return new Response(JSON.stringify({ status: "ok", provider: "Test", checkedAt: 100,
      windows: [{ label: "Monthly", remainingPercent: 12 }], balances: [], scope: "loom-shared-provider-account",
      privateUpstreamKey: "must-not-leak" }));
  });
  for (const [selection, suffix] of [["builtin:ant-ling", ""], ["builtin:minimax", "/minimax"], ["builtin:deepseek", "/deepseek"], ["builtin:intern:demo", "/intern"], ["builtin:opencode-go:demo", "/opencode-go"], ["builtin:opencode-console:demo", "/opencode-console"], ["managed:demo", "/managed-relay"]]) {
    const result = await service.query({ ...spec, selection, kind: "builtin", model: "demo", authMode: "loom-account",
      baseUrl: "https://account.smirel.com/model/v1" + suffix }, false, "loom_model_test");
    assert.equal(result.status, "ok");
    assert.equal(result.scope, "loom-shared-provider-account");
    assert.ok(!JSON.stringify(result).includes("must-not-leak"));
  }
  for (const baseUrl of ["https://evil.test/model/v1", "https://account.smirel.com/model/v1.evil", "http://account.smirel.com/model/v1"]) {
    assert.equal(accountUsageEndpoint({ ...spec, authMode: "loom-account", baseUrl }), null);
  }
  assert.equal(accountUsageEndpoint({ ...spec, kind: "saved", authMode: "loom-account", baseUrl: "https://account.smirel.com/model/v1" }), null);
});

test("account quota cache is partitioned by account token and refresh reaches the server", async () => {
  const calls = [];
  const service = new ModelUsageService(async (url, init) => {
    calls.push([String(url), init.headers.Authorization]);
    return new Response(JSON.stringify({ status: "unsupported", provider: "Intern", checkedAt: 100, windows: [], balances: [] }));
  });
  const account = { ...spec, selection: "builtin:intern:demo", kind: "builtin", authMode: "loom-account", baseUrl: "https://account.smirel.com/model/v1/intern" };
  await service.query(account, false, "account-A");
  await service.query(account, false, "account-B");
  await service.query(account, true, "account-B");
  assert.equal(calls.length, 3);
  assert.match(calls[2][0], /&force=1$/);
  assert.equal(calls[2][1], "Bearer account-B");
});

test("HTTP rejections remain distinct from unsupported or zero quota", async () => {
  for (const status of [401, 403, 429, 503]) {
    const result = await new ModelUsageService(async () => new Response("private-provider-body", { status })).query(spec);
    assert.equal(result.status, "error");
    assert.equal(result.code, `HTTP_${status}`);
    assert.deepEqual(result.windows, []);
    assert.ok(!JSON.stringify(result).includes("private-provider-body"));
  }
});

test("current Go endpoint aliases and Relay boolean success schemas are supported", () => {
  for (const path of ["/zen/go/v1", "/inference/go/v1", "/inference/go/openai/v1"]) {
    assert.equal(usageEndpoint("https://opencode.ai" + path).provider, "OpenCode Go");
  }
  assert.equal(usageEndpoint("https://opencode.ai/inference/openai/v1"), null);
  for (const code of [true, 0, "0", 200, "200"]) {
    assert.equal(parseModelUsage("Muxway Relay", { code, data: { total_available: 3 } }, "demo").balances[0].total, "3");
  }
  assert.throws(() => parseModelUsage("Muxway Relay", { code: false, data: { total_available: 3 } }, "demo"));
  const balance = parseModelUsage("OpenCode Go", { usage: {}, billing: { balance: true } }, "demo");
  assert.equal(balance.status, "unsupported");
  assert.equal(balance.code, "BALANCE_AMOUNT_UNAVAILABLE");
});

test("gateway denials retain safe cause and provider, but discard arbitrary server content", async () => {
  const builtin = { ...spec, selection: "builtin:minimax", kind: "builtin", authMode: "loom-account", baseUrl: "https://account.smirel.com/model/v1/minimax" };
  for (const code of ["MODEL_NOT_ENTITLED", "INVALID_MODEL_TOKEN", "ACCOUNT_DISABLED", "private-secret"]) {
    const service = new ModelUsageService(async () => new Response(JSON.stringify({ error: { code, message: "private-secret" } }), { status: 403 }));
    const result = await service.query(builtin, false, "account");
    assert.equal(result.provider, "MiniMax");
    assert.equal(result.code, code === "private-secret" ? "HTTP_403" : code);
    assert.ok(!JSON.stringify(result).includes("private-secret"));
  }
});
