import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../../src/components/modelProviderGroups.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const mod = { exports: {} };
new Function('exports', compiled)(mod.exports);
const { groupModelProfiles } = mod.exports;
const profile = (overrides = {}) => ({ kind: 'builtin', adapter: 'openai-compatible', baseUrl: 'https://gateway.example/v1', name: 'Model', model: 'model', selection: 'builtin:model', ...overrides });
test('provider identity keeps built-in groups separate on a shared gateway', () => {
  const groups = groupModelProfiles([profile({ groupId: 'a', groupName: 'Provider A' }), profile({ groupId: 'b', groupName: 'Provider B' }), profile({ groupId: 'a', selection: 'other' })]);
  assert.deepEqual(groups.map(g => [g.name, g.profiles.length]), [['Provider A', 2], ['Provider B', 1]]);
});
test('saved connections group by full endpoint and adapter, independent of built-in credentials', () => {
  const groups = groupModelProfiles([profile({ kind: 'saved' }), profile({ kind: 'saved', baseUrl: 'https://gateway.example/v1/' }), profile({ kind: 'saved', baseUrl: 'https://gateway.example/other' }), profile({ kind: 'saved', adapter: 'openai' }), profile()]);
  assert.deepEqual(groups.map(g => g.profiles.length), [2, 1, 1, 1]);
  assert.equal(groups[0].name, 'gateway.example');
});
test('unavailable models remain visible and empty catalogs are supported', () => {
  assert.deepEqual(groupModelProfiles([]), []);
  const retired = profile({ available: false, statusMessage: 'Retired' });
  assert.equal(groupModelProfiles([retired])[0].profiles[0], retired);
});

