/**
 * Tests for the Electron-side Loom account client.
 *
 * ``accountClient.ts`` owns the parts of sign-in that are easy to get wrong and
 * expensive to debug in a packaged build: when to pre-refresh, what to do when
 * ``/auth/me`` rejects a token, what happens when refresh fails, and whether
 * tokens ever reach disk in the clear. It imports ``electron`` for ``app`` and
 * ``safeStorage``, so this file mocks that module and drives the compiled
 * output in ``dist-electron`` — run ``npm run build:electron`` first, which the
 * ``test:electron`` script does for you.
 *
 * ``node --experimental-test-module-mocks`` is required for ``mock.module``.
 */

import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { beforeEach, mock, test } from "node:test";

const state = {
  isPackaged: false,
  home: "",
  userData: "",
  encryptionAvailable: true,
};

const fakeApp = {
  get isPackaged() {
    return state.isPackaged;
  },
  getPath(name) {
    if (name === "home") return state.home;
    if (name === "userData") return state.userData;
    throw new Error(`unexpected app.getPath(${name})`);
  },
};

const fakeSafeStorage = {
  isEncryptionAvailable: () => state.encryptionAvailable,
  // Stand-in for the OS keychain. It must actually obscure the payload, or the
  // "never stored in the clear" assertion below would pass for the wrong
  // reason; the real implementation uses DPAPI / Keychain / libsecret.
  encryptString: (value) =>
    Buffer.from(`sealed:${Buffer.from(value, "utf8").toString("base64")}`, "utf8"),
  decryptString: (buffer) => {
    const text = Buffer.from(buffer).toString("utf8");
    if (!text.startsWith("sealed:")) throw new Error("ciphertext was not sealed");
    return Buffer.from(text.slice("sealed:".length), "base64").toString("utf8");
  },
};

mock.module("electron", {
  namedExports: { app: fakeApp, safeStorage: fakeSafeStorage },
});

const { LoomAccountClient } = await import("../../dist-electron/accountClient.js");

const SERVICE_URL = "https://account.test/v1";
const USER = { id: 7, email: "user@example.com", display_name: "User", status: "active" };

let calls;

function installFetch(routes) {
  calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const pathname = new URL(url).pathname;
    const method = init.method || "GET";
    const headers = new Headers(init.headers);
    calls.push({ pathname, method, body: init.body, headers });
    const handler = routes[pathname];
    if (!handler) throw new Error(`unexpected request: ${method} ${pathname}`);
    const result = handler(init, calls.length);
    if (result instanceof Error) throw result;
    return result;
  };
}

