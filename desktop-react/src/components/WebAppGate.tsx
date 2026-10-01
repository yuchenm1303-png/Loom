import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime, localWebDeviceId } from "../webBridge";

type HostState = "idle" | "checking" | "online" | "offline" | "unbound";

type DeviceStatus = {
  online?: boolean;
  selectedDeviceId?: string | null;
  device?: { id?: string; name?: string } | null;
};

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const web = isLoomWebRuntime();
  const localDeviceId = web ? localWebDeviceId() : "";
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
    if (!localDeviceId) {
      setHostState("unbound");
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
          const error = cause as Error & { code?: string };
          setHostState(error.code === "HOST_NOT_SELECTED" ? "unbound" : "offline");
          setHostError(error.message || String(cause));
        }
      } finally {
        connecting = false;
      }
    };

    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<DeviceStatus>).detail;
      if (detail?.selectedDeviceId && detail.selectedDeviceId !== localDeviceId) return;
      if (!detail?.online) {
        setHostState("offline");
        setHostError("Loom on this computer is offline.");
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
  }, [account.account.authenticated, account.account.user, account.ready, localDeviceId, web]);

  if (!web) return children;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom Web</h1>
            <p>Sign in to use Loom on this computer. Loom Web will not silently connect to another computer on your account.</p>
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
    const unbound = hostState === "unbound";
    const checking = hostState === "checking" || hostState === "idle";
    return (
      <main className="boot-error" aria-label="Loom Host connection">
        <section className="boot-error-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>
            {unbound
              ? "Connect Loom on this computer"
              : checking
                ? "Connecting to this computer…"
                : "Loom on this computer is offline"}
          </h1>
          <p>
            {unbound
              ? "Open Loom on this computer, then choose “Open Loom Web” from the Loom tray once. This browser will remember this computer. Other computers are never selected automatically; remote control belongs in Loom Remote."
              : checking
                ? "Connecting this browser to the Loom Host running on this computer."
                : "Start Loom on this computer. Its Host can stay in the background after the Desktop window is closed, and this page will reconnect automatically."}
          </p>
          {!unbound && !checking && hostError ? <p className="boot-error-detail">{hostError}</p> : null}
        </section>
      </main>
    );
  }

  return children;
}