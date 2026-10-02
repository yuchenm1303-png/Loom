import "./portal-base.css";
import "./portal-modules.css";
import "./portal-host-card.css";
import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import { Laptop, Download, UserRound, LogOut, Check, ArrowRight, Github, Mail, KeyRound, ShieldCheck } from "lucide-react";
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
  version: "v0.1.9",
  package: "137.6 MiB",
  download: "https://github.com/yuchenm1303-png/Loom/releases/latest/download/Loom-Setup-x64.exe",
} as const;

const HOST_COPY = {
  en: {
    localSetup: "Loom Host is not running on this computer, or Loom has not been installed yet. If Loom is already installed, open it and this page will reconnect automatically.",
    localConnecting: "Connecting securely to the Loom Host on this computer.",
    localOffline: "Loom is installed for this account, but its Host is offline. Open Loom on this computer and this page will reconnect automatically.",
  },
  zh: {
    localSetup: "这台电脑上当前没有运行 Loom Host。可能是已经安装但尚未启动，也可能还没有安装。若已安装，直接启动 Loom，本页面会自动重新连接。",
    localConnecting: "正在安全连接这台电脑上的 Loom Host。",
    localOffline: "这台电脑的 Loom Host 当前离线。直接启动 Loom，本页面会自动重新连接。",
  },
} as const;

type AuthStep = "form" | "verify" | "forgot" | "reset";

