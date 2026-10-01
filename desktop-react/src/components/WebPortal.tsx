import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
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
  published: "2026-10-01 · Stable Channel",
  package: "137.6 MiB",
  download: "https://github.com/yuchenm1303-png/Loom/releases/download/v0.1.7/Loom-Setup-0.1.7-x64.exe",
  releases: "https://github.com/yuchenm1303-png/Loom/releases",
  latest: "https://github.com/yuchenm1303-png/Loom/releases/latest",
  support: "https://github.com/yuchenm1303-png/Loom/issues",
} as const;

const HOST_COPY = {
  en: {
    localSetup: "Open Loom on this computer, then choose “Open Loom Web” from the Loom tray once. This browser will remember this computer.",
    localConnecting: "Establishing a secure connection to the Loom Host on this computer.",
    localOffline: "Loom Host on this computer is offline. Start Loom and try again.",
  },
  zh: {
    localSetup: "在当前电脑打开 Loom，然后从托盘选择一次「Open Loom Web」。这个浏览器会记住当前电脑。",
    localConnecting: "正在建立浏览器与当前电脑 Loom Host 的安全连接。",
    localOffline: "当前电脑上的 Loom Host 已离线，请启动 Loom 后重试。",
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

  function openWeb() {
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
      ? (zh ? "连接中" : "Connecting")
      : hostState === "offline"
        ? (zh ? "离线" : "Offline")
        : hostState === "unbound"
          ? (zh ? "待绑定" : "Not linked")
          : (zh ? "等待连接" : "Waiting");

  return (
    <div className="loom-portal-page">
      <PortalWallpaper />
      <main className="release-shell loom-portal-shell">
        <header className="topbar fade loom-site-header">
          <a className="brand loom-portal-brand" href="/" aria-label="Smirel Loom">
            <img className="loom-smirel-wordmark" src={SMIREL_LOGO} alt="Smirel" />
            <span className="loom-brand-divider" aria-hidden="true" />
            <span className="brand-copy"><strong>Loom</strong><small>Personal AI Agent</small></span>
          </a>
          <nav className="loom-site-nav" aria-label={zh ? "页面导航" : "Portal navigation"}>
            <a href="#desktop">{zh ? "桌面端" : "Desktop"}</a>
            <button type="button" onClick={openWeb}>{zh ? "网页版" : "Web"}</button>
            <a href="#account">{zh ? "账户" : "Account"}</a>
          </nav>
        </header>

        <section className="loom-hero cards fade">
          <div className="loom-hero-copy">
            <p className="kicker">LOOM · PERSONAL AI AGENT</p>
            <h1>{zh ? "一个 Agent，随处继续。" : "One agent. Continue anywhere."}</h1>
            <p className="loom-hero-description">
              {zh
                ? "Loom 的 Agent Runtime 运行在你自己的电脑上。桌面端和网页版共享同一套会话、审批、文件与 Computer Use。"
                : "Loom runs its Agent Runtime on your own computer. Desktop and Web share the same conversations, approvals, files and Computer Use."}
            </p>
            <div className="loom-hero-actions">
              <button className="loom-action loom-action-primary" type="button" onClick={openWeb}><span>{zh ? "打开 Loom Web" : "Open Loom Web"}</span><span>→</span></button>
              <a className="loom-action loom-action-secondary" href={RELEASE.download}><span>{zh ? "下载 Windows 版" : "Download for Windows"}</span><span>↓</span></a>
            </div>
          </div>
          <div className="loom-hero-status">
            <div className="loom-status-kicker"><span className={`loom-live-dot${hostState === "online" ? " is-online" : ""}`} />{zh ? "当前连接" : "CURRENT CONNECTION"}</div>
            <strong className="loom-status-title">{authenticated ? hostLabel : (zh ? "尚未登录" : "Signed out")}</strong>
            <p>{authenticated ? (hostError || hostText) : (zh ? "登录后，浏览器会安全连接到你的 Loom Host。" : "Sign in and the browser will securely connect to your Loom Host.")}</p>
            <div className="loom-status-row"><span>{zh ? "账户" : "Account"}</span><strong>{authenticated ? account.account.user?.email : (zh ? "未登录" : "Signed out")}</strong></div>
            <div className="loom-status-row"><span>Host</span><strong>{authenticated ? hostLabel : "—"}</strong></div>
          </div>
        </section>

        <section className="loom-function-grid fade">
          <div className="loom-function-stack">
            <article className="loom-function-card cards" id="desktop">
              <div className="loom-card-head"><span className="loom-card-index">01</span><div><p className="kicker">DESKTOP</p><h2>Loom for Windows</h2></div></div>
              <p className="loom-card-copy">{zh ? "安装 Loom 桌面端与本地 Host。它是真正运行 Agent、Computer Use、文件和审批的地方。" : "Install Loom Desktop and the local Host. This is where the Agent, Computer Use, files and approvals actually run."}</p>
              <div className="loom-spec-row"><span><small>{zh ? "版本" : "Version"}</small><strong>{RELEASE.version}</strong></span><span><small>{zh ? "平台" : "Platform"}</small><strong>Windows x64</strong></span><span><small>{zh ? "大小" : "Size"}</small><strong>{RELEASE.package}</strong></span></div>
              <div className="loom-card-actions"><a className="loom-action loom-action-primary" href={RELEASE.download}><span>{zh ? "下载安装包" : "Download installer"}</span><span>↓</span></a><a className="loom-text-link" href={RELEASE.latest} target="_blank" rel="noreferrer">{zh ? "更新日志" : "Release notes"} ↗</a></div>
            </article>

            <article className="loom-function-card cards" id="web">
              <div className="loom-card-head"><span className="loom-card-index">02</span><div><p className="kicker">WEB</p><h2>Loom Web</h2></div></div>
              <p className="loom-card-copy">{zh ? "网页版不是另一套 Agent。它只是连接你自己的 Loom Host，让你在浏览器里继续同一个工作区。" : "Loom Web is not a second Agent. It connects to your own Loom Host so the same workspace continues in the browser."}</p>
              <div className="loom-host-state"><span className={`loom-live-dot${hostState === "online" ? " is-online" : ""}`} /><div><small>Loom Host</small><strong>{authenticated ? hostLabel : (zh ? "等待登录" : "Waiting for sign-in")}</strong></div></div>
              <div className="loom-card-actions"><button className="loom-action loom-action-primary" type="button" onClick={openWeb}><span>{authenticated ? (zh ? "连接当前 Host" : "Connect current Host") : (zh ? "先登录账户" : "Sign in first")}</span><span>→</span></button></div>
            </article>
          </div>

          <aside className="loom-account-card cards" id="account">
            <div className="loom-card-head"><span className="loom-card-index">03</span><div><p className="kicker">ACCOUNT</p><h2>{authenticated ? (zh ? "你的 Loom" : "Your Loom") : authMode === "login" ? (zh ? "登录账户" : "Sign in") : (zh ? "创建账户" : "Create account")}</h2></div></div>
            {!authenticated ? <>
              <p className="loom-card-copy">{zh ? "账户只负责身份与设备连接；浏览器不会直接持有 Loom Host access token。" : "Your account handles identity and device connection; the browser never directly holds the Loom Host access token."}</p>
              <form className="login-form loom-account-form" onSubmit={submitAuth} autoComplete="on">
                <label><span>{zh ? "邮箱" : "Email"}</span><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
                <label className="password-field"><span>{zh ? "密码" : "Password"}</span><input type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? (zh ? "隐藏" : "Hide") : (zh ? "显示" : "Show")}</button></label>
                <button className="loom-action loom-action-primary loom-account-submit" type="submit" disabled={!account.ready || account.busy}><span>{!account.ready ? (zh ? "加载中…" : "Loading…") : account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : (zh ? "创建账户" : "Create account")}</span><span>→</span></button>
              </form>
              <button className="loom-account-switch" type="button" onClick={() => { account.clearError(); setAuthMode(authMode === "login" ? "register" : "login"); }}>{authMode === "login" ? (zh ? "没有账户？创建一个" : "No account? Create one") : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}</button>
              {account.error ? <p className="form-note is-error">{account.error.message}</p> : null}
            </> : <div className="loom-signed-in">
              <div className="loom-account-identity"><span className="loom-account-check">✓</span><div><small>{zh ? "已登录" : "SIGNED IN"}</small><strong>{account.account.user?.email}</strong></div></div>
              <div className="loom-account-details"><div><span>{zh ? "当前设备" : "Current device"}</span><strong>{hostLabel}</strong></div><div><span>{zh ? "Host 规则" : "Host model"}</span><strong>{zh ? "当前账号唯一 Host" : "One current Host"}</strong></div></div>
              <p className="loom-host-note">{hostError || hostText}</p>
              <div className="loom-account-actions"><button className="loom-action loom-action-primary" type="button" onClick={openWeb}><span>{zh ? "重新连接 Host" : "Reconnect Host"}</span><span>→</span></button></div>
              <button className="loom-account-switch" type="button" onClick={() => void account.logout()}>{zh ? "退出当前账户" : "Sign out"}</button>
            </div>}
          </aside>
        </section>

        <footer className="footer fade loom-site-footer"><span>© 2026 Smirel · Loom</span><span className="loom-footer-links"><a href={RELEASE.releases} target="_blank" rel="noreferrer">{zh ? "全部版本" : "All releases"}</a><a href={RELEASE.support} target="_blank" rel="noreferrer">{zh ? "支持" : "Support"}</a><span>{RELEASE.published.split(" · ")[0]}</span></span></footer>
      </main>
    </div>
  );
}