function reply(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function sessionBody(overrides = {}) {
  return {
    access_token: "loom_access_fresh",
    refresh_token: "loom_refresh_fresh",
    expires_in: 900,
    token_type: "Bearer",
    user: USER,
    ...overrides,
  };
}

function sessionPath() {
  return path.join(state.userData, "loom-account-session.bin");
}

async function seedSession(session) {
  await fs.writeFile(sessionPath(), fakeSafeStorage.encryptString(JSON.stringify(session)), { mode: 0o600 });
}

function newClient() {
  process.env.LOOM_ACCOUNT_API_BASE_URL = SERVICE_URL;
  return new LoomAccountClient();
}

beforeEach(async () => {
  state.isPackaged = false;
  state.encryptionAvailable = true;
  state.home = await fs.mkdtemp(path.join(os.tmpdir(), "loom-account-home-"));
  state.userData = await fs.mkdtemp(path.join(os.tmpdir(), "loom-account-userdata-"));
  process.env.LOOM_ACCOUNT_API_BASE_URL = SERVICE_URL;
  installFetch({});
});

test("an unconfigured build reports itself instead of calling out", async () => {
  delete process.env.LOOM_ACCOUNT_API_BASE_URL;
  state.isPackaged = true;

  const snapshot = await new LoomAccountClient().status();

  assert.equal(snapshot.configured, false);
  assert.equal(snapshot.authenticated, false);
  assert.equal(snapshot.user, null);
  assert.deepEqual(calls, []);
});

test("plain HTTP is refused for a remote account host", async () => {
  process.env.LOOM_ACCOUNT_API_BASE_URL = "http://account.example.com/v1";
  state.isPackaged = true;

  const snapshot = await new LoomAccountClient().status();

  assert.equal(snapshot.configured, false);
  assert.equal(snapshot.serviceUrl, "");
  assert.deepEqual(calls, []);
});

test("plain HTTP is allowed on loopback, and HTTPS anywhere", async () => {
  process.env.LOOM_ACCOUNT_API_BASE_URL = "http://127.0.0.1:8787/v1";
  assert.equal((await new LoomAccountClient().status()).serviceUrl, "http://127.0.0.1:8787/v1");

  process.env.LOOM_ACCOUNT_API_BASE_URL = "https://account.example.com/v1";
  assert.equal((await new LoomAccountClient().status()).serviceUrl, "https://account.example.com/v1");
});

test("login stores the session encrypted and never in the clear", async () => {
  installFetch({
    "/v1/auth/login": () => reply(200, sessionBody()),
  });

  const snapshot = await newClient().login("user@example.com", "correct-horse");

  assert.equal(snapshot.authenticated, true);
  assert.equal(snapshot.user.email, "user@example.com");

  const onDisk = await fs.readFile(sessionPath());
  assert.ok(!onDisk.toString("utf8").includes("loom_access_fresh"), "access token must not be stored in the clear");
  assert.ok(!onDisk.toString("utf8").includes("loom_refresh_fresh"), "refresh token must not be stored in the clear");
  assert.match(onDisk.toString("utf8"), /^sealed:/);
});

test("a session is kept in memory but not written when safeStorage is unavailable", async () => {
  state.encryptionAvailable = false;
  installFetch({ "/v1/auth/login": () => reply(200, sessionBody()) });

  const client = newClient();
  assert.equal((await client.login("user@example.com", "correct-horse")).authenticated, true);

  await assert.rejects(() => fs.readFile(sessionPath()));
});

test("an incomplete session from the service is rejected", async () => {
  installFetch({
    "/v1/auth/login": () => reply(200, sessionBody({ refresh_token: "" })),
  });

  await assert.rejects(
    () => newClient().login("user@example.com", "correct-horse"),
    /incomplete sign-in session/,
  );
});

test("a live access token is validated without refreshing", async () => {
  installFetch({
    "/v1/auth/me": () => reply(200, { user: USER }),
  });
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, true);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/me"]);
});

test("a token inside the 30 second window is pre-refreshed", async () => {
  installFetch({
    "/v1/auth/refresh": () => reply(200, sessionBody({ access_token: "loom_access_rotated" })),
    "/v1/auth/me": () => reply(200, { user: USER }),
  });
  await seedSession({
    accessToken: "loom_access_expiring",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, true);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/refresh", "/v1/auth/me"]);
});

test("a 401 from /auth/me triggers exactly one recovery refresh", async () => {
  installFetch({
    "/v1/auth/me": (_init, nth) =>
      nth === 1 ? reply(401, { error: { code: "INVALID_TOKEN" } }) : reply(200, { user: USER }),
    "/v1/auth/refresh": () => reply(200, sessionBody({ access_token: "loom_access_recovered" })),
  });
  await seedSession({
    accessToken: "loom_access_stale",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, true);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/me", "/v1/auth/refresh"]);
});

test("a failed refresh clears the local session instead of looping", async () => {
  installFetch({
    "/v1/auth/me": () => reply(401, { error: { code: "INVALID_TOKEN" } }),
    "/v1/auth/refresh": () => reply(401, { error: { code: "INVALID_REFRESH_TOKEN" } }),
  });
  await seedSession({
    accessToken: "loom_access_stale",
    refreshToken: "loom_refresh_dead",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, false);
  assert.equal(snapshot.user, null);
  assert.equal(calls.length, 2, "must not retry past the first refresh failure");
  await assert.rejects(() => fs.readFile(sessionPath()), "the dead session file should be gone");
});

test("logout clears local credentials even when the service is unreachable", async () => {
  installFetch({});
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const client = newClient();
  globalThis.fetch = async () => {
    throw new Error("network down");
  };
  const snapshot = await client.logout();

  assert.equal(snapshot.authenticated, false);
  await assert.rejects(() => fs.readFile(sessionPath()));
});

test("logout revokes the refresh token on the service when it is reachable", async () => {
  installFetch({ "/v1/auth/logout": () => reply(200, { ok: true }) });
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().logout();

  assert.equal(snapshot.authenticated, false);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/logout"]);
  assert.deepEqual(JSON.parse(calls[0].body), { refresh_token: "loom_refresh_live" });
});

test("a corrupt session file is treated as signed out, not as a crash", async () => {
  installFetch({ "/v1/auth/me": () => reply(401, { error: { code: "MISSING_TOKEN" } }) });
  await fs.writeFile(sessionPath(), Buffer.from("not ciphertext at all", "utf8"));

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, false);
  assert.equal(snapshot.reachable, true, "a 401 still proves the service is answering");
});

test("with no stored session, reachability is probed rather than assumed", async () => {
  installFetch({ "/v1/auth/me": () => reply(401, { error: { code: "MISSING_TOKEN" } }) });

  const snapshot = await newClient().status();

  assert.equal(snapshot.authenticated, false);
  assert.equal(snapshot.reachable, true);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/me"]);
});

test("with no stored session, an unreachable service is reported as unreachable", async () => {
  // Nothing is configured to answer, so the probe fails at the transport level.
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };

  const snapshot = await newClient().status();

  assert.equal(snapshot.configured, true);
  assert.equal(snapshot.authenticated, false);
  assert.equal(snapshot.reachable, false, "a configured URL alone must not imply reachable");
});

test("the authorization header carries the stored access token", async () => {
  installFetch({ "/v1/auth/me": () => reply(200, { user: USER }) });
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  await newClient().status();

  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].pathname, "/v1/auth/me");
  assert.equal(calls[0].headers.get("Authorization"), "Bearer loom_access_live");
  assert.equal(calls[0].headers.get("Accept"), "application/json");
});

