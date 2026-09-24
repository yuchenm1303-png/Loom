/**
 * Tests for the account error translator that sits between the account client
 * and the renderer.
 *
 * The value under test is narrow but load-bearing: Electron's IPC only carries
 * an ``Error``'s message across the boundary, so ``code`` has to be extracted
 * here and returned as plain data. Getting it wrong means the UI can only show
 * raw English server text.
 *
 * ``accountErrors.ts`` deliberately avoids importing ``./accountClient.js``
 * (which would drag in ``electron``), so this file needs no module mocking —
 * it only imports the compiled output in ``dist-electron``.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

const { accountErrorPayload } = await import("../../dist-electron/accountErrors.js");

test("a coded service error keeps its code, status, and message", () => {
  const error = Object.assign(new Error("Email or password is incorrect."), {
    code: "INVALID_CREDENTIALS",
    status: 401,
  });

  assert.deepEqual(accountErrorPayload(error), {
    code: "INVALID_CREDENTIALS",
    message: "Email or password is incorrect.",
    status: 401,
  });
});

test("a transport failure without an HTTP response drops the status", () => {
  const error = Object.assign(new Error("Loom Account Service is not configured."), {
    code: "ACCOUNT_SERVICE_UNCONFIGURED",
    status: 0,
  });

  assert.deepEqual(accountErrorPayload(error), {
    code: "ACCOUNT_SERVICE_UNCONFIGURED",
    message: "Loom Account Service is not configured.",
  });
});

test("an abort is reported as a timeout", () => {
  const error = new Error("This operation was aborted");
  error.name = "AbortError";

  assert.deepEqual(accountErrorPayload(error), {
    code: "ACCOUNT_REQUEST_TIMEOUT",
    message: "The account service did not respond in time.",
  });
});

test("a fetch TypeError is reported as unreachable", () => {
  assert.deepEqual(accountErrorPayload(new TypeError("fetch failed")), {
    code: "ACCOUNT_SERVICE_UNREACHABLE",
    message: "Could not reach the account service.",
  });
});

test("an uncoded Error falls back to a generic failure and keeps its message", () => {
  assert.deepEqual(accountErrorPayload(new Error("something else broke")), {
    code: "ACCOUNT_REQUEST_FAILED",
    message: "something else broke",
  });
});

test("a thrown non-Error still yields a usable payload", () => {
  assert.deepEqual(accountErrorPayload("boom"), {
    code: "ACCOUNT_REQUEST_FAILED",
    message: "boom",
  });
});

test("nothing thrown yields a payload instead of crashing the handler", () => {
  assert.equal(accountErrorPayload(undefined).code, "ACCOUNT_REQUEST_FAILED");
  assert.equal(accountErrorPayload(null).code, "ACCOUNT_REQUEST_FAILED");
  assert.equal(accountErrorPayload(undefined).message.length > 0, true);
});

test("a blank code is ignored rather than forwarded", () => {
  const error = Object.assign(new Error("nope"), { code: "   " });

  assert.equal(accountErrorPayload(error).code, "ACCOUNT_REQUEST_FAILED");
});
