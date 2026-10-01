import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime } from "../webBridge";

type HostState = "idle" | "checking" | "online" | "offline";

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const web = isLoomWebRuntime();
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) {
      setHostState("idle");
      setHostError("");
      return;
    }
    let cancelled = false;
    let connecting = false;

    const connectHost = async () => {
      if (connecting) return;
      connecting = true;
      if (!cancelled) setHostState("checking");
      try {
        await window.loom.connect();
        if (!cancelled) {
          setHostState("online");
          setHostError("");
        }
      } catch (cause) {
        if (!cancelled) {
          setHostState("offline");
          setHostError(cause instanceof Error ? cause.message : String(cause));
        }
      } finally {
        connecting = false;
      }
    };

    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<{ online?: boolean }>).detail;
      if (!detail?.online) {
        setHostState("offline");
        setHostError("Your Loom Host is offline.");
        return;
      }
      void connectHost();
    };

    window.addEventListener("loom:web-device-status", onDeviceStatus);
    void connectHost();
    return () => {
      cancelled = true;
      window.removeEventListener("loom:web-device-status", onDeviceStatus);
    };
  }, [account.account.authenticated, account.account.user, account.ready, web]);

  if (!web) return children;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom Web</h1>
            <p>Sign in to connect this browser to your Loom Host. Web and Desktop use the same local Agent, conversations, models, tools and approvals.</p>
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

  if (hostState !== "online") {
    return (
      <main className="boot-error" aria-label="Loom Host connection">
        <section className="boot-error-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>{hostState === "checking" ? "Connecting to Loom Host…" : "Loom Host is offline"}</h1>
          <p>
            {hostState === "checking"
              ? "Connecting this browser to the same Loom Agent running on your computer."
              : "Start Loom on your computer. The Loom Host can stay in the background after the Desktop window is closed, and this page will reconnect automatically."}
          </p>
          {hostState === "offline" && hostError ? <p className="boot-error-detail">{hostError}</p> : null}
        </section>
      </main>
    );
  }

  return children;
}
