import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../../src/components/modelBrand.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
new Function('exports', compiled)(exports);
const { resolveModelBrand: resolve } = exports;
test('model brands take precedence over the gateway that serves them', () => {
  assert.equal(resolve('deepseek-v4-pro', 'opencode-go'), 'deepseek');
  assert.equal(resolve('anthropic/claude-sonnet', 'managed-relay:openai'), 'anthropic');
  assert.equal(resolve('unknown-model', 'opencode-go'), 'opencode');
});
test('known model families resolve to local brand assets', () => {
  for (const [model, brand] of Object.entries({ 'gpt-5': 'openai', 'o3': 'openai', 'gemini-3-pro': 'gemini', 'MiniMax-M3': 'minimax', 'qwen3-coder': 'qwen', 'kimi-k2': 'moonshot', 'MiMo-v2': 'xiaomi', 'hunyuan-2': 'hunyuan', 'grok-4': 'xai', 'glm-5': 'glm', 'Z.AI': 'zhipu', 'devstral-2': 'mistral', 'llama-4': 'meta' })) assert.equal(resolve(model), brand, model);
});
test('compatible protocols and unknown models keep a neutral icon', () => {
  assert.equal(resolve('custom-model', 'openai-compatible'), null);
  assert.equal(resolve('notdeepseek', 'private-gateway'), null);
  assert.equal(resolve(), null);
});
