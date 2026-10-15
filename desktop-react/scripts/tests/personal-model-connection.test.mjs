import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/personalModelConnection.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { personalConnectionTemplate } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("personal form uses the provider template instead of the built-in runtime endpoint", () => {
  const profile = { kind: "builtin", adapter: "openai-compatible", authMode: "loom-account",
    baseUrl: "https://account.smirel.com/model/v1/deepseek",
    personalConnection: { adapter: "openai-compatible", baseUrl: "https://api.deepseek.com" } };
  assert.deepEqual(personalConnectionTemplate(profile), profile.personalConnection);
  assert.equal(profile.baseUrl, "https://account.smirel.com/model/v1/deepseek");
});

test("older Hosts without personal templates leave URL empty rather than copying an account gateway", () => {
  assert.deepEqual(personalConnectionTemplate({ kind: "builtin", adapter: "openai-compatible",
    baseUrl: "https://account.smirel.com/model/v1/deepseek" }), { adapter: "openai-compatible", baseUrl: "" });
});

test("saved personal connections retain custom endpoints and ignore builtin template metadata", () => {
  const profile = { kind: "saved", adapter: "openai-compatible", baseUrl: "http://localhost:8080/v1",
    personalConnection: { adapter: "openai-compatible", baseUrl: "https://api.deepseek.com" } };
  assert.deepEqual(personalConnectionTemplate(profile), { adapter: "openai-compatible", baseUrl: profile.baseUrl });
  assert.deepEqual(personalConnectionTemplate({ ...profile, adapter: "openai" }), { adapter: "openai", baseUrl: "" });
});
