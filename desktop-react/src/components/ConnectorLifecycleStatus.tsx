import { Clock3, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";


type ConnectorLifecycle = {
  id?: string;
  connected?: boolean;
  credentialSource?: string;
  refreshable?: boolean;
  accessTokenExpiresIn?: number | null;
  refreshTokenExpiresIn?: number | null;
};

function duration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Not reported";
  const seconds = Math.max(0, Number(value) || 0);
  if (seconds <= 0) return "Expired";
  if (seconds < 3600) return `${Math.max(1, Math.ceil(seconds / 60))} min`;
  if (seconds < 86_400) return `${(seconds / 3600).toFixed(1)} h`;
  return `${Math.ceil(seconds / 86_400)} d`;
}

function DetailRow({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="settings-detail-row">
      <div><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</div>
      <code title={value}>{value}</code>
    </div>
  );
}

export function ConnectorLifecycleStatus() {
  const [github, setGithub] = useState<ConnectorLifecycle | null>(null);

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const result = await window.loom.call<{ connectors?: ConnectorLifecycle[] }>("connector/list", {});
        if (disposed) return;
        const rows = Array.isArray(result.connectors) ? result.connectors : [];
        setGithub(rows.find((item) => item.id === "github") ?? null);
      } catch {
        if (!disposed) setGithub(null);
      }
    };
    void load();
    const unsubscribe = window.loom.onNotification((payload) => {
      if (payload.method === "connector/updated" || payload.method === "runtime/updated") void load();
    });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, []);

  if (!github?.connected) return null;

  const deviceOAuth = github.credentialSource === "device-oauth-keyring";
  return (
    <section className="settings-section">
      <div className="settings-section-heading">
        <h2>Credential lifecycle</h2>
        <p>GitHub token rotation is handled before a future model Step is sampled; existing Step bindings are never retargeted.</p>
      </div>
      <div className="settings-card settings-detail-list">
        <DetailRow
          label="Authentication"
          value={deviceOAuth ? "GitHub device OAuth" : (github.credentialSource || "Connected credential")}
          detail="Access and refresh secrets stay in the OS credential vault."
        />
        <DetailRow
          label="Automatic rotation"
          value={github.refreshable ? "Ready" : (deviceOAuth ? "Reconnect required when expired" : "Not required")}
          detail={github.refreshable ? "Loom refreshes inside a five-minute safety window." : "PAT, environment, and GitHub CLI credentials use their own lifetime."}
        />
        {github.accessTokenExpiresIn !== null && github.accessTokenExpiresIn !== undefined ? (
          <DetailRow label="Access token" value={`${duration(github.accessTokenExpiresIn)} remaining`} detail="The next Step rotates this credential before it reaches the safety window." />
        ) : null}
        {github.refreshTokenExpiresIn !== null && github.refreshTokenExpiresIn !== undefined ? (
          <DetailRow label="Refresh token" value={`${duration(github.refreshTokenExpiresIn)} remaining`} detail="Reconnect GitHub after the refresh credential itself expires." />
        ) : null}
      </div>
      {github.refreshable ? (
        <div className="settings-callout">
          <RefreshCw size={16} />
          <div><strong>Automatic renewal is active.</strong><span>Loom rotates the keychain-backed GitHub access token without writing secret material to connector JSON state.</span></div>
        </div>
      ) : deviceOAuth ? (
        <div className="settings-callout warning">
          <Clock3 size={16} />
          <div><strong>This device authorization is not refreshable.</strong><span>GitHub did not issue a refresh token for this application configuration; reconnect when the access token expires.</span></div>
        </div>
      ) : null}
    </section>
  );
}
