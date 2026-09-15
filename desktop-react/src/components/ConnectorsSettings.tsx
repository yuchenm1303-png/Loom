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
import { useI18n } from "../i18n";


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
  webOAuthAvailable?: boolean;
  preferredBrowserLogin?: "web" | "device" | "github-cli" | string;
  error?: string;
};

type AuthorizationState = {
  sessionId: string;
  mode?: string;
  status?: string;
  userCode?: string;
  verificationUrl?: string;
  authorizationUrl?: string;
  redirectUrl?: string;
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
  const { language } = useI18n();
  const isChinese = language === "zh-CN";
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
          setNotice(isChinese ? "GitHub 已连接。" : "GitHub connected.");
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
    if (auth.mode === "web") {
      setNotice(isChinese
        ? "GitHub 已在默认浏览器中打开。授权完成后会自动返回 Loom，无需复制验证码。"
        : "GitHub opened in your browser. Loom will finish the connection automatically after authorization.");
    } else if (auth.mode === "github-cli") {
      setNotice(isChinese
        ? "此构建未配置 Loom Web OAuth，已使用 GitHub CLI 浏览器登录作为备用方式。"
        : "Loom Web OAuth is not configured in this build, so GitHub CLI browser login is being used as a fallback.");
    } else {
      setNotice(isChinese
        ? "此构建未配置 Loom Web OAuth，已进入 GitHub Device Flow 备用登录。"
        : "Loom Web OAuth is not configured in this build, so GitHub Device Flow is being used as a fallback.");
    }
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
      setNotice(isChinese ? "GitHub Token 已验证并保存到系统凭据库。" : "GitHub token validated and stored in the OS credential vault.");
    }
  };

  const copyCode = async () => {
    const code = String(authorization?.userCode || "");
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setNotice(isChinese ? "验证码已复制。" : "Device code copied.");
    } catch {
      setError("Could not copy the device code.");
    }
  };

  const authModeLabel = authorization?.mode === "web"
    ? "Browser OAuth + PKCE"
    : authorization?.mode === "github-cli"
      ? "GitHub CLI fallback"
      : "Device Flow fallback";

  return (
    <>
      <div className="settings-page-heading settings-heading-with-action">
        <div>
          <span className="settings-eyebrow">External services</span>
          <h1>Connectors</h1>
          <p>{isChinese
            ? "一次连接外部账号，Loom 会安全地复用授权，同时继续遵守每一步的权限与审批边界。"
            : "Connect an external account once. Loom reuses the authorization safely while preserving per-Step permissions and approvals."}</p>
        </div>
        <button className="mature-action-button" type="button" disabled={running || Boolean(busy)} onClick={() => void load()}>
          <RefreshCw size={14} />Refresh
        </button>
      </div>

      {running ? (
        <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Finish the active turn before changing authorization.</strong><span>Connector identity is frozen into each sampled Step, so Loom never swaps accounts underneath an action already being reviewed or executed.</span></div></div>
      ) : null}
      {error ? <div className="settings-callout warning"><CircleAlert size={16} /><div><strong>Connector action failed</strong><span>{error}</span></div></div> : null}
      {notice ? <div className="settings-callout"><Check size={16} /><div><strong>{notice}</strong><span>{isChinese ? "新的模型 Step 会使用更新后的连接。" : "New model Steps will use the updated connector binding."}</span></div></div> : null}

      <section className="settings-section">
        <div className="settings-section-heading"><h2>GitHub</h2><p>Repositories, files, code search, issues, pull requests, branches, and Actions runs.</p></div>
        <div className="settings-card mature-preference-list">
          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Github size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy">
              <strong>{github.connected
                ? (isChinese ? `已连接账号：${github.account || "GitHub 用户"}` : `Connected as ${github.account || "GitHub user"}`)
                : (isChinese ? "连接 GitHub" : "Connect GitHub")}</strong>
              <span>{github.connected
                ? (isChinese ? "GitHub 工具已可供新的 Agent Step 使用。" : "Authenticated GitHub tools are available to new agent Steps.")
                : github.webOAuthAvailable
                  ? (isChinese ? "点击一次，在浏览器中授权；GitHub 会自动返回 Loom 完成连接。" : "One click opens GitHub in your browser; authorization returns to Loom automatically.")
                  : (isChinese ? "当前构建尚未配置 Loom 的 GitHub OAuth App，将在必要时使用备用登录方式。" : "This build has no Loom GitHub OAuth App configured yet, so a fallback sign-in method will be used when needed.")}</span>
            </div>
            <div className="mature-preference-control"><StatusPill connected={Boolean(github.connected)} /></div>
          </div>

          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Link2 size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy">
              <strong>{isChinese ? "浏览器授权" : "Browser sign-in"}</strong>
              <span>{github.webOAuthAvailable
                ? (isChinese ? "使用 GitHub Web OAuth、PKCE、随机 state 与 127.0.0.1 动态回调；无需复制设备码。" : "Uses GitHub Web OAuth, PKCE, random state, and a dynamic 127.0.0.1 callback. No device code is required.")
                : (isChinese ? "正式 Web OAuth 未配置时，Loom 会安全降级到可用的 GitHub 登录方式。" : "Until Web OAuth is configured, Loom safely falls back to an available GitHub sign-in method.")}</span>
            </div>
            <div className="mature-preference-control">
              <button className="mature-action-button" type="button" disabled={running || Boolean(busy)} onClick={() => void startBrowserLogin()}>
                <Github size={14} />{github.connected ? (isChinese ? "重新连接" : "Reconnect") : (isChinese ? "连接 GitHub" : "Connect GitHub")}
              </button>
            </div>
          </div>
        </div>
      </section>

      {authorization ? (
        <section className="settings-section">
          <div className="settings-section-heading">
            <h2>{isChinese ? "正在连接 GitHub" : "Connecting GitHub"}</h2>
            <p>{authorization.mode === "web"
              ? (isChinese ? "请在浏览器中完成授权。完成后 Loom 会自动检测并连接，不需要粘贴任何验证码。" : "Finish authorization in your browser. Loom will detect it and connect automatically; there is no code to paste.")
              : (isChinese ? "当前正在使用备用授权方式。完成 GitHub 页面上的确认后，Loom 会自动继续。" : "A fallback authorization method is active. Finish the GitHub confirmation and Loom will continue automatically.")}</p>
          </div>
          <div className="settings-card settings-detail-list">
            <DetailRow label="Mode" value={authModeLabel} />
            {authorization.mode === "web" ? (
              <DetailRow
                label={isChinese ? "本机回调" : "Local callback"}
                value="127.0.0.1 · dynamic port"
                detail={isChinese ? "仅监听本机；授权完成即关闭。" : "Loopback-only listener; closes as soon as authorization completes."}
              />
            ) : (
              <>
                <DetailRow label="Verification" value={authorization.verificationUrl || "https://github.com/login/device"} />
                <DetailRow label="Device code" value={authorization.userCode || "Copied by GitHub CLI"} />
                {authorization.userCode ? <div className="settings-detail-row"><div><strong>{isChinese ? "复制验证码" : "Copy code"}</strong><span>{isChinese ? "仅备用 Device Flow 需要这一步。" : "Only the fallback Device Flow requires this step."}</span></div><button className="mature-action-button" type="button" onClick={() => void copyCode()}><Copy size={14} />Copy</button></div> : null}
              </>
            )}
          </div>
        </section>
      ) : null}

      <section className="settings-section">
        <div className="settings-section-heading"><h2>{isChinese ? "高级 / 恢复方式" : "Advanced / recovery"}</h2><p>{isChinese ? "正常连接不需要这些方式；仅在企业环境、开发调试或 OAuth 不可用时使用。" : "Normal sign-in does not require these options. Use them for managed environments, development, or recovery."}</p></div>
        <div className="settings-card mature-preference-list">
          <div className="mature-preference-row">
            <span className="mature-preference-icon"><Terminal size={17} strokeWidth={1.8} /></span>
            <div className="mature-preference-copy"><strong>Import GitHub CLI session</strong><span>{github.githubCliAvailable ? "Reuse an account already authorized by `gh auth login`. Loom copies the token into its own OS-keychain entry." : "GitHub CLI was not detected on this machine."}</span></div>
            <div className="mature-preference-control">
              <button className="mature-action-button" type="button" disabled={running || Boolean(busy) || !github.githubCliAvailable} onClick={async () => {
                const result = await manage("import_gh");
                if (result?.connector?.connected) setNotice(isChinese ? "已导入 GitHub CLI 授权。" : "GitHub CLI authorization imported into Loom.");
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

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Connection details</h2><p>Only non-secret metadata is shown here or included in diagnostics.</p></div>
        <div className="settings-card settings-detail-list">
          <DetailRow label="Account" value={github.account || "Not connected"} />
          <DetailRow label="Credential source" value={github.credentialSource || "None"} />
          <DetailRow label="OAuth scopes" value={github.scopes || "Not reported"} />
          <DetailRow label="Binding" value={github.bindingId || "github:disconnected"} detail="Process-local credential identity; it is not the token." />
          <DetailRow label="Browser OAuth" value={github.webOAuthAvailable ? "Web OAuth + PKCE ready" : "Not configured in this build"} />
          <DetailRow label="Device OAuth fallback" value={github.deviceFlowAvailable ? "Available" : "Not configured"} />
          <DetailRow label="GitHub CLI fallback" value={github.githubCliAvailable ? "Available" : "Not detected"} />
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
            if (result?.connector && !result.connector.connected) setNotice(isChinese ? "GitHub 已从 Loom 断开。" : "GitHub disconnected from Loom.");
          }}><LogOut size={14} />Disconnect</button>
        </div>
      ) : null}
    </>
  );
}
