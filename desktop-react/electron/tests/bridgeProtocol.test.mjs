import { test } from "node:test";
import assert from "node:assert/strict";
import { invokeOptionalBridgeOperation } from "../../dist-electron/bridgeProtocol.js";

test("legacy advertised command sets reject optional extensions without aborting startup", async () => {
  const result = await invokeOptionalBridgeOperation("loom_model_bridge.py", "record-image-capabilities", async () => {
    throw new Error("usage: loom_model_bridge.py {list|resolve|describe-model|save|delete|set-active}");
  });
  assert.deepEqual(result, { supported: false });
});

test("supported extensions return their result", async () => {
  assert.deepEqual(await invokeOptionalBridgeOperation("bridge.py", "extension", async () => 3), { supported: true, value: 3 });
});

test("operational errors and usage failures for supported commands are never hidden", async () => {
  for (const message of ["permission denied", "Host offline", "usage: other.py {list}", "usage: bridge.py {list|extension}"]) {
    await assert.rejects(invokeOptionalBridgeOperation("bridge.py", "extension", async () => { throw new Error(message); }), { message });
  }
});
