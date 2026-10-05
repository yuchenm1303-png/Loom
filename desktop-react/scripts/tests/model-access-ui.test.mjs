import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const modelPanel = readFileSync(new URL("../../src/components/ModelPanel.tsx", import.meta.url), "utf8");
const composer = readFileSync(new URL("../../src/components/ComposerBase.tsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../../src/App.tsx", import.meta.url), "utf8");
const main = readFileSync(new URL("../../electron/main.ts", import.meta.url), "utf8");
const accountClient = readFileSync(new URL("../../electron/accountClient.ts", import.meta.url), "utf8");

function rpcEntry(assertSignedInForModels, rpc) {
  const file = ts.createSourceFile("main.ts", main, ts.ScriptTarget.ES2022, true);
  const declaration = file.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "runRpcCall");
  assert.ok(declaration, "Host RPC entry must exist");
  const compiled = ts.transpileModule(declaration.getText(file), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return new Function("assertSignedInForModels", "rpc", `${compiled}; return runRpcCall;`)(assertSignedInForModels, rpc);
}

test("signed-out users cannot run or switch models", async () => {
  assert.match(accountClient, /async hasAuthenticatedSession\(\)/);
  // Startup now also ensures model readiness inside a braced guard. Verify the
  // actual entry's behavior rather than prescribing its whitespace (aa51b0b3).
  const calls = [];
  const request = rpcEntry(async () => { throw new Error("Sign in required"); }, {
    ensureModelReady: async () => calls.push("ready"),
    call: async () => calls.push("call"),
  });
  await assert.rejects(request("turn/start", {}), /Sign in required/);
  assert.deepEqual(calls, []);
  assert.match(main, /async function assertModelSelectionAllowed[\s\S]*await assertSignedInForModels\(\)/);
  assert.match(app, /archived \|\| !account\.account\.authenticated/);
  assert.match(composer, /Sign in to Loom to use models and send messages/);
});

test("signed-in turn requests authorize before model initialization and dispatch", async () => {
  const calls = [];
  const params = { threadId: "fixture" };
  const request = rpcEntry(async () => calls.push("auth"), {
    ensureModelReady: async () => calls.push("ready"),
    call: async (method, value) => { calls.push("call"); assert.equal(method, "turn/start"); assert.equal(value, params); return "result"; },
  });
  assert.equal(await request("turn/start", params), "result");
  assert.deepEqual(calls, ["auth", "ready", "call"]);
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


test("model manager actions never nest forms inside the chat composer", () => {
  assert.doesNotMatch(modelPanel, /<form\b/);
  assert.match(modelPanel, /type="button" className="mp-primary"[\s\S]*onClick=\{\(\) => void submitAdd\(\)\}/);
  assert.match(modelPanel, /type="button"[\s\S]*onClick=\{\(\) => void configureProvider\(credentialTarget\.provider\)\}/);
  assert.match(modelPanel, /event\.stopPropagation\(\)/);
});
