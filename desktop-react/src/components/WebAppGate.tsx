import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime } from "../webBridge";

type DesktopState = "idle" | "checking" | "online" | "offline";

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause || "");
}

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const web = isLoomWebRuntime();
  const [desktopState, setDesktopState] = useState<DesktopState>(web ? "idle" : "online");
  const [desktopError, setDesktopError] = useState("");

  const probeDesktop = useCallback(async () => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) return;
    setDesktopState("checking");
    setDesktopError("");
    try {
      await window.loom.connect();
      setDesktopState("online");
    } catch (cause) {
      setDesktopError(errorMessage(cause));
      setDesktopState("offline");
    }
  }, [account.account.authenticated, account.account.user, account.ready, web]);

  useEffect(() => {
    if (!web) return;
    const refresh = () => {
      setDesktopState("idle");
      void account.refresh();
    };
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) {
      if (web) setDesktopState("idle");
      return;
    }
    if (desktopState === "idle") void probeDesktop();
  }, [account.account.authenticated, account.account.user, account.ready, desktopState, probeDesktop, web]);

  useEffect(() => {
    if (!web || desktopState !== "offline") return;
    const timer = window.setTimeout(() => void probeDesktop(), 5_000);
    return () => window.clearTimeout(timer);
  }, [desktopState, probeDesktop, web]);

  if (!web) return children;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom Web</h1>
            <p>Sign in, then keep Loom Desktop open on the computer you want to control.</p>
          </section>
        </main>
        <AccountDialog
          open
          account={account.account}
          ready={account.ready}
          busy={account.busy}
          error={account.error}
          onClose={() => undefined}
          onClearError={account.clearError}
          onRetry={account.refresh}
          onLogin={account.login}
          onRegister={account.register}
          onLogout={account.logout}
        />
      </>
    );
  }

  if (desktopState !== "online") {
    const checking = desktopState === "checking" || desktopState === "idle";
    return (
      <main className="boot-error" aria-label="Loom Desktop connection status">
        <section className="boot-error-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>{checking ? "Connecting to Loom Desktop" : "Loom Desktop is offline"}</h1>
          <p>
            {checking
              ? "Looking for a signed-in Loom Desktop on this account…"
              : desktopError || "Open the latest Loom Desktop and sign in to the same account. This page will reconnect automatically."}
          </p>
          {!checking ? (
            <button className="button primary" type="button" onClick={() => void probeDesktop()}>
              Retry now
            </button>
          ) : null}
        </section>
      </main>
    );
  }

  return children;
}
