import "./portal-base.css";
import "./portal-modules.css";
import "./portal-host-card.css";
import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import { Laptop, Download, UserRound, LogOut, Check, ArrowRight } from "lucide-react";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import { FALLBACK_RELEASE, fetchPortalRelease } from "../portalRelease";
import { HostSetupActions } from "./HostSetupActions";

export type PortalHostState = "idle" | "checking" | "online" | "offline" | "unbound";
type AccountController = ReturnType<typeof useAccount>;

const SMIREL_LOGO = "/smirel-logo.png";
const PORTAL_STYLES = [
  "https://smirel.com/download/styles-v3.css",
  "https://smirel.com/download/cosmic-bright-v1.css",
  "https://smirel.com/download/portal-polish-v1.css",
  "https://smirel.com/download/portal-polish-v2.css",
  "https://smirel.com/download/portal-account-v1.css",
  "https://smirel.com/download/portal-account-v2.css",
  "https://smirel.com/download/layout-visual-restore-v1.css",
  "https://smirel.com/download/cursor-reference-source-v1.css",
  "https://smirel.com/download/session-boot-v1.css",
  "https://smirel.com/download/release-history-v1.css",
  "https://smirel.com/download/beach-wallpaper-v1.css",
  "https://smirel.com/download/wallpaper-ready-v1.css",
] as const;


const HOST_COPY = {
  en: {
    localSetup: "Start your local Loom Host to use files and apps on this computer. This page will connect automatically.",
    localConnecting: "Finding and securely connecting your Host. Your workspace will open automatically.",
    localOffline: "The connection to Loom Host was lost. Your conversations stay on this computer; reconnect to continue.",
  },
  zh: {
    localSetup: "启动本机 Loom Host，即可使用这台电脑上的文件和应用。本页会自动连接。",
    localConnecting: "正在查找并安全连接本机 Host，连接后会自动进入工作区。",
    localOffline: "与 Loom Host 的连接已断开。会话仍保留在本机，重新连接即可继续。",
  },
} as const;

function usePortalStyles() {
  useEffect(() => {
    document.documentElement.classList.add("wallpaper-ready");
    document.body.classList.add("beach-wallpaper-active");
    const links = PORTAL_STYLES.map((href) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.dataset.loomPortalStyle = href;
      document.head.appendChild(link);
      return link;
    });
    return () => {
      links.forEach((link) => link.remove());
      document.documentElement.classList.remove("wallpaper-ready");
      document.body.classList.remove("beach-wallpaper-active");
    };
  }, []);
}

function PortalWallpaper() {
  const style = {
    "--beach-image": "url('https://smirel.com/download/wallpaper-beach-blue-v1-original.png')",
    "--beach-position-x": "54%",
    "--beach-position-mobile-x": "57%",
    "--beach-veil-top": ".17",
    "--beach-veil-bottom": ".28",
  } as CSSProperties;
  return (
    <div className="cosmos beach-wallpaper-active" aria-hidden="true">
      <div className="beach-wallpaper is-ready" data-wallpaper="beach-blue" style={style} />
      <div className="cosmos-sky" /><div className="cosmos-glow" />
      <div className="cosmos-nebula cosmos-nebula-cyan" /><div className="cosmos-nebula cosmos-nebula-violet" />
      <div className="cosmos-vignette" />
    </div>
  );
}

