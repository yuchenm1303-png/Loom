import assert from "node:assert/strict";
import { test } from "node:test";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
const source = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/state/threadHandoff.ts", import.meta.url))], bundle: true, platform: "node", format: "esm", write: false }).outputFiles[0].text;
const { openHandoff, conversationFromHash } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const record = { sourceThreadId: "source", threadId: "receiver", display: "window" };
test("native handoff opens the receiver without navigating the source", async () => {
  const calls = [];
  assert.equal(await openHandoff(record, { current: id => calls.push(["current", id]) }, { openThreadWindow: async id => { calls.push(["window", id]); return true; } }), "window");
  assert.deepEqual(calls, [["window", "receiver"]]);
});
test("web/phone fallbacks and background handoff preserve their explicit behavior", async () => {
  const calls = [];
  const navigation = { current: id => calls.push(["current", id]), split: id => calls.push(["split", id]) };
  assert.equal(await openHandoff(record, navigation, {}), "split");
  assert.equal(await openHandoff(record, { current: navigation.current }, {}), "current");
  assert.equal(await openHandoff(record, navigation, {}, "background"), "background");
  assert.deepEqual(calls, [["split", "receiver"], ["current", "receiver"]]);
});
test("native window failure stays visible, and invalid self-handoffs never navigate", async () => {
  await assert.rejects(openHandoff(record, { current() { throw new Error("must not navigate"); } }, { openThreadWindow: async () => false }), /could not open/);
  await assert.rejects(openHandoff({ ...record, threadId: "source" }, { current() {} }, {}), /Invalid handoff/);
});
test("a conversation window only accepts a complete conversation id in its hash", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  assert.equal(conversationFromHash("#thread=" + id), id);
  for (const hash of ["#settings", "#thread=../../escape", "#thread=------------------------------------", "#thread=javascript:alert(1)"]) assert.equal(conversationFromHash(hash), null);
});
