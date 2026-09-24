/**
 * Live deployment self-check. Deliberately NOT part of `test:electron` — it
 * talks to a real service and creates a real account on every run.
 *
 * It drives the compiled Electron account client (dist-electron/accountClient.js)
 * against a deployed account service, so the shipped code path is exercised end
 * to end rather than against a mock: config-file lookup, HTTPS enforcement,
 * encrypted token storage, session restore, coded errors, logout.
 *
 *   npm run check:live-account                       # uses the default below
 *   LOOM_ACCOUNT_LIVE_URL=https://host/v1 npm run check:live-account
 *
 * Run `npm run build:electron` first, or the compiled output may be stale.
 * Delete the `live-*@example.com` accounts afterwards; the script prints the
 * address it created so you can find it.
 */

import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { mock, test } from "node:test";

const SERVICE_URL = (process.env.LOOM_ACCOUNT_LIVE_URL || "https://account.smirel.com/v1").replace(/\/+$/, "");

const home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-live-home-"));
const userData = await fs.mkdtemp(path.join(os.tmpdir(), "loom-live-userdata-"));

// The real config file, exactly as the client reads it.
await fs.mkdir(path.join(home, ".loom"), { recursive: true });
await fs.writeFile(
  path.join(home, ".loom", "account-service.json"),
  JSON.stringify({ baseUrl: SERVICE_URL }),
);

mock.module("electron", {
  namedExports: {
    app: {
      isPackaged: true,
      getPath: (name) => (name === "home" ? home : userData),
    },
    safeStorage: {
      isEncryptionAvailable: () => true,
      encryptString: (value) => Buffer.from(value, "utf8").toString("base64"),
      decryptString: (buffer) => Buffer.from(buffer.toString("utf8"), "base64").toString("utf8"),
    },
  },
});

delete process.env.LOOM_ACCOUNT_API_BASE_URL;

const { LoomAccountClient } = await import("../dist-electron/accountClient.js");

const email = `live-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}@example.com`;
const password = "correct-horse-battery";

test("the shipped client signs in against the live service", async () => {
  const client = new LoomAccountClient();

  const before = await client.status();
  assert.equal(before.configured, true, "the on-disk config should be picked up");
  assert.equal(before.serviceUrl, SERVICE_URL);
  assert.equal(before.reachable, true, "the live service should be reachable");
  assert.equal(before.authenticated, false);

  const registered = await client.register(email, password);
  assert.equal(registered.authenticated, true);
  assert.equal(registered.user.email, email);

  // The session must be on disk encrypted, never in the clear.
  const raw = await fs.readFile(path.join(userData, "loom-account-session.bin"), "utf8");
  assert.equal(raw.includes("loom_access_"), false, "access token must not be readable on disk");
  assert.equal(raw.includes("loom_refresh_"), false, "refresh token must not be readable on disk");

  // A fresh client reads the stored session back and validates it with /auth/me.
  const restored = await new LoomAccountClient().status();
  assert.equal(restored.authenticated, true);
  assert.equal(restored.user.email, email);

  // Wrong password must come back as a coded failure, not a raw message.
  const other = new LoomAccountClient();
  await assert.rejects(
    () => other.login(email, "definitely-wrong"),
    (error) => {
      assert.equal(error.code, "INVALID_CREDENTIALS");
      assert.equal(error.status, 401);
      return true;
    },
  );

  const signedOut = await client.logout();
  assert.equal(signedOut.authenticated, false);

  await assert.rejects(() => fs.readFile(path.join(userData, "loom-account-session.bin")));
  console.log("live account check passed for", email);
});
