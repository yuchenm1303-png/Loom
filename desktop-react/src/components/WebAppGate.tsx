import type { ReactNode } from "react";
import { useEffect } from "react";
import { AccountDialog } from "./AccountDialog";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime } from "../webBridge";

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const web = isLoomWebRuntime();

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  if (!web) return children;
  if (account.ready && account.account.authenticated && account.account.user) return children;

  return (
    <>
      <main className="boot-error" aria-label="Loom Web sign in">
        <section className="boot-error-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>Loom Web</h1>
          <p>Sign in to use the independent Loom Web Agent. Desktop is optional and only needed for local-device actions.</p>
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