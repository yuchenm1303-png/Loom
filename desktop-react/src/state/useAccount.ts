import { useCallback, useEffect, useState } from "react";
import type { LoomAccountError, LoomAccountResult, LoomAccountSnapshot } from "../types/account";

const EMPTY_ACCOUNT: LoomAccountSnapshot = {
  configured: false,
  reachable: false,
  authenticated: false,
  user: null,
  serviceUrl: "",
};

/**
 * The account IPC handlers resolve with a result union, so a rejected promise
 * now only means the IPC channel itself failed (for example a missing handler).
 * Normalise it into the same error shape the dialog already understands.
 */
function transportFailure(cause: unknown): LoomAccountError {
  return {
    code: "ACCOUNT_REQUEST_FAILED",
    message: cause instanceof Error ? cause.message : String(cause),
  };
}

export function useAccount() {
  const [account, setAccount] = useState<LoomAccountSnapshot>(EMPTY_ACCOUNT);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<LoomAccountError | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const refresh = useCallback(async () => {
    try {
      const result = await window.loom.accountStatus();
      if (result.ok) {
        setAccount(result.snapshot);
        setError(null);
      } else {
        setError(result.error);
      }
    } catch (cause) {
      setError(transportFailure(cause));
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = useCallback(
    async (call: () => Promise<LoomAccountResult>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        const result = await call();
        if (result.ok) {
          setAccount(result.snapshot);
          return true;
        }
        setError(result.error);
        return false;
      } catch (cause) {
        setError(transportFailure(cause));
        return false;
      } finally {
        setBusy(false);
        setReady(true);
      }
    },
    [],
  );

  const login = useCallback(
    (email: string, password: string) => run(() => window.loom.accountLogin(email, password)),
    [run],
  );

  const register = useCallback(
    (email: string, password: string) => run(() => window.loom.accountRegister(email, password)),
    [run],
  );

  const logout = useCallback(async () => {
    await run(() => window.loom.accountLogout());
  }, [run]);

  return { account, ready, busy, error, clearError, refresh, login, register, logout };
}
