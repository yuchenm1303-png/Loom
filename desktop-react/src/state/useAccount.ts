import { useCallback, useEffect, useRef, useState } from "react";
import type {
  LoomAccountError,
  LoomAccountResult,
  LoomAccountSnapshot,
  LoomAuthCapabilities,
  LoomAuthChallenge,
  LoomAuthChallengeResult,
} from "../types/account";

const EMPTY_ACCOUNT: LoomAccountSnapshot = {
  configured: false,
  reachable: false,
  authenticated: false,
  user: null,
  serviceUrl: "",
};

const EMPTY_CAPABILITIES: LoomAuthCapabilities = {
  emailVerification: false,
  passwordReset: false,
  google: false,
  github: false,
  legacyRegistration: true,
};

function transportFailure(cause: unknown): LoomAccountError {
  return {
    code: "ACCOUNT_REQUEST_FAILED",
    message: cause instanceof Error ? cause.message : String(cause),
  };
}

export function useAccount() {
  const revision = useRef(0);
  const mutating = useRef(false);
  const [account, setAccount] = useState<LoomAccountSnapshot>(EMPTY_ACCOUNT);
  const [capabilities, setCapabilities] = useState<LoomAuthCapabilities>(EMPTY_CAPABILITIES);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<LoomAccountError | null>(null);

  const clearError = useCallback(() => setError(null), []);

  const refresh = useCallback(async () => {
    if (mutating.current) return;
    const current = ++revision.current;
    try {
      const result = await window.loom.accountStatus();
      if (current !== revision.current) return;
      if (result.ok) {
        setAccount(result.snapshot);
        setError(null);
      } else {
        setError(result.error);
      }
    } catch (cause) {
      if (current !== revision.current) return;
      setError(transportFailure(cause));
    } finally {
      if (current === revision.current) setReady(true);
    }
  }, []);

  const refreshCapabilities = useCallback(async () => {
    try {
      const result = await window.loom.accountCapabilities();
      if (result.ok) setCapabilities(result.capabilities);
    } catch {
      // Older/unreachable account services keep the conservative defaults.
    }
  }, []);

  useEffect(() => {
    void Promise.all([refresh(), refreshCapabilities()]);
  }, [refresh, refreshCapabilities]);

  useEffect(() => window.loom.onAccountOAuthResult((result) => {
    if (result.ok) void refresh();
    else setError(result.error || {
      code: "OAUTH_PROVIDER_FAILED",
      message: "Quick sign-in could not be completed.",
    });
  }), [refresh]);

  useEffect(() => {
    const handleAccountRefresh = () => { void refresh(); };
    window.addEventListener("loom:account-refresh", handleAccountRefresh);
    window.addEventListener("focus", handleAccountRefresh);
    window.addEventListener("online", handleAccountRefresh);
    window.addEventListener("loom:web-host-reconnected", handleAccountRefresh);
    return () => {
      window.removeEventListener("loom:account-refresh", handleAccountRefresh);
      window.removeEventListener("focus", handleAccountRefresh);
      window.removeEventListener("online", handleAccountRefresh);
      window.removeEventListener("loom:web-host-reconnected", handleAccountRefresh);
    };
  }, [refresh]);

  const run = useCallback(
    async (call: () => Promise<LoomAccountResult>): Promise<boolean> => {
      const current = ++revision.current;
      mutating.current = true;
      setBusy(true);
      setError(null);
      try {
        const result = await call();
        if (current !== revision.current) return false;
        if (result.ok) {
          setAccount(result.snapshot);
          return true;
        }
        setError(result.error);
        return false;
      } catch (cause) {
        if (current !== revision.current) return false;
        setError(transportFailure(cause));
        return false;
      } finally {
        if (current === revision.current) {
          mutating.current = false;
          setBusy(false);
          setReady(true);
        }
      }
    },
    [],
  );

  const runChallenge = useCallback(
    async (call: () => Promise<LoomAuthChallengeResult>): Promise<LoomAuthChallenge | null> => {
      setBusy(true);
      setError(null);
      try {
        const result = await call();
        if (result.ok) return result.challenge;
        setError(result.error);
        return null;
      } catch (cause) {
        setError(transportFailure(cause));
        return null;
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

  const registerStart = useCallback(
    (email: string, password: string) => runChallenge(() => window.loom.accountRegisterStart(email, password)),
    [runChallenge],
  );

  const verifyEmail = useCallback(
    (challengeId: string, code: string) => run(() => window.loom.accountVerifyEmail(challengeId, code)),
    [run],
  );

  const resendEmail = useCallback(
    (challengeId: string) => runChallenge(() => window.loom.accountResendEmail(challengeId)),
    [runChallenge],
  );

  const forgotPassword = useCallback(
    (email: string) => runChallenge(() => window.loom.accountForgotPassword(email)),
    [runChallenge],
  );

  const resetPassword = useCallback(
    (challengeId: string, code: string, password: string) => run(() => window.loom.accountResetPassword(challengeId, code, password)),
    [run],
  );

  const oauthExchange = useCallback(
    (code: string) => run(() => window.loom.accountOAuthExchange(code)),
    [run],
  );

  const updateProfile = useCallback(
    (displayName: string, avatarDataUrl: string) => run(() => window.loom.accountUpdateProfile(displayName, avatarDataUrl)),
    [run],
  );

  const logout = useCallback(async () => {
    await run(() => window.loom.accountLogout());
  }, [run]);

  return {
    account,
    capabilities,
    ready,
    busy,
    error,
    clearError,
    refresh,
    refreshCapabilities,
    login,
    register,
    registerStart,
    verifyEmail,
    resendEmail,
    forgotPassword,
    resetPassword,
    oauthExchange,
    updateProfile,
    logout,
  };
}
