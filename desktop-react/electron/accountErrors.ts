/**
 * Translates anything thrown by the account client into a plain, serialisable
 * payload that survives the Electron IPC boundary.
 *
 * `ipcRenderer.invoke` only carries an `Error`'s message across, so the machine
 * readable `code` from the account service used to be lost and the renderer had
 * to render raw English server text inside a Chinese UI. Returning a structured
 * result from the handlers keeps the code intact.
 *
 * This module deliberately does not import `./accountClient.js`: that file pulls
 * in `electron`, which would make this mapping untestable outside Electron. The
 * error is duck-typed instead.
 *
 * `AccountErrorPayload` mirrors `LoomAccountError` in `src/types/account.ts`.
 * Keep the two in sync.
 */

export interface AccountErrorPayload {
  code: string;
  message: string;
  status?: number;
}

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  const text = String(error ?? "").trim();
  return text || "The account request failed.";
}

/** Omit `status` entirely rather than carrying an `undefined` key across IPC. */
function payload(code: string, message: string, status?: number): AccountErrorPayload {
  return status === undefined ? { code, message } : { code, message, status };
}

export function accountErrorPayload(error: unknown): AccountErrorPayload {
  const candidate = (error ?? {}) as { code?: unknown; status?: unknown; name?: unknown };
  const code = typeof candidate.code === "string" ? candidate.code.trim() : "";
  // A status of 0 marks a request that never produced an HTTP response.
  const status = typeof candidate.status === "number" && candidate.status > 0 ? candidate.status : undefined;

  if (code) return payload(code, messageOf(error), status);

  if (candidate.name === "AbortError") {
    return {
      code: "ACCOUNT_REQUEST_TIMEOUT",
      message: "The account service did not respond in time.",
    };
  }

  // A `fetch` that never reached the service throws a bare TypeError.
  if (error instanceof TypeError) {
    return {
      code: "ACCOUNT_SERVICE_UNREACHABLE",
      message: "Could not reach the account service.",
    };
  }

  return payload("ACCOUNT_REQUEST_FAILED", messageOf(error), status);
}
