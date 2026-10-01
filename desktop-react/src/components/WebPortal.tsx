import "./portal-base.css";
import "./portal-modules.css";
import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import { Laptop, Download } from "lucide-react";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";

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

const RELEASE = {
  version: "v0.1.7",
  package: "137.6 MiB",
  download: "https://github.com/yuchenm1303-png/Loom/releases/download/v0.1.7/Loom-Setup-0.1.7-x64.exe",
} as const;

const HOST_COPY = {
  en: {
    localSetup: "Open Loom on this computer once. The browser will discover and securely pair with your local Loom Host.",
    localConnecting: "Connecting securely to the Loom Host on this computer.",
    localOffline: "Loom Host is offline. Start Loom on this computer, then reconnect.",
  },
  zh: {
    localSetup: "在这台电脑上启动一次 Loom，浏览器会自动发现并安全关联本机 Loom Host。",
    localConnecting: "正在安全连接这台电脑上的 Loom Host。",
    localOffline: "Loom Host 已离线。请先在这台电脑上启动 Loom，然后重新连接。",
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

export function WebPortal({ account, hostState, hostError, selectedDeviceName, onEnter }: {
  account: AccountController;
  hostState: PortalHostState;
  hostError: string;
  selectedDeviceName: string;
  onEnter: () => void;
  remoteMode?: boolean;
  onRemote?: () => void;
}) {
  usePortalStyles();
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
    ? (zh ? "已连接，可进入 Loom Web" : "Connected · Loom Web ready")
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
          <div className="loom-version-chip"><span>{RELEASE.version}</span></div>
        </header>

        <section className="loom-module-grid">
          <article className="loom-stage-copy loom-intro-module cards fade">
            <div className="loom-intro-copy">
            <p className="kicker">LOOM · LOCAL-FIRST AGENT</p>
            <h1><span className="loom-heading-main">{zh ? "你的 Loom，" : "Your Loom stays"}</span>{" "}<span className="loom-heading-accent">{zh ? "始终在自己的电脑上。" : "on your computer."}</span></h1>
            <p className="loom-stage-description">
              {zh
                ? "桌面端负责真正运行 Agent，网页只是安全入口。无论从哪里打开，继续的都是同一个会话、文件、审批和 Computer Use。"
                : "Desktop runs the Agent. The web is simply a secure way back in — to the same conversations, files, approvals and Computer Use."}
            </p>

            {!authenticated ? <div className="loom-stage-actions"><button className="loom-primary-action" type="button" onClick={primaryAction}><span>{primaryLabel}</span><span aria-hidden="true">→</span></button></div> : null}
            </div>
          <div className="loom-download-module" aria-labelledby="loom-download-title">
            <div className="loom-download-product"><Laptop size={26} aria-hidden="true" /><div><h2 id="loom-download-title">Loom for Windows</h2><p>Windows 10 / 11 · x64 · {RELEASE.package}</p></div></div>
            <div className="loom-download-version"><span>{RELEASE.version} · Stable</span><a href="https://github.com/yuchenm1303-png/Loom/releases" target="_blank" rel="noreferrer">{zh ? "更新日志" : "Release notes"}<span aria-hidden="true"> ↗</span></a></div>
            <a className="loom-secondary-action" href={RELEASE.download}><span><strong>{zh ? "下载 Windows 版" : "Download for Windows"}</strong></span><Download size={16} aria-hidden="true" /></a>
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
              <p className="loom-control-copy">{zh ? "登录后会自动寻找并连接这台电脑上的 Loom Host。" : "Sign in and Loom Web will automatically find and connect to the Loom Host on this computer."}</p>
              <form className="login-form loom-account-form" onSubmit={submitAuth} autoComplete="on">
                <label><span>{zh ? "邮箱" : "Email"}</span><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
                <label className="password-field"><span>{zh ? "密码" : "Password"}</span><input type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? (zh ? "隐藏" : "Hide") : (zh ? "显示" : "Show")}</button></label>
                <button className="loom-form-submit" type="submit" disabled={!account.ready || account.busy}><span>{!account.ready ? (zh ? "加载中…" : "Loading…") : account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : (zh ? "创建账户" : "Create account")}</span><span aria-hidden="true">→</span></button>
              </form>
              <button className="loom-account-switch" type="button" onClick={() => { account.clearError(); setAuthMode(authMode === "login" ? "register" : "login"); }}>{authMode === "login" ? (zh ? "没有账户？创建一个" : "New to Loom? Create an account") : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}</button>
              {account.error ? <p className="form-note is-error">{account.error.message}</p> : null}
            </section> : <>
              <section className="loom-host-module" aria-label={zh ? "Host 连接" : "Host connection"}>
              <div className="loom-control-top">
                <div>
                  <p className="kicker">LOOM HOST</p>
                  <h2>{hostLabel}</h2>
                </div>
                <span className={`loom-connection-badge${hostState === "online" ? " is-online" : hostState === "checking" ? " is-working" : ""}`}><i aria-hidden="true" />{hostState === "checking" ? (zh ? "连接中" : "CONNECTING") : hostState === "online" ? (zh ? "在线" : "ONLINE") : (zh ? "需要连接" : "ACTION NEEDED")}</span>
              </div>

              <p className="loom-control-copy">{hostError || hostText}</p>
              <div className={`loom-connection-track${hostState === "checking" ? " is-working" : hostState === "online" ? " is-online" : ""}`} aria-hidden="true"><span /></div>

              {hostState !== "checking" ? <button className="loom-form-submit loom-host-action" type="button" onClick={primaryAction}><span>{primaryLabel}</span><span aria-hidden="true">→</span></button> : null}
              </section>
              <section className="loom-account-module" aria-label={zh ? "账户与设备" : "Account and device"}>
                <div className="loom-account-heading"><h2>{zh ? "账户与设备" : "Account & device"}</h2><button className="loom-account-switch" type="button" onClick={() => void account.logout()}>{zh ? "退出登录" : "Sign out"}</button></div>
              <dl className="loom-control-details">
                <div><dt>{zh ? "账户" : "Account"}</dt><dd>{account.account.user?.email}</dd></div>
                <div><dt>{zh ? "这台电脑" : "This computer"}</dt><dd>{selectedDeviceName || (zh ? "等待发现" : "Waiting for discovery")}</dd></div>
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
