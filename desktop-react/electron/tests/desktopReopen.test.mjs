import assert from "node:assert/strict";
import { test } from "node:test";
import { reopenDesktop } from "../../dist-electron/desktopReopen.js";
test("Desktop reopen retries while the old instance holds its lock", async () => {
  let launches = 0;
  assert.equal(await reopenDesktop(() => launches++, () => launches >= 3, 1, 5), true);
  assert.equal(launches, 3);
});
test("Desktop reopen avoids duplicates and bounds failed launches", async () => {
  let launches = 0;
  assert.equal(await reopenDesktop(() => launches++, () => true, 1, 3), true);
  assert.equal(launches, 0);
  assert.equal(await reopenDesktop(() => launches++, () => false, 1, 3), false);
  assert.equal(launches, 3);
});