test("an outage keeps the stored session instead of signing the user out", async () => {
  // A dropped connection must not be mistaken for a rejected credential: the
  // stored session has to survive so the next launch can recover.
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };

  const snapshot = await newClient().status();

  assert.equal(snapshot.configured, true);
  assert.equal(snapshot.reachable, false, "an unreachable service must be reported");
  assert.equal(snapshot.authenticated, true, "the cached identity stays visible");
  assert.equal(snapshot.user.email, USER.email);
  await fs.readFile(sessionPath(), "utf8");
});

test("an outage during the pre-refresh also keeps the stored session", async () => {
  await seedSession({
    accessToken: "loom_access_stale",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() - 1000,
    user: USER,
  });
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };

  const snapshot = await newClient().status();

  assert.equal(snapshot.reachable, false);
  assert.equal(snapshot.authenticated, true);
  await fs.readFile(sessionPath(), "utf8");
});

test("a reachable service is reported as reachable", async () => {
  installFetch({ "/v1/auth/me": () => reply(200, { user: USER }) });
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.reachable, true);
});

test("a 5xx from /auth/me is an outage, not a dead session", async () => {
  installFetch({ "/v1/auth/me": () => reply(503, { error: { code: "UNAVAILABLE" } }) });
  await seedSession({
    accessToken: "loom_access_live",
    refreshToken: "loom_refresh_live",
    expiresAt: Date.now() + 10 * 60 * 1000,
    user: USER,
  });

  const snapshot = await newClient().status();

  assert.equal(snapshot.reachable, false);
  assert.equal(snapshot.authenticated, true);
  assert.deepEqual(calls.map((call) => call.pathname), ["/v1/auth/me"], "must not attempt a refresh");
  await fs.readFile(sessionPath(), "utf8");
});

test("an unconfigured build rejects sign-in with a stable code", async () => {
  delete process.env.LOOM_ACCOUNT_API_BASE_URL;
  state.isPackaged = true;

  await assert.rejects(
    () => new LoomAccountClient().login("user@example.com", "correct-horse"),
    (error) => {
      assert.equal(error.code, "ACCOUNT_SERVICE_UNCONFIGURED");
      assert.equal(error.status, 0);
      return true;
    },
  );
});

test("a service that never answers is reported as unreachable, not as a bad password", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };

  await assert.rejects(
    () => newClient().login("user@example.com", "correct-horse"),
    (error) => {
      assert.equal(error.code, "ACCOUNT_SERVICE_UNREACHABLE");
      return true;
    },
  );
});

test("a request that never returns is reported as a timeout", async () => {
  globalThis.fetch = async () => {
    const abort = new Error("aborted");
    abort.name = "AbortError";
    throw abort;
  };

  await assert.rejects(
    () => newClient().login("user@example.com", "correct-horse"),
    (error) => {
      assert.equal(error.code, "ACCOUNT_REQUEST_TIMEOUT");
      return true;
    },
  );
});
