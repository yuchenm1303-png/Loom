import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const result = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/pendingUserMessage.ts", import.meta.url))], bundle: true, write: false, format: "esm", platform: "node" });
const { reconcilePendingUserMessage, preservePendingUserIdentity } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
const pending = { id: "pending-user-1", threadId: "a", type: "user_message", status: "sending", text: "你好" };

test("attachment confirmations replace pending text and image-only messages", () => {
  for (const text of ["你好", ""]) {
    const sent = { ...pending, text, hasAttachments: true };
    const incoming = { id: "server", threadId: "a", type: "user_message",
      text: `${text}${text ? "\n\n" : ""}Attached files (already saved in this workspace):\n- screenshot.png — .loom/attachments/turn/screenshot.png (image, shown above)` };
    const resolved = preservePendingUserIdentity([sent], incoming);
    assert.equal(resolved.clientMessageId, sent.id);
    assert.deepEqual(reconcilePendingUserMessage([sent], resolved), []);
    assert.equal(resolved.text, incoming.text);
  }
});

test("turn identity handles changed text but never consumes a different turn", () => {
  const sent = { ...pending, turnId: "new-turn" };
  const incoming = { threadId: "a", type: "user_message", turnId: "new-turn", text: "normalized text" };
  assert.deepEqual(reconcilePendingUserMessage([sent], incoming), []);
  assert.deepEqual(reconcilePendingUserMessage([sent], { ...incoming, turnId: "old-turn", text: sent.text }), [sent]);
});

test("confirmation consumes only one send and normalizes line endings", () => {
  const first = { ...pending, text: "one\r\ntwo" };
  const second = { ...first, id: "pending-user-2" };
  assert.deepEqual(reconcilePendingUserMessage([first, second], {
    threadId: "a", type: "user_message", text: "one\ntwo",
  }), [second]);
});

test("malformed attachment manifests do not acknowledge a pending send", () => {
  const sent = { ...pending, hasAttachments: true };
  assert.deepEqual(reconcilePendingUserMessage([sent], {
    threadId: "a", type: "user_message", text: "你好\n\nAttached files (already saved in this workspace):\nordinary text",
  }), [sent]);
});

test("authoritative user message replaces its pending bubble without dropping history", () => {
  const history = { id: "old", threadId: "a", type: "assistant_message", text: "old" };
  assert.deepEqual(reconcilePendingUserMessage([history, pending], { threadId: "a", type: "user_message", text: "你好" }), [history]);
});
test("unrelated threads, steering, and incomplete notifications preserve pending input", () => {
  for (const incoming of [
    { threadId: "b", type: "user_message", text: "你好" },
    { threadId: "a", type: "user_message", text: "你好", source: "steering" },
    { threadId: "a", type: "user_message", text: "" },
    { threadId: "a", type: "assistant_message", text: "你好" },
  ]) assert.deepEqual(reconcilePendingUserMessage([pending], incoming), [pending]);
});

test("server confirmation retains the optimistic bubble identity and submission time", () => {
  const sent = { ...pending, clientMessageId: pending.id, submittedAt: "2026-10-06T08:00:00Z" };
  const incoming = { id: "server-user", turnId: "server-turn", threadId: "a", type: "user_message", text: "你好", status: "completed" };
  const resolved = preservePendingUserIdentity([sent], incoming);
  assert.equal(resolved.clientMessageId, sent.id);
  assert.equal(resolved.submittedAt, sent.submittedAt);
  assert.equal(resolved.id, incoming.id);
  assert.equal(resolved.turnId, incoming.turnId);
  assert.equal(incoming.clientMessageId, undefined);
  assert.equal(preservePendingUserIdentity([sent], { ...incoming, source: "steering" }).clientMessageId, undefined);
});
