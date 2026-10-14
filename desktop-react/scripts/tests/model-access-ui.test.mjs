import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { isManagedBuiltinSelection } from "../../dist-electron/modelPolicySelection.js";

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
  assert.match(app, /accountAuthenticated=\{account\.account\.authenticated\}/);
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

function selectionEntry(authenticate, check) {
  const file = ts.createSourceFile("main.ts", main, ts.ScriptTarget.ES2022, true);
  const declaration = file.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "assertModelSelectionAllowed");
  const compiled = ts.transpileModule(declaration.getText(file), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return new Function("assertSignedInForModels", "isManagedBuiltinSelection", "accountClient", "policyAllowsSelection", "policyBlockedMessage",
    `${compiled}; return assertModelSelectionAllowed;`)(authenticate, isManagedBuiltinSelection,
    { modelPolicyCheck: check }, access => access.enabled, () => "Disabled by Loom Admin");
}

test("personal connections authenticate without depending on the built-in policy service", async () => {
  let authenticated = 0;
  const check = selectionEntry(async () => { authenticated++; }, async () => { throw new Error("policy offline"); });
  await check("profile:m123456789abc");
  await check("saved:legacy");
  assert.equal(authenticated, 2);
  await assert.rejects(check("builtin:deepseek"), /policy offline/);
  await assert.rejects(check("managed:relay"), /policy offline/);
  const signedOut = selectionEntry(async () => { throw new Error("Sign in required"); }, async () => assert.fail("must authenticate first"));
  await assert.rejects(signedOut("profile:m123456789abc"), /Sign in required/);
});

test("built-in model checks retain denial and unavailable-policy enforcement", async () => {
  for (const selection of ["builtin:deepseek", "managed:relay"]) {
    const denied = selectionEntry(async () => {}, async value => {
      assert.equal(value, selection);
      return { enabled: false };
    });
    await assert.rejects(denied(` ${selection} `), /Disabled by Loom Admin/);
    await assert.rejects(selectionEntry(async () => {}, async () => null)(selection), /permissions unavailable/);
    await selectionEntry(async () => {}, async () => ({ enabled: true }))(selection);
  }
});

test("new chats and both draft and existing-chat switches accept real personal IDs during a policy outage", async () => {
  const names = ["assertModelSelectionAllowed", "runRpcCall", "applySwitchModelProfile", "applySwitchCurrentModel"];
  const file = ts.createSourceFile("main.ts", main, ts.ScriptTarget.ES2022, true);
  const source = file.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
    .map(node => node.getText(file)).join("\n");
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const personal = { selection: "profile:m123456789abc", model: "deepseek-v4-pro" };
  const switches = [];
  const requests = [];
  const resolve = async selection => ({ selection, model: personal.model });
  const manager = { current: personal, ensureInitialAsync: async () => personal,
    resolveAsync: resolve, useProfileAsync: resolve,
    resolveModelNameForAsync: async (selection, model) => ({ selection, model }), useResolvedModel: spec => spec };
  const rpc = { ensureModelReady: async () => {}, call: async (method, params) => {
    requests.push({ method, params });
    return { thread: { id: "new-thread" } };
  } };
  const changeThreadModel = async (id, spec) => {
    switches.push({ id, ...spec });
    return { thread: { id, modelSelection: spec.selection, model: spec.model } };
  };
  const changeModel = async action => { const spec = await action(); switches.push({ id: "draft", ...spec }); return spec; };
  const entries = new Function("assertSignedInForModels", "isManagedBuiltinSelection", "accountClient", "modelManager", "rpc", "changeThreadModel", "changeModel",
    `${compiled}; return {${names.join(",")}};`)(async () => {}, isManagedBuiltinSelection,
    { modelPolicyCheck: async () => { throw new Error("policy offline"); } }, manager, rpc, changeThreadModel, changeModel);
  const started = await entries.runRpcCall("thread/start", { title: "test" });
  assert.equal(started.thread.modelSelection, personal.selection);
  await entries.runRpcCall("turn/start", { threadId: "new-thread" });
  await entries.applySwitchModelProfile(personal.selection);
  await entries.applySwitchModelProfile("existing-thread", personal.selection);
  await entries.applySwitchCurrentModel("deepseek-v4-pro");
  await entries.applySwitchCurrentModel("existing-thread", personal.selection, "deepseek-v4-pro");
  assert.deepEqual(requests.map(item => item.method), ["thread/start", "turn/start"]);
  assert.deepEqual(switches.map(item => item.id), ["new-thread", "draft", "existing-thread", "draft", "existing-thread"]);
  assert.ok(switches.every(item => item.selection === personal.selection));
  await assert.rejects(entries.applySwitchModelProfile("builtin:deepseek"), /policy offline/);
  await assert.rejects(entries.applySwitchModelProfile("existing-thread", "managed:relay"), /policy offline/);
  assert.equal(switches.length, 5, "managed requests must stop before changing the runtime");
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
