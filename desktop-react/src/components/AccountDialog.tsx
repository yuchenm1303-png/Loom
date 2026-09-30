import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  LogOut,
  MailCheck,
  ShieldCheck,
  UserRound,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import type { LoomAccountError, LoomAccountSnapshot } from "../types/account";
import {
  ACCOUNT_PASSWORD_MIN_LENGTH,
  accountErrorText,
  accountServiceLabel,
  isValidAccountEmail,
} from "./accountMessages";
import "./account-auth.css";

interface AccountDialogProps {
  open: boolean;
  account: LoomAccountSnapshot;
  ready: boolean;
  busy: boolean;
  error: LoomAccountError | null;
  onClose(): void;
  onClearError(): void;
  onRetry(): void | Promise<void>;
  onLogin(email: string, password: string): Promise<boolean>;
  onRegister(email: string, password: string): Promise<boolean>;
  onLogout(): Promise<void>;
}

type AuthMode = "login" | "register";

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), [href], select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function AccountDialog({
  open,
  account,
  ready,
  busy,
  error,
  onClose,
  onClearError,
  onRetry,
  onLogin,
  onRegister,
  onLogout,
}: AccountDialogProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const presence = useMotionPresence(open, 235);
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [revealPassword, setRevealPassword] = useState(false);
  const [localError, setLocalError] = useState("");
  const [verificationCode, setVerificationCode] = useState("");
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationError, setVerificationError] = useState("");
  const [verificationNotice, setVerificationNotice] = useState("");
  const [resendAvailableAt, setResendAvailableAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const dialogRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const emailRef = useRef<HTMLInputElement | null>(null);
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const confirmRef = useRef<HTMLInputElement | null>(null);
  const verificationRef = useRef<HTMLInputElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const verification = account.verification;
  const working = busy || verificationBusy;

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => {
      const previous = restoreFocusRef.current;
      restoreFocusRef.current = null;
      if (previous && document.contains(previous)) previous.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      setPassword("");
      setConfirm("");
      setLocalError("");
      setRevealPassword(false);
      setVerificationError("");
      setVerificationNotice("");
      return;
    }
    const timer = window.setTimeout(() => {
      (verification ? verificationRef.current : emailRef.current) ?? closeRef.current?.focus();
      if (verification) verificationRef.current?.focus();
      else emailRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, ready, verification?.email]);

  useEffect(() => {
    if (!verification) {
      setVerificationCode("");
      setVerificationError("");
      setVerificationNotice("");
      setResendAvailableAt(0);
      return;
    }
    setEmail(verification.email);
    setVerificationCode("");
    setVerificationError("");
    setVerificationNotice("");
    setResendAvailableAt(verification.issuedAt + verification.resendAfter * 1000);
  }, [verification?.email, verification?.issuedAt, verification?.resendAfter]);

  useEffect(() => {
    if (!verification || resendAvailableAt <= Date.now()) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [verification, resendAvailableAt]);

  const switchMode = useCallback(
    (next: AuthMode) => {
      setMode(next);
      setLocalError("");
      setConfirm("");
      setRevealPassword(false);
      onClearError();
    },
    [onClearError],
  );

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (!working) onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const root = dialogRef.current;
    if (!root) return;
    const focusable = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!focusable.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || !root.contains(active)) {
        event.preventDefault();
        last.focus();
      }
      return;
    }
    if (active === last || !root.contains(active)) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!presence.mounted) return null;

  const emailValue = email.trim();
  const emailInvalid = email.length > 0 && !isValidAccountEmail(emailValue);
  const confirmMismatch = mode === "register" && confirm.length > 0 && confirm !== password;
  const confirmMatches = mode === "register" && confirm.length > 0 && confirm === password;
  const submitLabel = mode === "login"
    ? (zh ? "登录 Loom" : "Sign in to Loom")
    : (zh ? "创建 Loom 账号" : "Create Loom account");
  const resendSeconds = Math.max(0, Math.ceil((resendAvailableAt - now) / 1000));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLocalError("");
    if (!isValidAccountEmail(emailValue)) {
      setLocalError(zh ? "请输入有效的邮箱地址。" : "Enter a valid email address.");
      emailRef.current?.focus();
      return;
    }
    if (mode === "register") {
      if (password.length < ACCOUNT_PASSWORD_MIN_LENGTH) {
        setLocalError(
          zh
            ? `密码至少需要 ${ACCOUNT_PASSWORD_MIN_LENGTH} 位。`
            : `Use at least ${ACCOUNT_PASSWORD_MIN_LENGTH} characters.`,
        );
        passwordRef.current?.focus();
        return;
      }
      if (password !== confirm) {
        setLocalError(zh ? "两次输入的密码不一致。" : "Passwords do not match.");
        confirmRef.current?.focus();
        return;
      }
    }

    const ok = mode === "login"
      ? await onLogin(emailValue, password)
      : await onRegister(emailValue, password);
    if (ok) {
      setPassword("");
      setConfirm("");
      setRevealPassword(false);
    }
  };

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    if (!verification) return;
    const code = verificationCode.replace(/\D/g, "").slice(0, 6);
    if (code.length !== 6) {
      setVerificationError(zh ? "请输入邮件中的 6 位验证码。" : "Enter the 6-digit code from the email.");
      verificationRef.current?.focus();
      return;
    }

    setVerificationBusy(true);
    setVerificationError("");
    setVerificationNotice("");
    try {
      const result = await window.loom.accountVerifyEmail(verification.email, code);
      if (!result.ok) {
        setVerificationError(accountErrorText(result.error, zh));
        return;
      }
      setVerificationCode("");
      await onRetry();
    } catch (cause) {
      setVerificationError(
        cause instanceof Error
          ? cause.message
          : (zh ? "验证失败，请稍后再试。" : "Verification failed. Try again shortly."),
      );
    } finally {
      setVerificationBusy(false);
    }
  };

  const resend = async () => {
    if (!verification || resendSeconds > 0 || verificationBusy) return;
    setVerificationBusy(true);
    setVerificationError("");
    setVerificationNotice("");
    try {
      const result = await window.loom.accountResendVerification(verification.email);
      if (!result.ok) {
        setVerificationError(accountErrorText(result.error, zh));
        return;
      }
      const next = result.snapshot.verification;
      const issuedAt = next?.issuedAt ?? Date.now();
      const waitSeconds = next?.resendAfter ?? verification.resendAfter;
      setNow(Date.now());
      setResendAvailableAt(issuedAt + waitSeconds * 1000);
      setVerificationNotice(zh ? "新的验证码已发送。" : "A new verification code was sent.");
    } catch (cause) {
      setVerificationError(
        cause instanceof Error
          ? cause.message
          : (zh ? "暂时无法重发验证码。" : "Could not resend the verification code."),
      );
    } finally {
      setVerificationBusy(false);
    }
  };

  const displayName = account.user?.display_name?.trim() || account.user?.email || "";
  const offline = account.configured && !account.reachable;
  const message = localError || accountErrorText(error, zh);
  const serviceLabel = accountServiceLabel(account.serviceUrl);

  const offlineNotice = offline ? (
    <p className="loom-account-notice" role="status">
      <AlertTriangle size={15} aria-hidden="true" />
      <span>{zh ? "无法连接账号服务，请检查网络或服务状态。" : "Could not reach the account service."}</span>
      <button type="button" onClick={() => void onRetry()} disabled={working}>
        {zh ? "重试" : "Retry"}
      </button>
    </p>
  ) : null;

  const title = account.authenticated
    ? (zh ? "Loom 账号" : "Loom account")
    : verification
      ? (zh ? "验证邮箱" : "Verify your email")
      : (zh ? "登录 Loom" : "Sign in to Loom");
  const subtitle = account.authenticated
    ? (zh ? "你的身份会用于后续的模型权限、额度和云端服务。" : "Your identity will be used for model access, quotas, and cloud services.")
    : verification
      ? (zh ? "输入邮件中的验证码即可完成账号创建。" : "Enter the code from your email to finish creating the account.")
      : (zh ? "登录后即可使用与你账号绑定的云端能力。" : "Sign in to use cloud features attached to your account.");

  return (
    <div
      className="loom-account-backdrop"
      data-motion-phase={presence.phase}
      role="presentation"
      onMouseDown={presence.phase === "exiting" ? undefined : (event) => {
        if (event.target === event.currentTarget && !working) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="loom-account-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="loom-account-title"
        aria-busy={working}
        onKeyDown={onKeyDown}
      >
        <button
          ref={closeRef}
          type="button"
          className="loom-account-close"
          onClick={onClose}
          disabled={working}
          aria-label={zh ? "关闭" : "Close"}
        >
          <X size={17} />
        </button>

        <header className="loom-account-header">
          <span className="loom-account-mark" aria-hidden="true">
            {verification ? <MailCheck size={21} /> : <UserRound size={21} />}
          </span>
          <div>
            <h2 id="loom-account-title">{title}</h2>
            <p>{subtitle}</p>
          </div>
        </header>

        {!ready ? (
          <div className="loom-account-state" role="status">
            <span className="loom-account-spinner" aria-hidden="true" />
            {zh ? "正在检查登录状态…" : "Checking sign-in status…"}
          </div>
        ) : !account.configured ? (
          <div className="loom-account-unconfigured">
            <ShieldCheck size={20} />
            <strong>{zh ? "账号服务尚未配置" : "Account service is not configured"}</strong>
            <p>
              {zh
                ? "当前版本不会连接任何第三方站点。部署 Loom Account Service 后，只需为桌面端设置 LOOM_ACCOUNT_API_BASE_URL。"
                : "This build will not connect to any third-party service. Deploy Loom Account Service and set LOOM_ACCOUNT_API_BASE_URL for the desktop app."}
            </p>
          </div>
        ) : account.authenticated && account.user ? (
          <div className="loom-account-signed-in">
            {offlineNotice}
            <div className="loom-account-profile">
              <span className="loom-account-avatar" aria-hidden="true">
                {displayName.slice(0, 1).toUpperCase()}
              </span>
              <div>
                <strong>{displayName}</strong>
                <span>{account.user.email}</span>
              </div>
              <CheckCircle2 size={18} aria-hidden="true" />
            </div>
            <div className="loom-account-security-note">
              <ShieldCheck size={16} aria-hidden="true" />
              <span>{zh ? "登录凭据已由系统安全存储加密保存。" : "Sign-in credentials are encrypted with the operating system secure storage."}</span>
            </div>
            {serviceLabel ? (
              <p className="loom-account-service">{zh ? "服务地址" : "Service"} · {serviceLabel}</p>
            ) : null}
            <button
              type="button"
              className="loom-account-secondary-button"
              disabled={working}
              onClick={() => void onLogout()}
            >
              {busy ? <span className="loom-account-spinner" aria-hidden="true" /> : <LogOut size={15} />}
              {busy ? (zh ? "正在退出…" : "Signing out…") : (zh ? "退出登录" : "Sign out")}
            </button>
          </div>
        ) : verification ? (
          <>
            {offlineNotice}
            <div className="loom-account-security-note">
              <MailCheck size={16} aria-hidden="true" />
              <span>
                {zh ? "验证码已发送至" : "We sent a verification code to"} <strong>{verification.email}</strong>
              </span>
            </div>
            <form className="loom-account-form" onSubmit={(event) => void verify(event)} noValidate>
              <div className="loom-account-field">
                <label htmlFor="loom-account-verification-code">{zh ? "邮箱验证码" : "Verification code"}</label>
                <span className="loom-account-input-wrap">
                  <input
                    id="loom-account-verification-code"
                    ref={verificationRef}
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={verificationCode}
                    onChange={(event) => {
                      setVerificationCode(event.target.value.replace(/\D/g, "").slice(0, 6));
                      setVerificationError("");
                    }}
                    disabled={working}
                    maxLength={6}
                    placeholder="000000"
                    aria-describedby="loom-account-verification-hint"
                  />
                </span>
                <p className="loom-account-hint" id="loom-account-verification-hint">
                  {zh
                    ? `验证码 ${Math.max(1, Math.ceil(verification.expiresIn / 60))} 分钟内有效。`
                    : `The code is valid for ${Math.max(1, Math.ceil(verification.expiresIn / 60))} minutes.`}
                </p>
              </div>

              {verificationError || message ? (
                <p className="loom-account-error" role="alert">{verificationError || message}</p>
              ) : verificationNotice ? (
                <p className="loom-account-hint tone-ok" role="status">
                  <CheckCircle2 size={13} aria-hidden="true" /> {verificationNotice}
                </p>
              ) : null}

              <button
                className="loom-account-primary-button"
                type="submit"
                disabled={working || verificationCode.length !== 6}
              >
                {verificationBusy ? <span className="loom-account-spinner" aria-hidden="true" /> : null}
                {verificationBusy ? (zh ? "正在验证…" : "Verifying…") : (zh ? "验证并创建账号" : "Verify and create account")}
              </button>
              <button
                className="loom-account-secondary-button"
                type="button"
                disabled={working || resendSeconds > 0}
                onClick={() => void resend()}
              >
                {resendSeconds > 0
                  ? (zh ? `${resendSeconds} 秒后可重发` : `Resend in ${resendSeconds}s`)
                  : (zh ? "重新发送验证码" : "Resend verification code")}
              </button>
            </form>

            <p className="loom-account-footnote">
              <ShieldCheck size={13} aria-hidden="true" />
              {zh
                ? "验证码仅用于确认邮箱归属，服务端不会保存验证码明文。"
                : "The code only proves mailbox ownership; the server never stores it in plaintext."}
            </p>
            {serviceLabel ? (
              <p className="loom-account-service">{zh ? "服务地址" : "Service"} · {serviceLabel}</p>
            ) : null}
          </>
        ) : (
          <>
            {offlineNotice}
            <div className="loom-account-tabs" role="group" aria-label={zh ? "登录或注册" : "Sign in or create an account"}>
              <button
                type="button"
                className={mode === "login" ? "active" : ""}
                aria-pressed={mode === "login"}
                onClick={() => switchMode("login")}
              >
                {zh ? "登录" : "Sign in"}
              </button>
              <button
                type="button"
                className={mode === "register" ? "active" : ""}
                aria-pressed={mode === "register"}
                onClick={() => switchMode("register")}
              >
                {zh ? "注册" : "Create account"}
              </button>
            </div>

            <form className="loom-account-form" onSubmit={(event) => void submit(event)} noValidate>
              <div className="loom-account-field">
                <label htmlFor="loom-account-email">{zh ? "邮箱" : "Email"}</label>
                <span className="loom-account-input-wrap">
                  <input
                    id="loom-account-email"
                    ref={emailRef}
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    autoComplete="email"
                    disabled={working}
                    spellCheck={false}
                    aria-invalid={emailInvalid}
                    aria-describedby={emailInvalid ? "loom-account-email-hint" : undefined}
                    placeholder="name@example.com"
                  />
                </span>
                {emailInvalid ? (
                  <p className="loom-account-hint tone-bad" id="loom-account-email-hint">
                    {zh ? "邮箱格式看起来不正确。" : "That email address looks incomplete."}
                  </p>
                ) : null}
              </div>

              <div className="loom-account-field">
                <label htmlFor="loom-account-password">{zh ? "密码" : "Password"}</label>
                <span className="loom-account-input-wrap has-reveal">
                  <input
                    id="loom-account-password"
                    ref={passwordRef}
                    type={revealPassword ? "text" : "password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    disabled={working}
                    minLength={mode === "register" ? ACCOUNT_PASSWORD_MIN_LENGTH : undefined}
                    aria-describedby={mode === "register" ? "loom-account-password-hint" : undefined}
                    placeholder="••••••••"
                  />
                  <button
                    type="button"
                    className="loom-account-reveal"
                    onClick={() => setRevealPassword((value) => !value)}
                    disabled={working}
                    aria-pressed={revealPassword}
                    aria-label={revealPassword ? (zh ? "隐藏密码" : "Hide password") : (zh ? "显示密码" : "Show password")}
                  >
                    {revealPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </span>
                {mode === "register" ? (
                  <p className="loom-account-hint" id="loom-account-password-hint">
                    {zh ? `至少 ${ACCOUNT_PASSWORD_MIN_LENGTH} 位字符。` : `At least ${ACCOUNT_PASSWORD_MIN_LENGTH} characters.`}
                  </p>
                ) : null}
              </div>

              {mode === "register" ? (
                <div className="loom-account-field">
                  <label htmlFor="loom-account-confirm">{zh ? "确认密码" : "Confirm password"}</label>
                  <span className="loom-account-input-wrap">
                    <input
                      id="loom-account-confirm"
                      ref={confirmRef}
                      type={revealPassword ? "text" : "password"}
                      value={confirm}
                      onChange={(event) => setConfirm(event.target.value)}
                      autoComplete="new-password"
                      disabled={working}
                      minLength={ACCOUNT_PASSWORD_MIN_LENGTH}
                      aria-invalid={confirmMismatch}
                      aria-describedby={confirmMismatch || confirmMatches ? "loom-account-confirm-hint" : undefined}
                      placeholder="••••••••"
                    />
                  </span>
                  {confirmMismatch ? (
                    <p className="loom-account-hint tone-bad" id="loom-account-confirm-hint">
                      {zh ? "两次输入的密码不一致。" : "Passwords do not match."}
                    </p>
                  ) : confirmMatches ? (
                    <p className="loom-account-hint tone-ok" id="loom-account-confirm-hint">
                      <CheckCircle2 size={13} aria-hidden="true" />
                      {zh ? "两次输入一致。" : "Passwords match."}
                    </p>
                  ) : null}
                </div>
              ) : null}

              {message ? <p className="loom-account-error" role="alert">{message}</p> : null}

              <button className="loom-account-primary-button" type="submit" disabled={working}>
                {busy ? <span className="loom-account-spinner" aria-hidden="true" /> : null}
                {busy ? (zh ? "处理中…" : "Working…") : submitLabel}
              </button>
            </form>

            <p className="loom-account-footnote">
              <ShieldCheck size={13} aria-hidden="true" />
              {zh
                ? "密码不会保存到本机；只有加密后的会话令牌会进入系统安全存储。"
                : "Your password is never stored locally; only encrypted session tokens enter OS secure storage."}
            </p>
            {serviceLabel ? (
              <p className="loom-account-service">{zh ? "服务地址" : "Service"} · {serviceLabel}</p>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
}
