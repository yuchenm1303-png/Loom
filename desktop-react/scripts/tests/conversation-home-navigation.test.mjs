import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/useLoomCore.ts", import.meta.url), "utf8");
function callback(name, context) {
  const start = source.indexOf(`  const ${name} = useCallback(`);
  const end = source.indexOf("\n  const ", start + 1);
  const code = ts.transpileModule(source.slice(start, end), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return Function(...Object.keys(context), "useCallback", `${code}; return ${name};`)(
    ...Object.values(context), (fn) => fn,
  );
}

test("deletion stays home even when the server notification already cleared selection", async () => {
  for (const selected of ["deleted", "", "other"]) {
    let clears = 0;
    let refreshes = 0;
    const remove = callback("deleteThread", {
      requireBridge: () => ({ call: async () => {} }),
      activeIdRef: { current: selected }, openingThreadIdRef: { current: "" },
      clearActive: () => clears++, refreshThreads: async () => { refreshes++; return [{ id: "other" }]; },
    });
    await remove("deleted");
    assert.equal(clears, selected === "deleted" ? 1 : 0);
    assert.equal(refreshes, 1);
  }
});

test("new conversation only prepares home, preserving project for first send", async () => {
  const draftThreadParamsRef = { current: {} };
  let clears = 0;
  const prepare = callback("newThread", {
    clearActive: () => clears++, threadViewRef: { current: "archived" },
    setThreadViewState: () => {}, draftThreadParamsRef, refreshThreads: async () => [],
  });
  await prepare("C:/project", "project-id");
  assert.equal(clears, 1);
  assert.deepEqual(draftThreadParamsRef.current, { projectId: "project-id" });
});

test("first home send creates once and sends text or attachments to the new conversation", async () => {
  for (const [input, attachments] of [["hello", []], ["", [{ path: "image.png", name: "image.png" }]]]) {
    let starts = 0;
    const calls = [];
    const send = callback("send", {
      active: null, homeSendPendingRef: { current: false },
      startThread: async () => { starts++; return { id: "new" }; },
      threadReadCacheRef: { current: new Map() }, crypto: { randomUUID: () => "id" },
      setItems: () => {}, setTurnActive: () => {}, setTurnStartedAt: () => {}, setActive: () => {},
      threadStateRevisionRef: { current: 0 },
      requireBridge: () => ({ call: async (method, params) => { calls.push({ method, params }); return { turn: { id: "turn" } }; } }),
    });
    await send("", []);
    assert.equal(starts, 0);
    await Promise.all([send(input, attachments), send(input, attachments)]);
    assert.equal(starts, 1);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].params.threadId, "new");
    assert.equal(calls[0].params.input, input);
    if (attachments.length) assert.deepEqual(calls[0].params.attachments, attachments);
  }
});
