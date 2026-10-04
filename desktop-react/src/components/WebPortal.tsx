import "./portal-base.css";
import "./portal-modules.css";
import "./portal-host-card.css";
import type { CSSProperties, FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { Laptop, Download, UserRound, LogOut, Check, ArrowRight, Github, Mail, KeyRound, ShieldCheck, Eye, EyeOff } from "lucide-react";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import { HostSetupActions } from "./HostSetupActions";
import { FALLBACK_RELEASE, fetchPortalRelease } from "../portalRelease";

export type PortalHostState = "idle" | "checking" | "online" | "offline" | "unbound";
type AccountController = ReturnType<typeof useAccount>;

const SMIREL_LOGO = "/smirel-logo.png";
const PORTAL_STYLES = [
  "https://smirel.com/download/styles-v3.css",
  "https://smirel.com/download/cosmic-bright-v1.css",
  "https://smirel.com/download/portal-polish-v1.css",
  "https://smirel.com/download/portal-polish-v2.css",
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
    localConnecting: "Finding and securely connecting your Host. Once connected, click Open workspace to enter.",
    localOffline: "The connection to Loom Host was lost. Your conversations stay on this computer; reconnect to continue.",
  },
  zh: {
    localSetup: "启动本机 Loom Host，即可使用这台电脑上的文件和应用。本页会自动连接。",
    localConnecting: "正在查找并安全连接本机 Host，连接后点击进入工作区。",
    localOffline: "与 Loom Host 的连接已断开。会话仍保留在本机，重新连接即可继续。",
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

export function WebPortal({ account, hostState, hostError, selectedDeviceName, onEnter, onOpenProfile, hostDetected = false }: {
  account: AccountController;
  hostState: PortalHostState;
  hostError: string;
  selectedDeviceName: string;
  onEnter: () => void;
  onOpenProfile?: () => void;
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
  const [authStep, setAuthStep] = useState<AuthStep>("form");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const emailInputRef = useRef<HTMLInputElement>(null);
  const passwordInputRef = useRef<HTMLInputElement>(null);
  const confirmPasswordInputRef = useRef<HTMLInputElement>(null);
  const [showPassword, setShowPassword] = useState(false);
  const [verificationCode, setVerificationCode] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [challengeEmail, setChallengeEmail] = useState("");
  const [resendWait, setResendWait] = useState(0);
  const [localError, setLocalError] = useState("");
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

  // Chrome/Edge may visually autofill controlled fields without immediately
  // dispatching the React change event. Mirror the real DOM values back into
  // state so password strength, validation, and submit all agree with what the
  // user actually sees.
  useEffect(() => {
    if (authenticated || authStep !== "form") return;
    const syncAutofill = () => {
      const nextEmail = emailInputRef.current?.value ?? "";
      const nextPassword = passwordInputRef.current?.value ?? "";
      const nextConfirm = confirmPasswordInputRef.current?.value ?? "";
      if (nextEmail) setEmail((current) => current === nextEmail ? current : nextEmail);
      if (nextPassword) setPassword((current) => current === nextPassword ? current : nextPassword);
      if (authMode === "register" && nextConfirm) {
        setConfirmPassword((current) => current === nextConfirm ? current : nextConfirm);
      }
    };
    const frame = window.requestAnimationFrame(syncAutofill);
    const timers = [80, 320, 900].map((delay) => window.setTimeout(syncAutofill, delay));
    return () => {
      window.cancelAnimationFrame(frame);
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [authenticated, authStep, authMode]);

  function fieldValue(form: FormData, name: string, fallback: string): string {
    const value = form.get(name);
    return typeof value === "string" ? value : fallback;
  }

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
    const form = new FormData(event.currentTarget);
    const submittedEmail = fieldValue(form, "email", email).trim();
    const submittedPassword = fieldValue(form, "password", password);
    const submittedConfirm = fieldValue(form, "confirmPassword", confirmPassword);
    setEmail(submittedEmail);
    setPassword(submittedPassword);
    if (authMode === "register") setConfirmPassword(submittedConfirm);
    if (authMode === "login") {
      const ok = await account.login(submittedEmail, submittedPassword);
      if (ok) setPassword("");
      return;
    }
    if (submittedPassword !== submittedConfirm) {
      setLocalError(zh ? "两次输入的密码不一致。" : "The passwords do not match.");
      return;
    }
    if (submittedPassword.length < 8) {
      setLocalError(zh ? "密码至少需要 8 位。" : "Password must be at least 8 characters.");
      return;
    }
    if (account.capabilities.emailVerification) {
      const challenge = await account.registerStart(submittedEmail, submittedPassword);
      if (!challenge) return;
      setChallengeId(challenge.id);
      setChallengeEmail(challenge.email);
      setResendWait(challenge.resend_after || 60);
      setVerificationCode("");
      setAuthStep("verify");
      return;
    }
    const ok = await account.register(submittedEmail, submittedPassword);
    if (ok) {
      setPassword("");
      setConfirmPassword("");
    }
  }

  async function submitVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    const form = new FormData(event.currentTarget);
    const submittedCode = fieldValue(form, "verificationCode", verificationCode);
    setVerificationCode(submittedCode);
    const ok = await account.verifyEmail(challengeId, submittedCode);
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
    const form = new FormData(event.currentTarget);
    const submittedEmail = fieldValue(form, "email", email).trim();
    setEmail(submittedEmail);
    const challenge = await account.forgotPassword(submittedEmail);
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
    const form = new FormData(event.currentTarget);
    const submittedCode = fieldValue(form, "verificationCode", verificationCode);
    const submittedPassword = fieldValue(form, "password", password);
    const submittedConfirm = fieldValue(form, "confirmPassword", confirmPassword);
    setVerificationCode(submittedCode);
    setPassword(submittedPassword);
    setConfirmPassword(submittedConfirm);
    if (submittedPassword !== submittedConfirm) {
      setLocalError(zh ? "两次输入的密码不一致。" : "The passwords do not match.");
      return;
    }
    const ok = await account.resetPassword(challengeId, submittedCode, submittedPassword);
    if (ok) resetAuthFlow("login");
  }

  function startOauth(provider: "google" | "github") {
    window.location.assign(`/api/auth/oauth/start/${provider}`);
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
    ? (zh ? "Host 已连接，点击进入工作区。" : "Host connected. Click Open workspace to enter.")
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

          <aside className={`loom-control${!authenticated ? " is-auth-mode" : ""}`} id="account">
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
                <p className="loom-control-copy">{zh ? "登录后会自动连接本机 Loom Host，连接成功后点击进入工作区。" : "Sign in to automatically connect your local Host, then click Open workspace."}</p>

                {(account.capabilities.google || account.capabilities.github) ? <>
                  <div className="loom-oauth-grid">
                    {account.capabilities.google ? <button className="loom-oauth-button" type="button" onClick={() => startOauth("google")} disabled={account.busy}><span className="loom-google-mark" aria-hidden="true">G</span><span>{zh ? "使用 Google 继续" : "Continue with Google"}</span></button> : null}
                    {account.capabilities.github ? <button className="loom-oauth-button" type="button" onClick={() => startOauth("github")} disabled={account.busy}><Github size={17} aria-hidden="true" /><span>{zh ? "使用 GitHub 继续" : "Continue with GitHub"}</span></button> : null}
                  </div>
                  <div className="loom-auth-divider"><span>{zh ? "或使用邮箱" : "or continue with email"}</span></div>
                </> : null}

                <form className="loom-account-form" onSubmit={submitAuth} autoComplete="on">
                  <label><span>{zh ? "邮箱" : "Email"}</span><div className="loom-input-shell"><Mail size={17} aria-hidden="true" /><input ref={emailInputRef} id="emailInput" name="email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false} placeholder="name@example.com" value={email} onInput={(event) => setEmail(event.currentTarget.value)} onChange={(event) => setEmail(event.target.value)} required /></div></label>
                  <label className="loom-password-field"><span className="loom-field-heading"><span>{zh ? "密码" : "Password"}</span>{authMode === "login" && account.capabilities.passwordReset ? <button className="loom-inline-link" type="button" onClick={() => { account.clearError(); setLocalError(""); setAuthStep("forgot"); }}>{zh ? "忘记密码？" : "Forgot password?"}</button> : null}</span><div className="loom-input-shell"><KeyRound size={17} aria-hidden="true" /><input ref={passwordInputRef} name="password" type={showPassword ? "text" : "password"} autoComplete={authMode === "login" ? "current-password" : "new-password"} placeholder="••••••••" value={password} onInput={(event) => setPassword(event.currentTarget.value)} onChange={(event) => setPassword(event.target.value)} required /><button className="loom-password-toggle" type="button" aria-pressed={showPassword} aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")} title={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}</button></div></label>
                  {authMode === "register" ? <>
                    <div className="loom-password-strength" data-score={strength}><div className="loom-strength-copy"><span>{zh ? "密码强度" : "Password strength"}</span><span>{strengthLabel}</span></div><div className="loom-strength-bars">{[1,2,3,4].map((level) => <i key={level} className={strength >= level ? "is-active" : ""} />)}</div><small>{zh ? "建议至少 12 位，并混合大小写、数字和符号" : "12+ characters with mixed case, numbers and symbols is recommended"}</small></div>
                    <label><span>{zh ? "确认密码" : "Confirm password"}</span><div className="loom-input-shell"><KeyRound size={17} aria-hidden="true" /><input ref={confirmPasswordInputRef} name="confirmPassword" type={showPassword ? "text" : "password"} autoComplete="new-password" placeholder="••••••••" value={confirmPassword} onInput={(event) => setConfirmPassword(event.currentTarget.value)} onChange={(event) => setConfirmPassword(event.target.value)} required /><button className="loom-password-toggle" type="button" aria-pressed={showPassword} aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}</button></div></label>
                  </> : null}
                  <button className="loom-form-submit" type="submit" disabled={!account.ready || account.busy}><span>{!account.ready ? (zh ? "加载中…" : "Loading…") : account.busy ? (zh ? "处理中…" : "Working…") : authMode === "login" ? (zh ? "登录" : "Sign in") : account.capabilities.emailVerification ? (zh ? "创建并验证邮箱" : "Create & verify email") : (zh ? "创建账户" : "Create account")}</span><span aria-hidden="true">→</span></button>
                </form>
                <button className="loom-account-switch" type="button" onClick={() => resetAuthFlow(authMode === "login" ? "register" : "login")}>{authMode === "login" ? (zh ? "没有账户？创建一个" : "New to Loom? Create an account") : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}</button>
              </> : authStep === "verify" ? <>
                <p className="loom-control-copy">{zh ? `我们已向 ${challengeEmail} 发送了 6 位验证码。` : `We sent a 6-digit code to ${challengeEmail}.`}</p>
                <form className="loom-account-form" onSubmit={submitVerification}>
                  <label><span>{zh ? "邮箱验证码" : "Email verification code"}</span><input className="loom-code-input" name="verificationCode" inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" placeholder="000000" value={verificationCode} onInput={(event) => setVerificationCode(event.currentTarget.value.replace(/\D/g, "").slice(0, 6))} onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required /></label>
                  <button className="loom-form-submit" type="submit" disabled={account.busy || verificationCode.length !== 6}><span>{account.busy ? (zh ? "验证中…" : "Verifying…") : (zh ? "验证并创建账户" : "Verify & create account")}</span><span aria-hidden="true">→</span></button>
                </form>
                <div className="loom-auth-row"><button className="loom-account-switch" type="button" disabled={account.busy || resendWait > 0} onClick={() => void resendVerification()}>{resendWait > 0 ? (zh ? `${resendWait} 秒后可重发` : `Resend in ${resendWait}s`) : (zh ? "重新发送验证码" : "Resend code")}</button><button className="loom-account-switch" type="button" onClick={() => resetAuthFlow("register")}>{zh ? "返回" : "Back"}</button></div>
              </> : authStep === "forgot" ? <>
                <p className="loom-control-copy">{zh ? "输入你的邮箱。若账户存在，我们会发送一次性验证码。" : "Enter your email. If the account exists, we'll send a one-time recovery code."}</p>
                <form className="loom-account-form" onSubmit={submitForgot}>
                  <label><span>{zh ? "邮箱" : "Email"}</span><div className="loom-input-shell"><Mail size={17} aria-hidden="true" /><input ref={emailInputRef} id="emailInput" name="email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false} placeholder="name@example.com" value={email} onInput={(event) => setEmail(event.currentTarget.value)} onChange={(event) => setEmail(event.target.value)} required /></div></label>
                  <button className="loom-form-submit" type="submit" disabled={account.busy}><span>{account.busy ? (zh ? "发送中…" : "Sending…") : (zh ? "发送重置验证码" : "Send reset code")}</span><span aria-hidden="true">→</span></button>
                </form>
                <button className="loom-account-switch" type="button" onClick={() => resetAuthFlow("login")}>{zh ? "← 返回登录" : "← Back to sign in"}</button>
              </> : <>
                <p className="loom-control-copy">{zh ? `如果 ${challengeEmail} 已注册，你会收到 6 位验证码。` : `If ${challengeEmail} is registered, a 6-digit code is on its way.`}</p>
                <form className="loom-account-form" onSubmit={submitReset}>
                  <label><span>{zh ? "验证码" : "Verification code"}</span><input className="loom-code-input" name="verificationCode" inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" placeholder="000000" value={verificationCode} onInput={(event) => setVerificationCode(event.currentTarget.value.replace(/\D/g, "").slice(0, 6))} onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))} required /></label>
                  <label><span>{zh ? "新密码" : "New password"}</span><div className="loom-input-shell"><KeyRound size={17} aria-hidden="true" /><input name="password" type={showPassword ? "text" : "password"} autoComplete="new-password" value={password} onInput={(event) => setPassword(event.currentTarget.value)} onChange={(event) => setPassword(event.target.value)} required minLength={8} /><button className="loom-password-toggle" type="button" aria-pressed={showPassword} aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}</button></div></label>
                  <label><span>{zh ? "确认新密码" : "Confirm new password"}</span><div className="loom-input-shell"><KeyRound size={17} aria-hidden="true" /><input name="confirmPassword" type={showPassword ? "text" : "password"} autoComplete="new-password" value={confirmPassword} onInput={(event) => setConfirmPassword(event.currentTarget.value)} onChange={(event) => setConfirmPassword(event.target.value)} required minLength={8} /><button className="loom-password-toggle" type="button" aria-pressed={showPassword} aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}</button></div></label>
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
                <p className="loom-control-copy">{hostState === "checking" && hostError ? hostError : hostText}</p>
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
                    <dd title={account.account.user?.email || ""}><span>{account.account.user?.display_name?.trim() || account.account.user?.email}</span><Check size={13} aria-hidden="true" /></dd>
                  </div>
                  <div className="loom-identity-row">
                    <dt><Laptop size={18} strokeWidth={1.5} aria-hidden="true" /><span>{zh ? "当前电脑" : "This computer"}</span></dt>
                    <dd><span>{selectedDeviceName || (zh ? "等待发现" : "Waiting for discovery")}</span></dd>
                  </div>
                </dl>
                {onOpenProfile ? <button className="loom-form-submit loom-profile-home-action" type="button" onClick={onOpenProfile}>
                  <span>{zh ? "进入个人主页" : "Open profile home"}</span><ArrowRight size={15} aria-hidden="true" />
                </button> : null}
              </section>
            </>}
            </div>
          </aside>
        </section>
      </main>
    </div>
  );
}
