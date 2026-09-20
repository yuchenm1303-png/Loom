import { useCallback, useEffect, useState } from "react";
import type { LoomAccountSnapshot } from "../types/account";

const EMPTY_ACCOUNT: LoomAccountSnapshot = {
  configured: false,
  authenticated: false,
  user: null,
  serviceUrl: "",
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useAccount() {
  const [account, setAccount] = useState<LoomAccountSnapshot>(EMPTY_ACCOUNT);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const next = await window.loom.accountStatus<LoomAccountSnapshot>();
      setAccount(next);
      setError("");
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await window.loom.accountLogin<LoomAccountSnapshot>(email, password);
      setAccount(next);
      return true;
    } catch (cause) {
      setError(messageOf(cause));
      return false;
    } finally {
      setBusy(false);
      setReady(true);
    }
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await window.loom.accountRegister<LoomAccountSnapshot>(email, password);
      setAccount(next);
      return true;
    } catch (cause) {
      setError(messageOf(cause));
      return false;
    } finally {
      setBusy(false);
      setReady(true);
    }
  }, []);

  const logout = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const next = await window.loom.accountLogout<LoomAccountSnapshot>();
      setAccount(next);
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setBusy(false);
      setReady(true);
    }
  }, []);

  return { account, ready, busy, error, refresh, login, register, logout };
}
