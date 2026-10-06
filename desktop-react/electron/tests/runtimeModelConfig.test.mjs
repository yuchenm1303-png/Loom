import assert from "node:assert/strict";
import { test } from "node:test";
import { runtimeModelArguments, runtimeModelParams } from "../../dist-electron/runtimeModelConfig.js";

test("cold launch and hot switch serialize identical limits and vision", () => {
  const spec = { provider: "openai-compatible", model: "demo", vision: false,
    contextLimits: { contextWindowTokens: 512000, workingContextTokens: 128000,
      outputReserveTokens: 4096, effectiveContextPercent: 95 } };
  const args = runtimeModelArguments(spec);
  const params = runtimeModelParams(spec);
  assert.deepEqual(JSON.parse(args[1]), params.contextLimits);
  assert.equal(args[2], "--no-vision");
  assert.equal(params.vision, false);
});
test("unknown model carries no previous profile limits", () => {
  runtimeModelArguments({ contextLimits: { contextWindowTokens: 512000 } });
  assert.deepEqual(runtimeModelArguments({}), ["--context-limits", "{}", "--vision"]);
});