function passwordScore(value: string): number {
  let score = 0;
  if (value.length >= 8) score += 1;
  if (value.length >= 12) score += 1;
  if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
  return score;
}

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
  const [authStep, setAuthStep] = useState<AuthStep>("form");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [verificationCode, setVerificationCode] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [challengeEmail, setChallengeEmail] = useState("");
  const [resendWait, setResendWait] = useState(0);
  const [localError, setLocalError] = useState("");
  const [launchingLoom, setLaunchingLoom] = useState(false);
  const authenticated = Boolean(account.account.authenticated && account.account.user);
  const strength = passwordScore(password);
  const strengthLabel = !password
    ? (zh ? "未输入" : "Not entered")
    : strength >= 4
      ? (zh ? "较强" : "Strong")
      : strength >= 3
        ? (zh ? "良好" : "Good")
        : strength >= 2
          ? (zh ? "一般" : "Fair")
          : (zh ? "较弱" : "Weak");

  useEffect(() => {
    if (resendWait <= 0) return;
    const timer = window.setInterval(() => setResendWait((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearInterval(timer);
  }, [resendWait > 0]);

  useEffect(() => {
    const url = new URL(window.location.href);
    const oauthCode = url.searchParams.get("loom_oauth_code");
    const oauthError = url.searchParams.get("loom_oauth_error");
    if (!oauthCode && !oauthError) return;
    url.searchParams.delete("loom_oauth_code");
    url.searchParams.delete("loom_oauth_provider");
    url.searchParams.delete("loom_oauth_error");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    if (oauthError) {
      setLocalError(zh ? "快捷登录没有完成，请重试。" : "Quick sign-in did not complete. Please try again.");
      return;
    }
    if (oauthCode) void account.oauthExchange(oauthCode);
  }, []);

  function resetAuthFlow(nextMode: "login" | "register" = authMode) {
    account.clearError();
    setLocalError("");
    setAuthMode(nextMode);
    setAuthStep("form");
    setPassword("");
    setConfirmPassword("");
    setVerificationCode("");
    setChallengeId("");
    setChallengeEmail("");
    setResendWait(0);
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    account.clearError();
    if (authMode === "login") {
      const ok = await account.login(email.trim(), password);
      if (ok) setPassword("");
      return;
    }
    if (password !== confirmPassword) {
      setLocalError(zh ? "两次输入的密码不一致。" : "The passwords do not match.");
      return;
    }
    if (password.length < 8) {
      setLocalError(zh ? "密码至少需要 8 位。" : "Password must be at least 8 characters.");
      return;
    }
    if (account.capabilities.emailVerification) {
      const challenge = await account.registerStart(email.trim(), password);
      if (!challenge) return;
      setChallengeId(challenge.id);
      setChallengeEmail(challenge.email);
      setResendWait(challenge.resend_after || 60);
      setVerificationCode("");
      setAuthStep("verify");
      return;
    }
    const ok = await account.register(email.trim(), password);
    if (ok) {
      setPassword("");
      setConfirmPassword("");
    }
  }

  async function submitVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    const ok = await account.verifyEmail(challengeId, verificationCode);
    if (ok) resetAuthFlow("login");
  }

  async function resendVerification() {
    if (!challengeId || resendWait > 0) return;
    const challenge = await account.resendEmail(challengeId);
    if (!challenge) return;
    setChallengeId(challenge.id);
    setResendWait(challenge.resend_after || 60);
  }

  async function submitForgot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    const challenge = await account.forgotPassword(email.trim());
    if (!challenge) return;
    setChallengeId(challenge.id);
    setChallengeEmail(challenge.email);
    setResendWait(challenge.resend_after || 60);
    setVerificationCode("");
    setPassword("");
    setConfirmPassword("");
    setAuthStep("reset");
  }

  async function submitReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    if (password !== confirmPassword) {
      setLocalError(zh ? "两次输入的密码不一致。" : "The passwords do not match.");
      return;
    }
    const ok = await account.resetPassword(challengeId, verificationCode, password);
    if (ok) resetAuthFlow("login");
  }

  function startOauth(provider: "google" | "github") {
    window.location.assign(`/api/auth/oauth/start/${provider}`);
  }

  function focusAccount() {
    document.getElementById("account")?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => document.getElementById("emailInput")?.focus(), 220);
  }

  function openInstalledLoom() {
    setLaunchingLoom(true);
    setLocalError("");
    window.location.href = "loom://open?source=web";
    window.setTimeout(() => onEnter(), 1_200);
    window.setTimeout(() => onEnter(), 3_200);
    window.setTimeout(() => setLaunchingLoom(false), 4_500);
  }

  function primaryAction() {
    if (!authenticated) {
      focusAccount();
      return;
    }
    if (hostState === "offline") {
      openInstalledLoom();
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
          ? (zh ? "未检测到 Loom Host" : "Loom Host not detected")
          : (zh ? "等待连接" : "Waiting");

  const primaryLabel = !authenticated
    ? (zh ? "登录并打开 Loom Web" : "Sign in to Loom Web")
    : hostState === "online"
      ? (zh ? "打开 Loom Web" : "Open Loom Web")
      : hostState === "checking"
        ? (zh ? "正在连接…" : "Connecting…")
        : hostState === "unbound"
          ? (zh ? "重新检测 Loom Host" : "Check again")
          : hostState === "offline"
            ? launchingLoom
              ? (zh ? "正在启动 Loom…" : "Opening Loom…")
              : (zh ? "启动已安装的 Loom" : "Open installed Loom")
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
                ? "Loom Host 会在后台真正运行 Agent。桌面端只是可选界面，网页会安全连接同一个会话、文件、审批和 Computer Use。"
                : "Loom Host runs the Agent in the background. Desktop is optional; the web securely connects to the same conversations, files, approvals and Computer Use."}
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
            {!authenticated ? <section className="loom-login-module loom-auth-v2">
              <div className="loom-control-top">
                <div>
                  <p className="kicker">LOOM ACCOUNT</p>
                  <h2>{authStep === "verify"
                    ? (zh ? "验证你的邮箱" : "Verify your email")
                    : authStep === "forgot"
                      ? (zh ? "找回 Loom 账户" : "Recover your Loom account")
                      : authStep === "reset"
                        ? (zh ? "设置新密码" : "Set a new password")
                        : authMode === "login"
                          ? (zh ? "登录后继续" : "Continue with Loom")
                          : (zh ? "创建你的 Loom 账户" : "Create your Loom account")}</h2>
                </div>
                <span className="loom-security-label"><ShieldCheck size={12} aria-hidden="true" /> SECURE</span>
              </div>

              {authStep === "form" ? <>
                <p className="loom-control-copy">{zh ? "登录后会自动寻找这台电脑上的 Loom Host，由你点击进入 Loom。" : "Sign in to securely reconnect to this computer's Loom Host, then enter when you're ready."}</p>

                {(account.capabilities.google || account.capabilities.github) ? <>
                  <div className="loom-oauth-grid">
                    {account.capabilities.google ? <button className="loom-oauth-button" type="button" onClick={() => startOauth("google")} disabled={account.busy}><span className="loom-google-mark" aria-hidden="true">G</span><span>{zh ? "使用 Google 继续" : "Continue with Google"}</span></button> : null}
                    {account.capabilities.github ? <button className="loom-oauth-button" type="button" onClick={() => startOauth("github")} disabled={account.busy}><Github size={17} aria-hidden="true" /><span>{zh ? "使用 GitHub 继续" : "Continue with GitHub"}</span></button> : null}
                  </div>
                  <div className="loom-auth-divider"><span>{zh ? "或使用邮箱" : "or continue with email"}</span></div>
                </> : null}

                <form className="login-form loom-account-form" onSubmit={submitAuth} autoComplete="on">
                  <label><span>{zh ? "邮箱" : "Email"}</span><div className="loom-input-shell"><Mail size={16} aria-hidden="true" /><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></div></label>
                  <label className="password-field"><span className="loom-field-heading"><span>{zh ? "密码" : "Password"}</span>{authMode === "login" && account.capabilities.passwordReset ? <button className="loom-inline-link" type="button" onClick={() => { account.clearError(); setLocalError(""); setAuthStep("forgot"); }}>{zh ? "忘记密码？" : "Forgot password?"}</button> : null}</span><div className="loom-input-shell"><KeyRound size={16} aria-hidden="true" /><input type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onChange={(event) => setPassword(event.target.value)} required /><button className="password-toggle" type="button" aria-pressed={showPassword} onClick={() => setShowPassword((value) => !value)}>{showPassword ? (zh ? "隐藏" : "Hide") : (zh ? "显示" : "Show")}</button></div></label>
                  {authMode === "register" ? <>
                    <div className="loom-password-strength" data-score={strength}><div className="loom-strength-copy"><span>{zh ? "密码强度" : "Password strength"}</span><span>{strengthLabel}</span></div><div className="loom-strength-bars">{[1,2,3,4].map((level) => <i key={level} className={strength >= level ? "is-active" : ""} />)}</div><small>{zh ? "建议至少 12 位，并混合大小写、数字和符号" : "12+ characters with mixed case, numbers and symbols is recommended"}</small></div>
                    <label><span>{zh ? "确认密码" : "Confirm password"}</span><div className="loom-input-shell"><KeyRound size={16} aria-hidden="true" /><input type={showPassword ? "text" : "password"} autoComplete="new-password" placeholder="••••••••" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></div></label>
                  </> : null}
                  <button className="loom-form-submit" type="submit" disabled={!account.ready || account.busy}><span>{!account.ready ? (zh ? "加载中…" : "Loading…") : account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : account.capabilities.emailVerification ? (zh ? "创建并验证邮箱" : "Create & verify email") : (zh ? "创建账户" : "Create account")}</span><span aria-hidden="true">→</span></button>
                </form>
                <button className="loom-account-switch" type="button" onClick={() => resetAuthFlow(authMode === "login" ? "register" : "login")}>{authMode === "login" ? (zh ? "没有账户？创建一个" : "New to Loom? Create an account") : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}</button>
              </> : authStep === "verify" ? <>
                <p className="loom-control-copy">{zh ? `我们已向 ${challengeEmail} 发送了 6 位验证码。` : `We sent a 6-digit code to ${challengeEmail}.`}</p>
                <form className="login-form loom-account-form" onSubmit={submitVerification}>
                  <label><span>{zh ? "邮箱验证码" : "Email verification code"}</span><input className="loom-code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" placeholder="000000" value={verificationCode} onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required /></label>
                  <button className="loom-form-submit" type="submit" disabled={account.busy || verificationCode.length !== 6}><span>{account.busy ? (zh ? "验证中…" : "Verifying…") : (zh ? "验证并创建账户" : "Verify & create account")}</span><span aria-hidden="true">→</span></button>
                </form>
                <div className="loom-auth-row"><button className="loom-account-switch" type="button" disabled={account.busy || resendWait > 0} onClick={() => void resendVerification()}>{resendWait > 0 ? (zh ? `${resendWait} 秒后可重发` : `Resend in ${resendWait}s`) : (zh ? "重新发送验证码" : "Resend code")}</button><button className="loom-account-switch" type="button" onClick={() => resetAuthFlow("register")}>{zh ? "返回" : "Back"}</button></div>
              </> : authStep === "forgot" ? <>
                <p className="loom-control-copy">{zh ? "输入你的邮箱。若账户存在，我们会发送一次性验证码。" : "Enter your email. If the account exists, we'll send a one-time recovery code."}</p>
                <form className="login-form loom-account-form" onSubmit={submitForgot}>
                  <label><span>{zh ? "邮箱" : "Email"}</span><div className="loom-input-shell"><Mail size={16} aria-hidden="true" /><input id="emailInput" type="email" autoComplete="email" placeholder="name@example.com" value={email} onChange={(event) => setEmail(event.target.value)} required /></div></label>
                  <button className="loom-form-submit" type="submit" disabled={account.busy}><span>{account.busy ? (zh ? "发送中…" : "Sending…") : (zh ? "发送重置验证码" : "Send reset code")}</span><span aria-hidden="true">→</span></button>
                </form>
                <button className="loom-account-switch" type="button" onClick={() => resetAuthFlow("login")}>{zh ? "← 返回登录" : "← Back to sign in"}</button>
              </> : <>
                <p className="loom-control-copy">{zh ? `如果 ${challengeEmail} 已注册，你会收到 6 位验证码。` : `If ${challengeEmail} is registered, a 6-digit code is on its way.`}</p>
                <form className="login-form loom-account-form" onSubmit={submitReset}>
                  <label><span>{zh ? "验证码" : "Verification code"}</span><input className="loom-code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" placeholder="000000" value={verificationCode} onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required /></label>
                  <label><span>{zh ? "新密码" : "New password"}</span><div className="loom-input-shell"><KeyRound size={16} aria-hidden="true" /><input type={showPassword ? "text" : "password"} autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} /></div></label>
                  <label><span>{zh ? "确认新密码" : "Confirm new password"}</span><div className="loom-input-shell"><KeyRound size={16} aria-hidden="true" /><input type={showPassword ? "text" : "password"} autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required minLength={8} /></div></label>
                  <button className="loom-form-submit" type="submit" disabled={account.busy || verificationCode.length !== 6}><span>{account.busy ? (zh ? "重置中…" : "Resetting…") : (zh ? "重置密码并登录" : "Reset password & sign in")}</span><span aria-hidden="true">→</span></button>
                </form>
                <button className="loom-account-switch" type="button" onClick={() => resetAuthFlow("login")}>{zh ? "← 返回登录" : "← Back to sign in"}</button>
              </>}
              {(localError || account.error) ? <p className="form-note is-error">{localError || account.error?.message}</p> : null}
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
                <p className="loom-control-copy">{hostError || hostText}</p>
                {hostState === "unbound" ? <div className="loom-host-onboarding">
                  <div className="loom-host-onboarding-head">
                    <span className="loom-host-onboarding-icon" aria-hidden="true"><Laptop size={17} /></span>
                    <div><strong>{zh ? "已经安装 Loom？" : "Already installed Loom?"}</strong><span>{zh ? "这种情况下通常只是 Loom 和 Host 还没有启动。" : "In this state, Loom may simply be installed but not running yet."}</span></div>
                  </div>
                  <button className="loom-host-install-action loom-host-open-action" type="button" onClick={openInstalledLoom} disabled={launchingLoom}>
                    <span>{launchingLoom ? (zh ? "正在启动 Loom…" : "Opening Loom…") : (zh ? "启动已安装的 Loom" : "Open installed Loom")}</span><ArrowRight size={15} aria-hidden="true" />
                  </button>
                  <p className="loom-host-onboarding-note">{zh ? "若浏览器没有拉起应用，请从 Windows 开始菜单启动 Loom。启动后网页会自动重试；也可以点击下方“重新检测”。" : "If the browser does not open the app, start Loom from the Windows Start menu. This page retries automatically, or you can use Check again below."}</p>
                  <div className="loom-host-onboarding-divider" aria-hidden="true" />
                  <div className="loom-host-onboarding-head">
                    <span className="loom-host-onboarding-icon" aria-hidden="true"><Download size={17} /></span>
                    <div><strong>{zh ? "这台电脑还没安装 Loom？" : "Loom not installed on this computer?"}</strong><span>{zh ? "只需要安装一次，本机 Host 与 Agent Runtime 会一起安装。" : "Install it once; the local Host and Agent Runtime are included."}</span></div>
                  </div>
                  <ol className="loom-host-onboarding-steps">
                    <li><span>1</span><p><strong>{zh ? "下载安装 Loom" : "Install Loom"}</strong><small>{zh ? "安装完成后会注册网页唤起能力。" : "The installer also registers browser-to-Loom launch support."}</small></p></li>
                    <li><span>2</span><p><strong>{zh ? "启动 Loom" : "Open Loom"}</strong><small>{zh ? "Loom Host 启动后会自动连接你的账户。" : "Loom Host connects to your account when the app starts."}</small></p></li>
                    <li><span>3</span><p><strong>{zh ? "继续留在这个页面" : "Stay on this page"}</strong><small>{zh ? "网页会自动发现、配对并进入当前电脑。" : "Loom Web will automatically discover, pair, and reconnect this computer."}</small></p></li>
                  </ol>
                  <a className="loom-host-install-action" href={RELEASE.download}><span>{zh ? "下载 Loom for Windows" : "Download Loom for Windows"}</span><Download size={15} aria-hidden="true" /></a>
                </div> : null}
                {hostState === "offline" ? <div className="loom-host-recovery"><span>{zh ? "这台电脑此前已经连接过 Loom，现在只是 Host 没有运行。点击下方“启动已安装的 Loom”，网页会自动重连。" : "This computer has connected before; its Host is simply not running now. Use Open installed Loom below and the web will reconnect automatically."}</span><a href={RELEASE.download}>{zh ? "需要重新安装？" : "Need to reinstall?"}</a></div> : null}
                {hostState === "checking" || hostState === "online" ? <div className={`loom-connection-track${hostState === "checking" ? " is-working" : " is-online"}`} aria-hidden="true"><span /></div> : null}
                {hostState !== "checking" ? <button className="loom-form-submit loom-host-action" type="button" onClick={primaryAction}><span>{primaryLabel}</span><ArrowRight size={15} aria-hidden="true" /></button> : null}
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
