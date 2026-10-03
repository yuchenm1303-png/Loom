import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const modelPanel = readFileSync(new URL("../../src/components/ModelPanel.tsx", import.meta.url), "utf8");
const composer = readFileSync(new URL("../../src/components/ComposerBase.tsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const main = readFileSync(new URL("../../electron/main.ts", import.meta.url), "utf8");
const accountClient = readFileSync(new URL("../../electron/accountClient.ts", import.meta.url), "utf8");

test("signed-out users cannot run or switch models", () => {
  assert.match(accountClient, /async hasAuthenticatedSession\(\)/);
  assert.match(main, /if \(method === "turn\/start"\) await assertSignedInForModels\(\)/);
  assert.match(main, /async function assertModelSelectionAllowed[\s\S]*await assertSignedInForModels\(\)/);
  assert.match(app, /archived \|\| !account\.account\.authenticated/);
  assert.match(composer, /Sign in to Loom to use models and send messages/);
});

test("model picker keeps blocked models visible and explains the restriction", () => {
  assert.match(modelPanel, /visibleProfiles\(group\)\.filter/);
  assert.match(modelPanel, /function unavailableLabel/);
  assert.match(modelPanel, /profile\.available === false \? unavailableLabel\(profile\)/);
  assert.match(modelPanel, /Admin blocked/);
  assert.match(modelPanel, /Disabled by Loom Admin/);
  assert.match(modelPanel, /<Lock size=\{13\}/);
});

test("BYOK is discoverable without pretending to bypass admin policy", () => {
  assert.match(modelPanel, /Use your own API key/);
  assert.match(modelPanel, /It does not change or bypass the built-in Loom Admin policy/);
  assert.match(modelPanel, /Use own API key/);
  assert.match(modelPanel, /setBaseUrl\(candidate\.adapter === "openai" \? "" : candidate\.baseUrl\)/);
});
