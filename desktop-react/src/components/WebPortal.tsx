import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";

export type PortalHostState = "idle" | "checking" | "online" | "offline" | "unbound";
type AccountController = ReturnType<typeof useAccount>;

const SMIREL_LOGO = "/smirel-logo.svg";
const SMIREL_MARK = "/smirel-mark.svg";
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
    remoteConnecting: "Connecting to the computer you explicitly selected in Loom Remote.",
    remoteOffline: "That remote computer is unavailable right now.",
    remoteDevices: "Remote devices",
  },
  zh: {
    localSetup: "在当前电脑打开 Loom，然后从托盘选择一次「Open Loom Web」。这个浏览器会记住当前电脑。",
    localConnecting: "正在建立浏览器与当前电脑 Loom Host 的安全连接。",
    localOffline: "当前电脑上的 Loom Host 已离线，请启动 Loom 后重试。",
    remoteConnecting: "正在连接你在 Loom Remote 中明确选择的电脑。",
    remoteOffline: "这台远程电脑当前不可用。",
    remoteDevices: "远程设备",
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

export function WebPortal({ account, hostState, hostError, selectedDeviceName, remoteMode, onEnter, onRemote }: {
  account: AccountController;
  hostState: PortalHostState;
  hostError: string;
  selectedDeviceName: string;
  remoteMode: boolean;
  onEnter: () => void;
  onRemote: () => void;
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

  function openWeb() {
    if (!authenticated) {
      document.getElementById("account")?.scrollIntoView({ behavior: "smooth", block: "center" });
      window.setTimeout(() => document.getElementById("emailInput")?.focus(), 220);
      return;
    }
    onEnter();
  }

  const hostText = hostState === "online"
    ? (zh ? "已连接，可进入 Loom Web" : "Connected · Loom Web ready")
    : hostState === "checking"
      ? (remoteMode ? copy.remoteConnecting : copy.localConnecting)
      : hostState === "unbound"
        ? copy.localSetup
        : hostState === "offline"
          ? (remoteMode ? copy.remoteOffline : copy.localOffline)
          : (zh ? "等待 Loom Host" : "Waiting for Loom Host");

  return (
    <div className="loom-portal-page">
      <PortalWallpaper />
      <main className="release-shell loom-portal-shell">
        <header className="topbar fade">
          <a className="brand loom-portal-brand" href="/" aria-label="Smirel Loom">
            <img className="loom-portal-logo" src={SMIREL_LOGO} alt="Smirel" />
            <span className="loom-portal-brand-divider" aria-hidden="true" />
            <span className="brand-copy"><strong>Loom</strong><small>Personal AI Agent</small></span>
          </a>
          <div className="topbar-right">
            <nav className="portal-nav" aria-label={zh ? "页面导航" : "Portal navigation"}>
              <a href="#release">{zh ? "下载" : "Download"}</a><a href="#account">{zh ? "账户" : "Account"}</a><button type="button" onClick={openWeb}>{zh ? "网页版" : "Web"}</button>
            </nav>
            <div className="service-status"><i /><span>Stable</span></div>
          </div>
        </header>

        <section className="portal-title fade">
          <div className="portal-title-copy"><p className="eyebrow">SMIREL · PERSONAL AI AGENT</p><h1>Loom for Windows</h1><p>{zh ? "下载 Loom Windows 客户端，或通过浏览器连接你自己的 Loom Host。" : "Download Loom for Windows or connect to your own Loom Host from the browser."}</p></div>
          <div className="portal-title-meta"><span className="title-chip live">Stable</span><span className="title-chip">Windows x64</span></div>
        </section>

        <section className="dashboard" id="release">
          <article className="release-card cards fade">
            <div className="release-product"><div className="release-product-icon loom-product-mark"><img src={SMIREL_MARK} alt="" /></div><div className="release-product-copy"><strong>Loom for Windows</strong><span>Personal AI Agent</span></div><span className="release-channel">STABLE CHANNEL</span></div>
            <div className="release-head"><div><p className="kicker">LATEST RELEASE</p></div><span className="release-badge">WINDOWS · X64</span></div>
            <div className="version-large"><strong>{RELEASE.version}</strong><span>{RELEASE.published}</span></div>
            <p className="release-summary">{zh ? "当前正式版本。桌面端与 Loom Web 共用同一个本地 Agent Runtime、会话、审批、文件与 Computer Use。" : "Current stable release. Desktop and Loom Web share the same local Agent Runtime, conversations, approvals, files and Computer Use."}</p>
            <div className="release-state-row"><span className="release-state-item live"><i />Stable release</span><span className="release-state-item"><i />Local Host</span><span className="release-state-item"><i />Secure Web relay</span></div>
            <div className="release-meta"><div className="meta-item"><span>PLATFORM</span><strong>Windows 10 / 11 · x64</strong></div><div className="meta-item"><span>PACKAGE</span><strong>{RELEASE.package}</strong></div><div className="meta-item"><span>ACCOUNT</span><strong>{authenticated ? account.account.user?.email : (zh ? "未登录" : "Signed out")}</strong></div></div>
            <div className="loom-portal-release-actions">
              <a className="download-button cards" href={RELEASE.download}><span className="download-copy"><strong>Download Loom</strong><small>Loom Setup {RELEASE.version} · x64</small></span><span className="download-arrow">⇩</span></a>
              <button className="download-button cards loom-open-web-button" type="button" onClick={openWeb}><span className="download-copy"><strong>Open Loom Web</strong><small>{authenticated ? hostText : (zh ? "登录后连接 Loom Host" : "Sign in to connect your Loom Host")}</small></span><span className="download-arrow">→</span></button>
            </div>
          </article>

          <aside className="account-card cards fade" id="account">
            <div className="account-head"><div><p className="kicker">ACCOUNT ACCESS</p><h2>{authenticated ? "Loom Account" : authMode === "login" ? (zh ? "账户登录" : "Sign in") : (zh ? "创建账户" : "Create account")}</h2></div><span className="secure-pill">ACCESS</span></div>
            {!authenticated ? <>
              <p className="account-intro">{zh ? "账户入口直接嵌入原玻璃卡片布局，登录后即可连接 Loom Host。" : "Account access is embedded directly in the original glass portal. Sign in to connect your Loom Host."}</p>
              <form className="login-form" onSubmit={submitAuth} autoComplete="on">
                <label><span>{zh ? "邮箱" : "Email"}</span><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
                <label className="password-field"><span>{zh ? "密码" : "Password"}</span><input type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? (zh ? "隐藏" : "Hide") : (zh ? "显示" : "Show")}</button></label>
                <button className="login-button cards" type="submit" disabled={account.busy}><span>{account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : (zh ? "创建账户" : "Create account")}</span></button>
              </form>
              <div className="account-secondary-actions"><button className="account-link-button" type="button" onClick={() => { account.clearError(); setAuthMode(authMode === "login" ? "register" : "login"); }}>{authMode === "login" ? (zh ? "创建账户" : "Create account") : (zh ? "返回登录" : "Back to sign in")}</button></div>
              <p className={`form-note${account.error ? " is-error" : ""}`}>{account.error?.message || (zh ? "浏览器使用安全会话，不直接持有 Loom Host access token。" : "The browser uses a secure session and never directly holds the Loom Host access token.")}</p>
            </> : <div className="signed-in loom-portal-signed-in">
              <div className="signed-in-state"><div className="account-avatar">✓</div><div><p className="kicker">SIGNED IN</p><p className="account-email">{account.account.user?.email}</p></div></div>
              <div className="account-status-panel"><div className="account-status-line"><span>{zh ? "会话状态" : "Session"}</span><strong data-state="ok">{zh ? "已登录" : "Signed in"}</strong></div><div className="account-status-line"><span>Loom Host</span><strong data-state={hostState === "online" ? "ok" : "neutral"}>{hostState === "online" ? (selectedDeviceName || "Online") : hostState}</strong></div></div>
              <p className="loom-host-note">{hostError || hostText}</p>
              <div className="signed-account-actions"><button className="switch-account-button" type="button" onClick={openWeb}>Open Loom Web</button><button className="switch-account-button" type="button" onClick={onRemote}>{copy.remoteDevices}</button><button className="logout-button" type="button" onClick={() => void account.logout()}>{zh ? "退出登录" : "Sign out"}</button></div>
            </div>}
            <div className="account-access-flow"><span className="flow-label">ACCESS FLOW</span><div className="flow-steps"><div className="flow-step"><span>01</span><strong>{zh ? "账户登录" : "Sign in"}</strong></div><div className="flow-step"><span>02</span><strong>{zh ? "连接 Host" : "Connect Host"}</strong></div><div className="flow-step"><span>03</span><strong>Loom Web</strong></div></div></div>
            <div className="account-footer"><span>Secure account access</span><span>smirel.com</span></div>
          </aside>
        </section>

        <section className="utility-grid fade" id="details">
          <a className="utility-card cards" href={RELEASE.latest} target="_blank" rel="noreferrer"><span className="utility-icon">☷</span><span className="utility-overline">RELEASE</span><h3>{zh ? "更新日志" : "Release notes"}</h3><p>{zh ? "查看当前正式版本与变更。" : "View the current stable release and changes."}</p></a>
          <a className="utility-card cards" href={RELEASE.releases} target="_blank" rel="noreferrer"><span className="utility-icon">↺</span><span className="utility-overline">ARCHIVE</span><h3>{zh ? "历史版本" : "Archive"}</h3><p>{zh ? "查看已发布的 Windows 正式版本。" : "Browse released Windows builds."}</p></a>
          <div className="utility-card cards"><span className="utility-icon">▣</span><span className="utility-overline">SYSTEM</span><h3>{zh ? "运行环境" : "System"}</h3><p>Windows 10 / 11 · x64 · Loom Host</p></div>
          <a className="utility-card cards" href={RELEASE.support} target="_blank" rel="noreferrer"><span className="utility-icon">?</span><span className="utility-overline">SUPPORT</span><h3>{zh ? "安装帮助" : "Support"}</h3><p>{zh ? "安装、登录、Host 与 Web 连接问题。" : "Installation, account, Host and Web connection help."}</p></a>
        </section>
        <footer className="footer fade"><span>© 2026 Smirel · Loom</span><span className="footer-status"><i />Official distribution</span></footer>
      </main>
    </div>
  );
}
