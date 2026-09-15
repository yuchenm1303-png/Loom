import {
  Check,
  CircleAlert,
  Copy,
  Github,
  KeyRound,
  Link2,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";


type ConnectorStatus = {
  id?: string;
  name?: string;
  connected?: boolean;
  enabled?: boolean;
  account?: string;
  credentialSource?: string;
  scopes?: string;
  bindingId?: string;
  githubCliAvailable?: boolean;
  deviceFlowAvailable?: boolean;
  error?: string;
};

type AuthorizationState = {
  sessionId: string;
  mode?: string;
  status?: string;
  userCode?: string;
  verificationUrl?: string;
  expiresIn?: number;
  pollInterval?: number;
};

interface ConnectorsSettingsProps {
  running: boolean;
}

function StatusPill({ connected }: { connected: boolean }) {
  return (
    <span className={`settings-status-pill ${connected ? "ready" : "off"}`}>
      <span className="settings-status-dot" />
      {connected ? "Connected" : "Not connected"}
    </span>
  );
}

function DetailRow({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="settings-detail-row">
      <div><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</div>
      <code title={value}>{value}</code>
    </div>
  );
}

export function ConnectorsSettings({ running }: ConnectorsSettingsProps) {
  const [connectors, setConnectors] = useState<ConnectorStatus[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tokenDraft, setTokenDraft] = useState("");
  const [authorization, setAuthorization] = useState<AuthorizationState | null>(null);
  const pollTimer = useRef<number | null>(null);

  const github = useMemo(
    () => connectors.find((item) => item.id === "github") ?? ({ id: "github", name: "GitHub" } as ConnectorStatus),
    [connectors],
  );

  const applyConnector = (connector?: ConnectorStatus) => {
    if (!connector?.id) return;
    setConnectors((current) => {
      const without = current.filter((item) => item.id !== connector.id);
      return [...without, connector];
    });
  };

  const load = async () => {
    setError("");
    try {
      const result = await window.loom.call<{ connectors?: ConnectorStatus[] }>("connector/list", {});
      setConnectors(Array.isArray(result.connectors) ? result.connectors : []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  useEffect(() => {
    void load();
    return () => {
      if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
    };
  }, []);

  const manage = async (action: string, extra: Record<string, unknown> = {}) => {
    if (running || busy) return null;
    setBusy(action);
    setError("");
    try {
      const result = await window.loom.call<{
        connector?: ConnectorStatus;
        authorization?: AuthorizationState;
      }>("connector/manage", { provider: "github", action, ...extra });
      applyConnector(result.connector);
      return result;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setBusy("");
    }
  };

  const pollAuthorization = (state: AuthorizationState) => {
    const delay = Math.max(1, Number(state.pollInterval || 2)) * 1000;
    if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
    pollTimer.current = window.setTimeout(async () => {
      if (running) {
        pollAuthorization(state);
        return;
      }
      setBusy("poll_auth");
      try {
        const result = await window.loom.call<{
          authorization?: AuthorizationState;
          runtime?: { connectors?: ConnectorStatus[] };
        }>("connector/manage", {
          provider: "github",
          action: "poll_auth",
          sessionId: state.sessionId,
        });
        const next = result.authorization;
        if (!next) throw new Error("GitHub authorization returned no status.");
        if (next.status === "connected") {
          setAuthorization(null);
          if (Array.isArray(result.runtime?.connectors)) setConnectors(result.runtime!.connectors!);
          else await load();
          setNotice("GitHub connected.");
          return;
        }
        setAuthorization(next);
        pollAuthorization(next);
      } catch (cause) {
        setAuthorization(null);
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy("");
      }
    }, delay);
  };

  const startBrowserLogin = async () => {
    const result = await manage("start_auth");
    const auth = result?.authorization;
    if (!auth?.sessionId) return;
    setAuthorization(auth);
    setNotice(auth.mode === "github-cli"
      ? "GitHub browser authorization started. The one-time code was copied to your clipboard by GitHub CLI."
      : "GitHub browser authorization started. Complete the device confirmation in your browser.");
    pollAuthorization(auth);
  };

  const connectToken = async () => {
    const token = tokenDraft.trim();
    if (!token) {
      setError("Paste a GitHub personal access token first.");
      return;
    }
    const result = await manage("connect_token", { token });
    if (result?.connector?.connected) {
      setTokenDraft("");
      setNotice("GitHub token validated and stored in the OS credential vault.");
    }
  };

  const copyCode = async () => {
    const code = String(authorization?.userCode || "");
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setNotice("Device code copied.");
    } catch {
      setError("Could not copy the device code.");
    }
  };

  return (
    <>
      <div className="settings-page-heading settings-heading-with-action">
        <div>
          <span className="settings-eyebrow">External services</span>
          <h1>Connectors</h1>
          <p>Connect accounts once, verify health, and expose authenticated service tools without putting secrets in Loom settings.</p>
        </div>
        <button className="mature-action-button" type="button" disabled={running || Boolean(busy)} onClick={() => void load()}>
          <RefreshCw size={14} />Refresh
        </button>
      </div>

      {running ? (
        <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Finish the active turn before changing authorization.</strong><span>Connector identity is frozen into each sampled Step, so Loom never swaps accounts underneath an action already being reviewed or executed.</span></div></div>
      ) : null}
      {error ? <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Connector action failed</strong><span>{error}</span></div></div> : null}
      {notice ? <div className="settings-callout"><Check size={16} /><div><strong>{notice}</strong><span>New model Steps will use the updated connector binding.</span></div></div> : null}

      <section className="settings-section">
        <div className="settings-section-heading"><h2>GitHub</h2><p>Repositories, files, code search, issues, pull requests, branches, and Actions runs.</p></div>
        <div className="settings-card mature-preference-list">
          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Github size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy">
              <strong>{github.connected ? `Connected as ${github.account || "GitHub user"}` : "Connect GitHub"}</strong>
              <span>{github.connected ? "Authenticated GitHub tools are available to new agent Steps." : "Use browser login, import an existing gh session, or store a personal access token in the OS keychain."}</span>
            </div>
            <div className="mature-preference-control"><StatusPill connected={Boolean(github.connected)} /></div>
          </div>

          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Link2 size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy"><strong>Browser sign-in</strong><span>Uses Loom's GitHub device OAuth client when configured; otherwise falls back to authenticated GitHub CLI web login.</span></div>
            <div className="mature-preference-control">
              <button className="mature-action-button" type="button" disabled={running || Boolean(busy)} onClick={() => void startBrowserLogin()}>
                <Github size={14} />{github.connected ? "Reconnect" : "Connect"}
              </button>
            </div>
          </div>

          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Terminal size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy"><strong>Import GitHub CLI session</strong><span>{github.githubCliAvailable ? "Reuse the account already authorized by `gh auth login`. Loom copies the token into its own OS-keychain entry." : "GitHub CLI was not detected on this machine."}</span></div>
            <div className="mature-preference-control">
              <button className="mature-action-button" type="button" disabled={running || Boolean(busy) || !github.githubCliAvailable} onClick={async () => {
                const result = await manage("import_gh");
                if (result?.connector?.connected) setNotice("GitHub CLI authorization imported into Loom.");
              }}><Terminal size={14} />Import gh</button>
            </div>
          </div>

          <div className="mature-preference-row">
            <span className="mature-preference-icon"><KeyRound size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy"><strong>Personal access token</strong><span>The token is validated once and stored in the OS credential vault. It is never persisted in settings.json or connectors.json.</span></div>
            <div className="mature-preference-control" style={{ display: "flex", gap: 8 }}>
              <input className="mature-input" type="password" autoComplete="off" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder="github_pat_…" aria-label="GitHub personal access token" />
              <button className="mature-action-button" type="button" disabled={running || Boolean(busy) || !tokenDraft.trim()} onClick={() => void connectToken()}><KeyRound size={14} />Save</button>
            </div>
          </div>
        </div>
      </section>

      {authorization ? (
        <section className="settings-section">
          <div className="settings-section-heading"><h2>Authorization in progress</h2><p>Finish the GitHub confirmation, then Loom will pick up the connected account automatically.</p></div>
          <div className="settings-card settings-detail-list">
            <DetailRow label="Mode" value={authorization.mode || "device"} />
            <DetailRow label="Verification" value={authorization.verificationUrl || "https://github.com/login/device"} />
            <DetailRow label="Device code" value={authorization.userCode || "Copied by GitHub CLI"} />
            {authorization.userCode ? <div className="settings-detail-row"><div><strong>Copy code</strong><span>Use this code on GitHub's device page.</span></div><button className="mature-action-button" type="button" onClick={() => void copyCode()}><Copy size={14} />Copy</button></div> : null}
          </div>
        </section>
      ) : null}

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Connection details</h2><p>Only non-secret metadata is shown here or included in diagnostics.</p></div>
        <div className="settings-card settings-detail-list">
          <DetailRow label="Account" value={github.account || "Not connected"} />
          <DetailRow label="Credential source" value={github.credentialSource || "None"} />
          <DetailRow label="OAuth scopes" value={github.scopes || "Not reported"} />
          <DetailRow label="Binding" value={github.bindingId || "github:disconnected"} detail="Process-local credential identity; it is not the token." />
          <DetailRow label="Browser device OAuth" value={github.deviceFlowAvailable ? "Configured" : "Uses GitHub CLI fallback when available"} />
          <DetailRow label="GitHub CLI" value={github.githubCliAvailable ? "Available" : "Not detected"} />
          {github.error && !github.connected ? <DetailRow label="Last check" value={github.error} /> : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Safety boundary</h2><p>Connector authentication does not bypass Loom's execution or approval model.</p></div>
        <div className="settings-card mature-preference-list">
          <div className="mature-preference-row"><span className="mature-preference-icon"><ShieldCheck size={17} /></span><div className="mature-preference-copy"><strong>Read operations stay read-only</strong><span>Repository/file/search/issue/PR/Actions reads are classified as read-only tools.</span></div></div>
          <div className="mature-preference-row"><span className="mature-preference-icon"><ShieldCheck size={17} /></span><div className="mature-preference-copy"><strong>Writes remain approval-aware</strong><span>Creating issues/comments/branches/PRs or committing files is SENSITIVE and crosses the normal Loom approval boundary.</span></div></div>
          <div className="mature-preference-row"><span className="mature-preference-icon"><ShieldCheck size={17} /></span><div className="mature-preference-copy"><strong>Account identity is Step-bound</strong><span>Changing or disconnecting an account only affects a future model Step. Existing sampled actions cannot be retargeted.</span></div></div>
        </div>
      </section>

      {github.connected ? (
        <div className="settings-callout warning">
          <LogOut size={16} />
          <div><strong>Disconnect GitHub from Loom</strong><span>This removes Loom's keychain credential and creates an explicit disconnect marker. It does not sign out GitHub CLI or delete environment variables.</span></div>
          <button className="mature-action-button" type="button" disabled={running || Boolean(busy)} onClick={async () => {
            const result = await manage("disconnect");
            if (result?.connector && !result.connector.connected) setNotice("GitHub disconnected from Loom.");
          }}><LogOut size={14} />Disconnect</button>
        </div>
      ) : null}
    </>
  );
}
