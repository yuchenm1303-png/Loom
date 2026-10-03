import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const result = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/pendingUserMessage.ts", import.meta.url))], bundle: true, write: false, format: "esm", platform: "node" });
const { reconcilePendingUserMessage } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
const pending = { id: "pending-user-1", threadId: "a", type: "user_message", status: "sending", text: "你好" };

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