export function WebPortal({ account, hostState, hostError, selectedDeviceName, onEnter, hostDetected = false }: {
  account: AccountController;
  hostState: PortalHostState;
  hostError: string;
  selectedDeviceName: string;
  onEnter: () => void;
  hostDetected?: boolean;
  remoteMode?: boolean;
  onRemote?: () => void;
}) {
  usePortalStyles();
  const [release, setRelease] = useState(FALLBACK_RELEASE);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    fetchPortalRelease(controller.signal).then(setRelease).catch(() => {
      // The generic latest download remains available without stale metadata.
    }).finally(() => window.clearTimeout(timeout));
    return () => { controller.abort(); window.clearTimeout(timeout); };
  }, []);
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const copy = HOST_COPY[zh ? "zh" : "en"];
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const authenticated = Boolean(account.account.authenticated && account.account.user);

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const ok = authMode === "login" ? await account.login(email.trim(), password) : await account.register(email.trim(), password);
    if (ok) setPassword("");
  }

  function focusAccount() {
    document.getElementById("account")?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => document.getElementById("emailInput")?.focus(), 220);
  }

  function primaryAction() {
    if (!authenticated) {
      focusAccount();
      return;
    }
    onEnter();
  }

  const hostText = hostState === "online"
    ? (zh ? "Host 已连接，正在进入工作区。" : "Host connected. Opening your workspace.")
    : hostState === "checking"
      ? copy.localConnecting
      : hostState === "unbound"
        ? copy.localSetup
        : hostState === "offline"
          ? copy.localOffline
          : (zh ? "等待 Loom Host" : "Waiting for Loom Host");

  const hostLabel = hostState === "online"
    ? (selectedDeviceName || (zh ? "已连接" : "Connected"))
    : hostState === "checking"
      ? (zh ? "正在连接" : "Connecting")
      : hostState === "offline"
        ? (zh ? "已离线" : "Offline")
        : hostState === "unbound"
          ? (zh ? "未发现 Host" : "Host not found")
          : (zh ? "等待连接" : "Waiting");

  const primaryLabel = !authenticated
    ? (zh ? "登录并打开 Loom Web" : "Sign in to Loom Web")
    : hostState === "online"
      ? (zh ? "打开 Loom Web" : "Open Loom Web")
      : hostState === "checking"
        ? (zh ? "正在连接…" : "Connecting…")
        : hostState === "unbound"
          ? (zh ? "查找本机 Loom Host" : "Find Loom Host")
          : (zh ? "重新连接 Loom Host" : "Reconnect Loom Host");

  return (
    <div className="loom-portal-page is-modular">
      <PortalWallpaper />
      <main className="release-shell loom-portal-shell">
        <header className="topbar fade loom-site-header">
          <a className="brand loom-portal-brand" href="/" aria-label="Smirel Loom">
            <img className="loom-smirel-wordmark" src={SMIREL_LOGO} alt="Smirel" />
            <span className="loom-brand-divider" aria-hidden="true" />
            <span className="brand-copy"><strong>Loom</strong><small>Personal AI Agent</small></span>
          </a>
          <div className="loom-version-chip"><span>{(release.version || (zh ? "最新稳定版" : "Latest stable"))}</span></div>
        </header>

        <section className="loom-module-grid">
          <article className="loom-stage-copy loom-intro-module cards fade">
            <div className="loom-intro-copy">
            <p className="kicker">LOOM · LOCAL-FIRST AGENT</p>
            <h1><span className="loom-heading-main">{zh ? "你的 Loom，" : "Your Loom stays"}</span>{" "}<span className="loom-heading-accent">{zh ? "始终在自己的电脑上。" : "on your computer."}</span></h1>
            <p className="loom-stage-description">
              {zh
                ? "Loom Host 在本机后台运行 Agent，打开网页即可继续工作。桌面界面可选，网页和桌面共享同一个会话、文件、审批和 Computer Use。"
                : "Loom Host runs the Agent in the background on your computer. Open the web to keep working. Desktop is optional; both share the same conversations, files, approvals and Computer Use."}
            </p>

            {!authenticated ? <div className="loom-stage-actions"><button className="loom-primary-action" type="button" onClick={primaryAction}><span>{primaryLabel}</span><span aria-hidden="true">→</span></button></div> : null}
            </div>
          <div className="loom-download-module" aria-labelledby="loom-download-title">
            <div className="loom-download-product"><Laptop size={26} aria-hidden="true" /><div><h2 id="loom-download-title">Loom for Windows</h2><p>Windows 10 / 11 · x64{release.package ? ` · ${release.package}` : ""}</p><p>{zh ? "包含本机 Host · 桌面界面可选" : "Local Host included · Desktop optional"}</p></div></div>
            <div className="loom-download-version"><span>{(release.version || (zh ? "最新稳定版" : "Latest stable"))} · Stable</span><a href={release.notes} target="_blank" rel="noreferrer">{zh ? "更新日志" : "Release notes"}<span aria-hidden="true"> ↗</span></a></div>
            <a className="loom-secondary-action" href={release.download}><span><strong>{zh ? "下载 Windows 版" : "Download for Windows"}</strong></span><Download size={16} aria-hidden="true" /></a>
          </div>

          </article>

          <aside className="loom-control" id="account">
            <div className="loom-sidebar-module cards fade">
            {!authenticated ? <section className="loom-login-module">
              <div className="loom-control-top">
                <div>
                  <p className="kicker">LOOM ACCOUNT</p>
                  <h2>{authMode === "login" ? (zh ? "登录后继续" : "Continue with Loom") : (zh ? "创建你的 Loom 账户" : "Create your Loom account")}</h2>
                </div>
                <span className="loom-security-label">SECURE</span>
              </div>
              <p className="loom-control-copy">{zh ? "登录后会自动连接本机 Loom Host，连接成功即可进入工作区。" : "Sign in to automatically connect your local Host and open your workspace."}</p>
              <form className="login-form loom-account-form" onSubmit={submitAuth} autoComplete="on">
                <label><span>{zh ? "邮箱" : "Email"}</span><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
                <label className="password-field"><span>{zh ? "密码" : "Password"}</span><input type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? (zh ? "隐藏" : "Hide") : (zh ? "显示" : "Show")}</button></label>
                <button className="loom-form-submit" type="submit" disabled={!account.ready || account.busy}><span>{!account.ready ? (zh ? "加载中…" : "Loading…") : account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : (zh ? "创建账户" : "Create account")}</span><span aria-hidden="true">→</span></button>
              </form>
              <button className="loom-account-switch" type="button" onClick={() => { account.clearError(); setAuthMode(authMode === "login" ? "register" : "login"); }}>{authMode === "login" ? (zh ? "没有账户？创建一个" : "New to Loom? Create an account") : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}</button>
              {account.error ? <p className="form-note is-error">{account.error.message}</p> : null}
            </section> : <>
              <section className="loom-host-module loom-host-refined" data-host-state={hostState} aria-label={zh ? "Host 连接" : "Host connection"}>
                <div className="loom-host-meta">
                  <p className="kicker">LOOM HOST</p>
                  <span className={`loom-connection-badge${hostState === "online" ? " is-online" : hostState === "checking" ? " is-working" : ""}`}>
                    <i aria-hidden="true" />{hostState === "online" ? (zh ? "在线" : "Online") : hostState === "checking" ? (zh ? "连接中" : "Connecting") : hostState === "idle" ? (zh ? "等待中" : "Waiting") : hostState === "unbound" ? (zh ? "未发现" : "Not found") : (zh ? "离线" : "Offline")}
                  </span>
                </div>
                <div className="loom-host-summary">
                  <span className="loom-host-emblem" aria-hidden="true"><Laptop size={27} strokeWidth={1.5} /><i /></span>
                  <div role="status" aria-live="polite" aria-atomic="true">
                    <h2>{hostLabel}</h2>
                    <p className="loom-host-target">{hostState === "online" ? (zh ? "当前电脑" : "This computer") : selectedDeviceName || (zh ? "你的本地工作区" : "Your local workspace")}</p>
                  </div>
                </div>
                <p className="loom-control-copy">{hostText}</p>
                {hostState === "unbound" || hostState === "offline" ? <HostSetupActions zh={zh} download={release.download} onRetry={onEnter} hostDetected={hostDetected} /> : null}
                {hostError ? <details className="loom-host-error"><summary>{zh ? "连接详情" : "Connection details"}</summary><p>{hostError}</p></details> : null}
                {hostState === "checking" || hostState === "online" ? <div className={`loom-connection-track${hostState === "checking" ? " is-working" : " is-online"}`} aria-hidden="true"><span /></div> : null}
                {hostState === "online" ? <button className="loom-form-submit loom-host-action" type="button" onClick={primaryAction}><span>{primaryLabel}</span><ArrowRight size={15} aria-hidden="true" /></button> : null}
              </section>
              <section className="loom-account-module loom-account-refined" aria-label={zh ? "账户与设备" : "Account and device"}>
                <div className="loom-account-heading">
                  <h2>{zh ? "账户与设备" : "Account & device"}</h2>
                  <button className="loom-account-switch" type="button" disabled={account.busy} onClick={() => void account.logout()}><LogOut size={13} aria-hidden="true" />{zh ? "退出登录" : "Sign out"}</button>
                </div>
                <dl className="loom-identity-list">
                  <div className="loom-identity-row">
                    <dt><UserRound size={18} strokeWidth={1.5} aria-hidden="true" /><span>{zh ? "已登录账户" : "Signed-in account"}</span></dt>
                    <dd><span>{account.account.user?.email}</span><Check size={13} aria-hidden="true" /></dd>
                  </div>
                  <div className="loom-identity-row">
                    <dt><Laptop size={18} strokeWidth={1.5} aria-hidden="true" /><span>{zh ? "当前电脑" : "This computer"}</span></dt>
                    <dd><span>{selectedDeviceName || (zh ? "等待发现" : "Waiting for discovery")}</span></dd>
                  </div>
                </dl>
              </section>
            </>}
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}
