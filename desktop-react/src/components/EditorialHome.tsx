import "./editorial-home.css";
import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { FALLBACK_RELEASE, fetchPortalRelease, type PortalRelease } from "../portalRelease";
import { PortalLiquidCursor } from "./PortalLiquidCursor";
import { HostSetupActions } from "./HostSetupActions";
import { ArrowRight, Github, KeyRound, Mail, Eye, EyeOff } from "./icons";
import type { PortalHostState } from "./WebPortal";

type AccountController = ReturnType<typeof import("../state/useAccount").useAccount>;

const SMIREL_LOGO = "/smirel-logo.png";
const LOOM_ICON = "/loom-icon.png";

type AuthStep = "form" | "verify" | "forgot" | "reset";

type Starter = {
  key: string;
  title: [string, string];
  note: [string, string];
  prompt: [string, string];
};

const STARTERS: Starter[] = [
  {
    key: "inspect",
    title: ["Inspect this project", "了解这个项目"],
    note: ["Map the architecture and tell me what matters first.", "梳理架构，告诉我最先该关注什么。"],
    prompt: [
      "Inspect this project, map the architecture, and tell me what I should understand first.",
      "检查这个项目，梳理它的架构，并告诉我最先应该了解什么。",
    ],
  },
  {
    key: "feature",
    title: ["Implement a feature", "实现一个功能"],
    note: ["Turn a product idea into a focused code change.", "把产品想法变成一次聚焦的代码改动。"],
    prompt: [
      "Help me implement a feature in this project. Start by identifying the smallest clean approach.",
      "帮我在这个项目中实现一个功能。先找出最小、最干净的实现方式。",
    ],
  },
  {
    key: "trace",
    title: ["Trace a problem", "追查一个问题"],
    note: ["Follow the failure to its root cause before changing code.", "先追到根本原因，再动手改代码。"],
    prompt: [
      "Investigate the current project for the problem I am seeing and trace it to the root cause before making changes.",
      "排查当前项目中我遇到的问题，在修改代码之前先追查到根本原因。",
    ],
  },
  {
    key: "workflow",
    title: ["Run a workflow", "运行一个工作流"],
    note: ["Use tools and the workspace to complete a multi-step task.", "借助工具和工作区完成一个多步骤任务。"],
    prompt: [
      "Use the available tools and workspace to complete a useful multi-step task for this project.",
      "使用可用的工具和工作区，为这个项目完成一个有用的多步骤任务。",
    ],
  },
];

const CHANGELOG: { version: string; note: string; date: string }[] = [
  { version: "v0.1.115", note: "browser cards, sidebar pages, steadier previews", date: "22 Oct" },
  { version: "v0.1.114", note: "conversation home and dock alignment", date: "21 Oct" },
  { version: "v0.1.113", note: "packaging and update channel", date: "18 Oct" },
];

function passwordScore(value: string): number {
  let score = 0;
  if (value.length >= 8) score += 1;
  if (value.length >= 12) score += 1;
  if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
  if (/\d/.test(value) && /[^A-Za-z0-9]/.test(value)) score += 1;
  return score;
}

