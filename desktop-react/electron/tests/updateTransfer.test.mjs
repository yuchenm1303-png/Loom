import { test } from "node:test";
import assert from "node:assert/strict";
import { updateTransferDeadline } from "../../dist-electron/updateTransfer.js";
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

test("slow transfers survive beyond the original deadline while bytes keep arriving", async () => {
  const transfer = updateTransferDeadline(new AbortController().signal, 100);
  try {
    for (let index = 0; index < 5; index++) {
      await wait(35);
      assert.equal(transfer.signal.aborted, false);
      transfer.progress();
    }
    await wait(140);
    assert.equal(transfer.signal.aborted, true, "stalled transfers must still stop");
  } finally { transfer.dispose(); }
});

test("caller cancellation is immediate and cleanup releases the transfer", () => {
  const parent = new AbortController();
  const transfer = updateTransferDeadline(parent.signal);
  const reason = new Error("user cancelled");
  parent.abort(reason);
  assert.equal(transfer.signal.reason, reason);
  transfer.dispose();
  const alreadyCancelled = updateTransferDeadline(parent.signal);
  assert.equal(alreadyCancelled.signal.aborted, true);
  alreadyCancelled.dispose();
});