export function EditorialHome({
  account,
  hostState,
  hostError,
  selectedDeviceName,
  onEnter,
  onOpenProfile,
  hostDetected = false,
}: {
  account: AccountController;
  hostState: PortalHostState;
  hostError: string;
  selectedDeviceName: string;
  onEnter(): void;
  onOpenProfile?(): void;
  hostDetected?: boolean;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const lang = zh ? 1 : 0;
  const [release, setRelease] = useState<PortalRelease>(FALLBACK_RELEASE);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [authOpen, setAuthOpen] = useState(false);
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
  const [ask, setAsk] = useState("");
  const emailInputRef = useRef<HTMLInputElement>(null);
  const passwordInputRef = useRef<HTMLInputElement>(null);

  const authenticated = Boolean(account.account.authenticated && account.account.user);
  const user = account.account.user;
  const strength = passwordScore(password);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    fetchPortalRelease(controller.signal).then(setRelease).catch(() => {
      // The generic latest download remains available without stale metadata.
    }).finally(() => window.clearTimeout(timeout));
    return () => { controller.abort(); window.clearTimeout(timeout); };
  }, []);

  useEffect(() => {
    const stored = (() => {
      try { return localStorage.getItem("loom.site.theme"); } catch { return null; }
    })();
    if (stored === "light" || stored === "dark") setTheme(stored);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.loomSiteTheme = theme;
  }, [theme]);

  useEffect(() => {
    if (resendWait <= 0) return;
    const timer = window.setInterval(() => setResendWait((value) => Math.max(0, value - 1)), 1_000);
    return () => window.clearInterval(timer);
  }, [resendWait > 0]);

  // The OAuth provider returns here with a one-time code; exchange it for a session.
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("loom_oauth_code");
    const error = url.searchParams.get("loom_oauth_error");
    if (!code && !error) return;
    url.searchParams.delete("loom_oauth_code");
    url.searchParams.delete("loom_oauth_provider");
    url.searchParams.delete("loom_oauth_error");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    if (error) {
      setLocalError(zh ? "快捷登录没有完成，请重试。" : "Quick sign-in did not complete. Please try again.");
      return;
    }
    if (code) void account.oauthExchange(code);
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

  function openAuth(mode: "login" | "register" = "login") {
    resetAuthFlow(mode);
    setAuthOpen(true);
  }

  function closeAuth() {
    setAuthOpen(false);
    setLocalError("");
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    account.clearError();
    setPassword("");
    setConfirmPassword("");
    if (authMode === "login") {
      const ok = await account.login(email.trim(), password);
      if (ok) { setPassword(""); closeAuth(); }
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
    if (ok) { setPassword(""); closeAuth(); }
  }

  async function submitVerification(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    const ok = await account.verifyEmail(challengeId, verificationCode);
    if (ok) { resetAuthFlow("login"); closeAuth(); }
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
    if (ok) { resetAuthFlow("login"); closeAuth(); }
  }

  function startOauth(provider: "google" | "github") {
    window.location.assign(`/api/auth/oauth/start/${provider}`);
  }

  function toggleTheme() {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try { localStorage.setItem("loom.site.theme", next); } catch { }
      return next;
    });
  }

  function sendAsk(prompt: string) {
    const text = prompt.trim();
    if (!text) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(() => { });
      }
    } catch { }
    window.open("https://app.smirel.com/", "_blank", "noopener");
  }

  const hostLabel = hostState === "online"
    ? (selectedDeviceName || (zh ? "已连接" : "Connected"))
    : hostState === "account-mismatch"
      ? (zh ? "账号不一致" : "Account mismatch")
      : hostState === "checking"
        ? (zh ? "正在连接" : "Connecting")
        : hostState === "offline"
          ? (zh ? "已离线" : "Offline")
          : hostState === "unbound"
            ? (zh ? "未发现" : "Not found")
            : (zh ? "等待连接" : "Waiting");

  const hostText = hostState === "online"
    ? (zh ? "本机工作区已就绪。" : "Your local workspace is ready.")
    : hostState === "account-mismatch"
      ? (zh ? "本机 Loom Host 与网页账号不同。" : "This computer's Host uses a different account.")
      : hostState === "checking"
        ? (zh ? "正在查找本机 Loom Host。" : "Finding your local Loom Host.")
        : hostState === "unbound" || hostState === "offline"
          ? (zh ? "启动本机 Loom Host 即可继续。" : "Start your local Loom Host to continue.")
          : (zh ? "等待 Loom Host。" : "Waiting for Loom Host.");

  return (
    <div className="loom-site loom-portal-page" data-loom-site-theme={theme}>
      <PortalLiquidCursor />
      <div className="loom-site-page">
        <header className="loom-site-head">
          <a className="loom-site-brand" href="https://smirel.com/" target="_blank" rel="noopener noreferrer">
            <img src={SMIREL_LOGO} alt="Smirel" />
            <span className="loom-site-brand-rule" aria-hidden="true" />
            <span className="loom-site-brand-name">
              <img className="loom-site-icon" src={LOOM_ICON} alt="" aria-hidden="true" />
              Loom
            </span>
          </a>

          <div className="loom-site-head-right">
            <span className="loom-site-runtime">Local · 127.0.0.1</span>
            {authenticated ? (
              <span className="loom-site-account">
                <span className="loom-site-account-name">{user?.display_name?.trim() || user?.email}</span>
                <button type="button" className="loom-site-link" onClick={() => void account.logout()}>
                  {zh ? "退出" : "Sign out"}
                </button>
              </span>
            ) : (
              <button type="button" className="loom-site-link" onClick={() => openAuth("login")}>
                {zh ? "登录" : "Sign in"}
              </button>
            )}
            <button
              type="button"
              className="loom-site-switch"
              role="switch"
              aria-checked={theme === "dark"}
              aria-label={theme === "dark" ? (zh ? "切换到浅色" : "Switch to light theme") : (zh ? "切换到深色" : "Switch to dark theme")}
              onClick={toggleTheme}
            />
          </div>
        </header>

        <main className="loom-site-main">
          <section className="loom-site-hero" aria-labelledby="loom-site-claim">
            <div className="loom-site-intro">
              <h1 id="loom-site-claim" className="loom-site-claim">
                {zh ? "一个真正干活的本地智能体。" : "A local agent runtime for real work."}
              </h1>
              <p className="loom-site-description">
                {zh ? (
                  <>它读你的代码库、改文件、跑命令、上网——都在你这台机器上，按你的授权执行。</>
                ) : (
                  <>It reads the codebase, edits files, runs commands and browses the web — on your machine, with your permission.</>
                )}
              </p>
              <div className="loom-site-entry">
                {authenticated ? (
                  <a href="https://app.smirel.com/" target="_blank" rel="noopener">
                    {zh ? "打开 Loom" : "Open Loom"}<ArrowRight size={16} aria-hidden="true" />
                  </a>
                ) : (
                  <button type="button" className="loom-site-claim-link" onClick={() => openAuth("login")}>
                    {zh ? "开始使用" : "Get started"}<ArrowRight size={16} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>

            <dl className="loom-site-facts">
              <div><dt>Runtime</dt><dd>Local</dd></div>
              <div><dt>Tools</dt><dd>Files · Shell · Web</dd></div>
              <div><dt>Models</dt><dd>Bring your own</dd></div>
              <div><dt>Release</dt><dd>{(release.version || "0.1.115").replace(/^v/, "")}</dd></div>
            </dl>
          </section>

          <section className="loom-site-section" aria-labelledby="loom-site-start">
            <div className="loom-site-section-head">
              <h2 id="loom-site-start">{zh ? "开始" : "Start"}</h2>
            </div>
            <div className="loom-site-starters">
              {STARTERS.map((starter) => (
                <button
                  key={starter.key}
                  type="button"
                  className="loom-site-starter"
                  onClick={() => sendAsk(starter.prompt[lang])}
                >
                  <span className="loom-site-starter-title">{starter.title[lang]}</span>
                  <span className="loom-site-starter-note">{starter.note[lang]}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="loom-site-section" aria-labelledby="loom-site-latest">
            <div className="loom-site-section-head">
              <h2 id="loom-site-latest">{zh ? "更新" : "Latest"}</h2>
            </div>
            <div className="loom-site-latest">
              {CHANGELOG.map((entry) => (
                <a
                  key={entry.version}
                  href="https://github.com/yuchenm1303-png/Loom/releases"
                  target="_blank"
                  rel="noopener"
                >
                  <span className="loom-site-latest-title">{entry.version} — {entry.note}</span>
                  <time>{entry.date}</time>
                </a>
              ))}
            </div>
          </section>

          {authenticated ? (
            <section className="loom-site-section" aria-labelledby="loom-site-host">
              <div className="loom-site-section-head">
                <h2 id="loom-site-host">{zh ? "本机" : "This computer"}</h2>
                <span>{hostLabel}</span>
              </div>
              <p className="loom-site-host-text" role="status">{hostText}</p>
              {hostState === "unbound" || hostState === "offline" ? (
                <HostSetupActions zh={zh} download={release.download} onRetry={onEnter} hostDetected={hostDetected} />
              ) : null}
              {hostError ? (
                <details className="loom-site-details">
                  <summary>{zh ? "连接详情" : "Connection details"}</summary>
                  <p>{hostError}</p>
                </details>
              ) : null}
              {hostState === "online" ? (
                <button type="button" className="loom-site-action" onClick={onEnter}>
                  <span>{zh ? "打开 Loom Web" : "Open Loom Web"}</span>
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
              ) : null}
              {onOpenProfile ? (
                <button type="button" className="loom-site-link loom-site-profile" onClick={onOpenProfile}>
                  {zh ? "进入个人主页" : "Open profile home"}
                </button>
              ) : null}
            </section>
          ) : null}
        </main>

        <div className="loom-site-ask">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              sendAsk(ask);
              setAsk("");
            }}
          >
            <div className="loom-site-ask-row">
              <input
                type="text"
                value={ask}
                onChange={(event) => setAsk(event.currentTarget.value)}
                placeholder={zh ? "让 Loom 检查代码、修改文件、运行命令…" : "Ask Loom to inspect, edit, run, browse…"}
                aria-label={zh ? "向 Loom 提问" : "Ask Loom"}
              />
              <button type="submit" className="loom-site-ask-send">{zh ? "发送" : "Send"}</button>
            </div>
            <div className="loom-site-ask-foot">
              <span>
                {zh
                  ? `Windows · ${(release.version || "v0.1.115")} · ${hostState === "online" ? "已连接" : "本机运行"}`
                  : `Windows · ${(release.version || "v0.1.115")} · ${hostState === "online" ? "Host connected" : "Runs locally"}`}
              </span>
              <span>{zh ? "提示词会复制到剪贴板，然后打开 Loom" : "Prompt is copied, then Loom opens"}</span>
            </div>
          </form>
        </div>
      </div>

      {authOpen ? (
        <div className="loom-site-auth" role="dialog" aria-modal="true" aria-label={zh ? "登录 Loom" : "Sign in to Loom"}>
          <div className="loom-site-auth-card">
            <button type="button" className="loom-site-auth-close" onClick={closeAuth} aria-label={zh ? "关闭" : "Close"}>×</button>
            <p className="loom-site-auth-eyebrow">{authMode === "login" ? (zh ? "登录" : "Sign in") : (zh ? "注册" : "Register")}</p>
            <h2>
              {authStep === "verify"
                ? (zh ? "验证你的邮箱" : "Verify your email")
                : authStep === "forgot"
                  ? (zh ? "找回 Loom 账户" : "Recover your Loom account")
                  : authStep === "reset"
                    ? (zh ? "设置新密码" : "Set a new password")
                    : authMode === "login"
                      ? (zh ? "欢迎回来" : "Welcome back")
                      : (zh ? "创建你的 Loom 账户" : "Create your Loom account")}
            </h2>

            {authStep === "form" ? (
              <>
                {(account.capabilities.google || account.capabilities.github) ? (
                  <>
                    <div className="loom-site-auth-oauth">
                      {account.capabilities.google ? (
                        <button type="button" onClick={() => startOauth("google")} disabled={account.busy}>
                          <span className="loom-site-auth-mark" aria-hidden="true">G</span>
                          {zh ? "使用 Google 继续" : "Continue with Google"}
                        </button>
                      ) : null}
                      {account.capabilities.github ? (
                        <button type="button" onClick={() => startOauth("github")} disabled={account.busy}>
                          <Github size={16} aria-hidden="true" />
                          {zh ? "使用 GitHub 继续" : "Continue with GitHub"}
                        </button>
                      ) : null}
                    </div>
                    <p className="loom-site-auth-divider"><span>{zh ? "或使用邮箱" : "or use email"}</span></p>
                  </>
                ) : null}

                <form onSubmit={submitAuth} autoComplete="on">
                  <label className="loom-site-field">
                    <span>{zh ? "邮箱" : "Email"}</span>
                    <div>
                      <Mail size={15} aria-hidden="true" />
                      <input
                        ref={emailInputRef}
                        type="email"
                        name="email"
                        autoComplete="email"
                        autoCapitalize="none"
                        spellCheck={false}
                        placeholder="name@example.com"
                        value={email}
                        onChange={(event) => setEmail(event.currentTarget.value)}
                        required
                      />
                    </div>
                  </label>

                  <label className="loom-site-field">
                    <span>
                      {zh ? "密码" : "Password"}
                      {authMode === "login" && account.capabilities.passwordReset ? (
                        <button type="button" onClick={() => { account.clearError(); setLocalError(""); setAuthStep("forgot"); }}>
                          {zh ? "忘记密码？" : "Forgot password?"}
                        </button>
                      ) : null}
                    </span>
                    <div>
                      <KeyRound size={15} aria-hidden="true" />
                      <input
                        ref={passwordInputRef}
                        type={showPassword ? "text" : "password"}
                        name="password"
                        autoComplete={authMode === "login" ? "current-password" : "new-password"}
                        placeholder="••••••••"
                        value={password}
                        onChange={(event) => setPassword(event.currentTarget.value)}
                        required
                      />
                      <button
                        type="button"
                        aria-pressed={showPassword}
                        aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")}
                        onClick={() => setShowPassword((value) => !value)}
                      >
                        {showPassword ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                      </button>
                    </div>
                  </label>

                  {authMode === "register" ? (
                    <>
                      <p className="loom-site-auth-note">
                        {zh ? "建议至少 12 位，并混合大小写、数字和符号。" : "12+ characters with mixed case, numbers and symbols is recommended."}
                      </p>
                      <label className="loom-site-field">
                        <span>{zh ? "确认密码" : "Confirm password"}</span>
                        <div>
                          <KeyRound size={15} aria-hidden="true" />
                          <input
                            type={showPassword ? "text" : "password"}
                            name="confirmPassword"
                            autoComplete="new-password"
                            placeholder="••••••••"
                            value={confirmPassword}
                            onChange={(event) => setConfirmPassword(event.currentTarget.value)}
                            required
                          />
                        </div>
                      </label>
                    </>
                  ) : null}

                  <button type="submit" className="loom-site-auth-submit" disabled={!account.ready || account.busy}>
                    {!account.ready
                      ? (zh ? "加载中…" : "Loading…")
                      : account.busy
                        ? (zh ? "处理中…" : "Working…")
                        : authMode === "login"
                          ? (zh ? "登录" : "Sign in")
                          : account.capabilities.emailVerification
                            ? (zh ? "创建并验证邮箱" : "Create & verify email")
                            : (zh ? "创建账户" : "Create account")}
                  </button>
                </form>

                <button type="button" className="loom-site-auth-switch" onClick={() => resetAuthFlow(authMode === "login" ? "register" : "login")}>
                  {authMode === "login"
                    ? (zh ? "还没有账户？创建一个" : "New to Loom? Create an account")
                    : (zh ? "已有账户？返回登录" : "Already have an account? Sign in")}
                </button>
              </>
            ) : authStep === "verify" ? (
              <>
                <p className="loom-site-auth-note">
                  {zh ? `我们已向 ${challengeEmail} 发送了 6 位验证码。` : `We sent a 6-digit code to ${challengeEmail}.`}
                </p>
                <form onSubmit={submitVerification}>
                  <label className="loom-site-field">
                    <span>{zh ? "验证码" : "Verification code"}</span>
                    <div>
                      <input
                        className="loom-site-code"
                        name="verificationCode"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        pattern="[0-9]{6}"
                        placeholder="000000"
                        value={verificationCode}
                        onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                        required
                      />
                    </div>
                  </label>
                  <button type="submit" className="loom-site-auth-submit" disabled={account.busy || verificationCode.length !== 6}>
                    {account.busy ? (zh ? "验证中…" : "Verifying…") : (zh ? "验证并创建账户" : "Verify & create account")}
                  </button>
                </form>
                <div className="loom-site-auth-row">
                  <button type="button" className="loom-site-auth-switch" disabled={account.busy || resendWait > 0} onClick={() => void resendVerification()}>
                    {resendWait > 0
                      ? (zh ? `${resendWait} 秒后可重发` : `Resend in ${resendWait}s`)
                      : (zh ? "重新发送验证码" : "Resend code")}
                  </button>
                  <button type="button" className="loom-site-auth-switch" onClick={() => resetAuthFlow("register")}>
                    {zh ? "返回" : "Back"}
                  </button>
                </div>
              </>
            ) : authStep === "forgot" ? (
              <>
                <p className="loom-site-auth-note">
                  {zh ? "输入你的邮箱，若账户存在我们会发送一次性验证码。" : "Enter your email. If the account exists, we'll send a one-time recovery code."}
                </p>
                <form onSubmit={submitForgot}>
                  <label className="loom-site-field">
                    <span>{zh ? "邮箱" : "Email"}</span>
                    <div>
                      <Mail size={15} aria-hidden="true" />
                      <input
                        type="email"
                        name="email"
                        autoComplete="email"
                        placeholder="name@example.com"
                        value={email}
                        onChange={(event) => setEmail(event.currentTarget.value)}
                        required
                      />
                    </div>
                  </label>
                  <button type="submit" className="loom-site-auth-submit" disabled={account.busy}>
                    {account.busy ? (zh ? "发送中…" : "Sending…") : (zh ? "发送重置验证码" : "Send reset code")}
                  </button>
                </form>
                <button type="button" className="loom-site-auth-switch" onClick={() => resetAuthFlow("login")}>
                  {zh ? "返回登录" : "Back to sign in"}
                </button>
              </>
            ) : (
              <>
                <p className="loom-site-auth-note">
                  {zh ? `如果 ${challengeEmail} 已注册，你会收到 6 位验证码。` : `If ${challengeEmail} is registered, a 6-digit code is on its way.`}
                </p>
                <form onSubmit={submitReset}>
                  <label className="loom-site-field">
                    <span>{zh ? "验证码" : "Verification code"}</span>
                    <div>
                      <input
                        className="loom-site-code"
                        name="verificationCode"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        maxLength={6}
                        pattern="[0-9]{6}"
                        placeholder="000000"
                        value={verificationCode}
                        onChange={(event) => setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                        required
                      />
                    </div>
                  </label>
                  <label className="loom-site-field">
                    <span>{zh ? "新密码" : "New password"}</span>
                    <div>
                      <KeyRound size={15} aria-hidden="true" />
                      <input
                        type={showPassword ? "text" : "password"}
                        name="password"
                        autoComplete="new-password"
                        value={password}
                        onChange={(event) => setPassword(event.currentTarget.value)}
                        required
                        minLength={8}
                      />
                      <button
                        type="button"
                        aria-pressed={showPassword}
                        aria-label={showPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")}
                        onClick={() => setShowPassword((value) => !value)}
                      >
                        {showPassword ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                      </button>
                    </div>
                  </label>
                  <label className="loom-site-field">
                    <span>{zh ? "确认新密码" : "Confirm new password"}</span>
                    <div>
                      <KeyRound size={15} aria-hidden="true" />
                      <input
                        type={showPassword ? "text" : "password"}
                        name="confirmPassword"
                        autoComplete="new-password"
                        value={confirmPassword}
                        onChange={(event) => setConfirmPassword(event.currentTarget.value)}
                        required
                        minLength={8}
                      />
                    </div>
                  </label>
                  <button type="submit" className="loom-site-auth-submit" disabled={account.busy || verificationCode.length !== 6}>
                    {account.busy ? (zh ? "重置中…" : "Resetting…") : (zh ? "重置密码并登录" : "Reset password & sign in")}
                  </button>
                </form>
                <button type="button" className="loom-site-auth-switch" onClick={() => resetAuthFlow("login")}>
                  {zh ? "返回登录" : "Back to sign in"}
                </button>
              </>
            )}

            {localError || account.error ? (
              <p className="loom-site-auth-error">{localError || account.error?.message}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
